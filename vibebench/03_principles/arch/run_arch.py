"""Martin component zones over the AFTER tree of each PR -- DESCRIPTIVE ONLY.

Runs Python and Java. Writes `corpus/arch_results.parquet` and touches nothing
else: no existing detector, threshold, or parquet is read for writing or
modified. The five SOLID results are unaffected by running this.

WHY AFTER-ONLY, NO DELTA
------------------------
The DIP validation in this project scored `mean_d_worse` at precision 0.000
(0 of 7). D moved when the module graph was RECOMPOSED -- a file added or
moved -- not when a dependency inverted. So D is a known-bad CHANGE signal on
this corpus. It remains a sound DESCRIPTOR of where a package sits, which is
all this harness reports. No flag, no risk column, no agent ranking.

ATTRIBUTION
-----------
Identical to `dip/run_dip.py` so the two are commensurable: same
`gitsnap.delta_pair` merge-pair resolution, same touched-file source, same
`src_tree` extraction and cache. Only the AFTER tree is analysed, and the
`touched` flag on each package row marks the packages the PR actually reached.

  python vibebench/03_principles/arch/run_arch.py --lang py   [--limit N]
  python vibebench/03_principles/arch/run_arch.py --lang java [--limit N]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
VB = HERE.parents[1]
sys.path.insert(0, str(VB))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "dip"))
sys.path.insert(0, str(HERE.parent / "java_lang"))

import config as C                                        # noqa: E402
import gitsnap                                            # noqa: E402
import graph as dipgraph                                  # noqa: E402
from graph import build_module_graph, martin_metrics, path_to_module  # noqa: E402
from zones import classify_packages, ZONE_RADIUS, MAIN_SEQUENCE_BAND  # noqa: E402

OUT = C.CORPUS_DIR / "arch_results.parquet"
CHECKPOINT_EVERY = 25
PERMANENT = {"ok", "no_touched_modules", "repo_too_large"}


def java_pkg(module: str) -> str:
    """`a.b.C` -> `a.b`. Copied from run_java.py -- top_package returns "net"
    for reverse-DNS packages and would collapse the whole corpus into three
    buckets (the defect that voided the first DIP-Java run)."""
    return module.rsplit(".", 1)[0] if "." in module else module


def py_pkg(module: str) -> str:
    """`pkg.sub.mod` -> `pkg.sub`; a top-level module maps to itself.

    DELIBERATE DEVIATION from `dip/run_dip.py`, which aggregates with
    `graph.top_package` (first dotted segment only). MEASURED REASON: most
    Python repos in this corpus are a SINGLE top-level package, so under
    `top_package` there are no inter-package edges to count -- a first pass
    produced Ce=Ca=0 for 917 of 3,345 packages, leaving instability undefined
    and 48% of the corpus unplaceable on the A/I plane.

    Using the parent package makes Python's granularity match `java_pkg`,
    which is what lets the two languages be compared at all. PR selection,
    merge-pair resolution and touched scope remain identical to run_dip.py;
    only the aggregation level differs, and this harness reports no delta, so
    no validated DIP number depends on the choice.
    """
    return module.rsplit(".", 1)[0] if "." in module else module


# ------------------------------------------------------------------ python
def py_snapshot(files: dict[str, str], touched: set[str]) -> list[dict]:
    g = build_module_graph(files)
    martin = martin_metrics(g, package_of=py_pkg)   # see py_pkg for why not top_package
    touched_pkgs = {py_pkg(m) for m in touched if m in g}
    rows = classify_packages(martin)
    for r in rows:
        r["touched"] = r["package"] in touched_pkgs
        r["n_modules"] = sum(1 for m in g if py_pkg(m) == r["package"])
    return rows


# -------------------------------------------------------------------- java
def java_snapshot(files: dict[str, str], touched_files: set[str]) -> list[dict]:
    import java_bridge
    import detectors as D
    facts = java_bridge.analyze_tree(files)
    if not facts.get("ok", True) and "modules" not in facts:
        return []
    ix = D.build_index(facts)
    mods = D.dip_modules(ix)
    martin = martin_metrics(mods, package_of=java_pkg)
    touched_mods = {m["name"] for m in ix.modules if m["file"] in touched_files}
    touched_pkgs = {java_pkg(m) for m in touched_mods}
    rows = classify_packages(martin)
    for r in rows:
        r["touched"] = r["package"] in touched_pkgs
        r["n_modules"] = sum(1 for m in mods if java_pkg(m) == r["package"])
    return rows


# ----------------------------------------------------------------- harness
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=["py", "java"], default="py")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    if args.lang == "py":
        gold, exts = C.GOLD_SET, {".py"}
        extract_dir = C.EXTRACT_DIR
    else:
        gold, exts = C.GOLD_SET_JAVA, set(C.JAVA_SOURCE_EXTENSIONS)
        extract_dir = getattr(C, "EXTRACT_DIR_JAVA", C.EXTRACT_DIR)

    prs = pd.read_parquet(gold)
    # The liveness set for eviction is the FULL gold set, never the --limit
    # subset: a smoke run with --limit 5 would otherwise evict every other row
    # in the parquet as "not in the current corpus" and destroy the results.
    live_pr_ids = set(prs.pr_id)
    if args.limit:
        prs = prs.head(args.limit)

    done: set = set()
    rows: list[dict] = []
    if OUT.exists():
        prev = pd.read_parquet(OUT)
        prev = prev[prev.lang == args.lang] if "lang" in prev.columns else prev
        keep = prev[prev.status.isin(PERMANENT)]
        # EVICT ROWS FROM A PREVIOUS CORPUS (V2). Resume keeps permanent-status
        # rows, which is what makes the harness restartable, and is also how a
        # re-curated corpus silently inherits the old one: the stale PRs are
        # never re-analysed and never noticed, so every count is taken over a
        # corpus that no longer exists. Same failure the Java result parquets
        # had to be renamed to avoid.
        stale = set(keep.pr_id.unique()) - live_pr_ids
        if stale:
            print(f"evicting {len(stale)} rows from a previous {args.lang} corpus "
                  f"(their PRs are not in the current gold set)", flush=True)
            keep = keep[keep.pr_id.isin(live_pr_ids)]
        done = set(keep.pr_id.unique())
        other = pd.read_parquet(OUT)
        other = other[other.lang != args.lang] if "lang" in other.columns else other.iloc[0:0]
        rows = other.to_dict("records") + keep.to_dict("records")
        print(f"resuming: {len(done)} PRs already done for {args.lang}", flush=True)

    n = len(prs)
    for i, pr in enumerate(prs.itertuples(index=False), 1):
        if pr.pr_id in done:
            continue
        # V2: `outcome` and `rejection_stratum` ride on every row of every
        # parquet (REDESIGN_V2.md 3) so any report can split on them without a
        # join. Defaulted rather than required, because this harness also has to
        # read pre-V2 gold sets.
        base = {"pr_id": pr.pr_id, "agent": pr.agent, "full_name": pr.full_name,
                "pr_number": pr.pr_number, "lang": args.lang,
                "outcome": getattr(pr, "outcome", "merged"),
                "rejection_stratum": getattr(pr, "rejection_stratum", "merged")}

        def emit(status: str, **extra):
            rows.append({**base, "row_type": "pr", "status": status, **extra})

        try:
            touched_paths = gitsnap.touched_files(pr, exts, (), extract_dir=extract_dir)
            if not touched_paths:
                emit("no_touched_modules")
                continue

            d = gitsnap.ensure_repo(pr.full_name)
            pair = gitsnap.delta_pair(d, pr)
            print(f"[{i}/{n}] {pr.agent:<12} {pr.full_name}#{pr.pr_number}", flush=True)
            if pair is None:
                emit("fetch_failed")
                continue
            _, after_sha = pair

            after_files = gitsnap.src_tree(d, after_sha, exts, (), pr.full_name)
            if after_files is None:
                emit("archive_failed")
                continue
            if "__TOO_LARGE__" in after_files:
                emit("repo_too_large")
                continue

            if args.lang == "py":
                tset = {m for m in (path_to_module(p) for p in touched_paths) if m}
                pkg_rows = py_snapshot(after_files, tset)
            else:
                pkg_rows = java_snapshot(after_files, set(touched_paths))

            if not pkg_rows:
                emit("no_packages")
                continue

            zc: dict[str, int] = {}
            for r in pkg_rows:
                zc[r["zone"]] = zc.get(r["zone"], 0) + 1
            emit("ok", n_packages=len(pkg_rows),
                 n_touched_packages=sum(1 for r in pkg_rows if r["touched"]),
                 n_pain=zc.get("pain", 0), n_uselessness=zc.get("uselessness", 0),
                 n_main_sequence=zc.get("main_sequence", 0),
                 n_intermediate=zc.get("intermediate", 0), n_unknown=zc.get("unknown", 0))
            for r in pkg_rows:
                rows.append({**base, "row_type": "package", "status": "ok", **r})

        except Exception as e:                     # noqa: BLE001
            emit("error", error=f"{type(e).__name__}: {e}")

        if i % CHECKPOINT_EVERY == 0:
            gitsnap.write_parquet_atomic(pd.DataFrame(rows), OUT, index=False)
            print(f"  checkpoint {i}/{n}", flush=True)

    df = pd.DataFrame(rows)
    gitsnap.write_parquet_atomic(df, OUT, index=False)
    pk = df[(df.row_type == "package") & (df.lang == args.lang)]
    print(f"\nwrote {OUT}  ({len(df)} rows)")
    print(f"zone radius {ZONE_RADIUS}, main-sequence band {MAIN_SEQUENCE_BAND}")
    if len(pk):
        print(pk.zone.value_counts().to_string())


if __name__ == "__main__":
    main()
