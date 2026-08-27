"""Step 4a-DIP helper: surface each sampled PR's import-graph DELTA so it can be
judged. For every PR in dip_labeling_sample.csv, re-fetch the before/after trees,
rebuild the internal import graph, and dump the concrete evidence a DIP judgment
needs: which touched modules added which NEW internal dependencies, whether each
new dependency points at an ABSTRACTION (a module defining ABC/Protocol classes)
or a CONCRETION, new import cycles, and the Martin distance / abstract-dependency
deltas.

Uses run_dip's fetch/tree/graph helpers, but with the GRAFT-SAFE first-parent read
(cat-file -p, not rev-parse ^1 -- the shallow-graft trap fixed in run_isp/ocp/lsp).

  python vibebench/labels/extract_dip_label_batches.py --out-dir vibebench/labels/dip_batches
"""
import argparse
import sys
from pathlib import Path
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "03_principles" / "dip"))
sys.path.insert(0, str(HERE.parents[0]))
import config as C  # noqa: E402
from graph import (  # noqa: E402
    build_module_graph, find_cycles, martin_metrics, abstract_dep_ratio,
    path_to_module,
)
sys.path.insert(0, str(HERE.parents[0] / "03_principles" / "dip"))
import run_dip as R  # noqa: E402
sys.path.insert(0, str(HERE.parents[0] / "03_principles"))
import gitsnap  # noqa: E402

SHOW_VERDICT = False   # set from --show-verdict in main()


def resolve_pair(d: Path, pr):
    """(before_sha, after_sha) via the SHARED 3-tier resolver, or None.

    REPLACED 2026-08-17 (was `graft_safe_parent`, merge_sha only). Same defect as
    the other Python extractors: 69% of flagged rows on this corpus have
    merge_sha = NaN, so `str(nan)` -> "nan" -> git fails -> the row renders as
    unjudgeable. gitsnap.delta_pair adds the commit-set and refs/pull/<N>/head
    tiers, and keeps the extractor on the same resolution path as the harness --
    so the judge sees exactly the before/after the detector scored.
    """
    with gitsnap.repo_lock(pr.full_name):
        return gitsnap.delta_pair(d, pr)


def touched_modules(pr) -> set[str]:
    mods = set()
    for f in R.touched_py_files(pr):
        m = path_to_module(f)
        if m:
            mods.add(m)
    return mods


def describe(pr) -> str:
    # BLIND BY DEFAULT (2026-08-17). The verdict, the hit list AND the signed
    # deltas the rubric thresholds on are all withheld unless --show-verdict:
    # printing them above the evidence anchors the judge toward the tool. Same
    # leak fixed for Java on 2026-08-04 and never backported here, so any DIP
    # figure from a pre-2026-08-17 batch is an UPPER BOUND.
    L = [f"### PR {pr.pr_id}  {pr.agent}  {pr.full_name}#{pr.pr_number}"]
    if SHOW_VERDICT:
        L.append(f"stratum={pr.stratum}  dip_risk(pred)={pr.dip_risk}  "
                 f"hits={pr.dip_risk_hits}")
        L.append(f"delta: mean_d={pr.delta_mean_d:+.3f}  abstract_dep_ratio="
                 f"{pr.delta_abstract_dep_ratio:+.3f}  "
                 f"touched_cycles={pr.delta_touched_cycles:+.0f}")
    d = R.ensure_repo(pr.full_name)
    pair = resolve_pair(d, pr)
    if pair is None:
        return "\n".join(L + ["  (could not resolve before/after pair)"])
    parent, after_sha = pair
    before = R.py_tree(d, parent)
    after = R.py_tree(d, after_sha)
    if not before or not after or "__TOO_LARGE__" in (before or {}) or "__TOO_LARGE__" in (after or {}):
        return "\n".join(L + ["  (tree unavailable / too large)"])
    gb, ga = build_module_graph(before), build_module_graph(after)
    tmods = touched_modules(pr)
    L.append(f"touched modules ({len(tmods)}): {', '.join(sorted(tmods)[:12])}")
    # new internal dependency edges from touched modules
    new_edges = []
    for m in sorted(tmods):
        bi = gb.get(m); ai = ga.get(m)
        b_imp = bi.imports if bi else set()
        a_imp = ai.imports if ai else set()
        for tgt in sorted(a_imp - b_imp):
            tinfo = ga.get(tgt)
            kind = ("ABSTRACTION" if tinfo and tinfo.n_abstract_classes > 0
                    else "concretion" if tinfo else "external/unknown")
            new_edges.append(f"    {m}  ->  {tgt}   [{kind}]")
    L.append(f"NEW internal dependencies added by touched modules ({len(new_edges)}):")
    L += new_edges[:30] if new_edges else ["    (none)"]
    # cycles touching touched modules
    cb = [c for c in find_cycles(gb) if c & tmods]
    ca = [c for c in find_cycles(ga) if c & tmods]
    new_cycles = [c for c in ca if c not in cb]
    # BLINDING LEAK, FIXED 2026-08-25. This printed "NEW CYCLE: ..." for every
    # cycle the detector considered new, unconditionally, while every other
    # principle's verdict sits behind --show-verdict. `new_cycle` IS the DIP
    # flag, so a judge reading that line is confirming the tool rather than
    # checking it, and the precision that comes back is an upper bound.
    #
    # The cycle MEMBERSHIP still has to be visible or the row cannot be judged
    # at all, so what is shown is the after-state cycles WITHOUT the novelty
    # label. The judge decides novelty from the new-edge list above, which is the
    # independent check the label round exists to provide.
    L.append(f"import cycles touching touched modules: before={len(cb)} after={len(ca)}")
    if SHOW_VERDICT:
        for c in new_cycles[:5]:
            L.append(f"    NEW CYCLE (detector's verdict): {' -> '.join(sorted(c))}")
    else:
        for c in ca[:5]:
            L.append(f"    cycle present AFTER: {' -> '.join(sorted(c))}")
        L.append("    (novelty is NOT marked -- decide it from the new-edge list above)")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="dip_labeling_sample.csv")
    ap.add_argument("--out-dir", default=str(HERE / "dip_batches"))
    ap.add_argument("--per-batch", type=int, default=10)
    ap.add_argument("--show-verdict", action="store_true",
                    help="print the rubric verdict and the delta metrics it "
                         "thresholds on (NOT blind -- debugging only)")
    a = ap.parse_args()
    global SHOW_VERDICT
    SHOW_VERDICT = a.show_verdict
    df = pd.read_csv(HERE / a.file)
    # touched_files / files_source live in the gold set, not the dip sample -- join them.
    gold = pd.read_parquet(C.GOLD_SET)[["pr_id", "touched_files", "files_source"]]
    df = df.merge(gold, on="pr_id", how="left")
    outdir = Path(a.out_dir); outdir.mkdir(parents=True, exist_ok=True)
    blocks = []
    for i, pr in enumerate(df.itertuples(index=False), 1):
        print(f"[{i}/{len(df)}] {pr.full_name}#{pr.pr_number}", flush=True)
        try:
            blocks.append(describe(pr))
        except Exception as e:  # noqa: BLE001
            blocks.append(f"### PR {pr.pr_id} {pr.full_name}#{pr.pr_number}\n  ERROR: {e}")
    for bi in range(0, len(blocks), a.per_batch):
        chunk = blocks[bi:bi + a.per_batch]
        p = outdir / f"dip_label_batch_{bi // a.per_batch:02d}.txt"
        p.write_text("\n\n".join(chunk), encoding="utf-8")
        print("wrote", p)


if __name__ == "__main__":
    main()
