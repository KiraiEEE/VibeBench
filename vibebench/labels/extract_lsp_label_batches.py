"""
Step 4a-LSP-helper - Dump the source behind each LSP labeling-sample row into
reviewable batch files.

For each override row we print BOTH sides that decide substitutability:
  * the OVERRIDE method (subclass `qualname` . `method` in `module`), and
  * the BASE method it overrides (from `base_id` = "module:Class").
You cannot judge an LSP violation from the override alone -- refused bequest,
strengthened preconditions, and type variance are all RELATIONS between the
override and the base contract, so both must be seen side by side.

TWO DEFECTS FIXED 2026-08-17 (both were Python-only; the TS extractors already
had the first fix and the Java extractor already had the second -- neither was
backported here, which is why this extractor was the one that broke):

  1. SHA RESOLUTION. This used `git show <merge_sha>:<path>` directly. Measured
     on the shipped corpus, **140 of 202 violating override rows (69%) have
     merge_sha = NaN** -- `str(nan)` -> "nan" -> `git show nan:path` fails ->
     "<<source unavailable>>". zfit had 84 violating rows with only 4 usable
     SHAs; onnxscript had 50 with ZERO. That is the documented 40%-unavailable
     blocker. The harnesses never had this problem because they resolve the
     after-state through `gitsnap.delta_pair`, which falls back to the PR's own
     commit SHAs when no merge event was recorded. This now does the same, and
     reads the tree through `gitsnap.src_tree` so the judge sees EXACTLY the
     tree the detector analysed (also removes the old path-guessing heuristic,
     which silently missed any layout other than <mod>.py / src/<mod>.py).

  2. BLIND LABELLING. The batch header printed `signals=[...]` and
     `rubric_violation=...` BEFORE the code, anchoring the judge toward the
     tool's own verdict. Blind is now the default; `--show-verdict` restores the
     old behaviour for debugging. Any figure produced from a pre-2026-08-17
     batch is an UPPER BOUND (same caveat as the Java samples).

Run:  python vibebench/labels/extract_lsp_label_batches.py \
          --file lsp_labeling_sample.csv --out-dir <dir> \
          [--per-batch 10] [--max-lines 45] [--show-verdict]
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE.parents[1] / "03_principles"))
sys.path.insert(0, str(HERE.parents[1] / "03_principles" / "dip"))
import config as C  # noqa: E402
import gitsnap  # noqa: E402
from graph import path_to_module  # noqa: E402

EXTS = C.SOURCE_EXTENSIONS


class _PR:
    """The minimal duck-type gitsnap.delta_pair expects."""

    def __init__(self, merge_sha, commit_shas):
        self.merge_sha = merge_sha
        self.commit_shas = commit_shas


def head_tree(full_name: str, merge_sha, commit_shas) -> dict[str, str] | None:
    """The PR's AFTER-state tree, resolved exactly as the harness resolves it.

    delta_pair prefers (merge commit, first parent) and falls back to the PR's
    own commit set when no merge event exists -- the fallback that this
    extractor was missing.
    """
    d = gitsnap.repo_dir(full_name)
    with gitsnap.repo_lock(full_name):
        pair = gitsnap.delta_pair(d, _PR(merge_sha, commit_shas))
        if pair is None:
            return None
        tree = gitsnap.src_tree(d, pair[1], EXTS, (), full_name)
    if tree is None or gitsnap.TOO_LARGE in tree:
        return None
    return tree


def module_index(tree: dict[str, str]) -> dict[str, str]:
    """{module name: path}, keyed the SAME way the detector keys modules.

    Uses dip/graph.py::path_to_module rather than re-deriving a path guess, so a
    module the detector could see is a module this extractor can find.
    """
    idx: dict[str, str] = {}
    for path in tree:
        mod = path_to_module(path)
        if mod and mod not in idx:
            idx[mod] = path
    return idx


def _find_class(tree: ast.Module, name: str):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def method_snippet(src: str | None, class_name: str, method: str,
                   max_lines: int) -> str:
    if src is None:
        return "<<source unavailable>>"
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError) as e:
        return f"<<unparseable: {e}>>"
    cls = _find_class(tree, class_name)
    if cls is None:
        return f"<<class {class_name} not found>>"
    node = next((s for s in cls.body
                 if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and s.name == method), None)
    if node is None:
        return f"<<method {method} not found in {class_name}>>"
    lines = src.splitlines()
    start = node.lineno - 1
    # include a leading decorator line if present
    if node.decorator_list:
        start = min(start, node.decorator_list[0].lineno - 1)
    end = getattr(node, "end_lineno", node.lineno)
    body = lines[start:end]
    out = [f"  L{start + off + 1}: {text}" for off, text in enumerate(body)]
    if len(out) > max_lines:
        out = out[:max_lines] + [f"... <<truncated, {len(body) - max_lines} more lines>>"]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="lsp_labeling_sample.csv")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--per-batch", type=int, default=10)
    ap.add_argument("--max-lines", type=int, default=45)
    ap.add_argument("--show-verdict", action="store_true",
                    help="print the rubric's signals/verdict in the header "
                         "(NOT blind -- debugging only, biases the judge)")
    args = ap.parse_args()

    df = pd.read_csv(C.LABELS_DIR / args.file)

    # commit_shas lives in the gold set, not in the results parquet: it is what
    # makes the no-merge-SHA fallback possible.
    gold = pd.read_parquet(C.GOLD_SET, columns=["pr_id", "commit_shas"])
    df = df.merge(gold, on="pr_id", how="left")

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    trees: dict[tuple, dict[str, str] | None] = {}
    indexes: dict[tuple, dict[str, str]] = {}
    n_unavailable = 0

    for b in range(0, len(df), args.per_batch):
        chunk = df.iloc[b:b + args.per_batch]
        parts = []
        for idx, r in chunk.iterrows():
            sub_cls = str(r.qualname)
            base_id = str(r.base_id)
            base_mod, _, base_cls = base_id.partition(":")
            method = str(r.method)

            key = (r.full_name, r.pr_id)
            if key not in trees:
                trees[key] = head_tree(r.full_name, r.merge_sha, r.commit_shas)
                indexes[key] = module_index(trees[key]) if trees[key] else {}
            tree, midx = trees[key], indexes[key]

            head = (f"{'=' * 72}\nROW {idx} | {r.agent} | {r.full_name}"
                    f"#{r.pr_number}\n"
                    f"override: {r.module}:{sub_cls}.{method}   "
                    f"overrides base: {base_id}.{method}\n")
            if args.show_verdict:
                head += (f"signals=[{r.signals if not pd.isna(r.signals) else ''}] "
                         f"rubric_violation={r.violation}\n")
            head += f"{'=' * 72}\n"

            sub_path = midx.get(str(r.module))
            base_path = midx.get(base_mod)
            sub_src = tree.get(sub_path) if (tree and sub_path) else None
            base_src = tree.get(base_path) if (tree and base_path) else None
            if sub_src is None or base_src is None:
                n_unavailable += 1

            body = (f"--- OVERRIDE  [{sub_path or '?'}]  {sub_cls}.{method} ---\n"
                    + method_snippet(sub_src, sub_cls, method, args.max_lines)
                    + f"\n\n--- BASE  [{base_path or '?'}]  {base_cls}.{method} ---\n"
                    + method_snippet(base_src, base_cls, method, args.max_lines))
            parts.append(head + body)
        p = out / f"lsp_label_batch_{b // args.per_batch:02d}.txt"
        p.write_text("\n\n".join(parts), encoding="utf-8")
        print(f"wrote {p} ({len(chunk)} rows)")

    mode = "WITH VERDICT (not blind)" if args.show_verdict else "blind"
    print(f"\n[lsp-extract] {len(df)} rows, {mode}; "
          f"{n_unavailable} row(s) missing one or both sides "
          f"({n_unavailable / max(len(df), 1):.1%})")


if __name__ == "__main__":
    main()
