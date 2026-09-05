"""
Step 3-ISP-TS - Interface Segregation evaluator over the TypeScript corpus.

Same delta shape as run_ocp_ts / run_lsp_ts (gitsnap + tree cache + threads +
resumable). Units are interface CONTRACTS and their IMPLEMENTERS.

Rows (`row_type`):
  pr           -- status, delta reasons, isp_risk
  interface    -- every in-scope contract at head (kind, width, flagged)
  implementer  -- every in-scope (interface, class) pair (coverage, stub_ratio)

PR delta reasons (same vocabulary as run_isp.py so languages compare):
  new_flagged_iface | widened_fat_iface | new_forced_stub

Calibration mode when config.ISP_TS_THRESHOLDS is None: emits everything,
flags nothing, prints the width/stub distributions so the fat-interface bar is
chosen from TS data BEFORE labeling.

Run:  python vibebench/03_principles/isp_ts/run_isp_ts.py [--limit N] [--workers N]
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
sys.path.insert(0, str(_HERE.parents[2]))
import config as C  # noqa: E402
import gitsnap  # noqa: E402
import ts_bridge  # noqa: E402

EXTS = C.TS_SOURCE_EXTENSIONS
EXCL = C.TS_EXCLUDED_PATH_PARTS
PERMANENT = {"ok", "no_touched_modules", "repo_too_large"}


def snapshot(files, tmods, thr):
    facts = ts_bridge.analyze_tree(files, script="isp")
    if not facts.get("ok"):
        return None, None, None
    # TWO filters, both of them removing things TypeScript calls an "interface"
    # that ISP has nothing to say about:
    #
    #  1. min_members -- a TS `interface` is USUALLY a data shape (82.6% declare
    #     no behaviour at all). A structural type alias forces no one to
    #     implement anything.
    #  2. has an internal IMPLEMENTER -- the remaining behavioural ones are
    #     dominated by React/hook idioms (component `Props` bags with callback
    #     fields, context values, zustand store shapes). Nobody `implements`
    #     those; consumers destructure them, so no client is ever forced to
    #     supply a method it does not use. Only 80 of 648 behavioural contracts
    #     (12.3%) have an implementer at all.
    #
    # This is stricter than Python's ISP, deliberately: declaring an ABC or a
    # Protocol in Python is an explicit statement "this is a contract", whereas
    # in TypeScript `interface` is simply how you write an object type. The
    # asymmetry is in the languages, not in the method.
    #
    # Implementer presence is judged on the UNFILTERED fact list -- the class
    # implementing a touched interface often lives in a module this PR did not
    # touch, and scoping first would wrongly discard it.
    min_members = (thr or {}).get("min_members", 1)
    implemented_ids = {m["iface_id"] for m in facts["implementers"]}
    ifaces, impls = {}, {}
    for i in facts["interfaces"]:
        if (i["module"] not in tmods or i["n_members"] < min_members
                or i["iface_id"] not in implemented_ids):
            continue
        i = dict(i)
        i["flagged"] = (thr is not None and i["n_members"] >= thr["fat_iface_members"])
        ifaces[i["iface_id"]] = i
    for m in facts["implementers"]:
        if m["module"] not in tmods or m["iface_width"] < min_members:
            continue
        m = dict(m)
        m["forced_stub"] = (
            thr is not None
            and m["n_stubs"] >= thr["min_stubs"]
            and m["stub_ratio"] >= thr["stub_ratio"]
        )
        impls[(m["iface_id"], m["class_id"])] = m
    # Declared widths of EVERY interface, unscoped and unfiltered. The delta
    # comparison uses this, not `ifaces`: an interface that existed before but
    # only gained its first implementer in this PR must not read as "newly
    # declared". Adding a class that implements an existing wide interface is
    # not an ISP violation -- stubbing its members is, and new_forced_stub is
    # the reason that catches that.
    declared = {i["iface_id"]: i["n_members"] for i in facts["interfaces"]}
    return ifaces, impls, declared


def process_pr(pr, thr) -> list[dict]:
    meta = {"pr_id": pr.pr_id, "agent": pr.agent, "full_name": pr.full_name,
            "pr_number": pr.pr_number, "merge_sha": pr.merge_sha}

    def pr_row(status, **kw):
        return [{**meta, "row_type": "pr", "status": status,
                 "n_touched_modules": kw.get("n_touched", 0),
                 "n_interfaces_in_scope": kw.get("n_if", 0),
                 "n_flagged_ifaces": kw.get("n_flag", 0),
                 "n_forced_stubs": kw.get("n_stub", 0),
                 "isp_risk_reasons": kw.get("reasons", ""),
                 "isp_risk": kw.get("risk", None)}]

    touched = gitsnap.touched_files(pr, EXTS, EXCL, extract_dir=C.EXTRACT_DIR_TS)
    tmods = {p.rsplit(".", 1)[0] for p in touched}
    if not tmods:
        return pr_row("no_touched_modules")

    d = gitsnap.ensure_repo(pr.full_name)
    with gitsnap.repo_lock(pr.full_name):
        pair = gitsnap.delta_pair(d, pr)
        if pair is None:
            has = isinstance(pr.merge_sha, str) and bool(pr.merge_sha)
            return pr_row("fetch_failed" if has else "no_merge_sha", n_touched=len(tmods))
        bsha, asha = pair
        bf = gitsnap.src_tree(d, bsha, EXTS, EXCL, pr.full_name)
        af = gitsnap.src_tree(d, asha, EXTS, EXCL, pr.full_name)

    if bf is None or af is None:
        return pr_row("archive_failed", n_touched=len(tmods))
    if gitsnap.TOO_LARGE in bf or gitsnap.TOO_LARGE in af:
        return pr_row("repo_too_large", n_touched=len(tmods))

    b_if, b_im, b_decl = snapshot(bf, tmods, thr)
    a_if, a_im, _ = snapshot(af, tmods, thr)
    if a_if is None or b_if is None:
        return pr_row("sidecar_failed", n_touched=len(tmods))

    reasons = []
    for iid, i in a_if.items():
        if not i["flagged"]:
            continue
        prev_width = b_decl.get(iid)
        if prev_width is None:
            reasons.append("new_flagged_iface")
        elif i["n_members"] > prev_width:
            reasons.append("widened_fat_iface")
    for key, m in a_im.items():
        if m["forced_stub"] and (key not in b_im or not b_im[key]["forced_stub"]):
            reasons.append("new_forced_stub")
    reasons = sorted(set(reasons))

    n_flag = sum(1 for i in a_if.values() if i["flagged"])
    n_stub = sum(1 for m in a_im.values() if m["forced_stub"])
    rows = pr_row("ok", n_touched=len(tmods), n_if=len(a_if), n_flag=n_flag,
                  n_stub=n_stub, reasons="|".join(reasons),
                  risk=(len(reasons) >= 1) if thr is not None else None)
    for i in a_if.values():
        rows.append({**meta, "row_type": "interface", "status": "ok",
                     "iface_id": i["iface_id"], "module": i["module"],
                     "qualname": i["name"], "kind": i["kind"],
                     "n_members": i["n_members"], "lineno": i["lineno"],
                     "flagged": i["flagged"]})
    for m in a_im.values():
        rows.append({**meta, "row_type": "implementer", "status": "ok",
                     "iface_id": m["iface_id"], "module": m["module"],
                     "qualname": m["qualname"], "kind": m["iface_kind"],
                     "iface_width": m["iface_width"], "lineno": m["lineno"],
                     "file_path": m["file_path"],
                     "n_implemented": m["n_implemented"], "n_stubs": m["n_stubs"],
                     "coverage": m["coverage"], "stub_ratio": m["stub_ratio"],
                     "forced_stub": m["forced_stub"]})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=C.FETCH_WORKERS)
    args = ap.parse_args()
    thr = C.ISP_TS_THRESHOLDS

    gold = pd.read_parquet(C.GOLD_SET_TS)
    if args.limit:
        gold = gold.head(args.limit)

    done, old_rows = set(), []
    if C.ISP_RESULTS_TS.exists() and not args.limit:
        prev = pd.read_parquet(C.ISP_RESULTS_TS)
        keep = set(prev[(prev.row_type == "pr") & (prev.status.isin(PERMANENT))]["pr_id"])
        if keep:
            old_rows = prev[prev.pr_id.isin(keep)].to_dict("records")
            done = keep
            print(f"[isp-ts] resuming: {len(done)} PRs already final", flush=True)

    todo = gold[~gold.pr_id.isin(done)]
    groups = list(todo.groupby("full_name", sort=False))
    print(f"[isp-ts] {len(todo)} PRs across {len(groups)} repos, {args.workers} workers, "
          f"thresholds={'CALIBRATION (none)' if thr is None else thr}", flush=True)

    rows, lock, n_done = list(old_rows), threading.Lock(), 0

    def do_repo(name, grp):
        out = []
        for pr in grp.itertuples(index=False):
            try:
                out.extend(process_pr(pr, thr))
            except Exception as e:  # noqa: BLE001
                print(f"  !! {name}#{pr.pr_number}: {e}", flush=True)
                out.append({**{"pr_id": pr.pr_id, "agent": pr.agent,
                               "full_name": pr.full_name, "pr_number": pr.pr_number,
                               "merge_sha": pr.merge_sha},
                            "row_type": "pr", "status": "error", "isp_risk": None})
        return out

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = {ex.submit(do_repo, n, g): (n, len(g)) for n, g in groups}
        for fut in as_completed(futs):
            name, n_grp = futs[fut]
            got = fut.result()
            with lock:
                rows.extend(got)
                n_done += n_grp
                if n_done % 25 < n_grp:
                    pd.DataFrame(rows).to_parquet(C.ISP_RESULTS_TS, index=False)
                    print(f"[isp-ts] progress {n_done}/{len(todo)}", flush=True)

    df = pd.DataFrame(rows)
    df.to_parquet(C.ISP_RESULTS_TS, index=False)
    prs, ifs = df[df.row_type == "pr"], df[df.row_type == "interface"]
    ims = df[df.row_type == "implementer"]
    print(f"\n[isp-ts] wrote {C.ISP_RESULTS_TS}")
    print(f"[isp-ts] PRs: {len(prs)}  interfaces: {len(ifs)}  implementers: {len(ims)}")
    print(prs["status"].value_counts().to_string())

    if len(ifs) and thr is None:
        print("\n[isp-ts] CALIBRATION -- interface width distribution:")
        s = ifs["n_members"]
        print("  " + " ".join(f"p{int(p*100)}={s.quantile(p):.0f}"
                              for p in (0.5, 0.75, 0.9, 0.95, 0.99)) + f" max={s.max()}")
        print("  by kind:"); print(ifs.groupby("kind")["n_members"]
                                   .agg(["size", "median", "max"]).to_string())
        if len(ims):
            print("\n  implementer stub_ratio distribution:")
            r = ims["stub_ratio"]
            print("  " + " ".join(f"p{int(p*100)}={r.quantile(p):.2f}"
                                  for p in (0.5, 0.75, 0.9, 0.95)) + f" max={r.max():.2f}")
            print(f"  implementers with >=1 stub: {int((ims.n_stubs>0).sum())} of {len(ims)}")
    elif len(ifs):
        print(f"\n[isp-ts] flagged interfaces: {int(ifs.flagged.sum())} of {len(ifs)}")
        print(f"[isp-ts] forced-stub implementers: {int(ims.forced_stub.sum())} of {len(ims)}")
        ok = prs[prs.status == "ok"]
        if len(ok):
            print("\n[isp-ts] PR-level ISP risk by agent:")
            print(ok.groupby("agent")["isp_risk"].agg(["size", "mean"]).round(4).to_string())


if __name__ == "__main__":
    main()
