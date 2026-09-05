"""
Step 3-DIP-TS - Dependency Inversion evaluator over the TypeScript corpus.

The sidecar emits per-module facts in exactly the shape of
`dip/graph.py::ModuleInfo`, so this harness rebuilds those objects and calls the
**already-validated Python maths verbatim**: `find_cycles` (Tarjan),
`martin_metrics` (Instability / Abstractness / Distance) and
`abstract_dep_ratio`. Nothing is re-implemented in JavaScript -- the only new
code is the TypeScript fact extraction.

Rows: one `pr` row per gold-set PR with before_*/after_*/delta_* for
touched_cycles, mean_d and abstract_dep_ratio, plus dip_risk. Same column
vocabulary as dip_results.parquet so the two languages compare directly.

Honest note carried over from the Python DIP validation (P=0.22 / R=0.67):
`new_cycle` is the trustworthy signal; `mean_d` is mostly graph-recomposition
noise. Both are recorded, but the report leads with cycles.

Run:  python vibebench/03_principles/dip_ts/run_dip_ts.py [--limit N] [--workers N]
"""
from __future__ import annotations

import argparse
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))            # 03_principles
sys.path.insert(0, str(_HERE.parents[1] / "dip"))    # graph.py (validated maths)
sys.path.insert(0, str(_HERE.parents[2]))            # vibebench
import config as C  # noqa: E402
import gitsnap  # noqa: E402
import ts_bridge  # noqa: E402
from graph import (  # noqa: E402
    ModuleInfo, abstract_dep_ratio, find_cycles, martin_metrics, top_package,
)

EXTS = C.TS_SOURCE_EXTENSIONS
EXCL = C.TS_EXCLUDED_PATH_PARTS
PERMANENT = {"ok", "no_touched_modules", "repo_too_large"}


def build_modules(files: dict[str, str]) -> dict[str, ModuleInfo] | None:
    """TS facts -> the same ModuleInfo dict the Python DIP code consumes."""
    facts = ts_bridge.analyze_tree(files, script="dip")
    if not facts.get("ok"):
        return None
    return {
        m["name"]: ModuleInfo(
            name=m["name"],
            n_classes=m["n_classes"],
            n_abstract_classes=m["n_abstract_classes"],
            imports=set(m["imports"]),
            parse_ok=True,
        )
        for m in facts["modules"]
    }


def snapshot(modules: dict[str, ModuleInfo], touched: set[str]) -> dict:
    """Same metric bundle as run_dip.py::snapshot_metrics, on TS modules.

    Deliberately mirrors run_dip.py line-for-line so the two languages are
    directly comparable: `scope` is the touched modules that exist in the graph,
    and mean D is averaged over the packages of THOSE modules only, skipping
    packages whose D is undefined (martin_metrics returns None when a package
    has no classes or no cross-package couplings -- not filtering that was a
    real bug that failed 616 of 682 PRs on the first run).
    """
    scope = {m for m in touched if m in modules}
    cycles = find_cycles(modules)
    touched_cycles = [c for c in cycles if c & scope]
    mm = martin_metrics(modules)
    pkgs = {top_package(m) for m in scope}
    ds = [mm[p]["D"] for p in pkgs if p in mm and mm[p]["D"] is not None]
    return {
        "n_modules": len(modules),
        "touched_in_graph": len(scope),
        "touched_fan_out": sum(len(modules[m].imports) for m in scope),
        "touched_cycles": len(touched_cycles),
        "repo_cycles": len(cycles),
        "mean_d": (sum(ds) / len(ds)) if ds else None,
        "abstract_dep_ratio": abstract_dep_ratio(modules, scope),
        "cycle_sets": {frozenset(c) for c in touched_cycles},
    }


def process_pr(pr, thr) -> dict:
    meta = {"pr_id": pr.pr_id, "agent": pr.agent, "full_name": pr.full_name,
            "pr_number": pr.pr_number, "merge_sha": pr.merge_sha}

    def row(status, **kw):
        return {**meta, "status": status, "n_touched_modules": kw.get("n_touched", 0),
                **{k: kw.get(k) for k in (
                    "before_touched_cycles", "after_touched_cycles",
                    "before_mean_d", "after_mean_d",
                    "before_abstract_dep_ratio", "after_abstract_dep_ratio",
                    "delta_touched_cycles", "delta_mean_d",
                    "delta_abstract_dep_ratio", "new_cycle",
                    "dip_risk_hits", "dip_risk")}}

    touched = gitsnap.touched_files(pr, EXTS, EXCL, extract_dir=C.EXTRACT_DIR_TS)
    tmods = {p.rsplit(".", 1)[0] for p in touched}
    if not tmods:
        return row("no_touched_modules")

    d = gitsnap.ensure_repo(pr.full_name)
    with gitsnap.repo_lock(pr.full_name):
        pair = gitsnap.delta_pair(d, pr)
        if pair is None:
            has = isinstance(pr.merge_sha, str) and bool(pr.merge_sha)
            return row("fetch_failed" if has else "no_merge_sha", n_touched=len(tmods))
        bsha, asha = pair
        bf = gitsnap.src_tree(d, bsha, EXTS, EXCL, pr.full_name)
        af = gitsnap.src_tree(d, asha, EXTS, EXCL, pr.full_name)

    if bf is None or af is None:
        return row("archive_failed", n_touched=len(tmods))
    if gitsnap.TOO_LARGE in bf or gitsnap.TOO_LARGE in af:
        return row("repo_too_large", n_touched=len(tmods))

    bm, am = build_modules(bf), build_modules(af)
    if bm is None or am is None:
        return row("sidecar_failed", n_touched=len(tmods))

    b, a = snapshot(bm, tmods), snapshot(am, tmods)
    new_cycles = a["cycle_sets"] - b["cycle_sets"]
    d_cycles = a["touched_cycles"] - b["touched_cycles"]
    d_mean = ((a["mean_d"] - b["mean_d"])
              if (a["mean_d"] is not None and b["mean_d"] is not None) else None)
    d_abs = ((a["abstract_dep_ratio"] - b["abstract_dep_ratio"])
             if (a["abstract_dep_ratio"] is not None
                 and b["abstract_dep_ratio"] is not None) else None)

    hits = []
    if len(new_cycles) > thr["new_touched_cycles"]:
        hits.append("new_cycle")
    if d_mean is not None and d_mean > thr["delta_mean_d"]:
        hits.append("mean_d_worse")

    return row("ok", n_touched=len(tmods),
               before_touched_cycles=b["touched_cycles"],
               after_touched_cycles=a["touched_cycles"],
               before_mean_d=b["mean_d"], after_mean_d=a["mean_d"],
               before_abstract_dep_ratio=b["abstract_dep_ratio"],
               after_abstract_dep_ratio=a["abstract_dep_ratio"],
               delta_touched_cycles=d_cycles, delta_mean_d=d_mean,
               delta_abstract_dep_ratio=d_abs,
               new_cycle=len(new_cycles) > 0,
               dip_risk_hits="|".join(hits), dip_risk=len(hits) >= 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=C.FETCH_WORKERS)
    args = ap.parse_args()
    thr = C.DIP_TS_THRESHOLDS

    gold = pd.read_parquet(C.GOLD_SET_TS)
    if args.limit:
        gold = gold.head(args.limit)

    done, old_rows = set(), []
    if C.DIP_RESULTS_TS.exists() and not args.limit:
        prev = pd.read_parquet(C.DIP_RESULTS_TS)
        keep = set(prev[prev.status.isin(PERMANENT)]["pr_id"])
        if keep:
            old_rows = prev[prev.pr_id.isin(keep)].to_dict("records")
            done = keep
            print(f"[dip-ts] resuming: {len(done)} PRs already final", flush=True)

    todo = gold[~gold.pr_id.isin(done)]
    groups = list(todo.groupby("full_name", sort=False))
    print(f"[dip-ts] {len(todo)} PRs across {len(groups)} repos, "
          f"{args.workers} workers", flush=True)

    rows, lock, n_done = list(old_rows), threading.Lock(), 0

    def do_repo(name, grp):
        out = []
        for pr in grp.itertuples(index=False):
            try:
                out.append(process_pr(pr, thr))
            except Exception as e:  # noqa: BLE001
                print(f"  !! {name}#{pr.pr_number}: {e}", flush=True)
                out.append({"pr_id": pr.pr_id, "agent": pr.agent,
                            "full_name": pr.full_name, "pr_number": pr.pr_number,
                            "merge_sha": pr.merge_sha, "status": "error",
                            "dip_risk": None})
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
                    pd.DataFrame(rows).to_parquet(C.DIP_RESULTS_TS, index=False)
                    print(f"[dip-ts] progress {n_done}/{len(todo)}", flush=True)

    df = pd.DataFrame(rows)
    df.to_parquet(C.DIP_RESULTS_TS, index=False)
    print(f"\n[dip-ts] wrote {C.DIP_RESULTS_TS}")
    print(f"[dip-ts] PRs: {len(df)}")
    print(df["status"].value_counts().to_string())
    ok = df[df.status == "ok"]
    if len(ok):
        print(f"\n[dip-ts] PRs creating a NEW import cycle: "
              f"{int(ok['new_cycle'].fillna(False).sum())} of {len(ok)}")
        print("\n[dip-ts] by agent (risk rate | new-cycle rate | mean delta D):")
        print(ok.groupby("agent").agg(
            prs=("pr_id", "size"),
            dip_risk=("dip_risk", "mean"),
            new_cycle=("new_cycle", "mean"),
            mean_delta_d=("delta_mean_d", "mean"),
        ).round(4).to_string())


if __name__ == "__main__":
    main()
