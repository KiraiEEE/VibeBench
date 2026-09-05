"""Step 4a-DIP: stratified labeling sample from dip_results.parquet.

DIP's positive signal is SPARSE (only ~9 flagged PRs across 389 analyzable), so a
classic 50/50 tuning+holdout split would give ~1 positive per fold -- meaningless.
Instead we draw ONE stratified validation sample that (a) includes EVERY flagged PR
(are the flags real?), and (b) over-samples the highest-delta NON-flagged PRs (are
there missed violations just under threshold?), plus a benign random tail as true
negatives. Judge each from the surfaced import-graph delta (extract_dip_label_batches.py).

Prediction column: dip_risk. Label column to fill: llm_dip_risk.
"""
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

SEED = C.RANDOM_SEED
OUT = Path(__file__).resolve().parent / "dip_labeling_sample.csv"
KEEP = ["pr_id", "agent", "full_name", "pr_number", "merge_sha", "status",
        "n_touched_modules", "dip_risk", "dip_risk_hits",
        "delta_touched_cycles", "delta_mean_d", "delta_abstract_dep_ratio",
        "before_mean_d", "after_mean_d",
        "before_abstract_dep_ratio", "after_abstract_dep_ratio",
        "before_touched_cycles", "after_touched_cycles"]


def main() -> None:
    df = pd.read_parquet(C.DIP_RESULTS)
    df = df[df["status"] == "ok"].copy()
    df["delta_mean_d"] = df["delta_mean_d"].fillna(0.0)
    df["delta_abstract_dep_ratio"] = df["delta_abstract_dep_ratio"].fillna(0.0)
    df["delta_touched_cycles"] = df["delta_touched_cycles"].fillna(0)

    picked = []

    def take(sub, stratum, n=None):
        sub = sub[~sub["pr_id"].isin([p["pr_id"] for p in picked])]
        if n is not None:
            sub = sub.sample(min(n, len(sub)), random_state=SEED) if len(sub) else sub
        for _, r in sub.iterrows():
            row = {k: r.get(k) for k in KEEP}
            row["stratum"] = stratum
            picked.append(row)

    # 1. every flagged PR
    take(df[df["dip_risk"] == True], "flagged")
    # 2. new import cycles among touched modules, not otherwise flagged
    take(df[df["delta_touched_cycles"] > 0], "new_cycle")
    # 3. worst distance-from-main-sequence regressions
    take(df.sort_values("delta_mean_d", ascending=False).head(10), "worst_delta_d")
    # 4. biggest abstractness-dependency drops (more concrete after)
    take(df.sort_values("delta_abstract_dep_ratio").head(8), "abstract_drop")
    # 5. benign random tail (true negatives)
    take(df[(df["delta_mean_d"].abs() < 0.05) & (df["delta_touched_cycles"] == 0)],
         "benign", n=12)

    out = pd.DataFrame(picked)
    out["llm_dip_risk"] = ""
    out["notes"] = ""
    out.to_csv(OUT, index=False)
    print(f"wrote {OUT}  ({len(out)} rows)")
    print("strata:", out["stratum"].value_counts().to_dict())
    print("flagged (dip_risk) in sample:", int(out["dip_risk"].sum()))


if __name__ == "__main__":
    main()
