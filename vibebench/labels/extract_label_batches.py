"""
Step 4a-helper - Dump the source of every class in a labeling sample CSV into
reviewable batch files, so a reviewer (LLM or human) can read the actual code
without opening 100+ files by hand.

Each class is printed with a header (repo, PR, metrics) followed by its source,
truncated to --max-lines lines plus a full `def` index so long classes remain
judgeable. Batches go to --out-dir as label_batch_NN.txt.

Run:  python vibebench/labels/extract_label_batches.py --file srp_holdout_sample.csv \
          --out-dir <dir> [--per-batch 12] [--max-lines 120]
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402


def class_source(local_path: str, class_name: str, max_lines: int) -> str:
    try:
        src = Path(local_path).read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
    except (OSError, SyntaxError) as e:
        return f"<<unavailable: {e}>>"
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            lines = src.splitlines()[node.lineno - 1:getattr(node, "end_lineno", node.lineno)]
            defs = [f"  L{n.lineno}: def {n.name}(...)"
                    for n in ast.walk(node)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            body = "\n".join(lines[:max_lines])
            if len(lines) > max_lines:
                body += f"\n... <<truncated, {len(lines) - max_lines} more lines>>"
            return "METHOD INDEX:\n" + "\n".join(defs) + "\n\nSOURCE:\n" + body
    return "<<class not found in file>>"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="srp_labeling_sample.csv")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--per-batch", type=int, default=12)
    ap.add_argument("--max-lines", type=int, default=120)
    args = ap.parse_args()

    df = pd.read_csv(C.LABELS_DIR / args.file)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    for b in range(0, len(df), args.per_batch):
        chunk = df.iloc[b:b + args.per_batch]
        parts = []
        for idx, r in chunk.iterrows():
            parts.append(
                f"{'=' * 70}\nROW {idx} | {r.agent} | {r.full_name}#{r.pr_number} | "
                f"{r.file_path} :: {r.class_name}\n"
                f"loc={r.class_loc} methods={r.method_count} wmc={r.wmc} "
                f"lcom={r.lcom} rubric_flag={r.srp_risk}\n{'=' * 70}\n"
                + class_source(r.local_path, r.class_name, args.max_lines)
            )
        p = out / f"label_batch_{b // args.per_batch:02d}.txt"
        p.write_text("\n\n".join(parts), encoding="utf-8")
        print(f"wrote {p} ({len(chunk)} classes)")


if __name__ == "__main__":
    main()
