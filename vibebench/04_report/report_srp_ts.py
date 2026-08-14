"""
Step 5-TS - SRP aggregates for the TypeScript corpus.

Mirrors 04_report/report.py's reporting rules: lead with the repo-mix-
controlled numbers (macro per-repo mean + top_repo_share) next to the pooled
rate, because pooled rates mislead when an agent's classes concentrate in one
repository (the Codex/MontrealAI lesson from Python).

Outputs: 04_report/srp_ts_summary.csv, srp_ts_by_class.csv, srp_ts_within_repo.csv

Run:  python vibebench/04_report/report_srp_ts.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402


def main() -> None:
    df = pd.read_parquet(C.SRP_RESULTS_TS)
    cls = df[df.class_name.notna()].copy()
    if not len(cls):
        raise SystemExit("[report-ts] no class rows")
    flagged = cls["srp_risk"].notna().any()
    # AUDIT FIX 2026-07-28: test code is a confound (agents differ in how much
    # they write, and test classes flag at a lower rate). Production code is the
    # headline; see config.is_test_path.
    cls["is_test"] = cls["file_path"].apply(lambda p: C.is_test_path(p, "ts"))
    prod = cls[~cls.is_test]

    print(f"=== SRP-TS: {len(cls)} classes ({len(prod)} production, "
          f"{int(cls.is_test.sum())} test), "
          f"{cls.pr_id.nunique()} PRs, {cls.full_name.nunique()} repos ===\n")

    rows = []
    for agent, g in cls.groupby("agent"):
        gp = prod[prod.agent == agent]
        per_repo = g.groupby("full_name")["srp_risk"].mean() if flagged else None
        share = g.groupby("full_name").size().max() / len(g)
        rows.append({
            "agent": agent,
            "prs": g.pr_id.nunique(),
            "repos": g.full_name.nunique(),
            "classes": len(g),
            "pct_test": g.is_test.mean(),
            "risk_rate": g["srp_risk"].mean() if flagged else None,
            "strict_rate": g["srp_risk_strict"].mean() if flagged else None,
            "macro_risk_rate": per_repo.mean() if flagged else None,
            "prod_risk_rate": gp["srp_risk"].mean() if flagged and len(gp) else None,
            "prod_macro_risk_rate": (gp.groupby("full_name")["srp_risk"].mean().mean()
                                     if flagged and len(gp) else None),
            "top_repo_share": share,
            "median_c3": g["c3"].median(),
            "median_class_loc": g["class_loc"].median(),
        })
    summary = pd.DataFrame(rows).sort_values(
        "prod_macro_risk_rate" if flagged else "classes", ascending=False)
    print(summary.round(4).to_string(index=False))
    if flagged:
        print("\n=== HEADLINE: production only + repo-mix-controlled "
              "(prod_macro_risk_rate) ===")
        print(summary[["agent", "classes", "pct_test", "risk_rate",
                       "prod_risk_rate", "macro_risk_rate",
                       "prod_macro_risk_rate"]].round(4).to_string(index=False))

    out = C.REPORT_DIR
    summary.to_csv(out / "srp_ts_summary.csv", index=False)
    cls.to_csv(out / "srp_ts_by_class.csv", index=False)

    # Within-repo: repos where >=2 agents contributed classes.
    if flagged:
        wr = (cls.groupby(["full_name", "agent"])
              .agg(classes=("class_name", "size"), risk_rate=("srp_risk", "mean"))
              .reset_index())
        multi = wr[wr.groupby("full_name")["agent"].transform("nunique") >= 2]
        multi.to_csv(out / "srp_ts_within_repo.csv", index=False)
        if len(multi):
            print("\n=== Within-repo comparisons (repos with >=2 agents) ===")
            print(multi.round(4).to_string(index=False))

    print(f"\n[report-ts] wrote srp_ts_summary.csv / srp_ts_by_class.csv"
          + (" / srp_ts_within_repo.csv" if flagged else "  (calibration mode: no flags yet)"))


if __name__ == "__main__":
    main()
