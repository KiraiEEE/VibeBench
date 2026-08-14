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


def graft_safe_parent(d: Path, merge_sha: str) -> str | None:
    """First parent via `git cat-file -p` (shallow-graft-proof)."""
    if not R.have_commit(d, merge_sha):
        R.run_git(["fetch", "--depth", "2", "origin", merge_sha], cwd=d)
    if not R.have_commit(d, merge_sha):
        return None
    r = R.run_git(["cat-file", "-p", merge_sha], cwd=d)
    if r is None:
        return None
    parents = [ln.split()[1] for ln in r.stdout.splitlines()
               if ln.startswith("parent ")]
    if not parents:
        return None
    p = parents[0]
    if not R.have_commit(d, p):
        R.run_git(["fetch", "--depth", "2", "origin", p], cwd=d)
    return p if R.have_commit(d, p) else None


def touched_modules(pr) -> set[str]:
    mods = set()
    for f in R.touched_py_files(pr):
        m = path_to_module(f)
        if m:
            mods.add(m)
    return mods


def describe(pr) -> str:
    L = [f"### PR {pr.pr_id}  {pr.agent}  {pr.full_name}#{pr.pr_number}",
         f"stratum={pr.stratum}  dip_risk(pred)={pr.dip_risk}  hits={pr.dip_risk_hits}",
         f"delta: mean_d={pr.delta_mean_d:+.3f}  abstract_dep_ratio="
         f"{pr.delta_abstract_dep_ratio:+.3f}  touched_cycles={pr.delta_touched_cycles:+.0f}"]
    d = R.ensure_repo(pr.full_name)
    parent = graft_safe_parent(d, pr.merge_sha)
    if parent is None:
        return "\n".join(L + ["  (could not resolve parent / fetch)"])
    before = R.py_tree(d, parent)
    after = R.py_tree(d, pr.merge_sha)
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
    L.append(f"import cycles touching touched modules: before={len(cb)} after={len(ca)}")
    for c in new_cycles[:5]:
        L.append(f"    NEW CYCLE: {' -> '.join(sorted(c))}")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="dip_labeling_sample.csv")
    ap.add_argument("--out-dir", default=str(HERE / "dip_batches"))
    ap.add_argument("--per-batch", type=int, default=10)
    a = ap.parse_args()
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
