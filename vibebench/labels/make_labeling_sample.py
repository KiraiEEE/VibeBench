"""
Step 4a - Draw a stratified sample of classes for SRP reference labeling.

PROVENANCE NOTE: to date these samples have been labeled by an LLM (Claude)
reading each class's actual source -- NOT by human experts. The label column is
named `llm_srp_risk` to keep that explicit. Human expert labels remain future
work; if a human labels a sample, add a separate `human_srp_risk` column rather
than overwriting the LLM one.

Pulls from corpus/srp_results.parquet (Step 3 output): a mix of rubric-flagged
and non-flagged classes, spread across agents, so precision AND recall are
measurable (an all-flagged sample can only measure precision).

Writes a CSV with columns to fill in:
  llm_srp_risk     -- 1 if the reviewer (LLM, reading the class source) judges it
                       a real SRP smell (God Class / mixed responsibilities),
                       0 otherwise, blank if unsure
  notes            -- free text, optional

Open the extracted file at `local_path` (or `file_path` within the repo on GitHub,
commit `head_sha` from clone_manifest.parquet) to review the actual code.

Run:  python vibebench/labels/make_labeling_sample.py [--n-per-stratum 15]
      python vibebench/labels/make_labeling_sample.py --holdout --n-per-stratum 8
        (--holdout: fresh seed, excludes rows already in srp_labeling_sample.csv,
         writes srp_holdout_sample.csv -- for out-of-sample P/R confirmation)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-per-stratum", type=int, default=15,
                     help="classes to sample per (agent, risk-flag) stratum")
    ap.add_argument("--holdout", action="store_true",
                     help="fresh seed + exclude rows already in "
                          "srp_labeling_sample.csv; write srp_holdout_sample.csv")
    args = ap.parse_args()

    seed = C.RANDOM_SEED + 1000 if args.holdout else C.RANDOM_SEED

    results = pd.read_parquet(C.SRP_RESULTS)
    class_rows = results[results.class_name.notna()].copy()

    manifest = pd.read_parquet(C.CLONE_MANIFEST)[
        ["pr_id", "file_path", "local_path", "head_sha"]
    ].drop_duplicates()
    class_rows = class_rows.merge(manifest, on=["pr_id", "file_path"], how="left")

    if args.holdout:
        prev = pd.read_csv(C.LABELS_DIR / "srp_labeling_sample.csv")
        seen = set(zip(prev.pr_id, prev.file_path, prev.class_name))
        keys = list(zip(class_rows.pr_id, class_rows.file_path, class_rows.class_name))
        class_rows = class_rows[[k not in seen for k in keys]]
        print(f"[labels] holdout mode: {len(seen)} tuning rows excluded, "
              f"{len(class_rows)} classes eligible")

    parts = []
    for (agent, risk), grp in class_rows.groupby(["agent", "srp_risk"]):
        n = min(args.n_per_stratum, len(grp))
        parts.append(grp.sample(n, random_state=seed))
    sample = pd.concat(parts).sample(frac=1, random_state=seed).reset_index(drop=True)

    sample["llm_srp_risk"] = ""
    sample["notes"] = ""

    cols = [
        "pr_id", "agent", "full_name", "pr_number", "file_path", "class_name",
        "class_loc", "method_count", "wmc", "lcom", "max_class_params",
        "srp_risk", "local_path", "head_sha", "llm_srp_risk", "notes",
    ]
    C.LABELS_DIR.mkdir(parents=True, exist_ok=True)
    name = "srp_holdout_sample.csv" if args.holdout else "srp_labeling_sample.csv"
    out_path = C.LABELS_DIR / name
    sample[cols].to_csv(out_path, index=False)

    print(f"[labels] wrote {len(sample)} rows to {out_path}")
    print(sample.groupby(["agent", "srp_risk"]).size().to_string())
    print("\nNext: open each local_path, judge if it's a real SRP smell, fill "
          "llm_srp_risk with 1/0, then run score_labels.py"
          + (" --file srp_holdout_sample.csv" if args.holdout else ""))


if __name__ == "__main__":
    main()
