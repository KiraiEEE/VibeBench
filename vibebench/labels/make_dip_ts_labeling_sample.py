"""
Step 4a-DIP-TS - Stratified sample of TypeScript PR-level DIP deltas for labeling.

PROVENANCE: labels are LLM-produced (`llm_dip_risk`), NOT human.

DIP has no head-state entity to label the way SRP has classes or OCP has chains:
the claim is about the DEPENDENCY DELTA a PR introduces, so PR rows are the unit.
Positives are very sparse (5 new-cycle PRs in 619), so as with Python DIP this is
a single indicative sample -- there are not enough positives to split a holdout,
and that limitation is reported rather than hidden.

Strata: (agent, dip_risk). A stratum smaller than --n-per-stratum is taken whole.

Labeling criterion for llm_dip_risk (fill 1/0, blank if unsure):
  1 -- the PR genuinely makes a high-level module depend on a concrete
       low-level one (or closes a real import cycle) in a way an abstraction
       would have prevented
  0 -- the flag is graph recomposition, a test/demo wiring edge, or a
       dependency that is already on an abstraction

Run:  python vibebench/labels/make_dip_ts_labeling_sample.py [--n-per-stratum 10]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

COLS = ["pr_id", "agent", "full_name", "pr_number", "merge_sha",
        "n_touched_modules", "before_touched_cycles", "after_touched_cycles",
        "delta_touched_cycles", "before_mean_d", "after_mean_d", "delta_mean_d",
        "delta_abstract_dep_ratio", "new_cycle", "dip_risk_hits", "dip_risk"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-per-stratum", type=int, default=10)
    ap.add_argument("--holdout", action="store_true")
    args = ap.parse_args()

    seed = C.RANDOM_SEED + 1000 if args.holdout else C.RANDOM_SEED
    df = pd.read_parquet(C.DIP_RESULTS_TS)
    ok = df[df.status == "ok"].copy()
    ok["dip_risk"] = ok["dip_risk"].fillna(False)

    if args.holdout:
        prev = pd.read_csv(C.LABELS_DIR / "dip_ts_labeling_sample.csv")
        ok = ok[~ok.pr_id.astype(str).isin(prev.pr_id.astype(str))]
        print(f"[labels-dip-ts] holdout: {len(prev)} tuning PRs excluded, "
              f"{len(ok)} eligible")

    parts = []
    for _, grp in ok.groupby(["agent", "dip_risk"]):
        parts.append(grp.sample(min(args.n_per_stratum, len(grp)), random_state=seed))
    sample = pd.concat(parts).sample(frac=1, random_state=seed).reset_index(drop=True)
    sample["llm_dip_risk"] = ""
    sample["notes"] = ""

    name = ("dip_ts_holdout_sample.csv" if args.holdout
            else "dip_ts_labeling_sample.csv")
    out = C.LABELS_DIR / name
    sample[COLS + ["llm_dip_risk", "notes"]].to_csv(out, index=False)
    print(f"[labels-dip-ts] wrote {len(sample)} rows to {out}")
    print(sample.groupby(["agent", "dip_risk"]).size().to_string())


if __name__ == "__main__":
    main()
