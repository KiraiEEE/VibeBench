"""
Step 4a-OCP-helper - Dump the source behind each OCP labeling-sample row into
reviewable batch files.

Each dispatch chain lives in the full repo tree at the merge commit, so sources
are pulled with `git show <merge_sha>:<path>` from the cached clone dirs (same
as the ISP extractor). For each row the ENCLOSING function/method (resolved by
qualname) is printed with a marker on the dispatch line -- the switch must be
seen in its function context to judge whether adding a variant forces an edit
(genuine OCP violation) or the branch set is legitimately closed.

Run:  python vibebench/labels/extract_ocp_label_batches.py \
          --file ocp_labeling_sample.csv --out-dir <dir> \
          [--per-batch 10] [--max-lines 80]
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


def _find_def(tree: ast.Module, qualname: str):
    """The FunctionDef/AsyncFunctionDef node matching a dotted qualname, else
    None (module-level dispatch has an empty qualname)."""
    if not qualname:
        return None
    parts = qualname.split(".")
    scope: list[ast.stmt] = tree.body
    node = None
    for i, part in enumerate(parts):
        node = next((s for s in scope
                     if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef,
                                       ast.ClassDef)) and s.name == part), None)
        if node is None:
            return None
        scope = node.body
    return node   # FunctionDef/AsyncFunctionDef, or a ClassDef for class-body dispatch


def dispatch_snippet(src: str, qualname: str, lineno: int, max_lines: int) -> str:
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError) as e:
        return f"<<unparseable: {e}>>"
    node = _find_def(tree, qualname)
    lines = src.splitlines()
    if node is not None:
        start = node.lineno - 1
        end = getattr(node, "end_lineno", node.lineno)
    else:  # module-level dispatch: window around the switch
        start = max(0, lineno - 6)
        end = lineno + max_lines
    body = lines[start:end]
    out = []
    for off, text in enumerate(body):
        marker = ">>" if (start + off + 1) == lineno else "  "
        out.append(f"{marker} L{start + off + 1}: {text}")
    if len(body) > max_lines:
        out = out[:max_lines] + [f"... <<truncated, {len(body) - max_lines} more lines>>"]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="ocp_labeling_sample.csv")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--per-batch", type=int, default=10)
    ap.add_argument("--max-lines", type=int, default=80)
    ap.add_argument("--show-verdict", action="store_true",
                    help="print the rubric's verdict in the header (NOT blind -- "
                         "debugging only, biases the judge)")
    args = ap.parse_args()

    df = pd.read_csv(C.LABELS_DIR / args.file)
    # commit_shas enables delta_pair's tier-2 fallback; pr_number enables tier 3.
    gold = pd.read_parquet(C.GOLD_SET, columns=["pr_id", "commit_shas"])
    df = df.merge(gold, on="pr_id", how="left")

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    trees, indexes, n_unavailable = {}, {}, 0

    for b in range(0, len(df), args.per_batch):
        chunk = df.iloc[b:b + args.per_batch]
        parts = []
        for idx, r in chunk.iterrows():
            qual = "" if pd.isna(r.qualname) else str(r.qualname)
            head = (f"{'=' * 70}\nROW {idx} | {r.agent} | {r.full_name}"
                    f"#{r.pr_number}\n"
                    f"dispatch in {r.module}:{qual or '<module>'} "
                    f"subject=`{r.subject}` kind={r.kind}\n")
            # n_branches is WITHHELD unless --show-verdict: the branch count is
            # the rubric's own threshold variable, so showing it anchors the
            # judge toward the tool exactly as rubric_smell did.
            if args.show_verdict:
                head += (f"n_branches={r.n_branches} rubric_smell={r.smell}\n")
            head += f"{'=' * 70}\n"

            key = (r.full_name, r.pr_id)
            if key not in trees:
                trees[key] = head_tree(r.full_name, r.merge_sha,
                                       r.commit_shas, r.pr_number)
                indexes[key] = module_index(trees[key])
            tree, midx = trees[key], indexes[key]

            path = midx.get(str(r.module))
            src = tree.get(path) if (tree and path) else None
            if src is None:
                n_unavailable += 1
                body = f"<<source unavailable: {r.module}>>"
            else:
                body = f"[{path}]\n" + dispatch_snippet(
                    src, qual, int(r.lineno), args.max_lines)
            parts.append(head + body)
        p = out / f"ocp_label_batch_{b // args.per_batch:02d}.txt"
        p.write_text("\n\n".join(parts), encoding="utf-8")
        print(f"wrote {p} ({len(chunk)} rows)")

    mode = "WITH VERDICT (not blind)" if args.show_verdict else "blind"
    print(f"\n[ocp-extract] {len(df)} rows, {mode}; {n_unavailable} unavailable "
          f"({n_unavailable / max(len(df), 1):.1%})")


if __name__ == "__main__":
    main()
