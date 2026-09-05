"""
Step 4a-ISP-helper - Dump the source behind each ISP labeling-sample row into
reviewable batch files.

Unlike SRP (whose classes were already extracted to corpus/ai_files/), ISP
entity classes live in the full repo tree at the merge commit -- so sources
are pulled with `git show <merge_sha>:<path>` from the cached clone dirs.
Module names were derived by path_to_module (leading `src/` stripped), so
both layouts are tried when resolving a module back to a path.

For implementer rows both sides are printed (the interface contract first,
then the implementer), because "forced to stub methods it does not use" can
only be judged with the contract in view.

Run:  python vibebench/labels/extract_isp_label_batches.py \
          --file isp_labeling_sample.csv --out-dir <dir> \
          [--per-batch 10] [--max-lines 120]
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "03_principles"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "03_principles" / "dip"))
import config as C  # noqa: E402
import gitsnap  # noqa: E402
from graph import path_to_module  # noqa: E402


# ---------------------------------------------------------------------------
# SHA RESOLUTION -- backported from the TS extractors 2026-08-17.
# The old code called `git show <merge_sha>:<path>` directly. On the shipped
# corpus 69% of flagged rows have merge_sha = NaN (`str(nan)` -> "nan" -> git
# fails -> "<<source unavailable>>"), which is what blocked the LSP validation.
# The harnesses were never affected because they resolve the after-state through
# gitsnap.delta_pair, whose tier 2 falls back to the PR's own commit SHAs and
# whose tier 3 falls back to refs/pull/<N>/head. Reading the tree through
# gitsnap.src_tree also means the judge sees EXACTLY the tree the detector
# analysed, and removes the old path-guessing heuristic (<mod>.py / src/<mod>.py
# only), which silently missed any other repo layout.
# ---------------------------------------------------------------------------
class _PR:
    """The minimal duck-type gitsnap.delta_pair expects."""

    def __init__(self, merge_sha, commit_shas, pr_number):
        self.merge_sha = merge_sha
        self.commit_shas = commit_shas
        self.pr_number = pr_number


def head_tree(full_name, merge_sha, commit_shas, pr_number):
    d = gitsnap.repo_dir(full_name)
    with gitsnap.repo_lock(full_name):
        pair = gitsnap.delta_pair(d, _PR(merge_sha, commit_shas, pr_number))
        if pair is None:
            return None
        tree = gitsnap.src_tree(d, pair[1], C.SOURCE_EXTENSIONS, (), full_name)
    if tree is None or gitsnap.TOO_LARGE in tree:
        return None
    return tree


def module_index(tree):
    """{module: path}, keyed exactly as the detector keys modules."""
    idx = {}
    if not tree:
        return idx
    for path in tree:
        mod = path_to_module(path)
        if mod and mod not in idx:
            idx[mod] = path
    return idx


def class_snippet(src: str, class_name: str, max_lines: int) -> str:
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError) as e:
        return f"<<unparseable: {e}>>"
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            lines = src.splitlines()[node.lineno - 1:
                                     getattr(node, "end_lineno", node.lineno)]
            defs = [f"  L{n.lineno}: def {n.name}(...)"
                    for n in ast.walk(node)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            body = "\n".join(lines[:max_lines])
            if len(lines) > max_lines:
                body += f"\n... <<truncated, {len(lines) - max_lines} more lines>>"
            return "METHOD INDEX:\n" + "\n".join(defs) + "\n\nSOURCE:\n" + body
    return "<<class not found in file>>"


def fetch_class(tree, midx, module: str, class_name: str,
                max_lines: int) -> str:
    """Render a class from the PR's cached after-tree (see the SHA note above)."""
    path = midx.get(str(module))
    src = tree.get(path) if (tree and path) else None
    if src is None:
        return f"<<source unavailable: {module}>>"
    return f"[{path}]\n" + class_snippet(src, class_name, max_lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="isp_labeling_sample.csv")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--per-batch", type=int, default=10)
    ap.add_argument("--max-lines", type=int, default=120)
    ap.add_argument("--show-verdict", action="store_true",
                    help="print the rubric's verdict + its threshold variables "
                         "(NOT blind -- debugging only, biases the judge)")
    args = ap.parse_args()

    df = pd.read_csv(C.LABELS_DIR / args.file)
    # commit_shas enables delta_pair tier 2; pr_number enables tier 3.
    gold = pd.read_parquet(C.GOLD_SET, columns=["pr_id", "commit_shas"])
    df = df.merge(gold, on="pr_id", how="left")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    trees, indexes, n_unavailable = {}, {}, 0

    for b in range(0, len(df), args.per_batch):
        chunk = df.iloc[b:b + args.per_batch]
        parts = []
        for idx, r in chunk.iterrows():
            head = (f"{'=' * 70}\nROW {idx} | {r.row_type} | {r.agent} | "
                    f"{r.full_name}#{r.pr_number}\n")

            key = (r.full_name, r.pr_id)
            if key not in trees:
                trees[key] = head_tree(r.full_name, r.merge_sha,
                                       r.commit_shas, r.pr_number)
                indexes[key] = module_index(trees[key])
            tree, midx = trees[key], indexes[key]

            # Rubric verdict AND its threshold variables (widths, coverage,
            # stub_ratio) are withheld unless --show-verdict: showing the numbers
            # the rule thresholds on anchors the judge as surely as the flag does.
            if r.row_type == "interface":
                head += f"interface {r.iface_id} kind={r.kind}\n"
                if args.show_verdict:
                    head += (f"n_abstract={r.n_abstract_methods} "
                             f"n_implementers={r.n_implementers} "
                             f"mean_coverage={r.mean_coverage} "
                             f"rubric_flag={r.isp_risk}\n")
                head += f"{'=' * 70}\n"
                body = fetch_class(tree, midx, r.module,
                                   r.class_name, args.max_lines)
            else:
                head += (f"implementer {r.module}:{r.class_name} of "
                         f"{r.iface_id} (kind={r.iface_kind})\n")
                if args.show_verdict:
                    head += (f"n_iface_methods={r.n_iface_methods} "
                             f"n_stubbed={r.n_stubbed} coverage={r.coverage} "
                             f"stub_ratio={r.stub_ratio} "
                             f"rubric_flag={r.isp_risk}\n")
                head += f"{'=' * 70}\n"
                imod, iname = str(r.iface_id).split(":")
                body = ("--- INTERFACE (the contract) ---\n"
                        + fetch_class(tree, midx, imod, iname, args.max_lines)
                        + "\n\n--- IMPLEMENTER ---\n"
                        + fetch_class(tree, midx, r.module, r.class_name,
                                      args.max_lines))
            if "<<source unavailable" in body:
                n_unavailable += 1
            parts.append(head + body)
        p = out / f"isp_label_batch_{b // args.per_batch:02d}.txt"
        p.write_text("\n\n".join(parts), encoding="utf-8")
        print(f"wrote {p} ({len(chunk)} rows)")

    mode = "WITH VERDICT (not blind)" if args.show_verdict else "blind"
    print(f"\n[isp-extract] {len(df)} rows, {mode}; {n_unavailable} with a "
          f"missing side ({n_unavailable / max(len(df), 1):.1%})")


if __name__ == "__main__":
    main()
