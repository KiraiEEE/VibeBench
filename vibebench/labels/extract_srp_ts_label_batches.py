"""
Step 4a-TS helper - dump each sampled TypeScript class's source into
reviewable batch files so the judge reads REAL code, not metrics.

For each row of the sample CSV, extracts the class body (by line span from
srp_results_ts.parquet's class_lineno + class_loc) from the extracted file at
local_path, with a small header of the structural context. ~15 classes per
batch file.

Run:  python vibebench/labels/extract_srp_ts_label_batches.py \
          --file srp_ts_labeling_sample.csv --out-dir vibebench/labels/srp_ts_batches
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0]))
import config as C  # noqa: E402


def class_snippet(row, results: pd.DataFrame) -> str:
    match = results[
        (results.pr_id == row.pr_id)
        & (results.file_path == row.file_path)
        & (results.class_name == row.class_name)
    ]
    header = (f"### {row.class_name}  ({row.agent}  {row.full_name}#{row.pr_number}  "
              f"{row.file_path})\n"
              f"metrics: loc={row.class_loc} methods={row.method_count} "
              f"wmc={row.wmc} lcom={row.lcom} c3={row.c3} "
              f"rubric_flag={row.srp_risk}\n")
    try:
        src = Path(row.local_path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return header + f"  (source unavailable: {e})\n"
    lines = src.splitlines()
    if len(match):
        start = int(match.iloc[0]["class_lineno"]) - 1
        span = int(match.iloc[0]["class_loc"])
        body = "\n".join(lines[start:start + span])
    else:
        body = "\n".join(lines)  # fallback: whole file
    return header + "```ts\n" + body + "\n```\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="srp_ts_labeling_sample.csv")
    ap.add_argument("--out-dir", default=str(HERE / "srp_ts_batches"))
    ap.add_argument("--per-batch", type=int, default=15)
    args = ap.parse_args()

    df = pd.read_csv(HERE / args.file)
    results = pd.read_parquet(C.SRP_RESULTS_TS)
    outdir = Path(args.out_dir)
    outdir.mkdir(parents=True, exist_ok=True)

    blocks = [class_snippet(row, results) for row in df.itertuples(index=False)]
    for i in range(0, len(blocks), args.per_batch):
        p = outdir / f"srp_ts_label_batch_{i // args.per_batch:02d}.txt"
        p.write_text("\n\n".join(blocks[i:i + args.per_batch]), encoding="utf-8")
        print("wrote", p)


if __name__ == "__main__":
    main()
