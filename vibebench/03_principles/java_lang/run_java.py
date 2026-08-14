"""
Step 3-JAVA - all five principles in ONE delta pass over the Codex Java corpus.

Replaces run_delta_java.py + run_srp_java.py, which were built on the void
tree-sitter layer.

WHY ONE PASS: each PR needs two fully-parsed, symbol-resolved trees. Parsing a
tree costs ~10-20 s, so running five harnesses would parse each tree five
times. One pass parses each tree once (and the java_bridge facts cache means
trees shared between PRs in the same repo are parsed once overall -- with 867
PRs over 27 repos that is a large saving).

SCOPE PER PRINCIPLE, and why they differ (the defect-#13 fix):
  SRP  every type in a file the PR touched, scored on the AFTER tree, with
       `is_new` recorded so "Codex WROTE a god class" is separable from
       "Codex edited a pre-existing one". The void run conflated them.
  OCP  per dispatch chain keyed (type, method, subject); charged only when the
       PR creates a new smell or widens an existing chain past the bar.
  LSP  per override pair; needs the whole-repo resolved hierarchy. A pair is
       charged when the PR touched EITHER side -- subclass or base (ripple-aware
       attribution; scoping on the subclass alone missed base-class edits that
       break untouched subclasses).
  ISP  per contract + implementer; same either-side rule.
  DIP  package graph over the whole repo; only packages the PR touched are
       charged, matching run_dip.py.

CALIBRATION MODE: when a principle's thresholds are None in config, its flags
are left null and the raw distribution is printed at the end, so thresholds are
set from Java's own percentiles BEFORE any labelling. That is the non-circular
method the OCP and TypeScript ports used; the void run copied Python's numbers
and put the switch bar at Java's p75.

Run:  python vibebench/03_principles/java_lang/run_java.py [--limit N] [--workers N]
      [--calibrate]   force calibration mode regardless of config
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))            # vibebench/
sys.path.insert(0, str(_HERE.parents[1]))            # 03_principles/
sys.path.insert(0, str(_HERE.parent))                # java_lang/
sys.path.insert(0, str(_HERE.parents[1] / "dip"))

import config as C            # noqa: E402
import gitsnap                # noqa: E402
import graph as dipgraph      # noqa: E402
import detectors as D         # noqa: E402
from java_bridge import analyze_tree  # noqa: E402

CHECKPOINT_EVERY = 25
PERMANENT = {"ok", "no_touched_files", "no_delta_pair", "repo_too_large"}

_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def java_pkg(module: str) -> str:
    """Compilation-unit name -> its Java package (`a.b.C` -> `a.b`).

    The Java replacement for `dipgraph.top_package`, which returns the first
    dotted segment and is meaningless for reverse-DNS packages.
    """
    return module.rsplit(".", 1)[0] if "." in module else module


# ---------------------------------------------------------------- snapshots

def snapshot(ix: D.Index, touched: set[str], th: dict) -> dict:
    """Everything the delta needs from ONE tree, restricted to touched scope."""
    tp = set()
    for f in touched:
        for tid in ix.by_file.get(f, []):
            t = ix.types.get(tid)
            if t and t.get("pkg"):
                tp.add(t["pkg"])

    chains = {c["key"]: c for c in D.dispatch_chains(ix)
              if c["file_path"] in touched}
    # ATTRIBUTION IS RIPPLE-AWARE: a relation is charged to the PR when the PR
    # touched EITHER side of it. Scoping on the flagged entity alone missed the
    # reverse direction -- an agent editing a BASE class or WIDENING an
    # interface breaks subclasses and implementers in files it never opened,
    # and those are untouched by construction. Measured exposure before the fix:
    # 4% of PRs, and where it occurred the invisible pairs were ~0.5x the
    # attributed ones.
    def _file_of(tid):
        t = ix.types.get(tid)
        return t["file"] if t else None

    pairs = {p["key"]: p for p in D.override_pairs(ix)
             if p["file_path"] in touched or _file_of(p["base_id"]) in touched}
    crows, irows = D.contracts(ix, th["isp"])
    contracts = {c["key"]: c for c in crows
                 if c["file_path"] in touched
                 or any(_file_of(i) in touched for i in c.get("impl_ids", ()))}
    impls = {i["key"]: i for i in irows
             if i["file_path"] in touched or _file_of(i["iface_id"]) in touched}

    mods = D.dip_modules(ix)
    cycles = dipgraph.find_cycles(mods)
    # THE JAVA PACKAGE UNIT. `dipgraph.top_package` takes the FIRST dotted
    # segment, which is right for Python/TypeScript (`pkg.mod`) and catastrophic
    # for Java: reverse-DNS names collapse the whole corpus into `net`/`com`/
    # `org`. `tp` holds real package names, so the filter below never matched and
    # `martin[p]` was never found -- which is why DIP-Java reported new_cycle
    # False on all 867 PRs and a null mean_d on 866 of them. A module here is a
    # compilation unit (`net.openhft.chronicle.core.Maths`), so its package is
    # the name minus the type.
    touched_cycles = [c for c in cycles if any(java_pkg(m) in tp for m in c)]
    martin = dipgraph.martin_metrics(mods, package_of=java_pkg)
    ds = [martin[p]["D"] for p in tp if p in martin and martin[p].get("D") is not None]
    return {
        "chains": chains, "pairs": pairs, "contracts": contracts, "impls": impls,
        # PAIR-level novelty (dipgraph.cyclic_pairs). Comparing whole SCCs makes
        # any membership change in a large pre-existing component look brand new
        # -- including a PR that REMOVES a module from a cycle. A pair (u,v) is
        # newly cyclic iff the two became mutually reachable.
        "cyclic_pairs": dipgraph.cyclic_pairs(cycles),
        "n_touched_cycles": len(touched_cycles),
        # compilation units (not packages) that the PR touched
        "touched_modules": {m["name"] for m in ix.modules if m["file"] in touched},
        "mean_d": (sum(ds) / len(ds)) if ds else None,
        "touched_pkgs": tp,
    }


def analyse_pr(pr, th: dict, calibrate: dict) -> dict:
    """-> {'pr': {...}, 'srp': [...], 'ocp': [...], 'lsp': [...], 'isp': [...], 'impl': [...]}"""
    base = {"pr_id": pr.pr_id, "agent": pr.agent, "full_name": pr.full_name,
            "pr_number": int(pr.pr_number)}
    out = {"pr": dict(base), "srp": [], "ocp": [], "lsp": [], "isp": [], "impl": []}

    touched = set(gitsnap.touched_files(pr, C.JAVA_SOURCE_EXTENSIONS,
                                        C.JAVA_EXCLUDED_PATH_PARTS,
                                        extract_dir=C.EXTRACT_DIR_JAVA))
    if not touched:
        out["pr"]["status"] = "no_touched_files"
        return out

    d = gitsnap.ensure_repo(pr.full_name)
    with gitsnap.repo_lock(pr.full_name):
        pair = gitsnap.delta_pair(d, pr)
        if pair is None:
            out["pr"]["status"] = "no_delta_pair"
            return out
        before_sha, after_sha = pair
        bt = gitsnap.src_tree(d, before_sha, C.JAVA_SOURCE_EXTENSIONS,
                              C.JAVA_EXCLUDED_PATH_PARTS, pr.full_name)
        at = gitsnap.src_tree(d, after_sha, C.JAVA_SOURCE_EXTENSIONS,
                              C.JAVA_EXCLUDED_PATH_PARTS, pr.full_name)
    if bt is None or at is None:
        out["pr"]["status"] = "archive_failed"
        return out
    if gitsnap.TOO_LARGE in bt or gitsnap.TOO_LARGE in at:
        out["pr"]["status"] = "repo_too_large"
        return out

    bf, af = analyze_tree(bt), analyze_tree(at)
    if not af.get("ok"):
        out["pr"]["status"] = "sidecar_failed"
        out["pr"]["error"] = str(af.get("error"))[:200]
        return out
    if not bf.get("ok"):
        bf = {"ok": True, "files": [], "types": [], "modules": []}

    bix, aix = D.build_index(bf), D.build_index(af)
    b = snapshot(bix, touched, th)
    a = snapshot(aix, touched, th)

    out["pr"]["status"] = "ok"
    out["pr"]["n_touched_files"] = len(touched)
    out["pr"]["n_parse_errors"] = sum(
        1 for p, f in aix.files.items() if p in touched and not f["parseOk"])
    out["pr"]["resolved_supertype_rate"] = (
        af["resolvedSupertypes"] / max(af["resolvedSupertypes"] + af["unresolvedSupertypes"], 1))

    # ---------------- SRP (per type, after tree, touched files)
    before_ids = set(bix.types)
    for f in sorted(touched):
        for tid in aix.by_file.get(f, []):
            t = aix.types[tid]
            mv = D.srp_metrics(t)
            row = dict(base, type_id=tid, type_name=t["name"], file_path=f,
                       kind=t["kind"], is_nested=t["isNested"], is_abstract=t["isAbstract"],
                       is_new=tid not in before_ids, **mv)
            if calibrate["srp"]:
                row.update(srp_risk=None, srp_risk_strict=None, hits="", c3_suppressed=None)
            else:
                risk, strict, hits, sup = D.srp_risk(mv, th["srp"])
                row.update(srp_risk=risk, srp_risk_strict=strict,
                           hits="|".join(hits), c3_suppressed=sup)
            out["srp"].append(row)

    # ---------------- OCP
    reasons = Counter()
    for k, c in a["chains"].items():
        smell = None if calibrate["ocp"] else D.is_dispatch_smell(c, th["ocp"])
        out["ocp"].append(dict(base, type_id=c["type_id"], method=c["method"],
                               subject=c["subject"], subject_type=c["subject_type"],
                               kind=c["kind"], n_branches=c["n_branches"],
                               lineno=c["lineno"], file_path=c["file_path"],
                               type_names=c["type_names"], closed_set=c["closed_set"],
                               smell=smell))
        if calibrate["ocp"]:
            continue
        old = b["chains"].get(k)
        if smell and old is None:
            reasons["new_dispatch_smell"] += 1
        elif smell and old is not None and c["n_branches"] > old["n_branches"]:
            reasons["dispatch_widened"] += 1
    out["pr"]["n_chains_in_scope"] = len(a["chains"])
    out["pr"]["n_smells_in_scope"] = sum(1 for c in out["ocp"] if c["smell"])
    out["pr"]["ocp_risk_reasons"] = "|".join(sorted(reasons))
    out["pr"]["ocp_risk"] = None if calibrate["ocp"] else bool(reasons)

    # ---------------- LSP
    strong = getattr(C, "LSP_JAVA_STRONG_SIGNALS",
                     ["refused_bequest", "precondition_strengthened", "postcondition_weakened"])
    lr = Counter()
    for k, p in a["pairs"].items():
        viol = D.is_lsp_violation(p, strong)
        out["lsp"].append(dict(base, type_id=p["type_id"], base_id=p["base_id"],
                               method=p["method"], file_path=p["file_path"],
                               lineno=p["lineno"], signals=p["signals"], violation=viol))
        if not viol:
            continue
        old = b["pairs"].get(k)
        if old is None or not D.is_lsp_violation(old, strong):
            for s in p["signals"].split(";"):
                if s in strong:
                    lr[f"new_{s}"] += 1
    out["pr"]["n_overrides_in_scope"] = len(a["pairs"])
    out["pr"]["n_violations_in_scope"] = sum(1 for p in out["lsp"] if p["violation"])
    out["pr"]["lsp_risk_reasons"] = "|".join(sorted(lr))
    out["pr"]["lsp_risk"] = bool(lr)

    # ---------------- ISP
    ir = Counter()
    for k, c in a["contracts"].items():
        out["isp"].append(dict(base, iface_id=c["iface_id"], file_path=c["file_path"],
                               lineno=c["lineno"], kind=c["kind"], n_members=c["n_members"],
                               n_implementers=c["n_implementers"],
                               flagged=None if calibrate["isp"] else c["flagged"]))
        if calibrate["isp"]:
            continue
        old = b["contracts"].get(k)
        if c["flagged"] and old is None:
            ir["new_flagged_iface"] += 1
        elif c["flagged"] and old is not None and c["n_members"] > old["n_members"]:
            ir["widened_fat_iface"] += 1
    for k, i in a["impls"].items():
        out["impl"].append(dict(base, iface_id=i["iface_id"], type_id=i["type_id"],
                                file_path=i["file_path"], iface_width=i["iface_width"],
                                n_implemented=i["n_implemented"], n_stubs=i["n_stubs"],
                                coverage=i["coverage"], stub_ratio=i["stub_ratio"],
                                forced_stub=None if calibrate["isp"] else i["forced_stub"]))
        if calibrate["isp"]:
            continue
        old = b["impls"].get(k)
        if i["forced_stub"] and (old is None or not old["forced_stub"]):
            ir["new_forced_stub"] += 1
    out["pr"]["n_contracts_in_scope"] = len(a["contracts"])
    out["pr"]["isp_risk_reasons"] = "|".join(sorted(ir))
    out["pr"]["isp_risk"] = None if calibrate["isp"] else bool(ir)

    # ---------------- DIP
    # A cycle is NEW only if the before graph did not contain it AT ALL;
    # it is charged to this PR only if it also involves a touched package.
    new_pairs = a["cyclic_pairs"] - b["cyclic_pairs"]
    # Filter at COMPILATION-UNIT level, matching run_dip.py. Filtering by package
    # is far too coarse for the common single-package Android/Java layout: one
    # touched file then admits every new pair in the whole package. Measured on
    # Harmonic-HN#220 that was 44 pairs where only ONE involved a touched file.
    tm_ = a["touched_modules"]
    new_cycles = {q for q in new_pairs if q[0] in tm_ or q[1] in tm_}
    dh = []
    if new_cycles:
        dh.append("new_cycle")
    dd = None
    if a["mean_d"] is not None and b["mean_d"] is not None:
        dd = a["mean_d"] - b["mean_d"]
        if dd >= th["dip"]["delta_mean_d"]:
            dh.append("mean_d_worse")
    out["pr"].update(
        before_touched_cycles=b["n_touched_cycles"], after_touched_cycles=a["n_touched_cycles"],
        new_cycle=bool(new_cycles), before_mean_d=b["mean_d"], after_mean_d=a["mean_d"],
        delta_mean_d=dd, dip_risk_hits="|".join(dh), dip_risk=bool(dh),
        n_touched_packages=len(a["touched_pkgs"]),
    )
    return out


# ---------------------------------------------------------------- driver

OUTPUTS = {
    "pr": None,
    "srp": C.SRP_RESULTS_JAVA, "ocp": C.OCP_RESULTS_JAVA,
    "lsp": C.LSP_RESULTS_JAVA, "isp": C.ISP_RESULTS_JAVA,
}
PR_OUT = C.CORPUS_DIR / "java_pr_results.parquet"


def write_all(prs: list[dict], buckets: dict[str, list]) -> None:
    pd.DataFrame(prs).to_parquet(PR_OUT, index=False)
    pd.DataFrame(buckets["srp"]).to_parquet(C.SRP_RESULTS_JAVA, index=False)
    pd.DataFrame(buckets["ocp"]).to_parquet(C.OCP_RESULTS_JAVA, index=False)
    pd.DataFrame(buckets["lsp"]).to_parquet(C.LSP_RESULTS_JAVA, index=False)
    isp = pd.DataFrame(buckets["isp"]).assign(row_type="interface")
    imp = pd.DataFrame(buckets["impl"]).assign(row_type="implementer")
    pd.concat([isp, imp], ignore_index=True).to_parquet(C.ISP_RESULTS_JAVA, index=False)
    pd.DataFrame(prs).to_parquet(C.DIP_RESULTS_JAVA, index=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=getattr(C, "ANALYZE_WORKERS", 6))
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--fresh", action="store_true", help="ignore existing results")
    a = ap.parse_args()

    th = {"srp": C.SRP_JAVA_THRESHOLDS, **C.JAVA_DELTA_THRESHOLDS}
    calibrate = {k: (a.calibrate or th.get(k) is None) for k in ("srp", "ocp", "isp")}
    # During calibration the rows must still be EMITTED so their distribution can
    # be measured; only the flags are suppressed. These placeholders are used for
    # scoping only and never reach a published number.
    CAL_DEFAULTS = {
        "srp": {"class_loc": 10 ** 9, "class_wmc": 10 ** 9, "class_methods": 10 ** 9,
                "func_ccn": 10 ** 9, "func_params": 10 ** 9, "class_lcom": 2.0,
                "lcom_min_methods": 4, "c3_suppress": 2.0, "min_hits": 99, "strict_hits": 99},
        "ocp": {"type_branches": 10 ** 9, "value_branches": 10 ** 9},
        "isp": {"fat_members": 10 ** 9, "min_stubs": 10 ** 9, "stub_ratio": 2.0},
    }
    for k in ("srp", "ocp", "isp"):
        if th.get(k) is None:
            th[k] = CAL_DEFAULTS[k]
    if any(calibrate.values()):
        log(f"CALIBRATION MODE for: {[k for k, v in calibrate.items() if v]} "
            f"-- flags left null, distributions printed at the end")

    gold = pd.read_parquet(C.GOLD_SET_JAVA)
    done: set[str] = set()
    prs: list[dict] = []
    buckets = {k: [] for k in ("srp", "ocp", "lsp", "isp", "impl")}
    if PR_OUT.exists() and not a.fresh:
        old = pd.read_parquet(PR_OUT)
        keep = old[old.status.isin(PERMANENT)]
        done = set(keep.pr_id)
        prs = keep.to_dict("records")
        for k, p in (("srp", C.SRP_RESULTS_JAVA), ("ocp", C.OCP_RESULTS_JAVA),
                     ("lsp", C.LSP_RESULTS_JAVA)):
            if p.exists():
                dfp = pd.read_parquet(p)
                buckets[k] = dfp[dfp.pr_id.isin(done)].to_dict("records")
        if C.ISP_RESULTS_JAVA.exists():
            dfp = pd.read_parquet(C.ISP_RESULTS_JAVA)
            dfp = dfp[dfp.pr_id.isin(done)]
            buckets["isp"] = dfp[dfp.row_type == "interface"].drop(columns=["row_type"]).to_dict("records")
            buckets["impl"] = dfp[dfp.row_type == "implementer"].drop(columns=["row_type"]).to_dict("records")
        log(f"resuming: {len(done)} PRs already have a permanent status")

    todo = [r for r in gold.itertuples() if r.pr_id not in done]
    if a.limit:
        todo = todo[:a.limit]
    log(f"{len(todo)} PRs to analyse, {a.workers} workers")

    t0, n = time.time(), 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(analyse_pr, pr, th, calibrate): pr for pr in todo}
        for fut in as_completed(futs):
            pr = futs[fut]
            try:
                res = fut.result()
            except Exception as e:
                res = {"pr": {"pr_id": pr.pr_id, "agent": pr.agent, "full_name": pr.full_name,
                              "pr_number": int(pr.pr_number), "status": "error",
                              "error": f"{type(e).__name__}: {e}"[:200]},
                       "srp": [], "ocp": [], "lsp": [], "isp": [], "impl": []}
                log(f"  ERROR {pr.pr_id}: {type(e).__name__}: {e}")
                traceback.print_exc(limit=2)
            prs.append(res["pr"])
            for k in buckets:
                buckets[k].extend(res[k])
            n += 1
            if n % CHECKPOINT_EVERY == 0:
                write_all(prs, buckets)
                el = time.time() - t0
                log(f"  [{n}/{len(todo)}] {res['pr']['status']:<16} "
                    f"{el/60:.1f} min elapsed, ~{el/n*(len(todo)-n)/60:.1f} min left")

    write_all(prs, buckets)
    df = pd.DataFrame(prs)
    log(f"\ndone in {(time.time()-t0)/60:.1f} min")
    log(f"status: {df.status.value_counts().to_dict()}")

    ok = df[df.status == "ok"]
    if len(ok):
        log(f"analysable PRs: {len(ok)}   mean supertype resolution: "
            f"{ok.resolved_supertype_rate.mean():.1%}")

    _distributions(buckets, calibrate)


def _distributions(buckets: dict, calibrate: dict) -> None:
    """The non-circular calibration step: print Java's OWN percentiles so
    thresholds are set from this corpus, never copied from Python."""
    qs = [.5, .75, .9, .95, .99]
    if calibrate["srp"] and buckets["srp"]:
        d = pd.DataFrame(buckets["srp"])
        log("\nSRP metric percentiles (set SRP_JAVA_THRESHOLDS from ~p90):")
        for c in ("class_loc", "wmc", "method_count", "max_method_ccn", "max_params", "lcom", "c3"):
            if c in d:
                log(f"  {c:<16} " + "  ".join(f"p{int(q*100)}={d[c].quantile(q):.2f}" for q in qs))
    if calibrate["ocp"] and buckets["ocp"]:
        d = pd.DataFrame(buckets["ocp"])
        log("\nOCP branch-count percentiles BY KIND (type-based vs tag-based bars):")
        for k, g in d.groupby("kind"):
            log(f"  {k:<14} n={len(g):<6} " + "  ".join(f"p{int(q*100)}={g.n_branches.quantile(q):.0f}" for q in qs))
        log(f"  closed-set chains (enum subject): {int(d.closed_set.sum())} of {len(d)}")
    if calibrate["isp"] and buckets["isp"]:
        d = pd.DataFrame(buckets["isp"])
        log("\nISP contract-width percentiles:")
        log("  n_members      " + "  ".join(f"p{int(q*100)}={d.n_members.quantile(q):.0f}" for q in qs))


if __name__ == "__main__":
    main()
