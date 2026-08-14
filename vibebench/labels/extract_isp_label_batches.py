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


def fetch_class(full_name: str, sha: str, module: str, class_name: str,
                max_lines: int) -> str:
    found = module_source(full_name, sha, module)
    if found is None:
        return f"<<source unavailable: {module} at {sha[:10]}>>"
    path, src = found
    return f"[{path}]\n" + class_snippet(src, class_name, max_lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="isp_labeling_sample.csv")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--per-batch", type=int, default=10)
    ap.add_argument("--max-lines", type=int, default=120)
    args = ap.parse_args()

    df = pd.read_csv(C.LABELS_DIR / args.file)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    for b in range(0, len(df), args.per_batch):
        chunk = df.iloc[b:b + args.per_batch]
        parts = []
        for idx, r in chunk.iterrows():
            head = (f"{'=' * 70}\nROW {idx} | {r.row_type} | {r.agent} | "
                    f"{r.full_name}#{r.pr_number}\n")
            if r.row_type == "interface":
                head += (f"interface {r.iface_id} kind={r.kind} "
                         f"n_abstract={r.n_abstract_methods} "
                         f"n_implementers={r.n_implementers} "
                         f"mean_coverage={r.mean_coverage} "
                         f"rubric_flag={r.isp_risk}\n{'=' * 70}\n")
                body = fetch_class(r.full_name, r.merge_sha, r.module,
                                   r.class_name, args.max_lines)
            else:
                head += (f"implementer {r.module}:{r.class_name} of "
                         f"{r.iface_id} (kind={r.iface_kind}) "
                         f"n_iface_methods={r.n_iface_methods} "
                         f"n_stubbed={r.n_stubbed} coverage={r.coverage} "
                         f"stub_ratio={r.stub_ratio} "
                         f"rubric_flag={r.isp_risk}\n{'=' * 70}\n")
                imod, iname = str(r.iface_id).split(":")
                body = ("--- INTERFACE (the contract) ---\n"
                        + fetch_class(r.full_name, r.merge_sha, imod, iname,
                                      args.max_lines)
                        + "\n\n--- IMPLEMENTER ---\n"
                        + fetch_class(r.full_name, r.merge_sha, r.module,
                                      r.class_name, args.max_lines))
            parts.append(head + body)
        p = out / f"isp_label_batch_{b // args.per_batch:02d}.txt"
        p.write_text("\n\n".join(parts), encoding="utf-8")
        print(f"wrote {p} ({len(chunk)} rows)")


if __name__ == "__main__":
    main()
