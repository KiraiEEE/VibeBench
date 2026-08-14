"""
Step 5-DIP-TS - DIP aggregates for the TypeScript corpus.

Mirrors report_dip.py. Leads with the NEW-CYCLE count, not the composite risk
flag: the Python DIP validation (P=0.22 / R=0.67) showed `new_cycle` is the
trustworthy half of the signal while `mean_d` is mostly graph-recomposition
noise -- 4 of its 7 false positives had ZERO new dependencies.

Counts are printed BESIDE every rate (audit 2026-07-28, finding 7).

Outputs: 04_report/dip_ts_summary.csv, dip_ts_by_pr.csv

Run:  python vibebench/04_report/report_dip_ts.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402


def main() -> None:
    df = pd.read_parquet(C.DIP_RESULTS_TS)
    ok = df[df.status == "ok"].copy()

    print(f"=== DIP-TS: {len(df)} PRs ({len(ok)} analyzable) ===\n")
    print("PR status:")
    print(df["status"].value_counts().to_string())

    ok["new_cycle"] = ok["new_cycle"].fillna(False)
    rows = []
    for agent, g in ok.groupby("agent"):
        share = (g.groupby("full_name").size().max() / len(g)) if len(g) else float("nan")
        rows.append({
            "agent": agent,
            "prs": len(g),
            "repos": g.full_name.nunique(),
            "n_new_cycle": int(g["new_cycle"].sum()),
            "new_cycle_rate": g["new_cycle"].mean(),
            "macro_new_cycle_rate": g.groupby("full_name")["new_cycle"].mean().mean(),
            "n_dip_risk": int(g["dip_risk"].fillna(False).sum()),
            "dip_risk_rate": g["dip_risk"].mean(),
            "macro_dip_risk_rate": g.groupby("full_name")["dip_risk"].mean().mean(),
            "mean_delta_d": g["delta_mean_d"].mean(),
            "top_repo_share": share,
        })
    summary = pd.DataFrame(rows).sort_values("macro_new_cycle_rate", ascending=False)
    print("\n=== Per-agent summary (lead with NEW CYCLES; mean_d is noisy) ===")
    print("NOTE: read the n_ columns first -- these rates rest on single-digit counts.")
    print(summary.round(4).to_string(index=False))

    cyc = ok[ok["new_cycle"]]
    if len(cyc):
        print(f"\n=== The {len(cyc)} PRs that created a new import cycle ===")
        print(cyc[["agent", "full_name", "pr_number", "n_touched_modules",
                   "before_touched_cycles", "after_touched_cycles"]]
              .to_string(index=False))

    hits = ok["dip_risk_hits"].fillna("")
    print("\nReason breakdown (a PR can hit both):")
    print(f"  new_cycle     {int(hits.str.contains('new_cycle').sum())}")
    print(f"  mean_d_worse  {int(hits.str.contains('mean_d_worse').sum())}"
          "   <- treat as indicative only")

    out = C.REPORT_DIR
    summary.to_csv(out / "dip_ts_summary.csv", index=False)
    df.to_csv(out / "dip_ts_by_pr.csv", index=False)
    print("\n[report-dip-ts] wrote dip_ts_summary.csv / dip_ts_by_pr.csv")


if __name__ == "__main__":
    main()
