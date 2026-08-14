"""
Step 4a-LSP-helper - Dump the source behind each LSP labeling-sample row into
reviewable batch files.

For each override row we print BOTH sides that decide substitutability:
  * the OVERRIDE method (subclass `qualname` . `method` in `module`), and
  * the BASE method it overrides (from `base_id` = "module:Class").
Both are pulled with `git show <merge_sha>:<path>` from the cached clone dirs
(same as the ISP/OCP extractors). You cannot judge an LSP violation from the
override alone -- refused bequest, strengthened preconditions, and type variance
are all RELATIONS between the override and the base contract, so both must be
seen side by side.

Run:  python vibebench/labels/extract_lsp_label_batches.py \
          --file lsp_labeling_sample.csv --out-dir <dir> \
          [--per-batch 10] [--max-lines 45]
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402


def git_show(full_name: str, sha: str, path: str) -> str | None:
    d = C.CLONES_DIR / full_name.replace("/", "__")
    if not (d / ".git").exists():
        return None
    try:
        r = subprocess.run(
            ["git", "show", f"{sha}:{path}"], cwd=str(d), capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=C.GIT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return None
    return r.stdout if r.returncode == 0 else None


def module_source(full_name: str, sha: str, module: str) -> tuple[str, str] | None:
    base = module.replace(".", "/")
    for cand in (f"{base}.py", f"{base}/__init__.py",
                 f"src/{base}.py", f"src/{base}/__init__.py"):
        src = git_show(full_name, sha, cand)
        if src is not None:
            return cand, src
    return None


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
    args = ap.parse_args()

    df = pd.read_csv(C.LABELS_DIR / args.file)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    for b in range(0, len(df), args.per_batch):
        chunk = df.iloc[b:b + args.per_batch]
        parts = []
        for idx, r in chunk.iterrows():
            sub_cls = str(r.qualname)
            base_id = str(r.base_id)
            base_mod, _, base_cls = base_id.partition(":")
            method = str(r.method)
            sha = str(r.merge_sha)
            head = (f"{'=' * 72}\nROW {idx} | {r.agent} | {r.full_name}"
                    f"#{r.pr_number}\n"
                    f"override: {r.module}:{sub_cls}.{method}   "
                    f"overrides base: {base_id}.{method}\n"
                    f"signals=[{r.signals if not pd.isna(r.signals) else ''}] "
                    f"rubric_violation={r.violation}\n{'=' * 72}\n")

            sub_found = module_source(r.full_name, sha, str(r.module))
            sub_src = sub_found[1] if sub_found else None
            sub_path = sub_found[0] if sub_found else "?"
            base_found = module_source(r.full_name, sha, base_mod)
            base_src = base_found[1] if base_found else None
            base_path = base_found[0] if base_found else "?"

            body = (f"--- OVERRIDE  [{sub_path}]  {sub_cls}.{method} ---\n"
                    + method_snippet(sub_src, sub_cls, method, args.max_lines)
                    + f"\n\n--- BASE  [{base_path}]  {base_cls}.{method} ---\n"
                    + method_snippet(base_src, base_cls, method, args.max_lines))
            parts.append(head + body)
        p = out / f"lsp_label_batch_{b // args.per_batch:02d}.txt"
        p.write_text("\n\n".join(parts), encoding="utf-8")
        print(f"wrote {p} ({len(chunk)} rows)")


if __name__ == "__main__":
    main()
