"""
Step 4a-OCP-TS helper - dump each sampled dispatch chain WITH its enclosing
function so the judge sees real code, not just a branch count.

A dispatch chain can only be judged in context: the question is whether the
tested family is OPEN (new variants will keep arriving, so the ladder must be
edited -- an OCP violation) or CLOSED (a fixed, exhaustive set -- not a
violation). That needs the surrounding function, so this extractor pulls a
window around the chain's line from the head-state file in the tree cache.

Run:  python vibebench/labels/extract_ocp_ts_label_batches.py \
          --file ocp_ts_labeling_sample.csv --out-dir vibebench/labels/ocp_ts_batches
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0]))
sys.path.insert(0, str(HERE.parents[0] / "03_principles"))
import config as C  # noqa: E402
import gitsnap  # noqa: E402

EXTS, EXCL = C.TS_SOURCE_EXTENSIONS, C.TS_EXCLUDED_PATH_PARTS


def snippet(row, before=25, after=90) -> str:
    head = (f"### {row.qualname or '<module>'}  [{row.kind}] subject=`{row.subject}`\n"
            f"    {row.agent}  {row.full_name}#{row.pr_number}  {row.file_path}\n"
            f"    branches={row.n_branches}  closed_set={row.closed_set}  "
            f"rubric_smell={row.smell}\n"
            f"    tested: {str(row.type_names)[:200]}\n")
    try:
        d = gitsnap.repo_dir(row.full_name)
        tree = gitsnap.src_tree(d, row.merge_sha, EXTS, EXCL, row.full_name)
        src = (tree or {}).get(row.file_path)
        if not src:
            return head + "    (source unavailable)\n"
        lines = src.splitlines()
        start = max(0, int(row.lineno) - before)
        end = min(len(lines), int(row.lineno) + after)
        body = "\n".join(lines[start:end])
        return head + f"```ts\n// lines {start+1}-{end}\n{body}\n```\n"
    except Exception as e:  # noqa: BLE001
        return head + f"    (error: {e})\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="ocp_ts_labeling_sample.csv")
    ap.add_argument("--out-dir", default=str(HERE / "ocp_ts_batches"))
    ap.add_argument("--per-batch", type=int, default=15)
    a = ap.parse_args()

    df = pd.read_csv(HERE / a.file)
    outdir = Path(a.out_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    blocks = []
    for i, row in enumerate(df.itertuples(index=False), 1):
        print(f"[{i}/{len(df)}] {row.full_name}#{row.pr_number} {row.module}", flush=True)
        blocks.append(snippet(row))
    for i in range(0, len(blocks), a.per_batch):
        p = outdir / f"ocp_ts_label_batch_{i // a.per_batch:02d}.txt"
        p.write_text("\n\n".join(blocks[i:i + a.per_batch]), encoding="utf-8")
        print("wrote", p)


if __name__ == "__main__":
    main()
