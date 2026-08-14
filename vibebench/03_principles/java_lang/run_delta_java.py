"""
!! SUPERSEDED AND VOID -- DO NOT RUN, DO NOT QUOTE ITS OUTPUT (2026-08-03) !!

This module is part of the tree-sitter Java layer that produced the VOID Java
results. Kept only as a record of what was tried and why it failed; see
JAVA_STATUS.md sections 2 and 6 for the measured defects.

The replacement is:
  03_principles/java_sidecar/Facts.java   -- JavaParser + SymbolSolver extractor
  03_principles/java_bridge.py            -- Python bridge + facts cache
  03_principles/java_lang/detectors.py    -- all five principles, self-checked
  03_principles/java_lang/run_java.py     -- one-pass delta harness

Why this one had to go, in one line: tree-sitter parses Java but cannot RESOLVE
symbols, so two classes named `Node` in different packages collapsed into one
and every type hierarchy silently corrupted -- which is why ISP found nothing
and LSP found seven override pairs in 867 pull requests.
"""

"""
Step 3-DELTA-JAVA - OCP, LSP, ISP and DIP over the Java corpus, in ONE pass.

Design note: the TypeScript port ran four separate delta harnesses, each
re-fetching and re-archiving the same (repo, sha) trees. Java runs all four
principles over a SINGLE before/after tree pair, so the expensive part -- git
fetch + archive + parse -- is paid once instead of four times.

Emits four parquets with the same column vocabulary as the Python and
TypeScript twins, so the three languages compare directly.

Run: python vibebench/03_principles/java_lang/run_delta_java.py [--limit N] [--workers N]
"""
from __future__ import annotations

import argparse
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))
sys.path.insert(0, str(_HERE.parents[1] / "dip"))
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parents[2]))
import config as C  # noqa: E402
import gitsnap  # noqa: E402
import principles as P  # noqa: E402
from graph import abstract_dep_ratio, find_cycles, martin_metrics, top_package  # noqa: E402

EXTS = C.JAVA_SOURCE_EXTENSIONS
EXCL = C.JAVA_EXCLUDED_PATH_PARTS
PERMANENT = {"ok", "no_touched_modules", "repo_too_large"}


def snapshot(files: dict[str, str], touched_pkgs: set[str], index=None) -> dict:
    """All four principles' in-scope state for one tree.

    The tree is PARSED ONCE and the index shared across all four detectors.
    Without this each detector rebuilt its own index -- 4 full-tree parses per
    snapshot, 8 per pull request. Measured on the first full run: ~1 CPU-core
    saturated and 654 of 867 PRs after 71 minutes. tree-sitter parsing is
    Python-level and GIL-bound, so extra threads do not help; removing the
    redundancy is what does.
    """
    idx = index if index is not None else P.build_index(files)
    chains = [c for c in P.find_dispatch_chains(files, index=idx)
              if c.module in touched_pkgs]
    pairs = [p for p in P.find_overrides(files, index=idx) if p.module in touched_pkgs]
    contracts, impls = P.find_interfaces(files, index=idx)
    contracts = {k: v for k, v in contracts.items() if v.module in touched_pkgs}
    impls = [i for i in impls if i.module in touched_pkgs]
    mods = P.build_modules(files, index=idx)
    return {"chains": chains, "pairs": pairs, "contracts": contracts,
            "impls": impls, "mods": mods}


def process_pr(pr, thr) -> dict:
    meta = {"pr_id": pr.pr_id, "agent": pr.agent, "full_name": pr.full_name,
            "pr_number": pr.pr_number, "merge_sha": pr.merge_sha}
    touched = gitsnap.touched_files(pr, EXTS, EXCL, extract_dir=C.EXTRACT_DIR_JAVA)
    if not touched:
        return {"status": "no_touched_modules", "meta": meta}

    d = gitsnap.ensure_repo(pr.full_name)
    with gitsnap.repo_lock(pr.full_name):
        pair = gitsnap.delta_pair(d, pr)
        if pair is None:
            has = isinstance(pr.merge_sha, str) and bool(pr.merge_sha)
            return {"status": "fetch_failed" if has else "no_merge_sha", "meta": meta}
        bsha, asha = pair
        bf = gitsnap.src_tree(d, bsha, EXTS, EXCL, pr.full_name)
        af = gitsnap.src_tree(d, asha, EXTS, EXCL, pr.full_name)
    if bf is None or af is None:
        return {"status": "archive_failed", "meta": meta}
    if gitsnap.TOO_LARGE in bf or gitsnap.TOO_LARGE in af:
        return {"status": "repo_too_large", "meta": meta}

    # Build each tree's index ONCE and reuse it for everything below --
    # including the touched-package lookup, which previously re-parsed every
    # touched file a second time before snapshot() parsed it again.
    bidx, aidx = P.build_index(bf), P.build_index(af)
    tp = {aidx[0][p].package or bidx[0].get(p, aidx[0][p]).package
          for p in touched if p in aidx[0] or p in bidx[0]}
    tp = {x for x in tp if x}
    if not tp:
        return {"status": "no_touched_modules", "meta": meta}

    n_parse_err = sum(1 for f in aidx[0].values() if not f.parse_ok)
    b, a = snapshot(bf, tp, index=bidx), snapshot(af, tp, index=aidx)

    # ---- OCP: new or widened dispatch smell -------------------------------
    bkey = {(c.module, c.qualname, c.subject): c for c in b["chains"]}
    ocp_reasons = []
    for c in a["chains"]:
        if not P.is_dispatch_smell(c, thr["ocp"]):
            continue
        prev = bkey.get((c.module, c.qualname, c.subject))
        if prev is None:
            ocp_reasons.append("new_dispatch_smell")
        elif c.n_branches > prev.n_branches:
            ocp_reasons.append("dispatch_widened")

    # ---- LSP: new violating override --------------------------------------
    bp = {(p.module, p.qualname, p.method) for p in b["pairs"]}
    lsp_reasons = sorted({("new_" + s) for p in a["pairs"]
                          if (p.module, p.qualname, p.method) not in bp
                          for s in p.signals})

    # ---- ISP: new fat contract / new forced stub --------------------------
    isp_reasons = []
    for cid, c in a["contracts"].items():
        if c.n_members < thr["isp"]["fat_members"]:
            continue
        prev = b["contracts"].get(cid)
        if prev is None:
            isp_reasons.append("new_flagged_iface")
        elif c.n_members > prev.n_members:
            isp_reasons.append("widened_fat_iface")
    bstub = {(i.iface_id, i.class_id) for i in b["impls"]
             if i.n_stubs >= thr["isp"]["min_stubs"]
             and i.stub_ratio >= thr["isp"]["stub_ratio"]}
    for i in a["impls"]:
        if (i.n_stubs >= thr["isp"]["min_stubs"]
                and i.stub_ratio >= thr["isp"]["stub_ratio"]
                and (i.iface_id, i.class_id) not in bstub):
            isp_reasons.append("new_forced_stub")

    # ---- DIP: new cycle / worse mean distance -----------------------------
    def dip_state(mods):
        scope = {m for m in tp if m in mods}
        cyc = [c for c in find_cycles(mods) if c & scope]
        mm = martin_metrics(mods)
        pkgs = {top_package(m) for m in scope}
        ds = [mm[p]["D"] for p in pkgs if p in mm and mm[p]["D"] is not None]
        return ({frozenset(c) for c in cyc}, len(cyc),
                (sum(ds) / len(ds)) if ds else None,
                abstract_dep_ratio(mods, scope))
    bc, bn, bd, ba = dip_state(b["mods"])
    ac, an, ad, aa = dip_state(a["mods"])
    new_cycles = ac - bc
    dmean = (ad - bd) if (ad is not None and bd is not None) else None
    dip_hits = []
    if len(new_cycles) > thr["dip"]["new_touched_cycles"]:
        dip_hits.append("new_cycle")
    if dmean is not None and dmean > thr["dip"]["delta_mean_d"]:
        dip_hits.append("mean_d_worse")

    return {
        "status": "ok", "meta": meta, "n_touched": len(tp),
        "n_parse_errors": n_parse_err,
        "ocp": {"reasons": sorted(set(ocp_reasons)),
                "n_chains": len(a["chains"]),
                "n_smells": sum(1 for c in a["chains"]
                                if P.is_dispatch_smell(c, thr["ocp"])),
                "chains": a["chains"]},
        "lsp": {"reasons": lsp_reasons, "n_pairs": len(a["pairs"]),
                "pairs": a["pairs"]},
        "isp": {"reasons": sorted(set(isp_reasons)),
                "n_contracts": len(a["contracts"]),
                "n_fat": sum(1 for c in a["contracts"].values()
                             if c.n_members >= thr["isp"]["fat_members"]),
                "contracts": list(a["contracts"].values()), "impls": a["impls"]},
        "dip": {"hits": dip_hits, "new_cycle": len(new_cycles) > 0,
                "before_cycles": bn, "after_cycles": an,
                "before_mean_d": bd, "after_mean_d": ad, "delta_mean_d": dmean,
                "delta_adr": (aa - ba) if (aa is not None and ba is not None) else None},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=C.FETCH_WORKERS)
    args = ap.parse_args()
    thr = C.JAVA_DELTA_THRESHOLDS

    gold = pd.read_parquet(C.GOLD_SET_JAVA)
    if args.limit:
        gold = gold.head(args.limit)
    groups = list(gold.groupby("full_name", sort=False))
    print(f"[java-delta] {len(gold)} PRs across {len(groups)} repos, "
          f"{args.workers} workers", flush=True)

    ocp_rows, lsp_rows, isp_rows, dip_rows = [], [], [], []
    lock, done = threading.Lock(), 0

    def do_repo(name, grp):
        out = []
        for pr in grp.itertuples(index=False):
            try:
                out.append(process_pr(pr, thr))
            except Exception as e:  # noqa: BLE001
                print(f"  !! {name}#{pr.pr_number}: {type(e).__name__}: {e}", flush=True)
                out.append({"status": "error",
                            "meta": {"pr_id": pr.pr_id, "agent": pr.agent,
                                     "full_name": pr.full_name,
                                     "pr_number": pr.pr_number,
                                     "merge_sha": pr.merge_sha}})
        return out

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = {ex.submit(do_repo, n, g): (n, len(g)) for n, g in groups}
        for fut in as_completed(futs):
            name, n_grp = futs[fut]
            for r in fut.result():
                m, st = r["meta"], r["status"]
                base = {**m, "row_type": "pr", "status": st}
                if st != "ok":
                    ocp_rows.append({**base, "ocp_risk": None})
                    lsp_rows.append({**base, "lsp_risk": None})
                    isp_rows.append({**base, "isp_risk": None})
                    dip_rows.append({**base, "dip_risk": None})
                    continue
                o, l, i, d = r["ocp"], r["lsp"], r["isp"], r["dip"]
                ocp_rows.append({**base, "n_touched_modules": r["n_touched"],
                                 "n_chains_in_scope": o["n_chains"],
                                 "n_smells_in_scope": o["n_smells"],
                                 "ocp_risk_reasons": "|".join(o["reasons"]),
                                 "ocp_risk": len(o["reasons"]) > 0})
                for c in o["chains"]:
                    ocp_rows.append({**m, "row_type": "dispatch", "status": "ok",
                                     "module": c.module, "qualname": c.qualname,
                                     "subject": c.subject, "kind": c.kind,
                                     "n_branches": c.n_branches, "lineno": c.lineno,
                                     "file_path": c.file_path,
                                     "closed_set": c.closed_set,
                                     "type_names": c.type_names,
                                     "smell": P.is_dispatch_smell(c, thr["ocp"])})
                lsp_rows.append({**base, "n_overrides_in_scope": l["n_pairs"],
                                 "lsp_risk_reasons": "|".join(l["reasons"]),
                                 "lsp_risk": len(l["reasons"]) > 0})
                for p in l["pairs"]:
                    lsp_rows.append({**m, "row_type": "override", "status": "ok",
                                     "module": p.module, "qualname": p.qualname,
                                     "base_id": p.base_id, "method": p.method,
                                     "lineno": p.lineno, "file_path": p.file_path,
                                     "signals": ";".join(p.signals),
                                     "violation": True})
                isp_rows.append({**base, "n_interfaces_in_scope": i["n_contracts"],
                                 "n_flagged_ifaces": i["n_fat"],
                                 "isp_risk_reasons": "|".join(i["reasons"]),
                                 "isp_risk": len(i["reasons"]) > 0})
                for c in i["contracts"]:
                    isp_rows.append({**m, "row_type": "interface", "status": "ok",
                                     "iface_id": c.iface_id, "module": c.module,
                                     "qualname": c.name, "kind": c.kind,
                                     "n_members": c.n_members, "lineno": c.lineno,
                                     "file_path": c.file_path,
                                     "flagged": c.n_members >= thr["isp"]["fat_members"]})
                for im in i["impls"]:
                    isp_rows.append({**m, "row_type": "implementer", "status": "ok",
                                     "iface_id": im.iface_id, "module": im.module,
                                     "qualname": im.qualname,
                                     "iface_width": im.iface_width,
                                     "n_implemented": im.n_implemented,
                                     "n_stubs": im.n_stubs, "coverage": im.coverage,
                                     "stub_ratio": im.stub_ratio,
                                     "file_path": im.file_path,
                                     "forced_stub": (im.n_stubs >= thr["isp"]["min_stubs"]
                                                     and im.stub_ratio >= thr["isp"]["stub_ratio"])})
                dip_rows.append({**base, "n_touched_modules": r["n_touched"],
                                 "before_touched_cycles": d["before_cycles"],
                                 "after_touched_cycles": d["after_cycles"],
                                 "before_mean_d": d["before_mean_d"],
                                 "after_mean_d": d["after_mean_d"],
                                 "delta_mean_d": d["delta_mean_d"],
                                 "delta_abstract_dep_ratio": d["delta_adr"],
                                 "new_cycle": d["new_cycle"],
                                 "dip_risk_hits": "|".join(d["hits"]),
                                 "dip_risk": len(d["hits"]) > 0})
            with lock:
                done += n_grp
                print(f"[java-delta] {done}/{len(gold)}", flush=True)
                # Checkpoint: the first full run wrote nothing until the very
                # end, so killing it at 654/867 lost everything. Flush every
                # ~50 PRs instead.
                if done % 50 < n_grp:
                    for rows, path in ((ocp_rows, C.OCP_RESULTS_JAVA),
                                       (lsp_rows, C.LSP_RESULTS_JAVA),
                                       (isp_rows, C.ISP_RESULTS_JAVA),
                                       (dip_rows, C.DIP_RESULTS_JAVA)):
                        if rows:
                            pd.DataFrame(rows).to_parquet(path, index=False)

    for rows, path, name in ((ocp_rows, C.OCP_RESULTS_JAVA, "OCP"),
                             (lsp_rows, C.LSP_RESULTS_JAVA, "LSP"),
                             (isp_rows, C.ISP_RESULTS_JAVA, "ISP"),
                             (dip_rows, C.DIP_RESULTS_JAVA, "DIP")):
        df = pd.DataFrame(rows)
        df.to_parquet(path, index=False)
        prs = df[df.row_type == "pr"]
        ok = prs[prs.status == "ok"]
        col = f"{name.lower()}_risk"
        n = int(ok[col].fillna(False).sum()) if len(ok) else 0
        print(f"\n[{name}] {len(prs)} PRs ({len(ok)} ok) -> {n} flagged")
        if name == "DIP" and len(ok):
            print(f"      new_cycle: {int(ok['new_cycle'].fillna(False).sum())}")


if __name__ == "__main__":
    main()
