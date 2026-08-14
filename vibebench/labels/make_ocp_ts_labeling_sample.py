"""
Step 4a-OCP-TS - Stratified sample of TypeScript dispatch chains for labeling.

Samples `dispatch` rows (every in-scope chain, flagged or not) so precision AND
recall are measurable, stratified by (agent, smell). Same provenance rules as
every other sampler: labels are LLM-produced (`llm_ocp_risk`), NOT human; the
holdout uses a fresh seed and excludes tuning rows via a key built only from
columns present in both the parquet and the CSV.

Run:  python vibebench/labels/make_ocp_ts_labeling_sample.py [--n-per-stratum 8]
      python vibebench/labels/make_ocp_ts_labeling_sample.py --holdout --n-per-stratum 5
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

KEY = ["pr_id", "module", "qualname", "subject"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-per-stratum", type=int, default=8)
    ap.add_argument("--holdout", action="store_true")
    args = ap.parse_args()

    seed = C.RANDOM_SEED + 1000 if args.holdout else C.RANDOM_SEED
    df = pd.read_parquet(C.OCP_RESULTS_TS)
    disp = df[df.row_type == "dispatch"].copy()
    if disp["smell"].isna().all():
        raise SystemExit("[labels-ocp-ts] smell is null -- thresholds not "
                         "calibrated yet (OCP_TS_THRESHOLDS is None).")

    if args.holdout:
        prev = pd.read_csv(C.LABELS_DIR / "ocp_ts_labeling_sample.csv")
        for k in KEY:
            prev[k] = prev[k].astype(str)
            disp[k] = disp[k].astype(str)
        seen = set(map(tuple, prev[KEY].values))
        keys = list(map(tuple, disp[KEY].values))
        disp = disp[[k not in seen for k in keys]]
        print(f"[labels-ocp-ts] holdout: {len(seen)} tuning rows excluded, "
              f"{len(disp)} chains eligible")

    parts = []
    for (agent, smell), grp in disp.groupby(["agent", "smell"]):
        parts.append(grp.sample(min(args.n_per_stratum, len(grp)), random_state=seed))
    sample = pd.concat(parts).sample(frac=1, random_state=seed).reset_index(drop=True)
    sample["llm_ocp_risk"] = ""
    sample["notes"] = ""

    cols = ["pr_id", "agent", "full_name", "pr_number", "merge_sha", "module",
            "qualname", "subject", "kind", "n_branches", "lineno", "file_path",
            "closed_set", "type_names", "smell", "llm_ocp_risk", "notes"]
    name = ("ocp_ts_holdout_sample.csv" if args.holdout
            else "ocp_ts_labeling_sample.csv")
    out = C.LABELS_DIR / name
    sample[cols].to_csv(out, index=False)
    print(f"[labels-ocp-ts] wrote {len(sample)} rows to {out}")
    print(sample.groupby(["agent", "smell"]).size().to_string())


if __name__ == "__main__":
    main()
