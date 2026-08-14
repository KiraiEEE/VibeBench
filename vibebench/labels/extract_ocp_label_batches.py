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
    args = ap.parse_args()

    df = pd.read_csv(C.LABELS_DIR / args.file)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    for b in range(0, len(df), args.per_batch):
        chunk = df.iloc[b:b + args.per_batch]
        parts = []
        for idx, r in chunk.iterrows():
            qual = "" if pd.isna(r.qualname) else str(r.qualname)
            head = (f"{'=' * 70}\nROW {idx} | {r.agent} | {r.full_name}"
                    f"#{r.pr_number}\n"
                    f"dispatch in {r.module}:{qual or '<module>'} "
                    f"subject=`{r.subject}` kind={r.kind} "
                    f"n_branches={r.n_branches} rubric_smell={r.smell}\n"
                    f"{'=' * 70}\n")
            found = module_source(r.full_name, r.merge_sha, r.module)
            if found is None:
                body = f"<<source unavailable: {r.module} at {str(r.merge_sha)[:10]}>>"
            else:
                path, src = found
                body = f"[{path}]\n" + dispatch_snippet(
                    src, qual, int(r.lineno), args.max_lines)
            parts.append(head + body)
        p = out / f"ocp_label_batch_{b // args.per_batch:02d}.txt"
        p.write_text("\n\n".join(parts), encoding="utf-8")
        print(f"wrote {p} ({len(chunk)} rows)")


if __name__ == "__main__":
    main()
