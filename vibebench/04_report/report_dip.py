"""
Step 5-DIP - Aggregate + report DIP delta results per agent.

Reads corpus/dip_results.parquet (Step 3-DIP output) and produces:
  - a console summary (pooled + per-repo macro-averaged, same repo-mix
    controls as the SRP report)
  - 04_report/dip_summary.csv
  - 04_report/dip_by_pr.csv

All DIP metrics are BEFORE/AFTER DELTAS around the PR's merge commit -- an
agent is only credited/debited for what its PR changed.

Run:  python vibebench/04_report/report_dip.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402


def main() -> None:
    C.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    results = pd.read_parquet(C.DIP_RESULTS)

    print("[dip-report] PR status counts:")
    print(results["status"].value_counts().to_string())

    ok = results[results.status == "ok"].copy()
    if ok.empty:
        print("[dip-report] no analyzable PRs yet")
        return

    summary = ok.groupby("agent").agg(
        n_prs=("pr_id", "size"),
        n_repos=("full_name", "nunique"),
        dip_risk_rate=("dip_risk", "mean"),
        cycle_creating_pr_rate=("delta_touched_cycles", lambda s: (s > 0).mean()),
        cycle_removing_pr_rate=("delta_touched_cycles", lambda s: (s < 0).mean()),
        mean_delta_cycles=("delta_touched_cycles", "mean"),
        mean_delta_d=("delta_mean_d", "mean"),
        mean_delta_abstract_dep=("delta_abstract_dep_ratio", "mean"),
        mean_after_abstract_dep=("after_abstract_dep_ratio", "mean"),
    ).round(3).sort_values("dip_risk_rate", ascending=False)

    # Repo-mix control: every repo weighs the same (see SRP report rationale).
    per_repo = (ok.groupby(["agent", "full_name"])
                .agg(n=("pr_id", "size"), rate=("dip_risk", "mean"))
                .reset_index())
    macro = per_repo.groupby("agent")["rate"].mean().rename("macro_dip_risk_rate")
    conc = (per_repo.groupby("agent")
            .apply(lambda g: g.n.max() / g.n.sum(), include_groups=False)
            .rename("top_repo_share"))
    summary = summary.join(macro.round(3)).join(conc.round(3))

    print("\n=== DIP summary per agent (PR-level deltas) ===")
    print(summary.to_string())

    summary.to_csv(C.REPORT_DIR / "dip_summary.csv")
    ok.drop(columns=["dip_risk_hits"]).to_csv(C.REPORT_DIR / "dip_by_pr.csv", index=False)
    print(f"\n[dip-report] wrote {C.REPORT_DIR / 'dip_summary.csv'}")
    print(f"[dip-report] wrote {C.REPORT_DIR / 'dip_by_pr.csv'}")


if __name__ == "__main__":
    main()
