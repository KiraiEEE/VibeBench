"""Complete the Dependency Inversion flagged stratum into a census.

DIP flags only 195 pull requests in the whole corpus, and 100 of them were
already judged. Judging the remaining 95 removes sampling error from DIP
precision entirely: the figure stops being an estimate with an interval and
becomes the exact proportion over every flagged case in the study.

No other principle can do this. SRP would need 28,756 further rows.

Writes java_dip_labeling_sample_census.csv in the same schema as the round-2
sample, so the existing extractor, audit gate and recorder all work unchanged.

Run:  python vibebench/labels/make_dip_census.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))
import config as C  # noqa: E402

OUT = C.LABELS_DIR / "java_dip_labeling_sample_census.csv"
PRIOR = C.LABELS_DIR / "java_dip_labeling_sample.csv"


def main() -> None:
    r = pd.read_parquet(C.CORPUS_DIR / "dip_results_java.parquet")
    ok = r[r.status == "ok"]
    flagged = ok[ok.dip_risk.fillna(False).astype(bool)].copy()

    prior = pd.read_csv(PRIOR)
    judged = set(prior[prior.llm_dip_risk.notna()].pr_id)

    remaining = flagged[~flagged.pr_id.isin(judged)].copy()

    print(f"flagged in corpus      : {len(flagged)}")
    print(f"already judged         : {len(flagged) - len(remaining)}")
    print(f"remaining for a census : {len(remaining)}")
    if remaining.empty:
        print("nothing to do; DIP is already a census")
        return

    # Same schema as the round-2 sample so every downstream tool works unchanged.
    remaining["row_key"] = (remaining[["pr_id", "full_name", "pr_number"]]
                            .astype(str).agg("||".join, axis=1))
    remaining["llm_dip_risk"] = pd.NA
    remaining["notes"] = pd.NA

    cols = [c for c in prior.columns if c in remaining.columns]
    missing = [c for c in prior.columns if c not in remaining.columns]
    for m in missing:
        remaining[m] = pd.NA
    remaining = remaining[list(prior.columns)]

    # Deterministic order, so a re-run produces the same file.
    remaining = remaining.sort_values("pr_id").reset_index(drop=True)
    remaining.to_csv(OUT, index=False)
    print(f"\nwrote {OUT.name}: {len(remaining)} rows, {len(remaining.columns)} columns")
    print("agents:", dict(remaining.agent.value_counts()))
    print("\nnext:")
    print("  python vibebench/labels/extract_java_label_batches.py "
          "--principle dip --file java_dip_labeling_sample_census.csv")
    print("  python vibebench/07_verify/audit_label_batches.py "
          "--principle dip --round _census")


if __name__ == "__main__":
    main()
