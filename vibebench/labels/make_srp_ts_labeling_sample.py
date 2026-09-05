"""
Step 4a-TS - Stratified sample of TypeScript classes for SRP reference labeling.

Same protocol and provenance rules as the Python sampler
(make_labeling_sample.py): labels are LLM-produced (`llm_srp_risk`), NOT human;
stratified by (agent, srp_risk) so precision AND recall are measurable; the
holdout uses a fresh seed and excludes tuning rows by a key built ONLY from
columns present in both the parquet and the CSV (the ISP sampler-bug lesson).

Requires srp_results_ts.parquet to have srp_risk set (i.e. thresholds
calibrated and run_srp_ts re-run) -- refuses to sample in calibration mode.

Run:  python vibebench/labels/make_srp_ts_labeling_sample.py [--n-per-stratum 12]
      python vibebench/labels/make_srp_ts_labeling_sample.py --holdout --n-per-stratum 6
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
    ap.add_argument("--n-per-stratum", type=int, default=12)
    ap.add_argument("--holdout", action="store_true")
    args = ap.parse_args()

    seed = C.RANDOM_SEED + 1000 if args.holdout else C.RANDOM_SEED

    results = pd.read_parquet(C.SRP_RESULTS_TS)
    class_rows = results[results.class_name.notna()].copy()
    if class_rows["srp_risk"].isna().all():
        raise SystemExit("[labels-ts] srp_risk is null everywhere -- thresholds "
                         "not calibrated yet (SRP_TS_THRESHOLDS is None). "
                         "Calibrate + re-run run_srp_ts.py first.")

    manifest = pd.read_parquet(C.CLONE_MANIFEST_TS)[
        ["pr_id", "file_path", "local_path", "head_sha"]
    ].drop_duplicates()
    class_rows = class_rows.merge(manifest, on=["pr_id", "file_path"], how="left")

    if args.holdout:
        prev = pd.read_csv(C.LABELS_DIR / "srp_ts_labeling_sample.csv")
        seen = set(zip(prev.pr_id, prev.file_path, prev.class_name))
        keys = list(zip(class_rows.pr_id, class_rows.file_path, class_rows.class_name))
        class_rows = class_rows[[k not in seen for k in keys]]
        print(f"[labels-ts] holdout mode: {len(seen)} tuning rows excluded, "
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
        "class_loc", "method_count", "wmc", "lcom", "c3", "max_class_params",
        "srp_risk", "local_path", "head_sha", "llm_srp_risk", "notes",
    ]
    C.LABELS_DIR.mkdir(parents=True, exist_ok=True)
    name = "srp_ts_holdout_sample.csv" if args.holdout else "srp_ts_labeling_sample.csv"
    out_path = C.LABELS_DIR / name
    sample[cols].to_csv(out_path, index=False)

    print(f"[labels-ts] wrote {len(sample)} rows to {out_path}")
    print(sample.groupby(["agent", "srp_risk"]).size().to_string())
    print("\nNext: extract_srp_ts_label_batches.py --file " + name
          + " , judge each class, fill llm_srp_risk, then "
          "score_labels.py --file " + name
          + " --pred-col srp_risk --label-col llm_srp_risk")


if __name__ == "__main__":
    main()
