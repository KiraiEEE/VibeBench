"""
Step 5-ISP - Aggregate + report ISP delta results per agent.

Reads corpus/isp_results.parquet (Step 3-ISP output) and produces:
  - a console summary (pooled + per-repo macro-averaged, same repo-mix
    controls as the SRP/DIP reports)
  - 04_report/isp_summary.csv    (per-agent PR-level delta aggregates)
  - 04_report/isp_by_pr.csv      (one row per analyzable PR)
  - 04_report/isp_interfaces.csv (head-state interface inventory, context)

All PR-level ISP metrics are BEFORE/AFTER DELTAS around the merge commit --
an agent is only debited for interfaces/implementers its PR created or
changed, never for pre-existing architecture.

Run:  python vibebench/04_report/report_isp.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402


def main() -> None:
    C.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    results = pd.read_parquet(C.ISP_RESULTS)
    pr_rows = results[results.row_type == "pr"].copy()

    print("[isp-report] PR status counts:")
    print(pr_rows["status"].value_counts().to_string())

    ok = pr_rows[pr_rows.status == "ok"].copy()
    if ok.empty:
        print("[isp-report] no analyzable PRs yet")
        return

    def reason_rate(reason: str):
        return ("isp_risk_reasons",
                lambda s: s.apply(lambda r: reason in list(r)).mean())

    summary = ok.groupby("agent").agg(
        n_prs=("pr_id", "size"),
        n_repos=("full_name", "nunique"),
        isp_risk_rate=("isp_risk", "mean"),
        new_flagged_iface_rate=reason_rate("new_flagged_iface"),
        widened_fat_iface_rate=reason_rate("widened_fat_iface"),
        new_forced_stub_rate=reason_rate("new_forced_stub"),
        prs_with_iface_in_scope=("n_ifaces_in_scope", lambda s: (s > 0).mean()),
        ifaces_in_scope=("n_ifaces_in_scope", "sum"),
        impl_pairs_in_scope=("n_impl_pairs_in_scope", "sum"),
    ).round(3).sort_values("isp_risk_rate", ascending=False)

    # Repo-mix control: every repo weighs the same (see SRP report rationale).
    per_repo = (ok.groupby(["agent", "full_name"])
                .agg(n=("pr_id", "size"), rate=("isp_risk", "mean"))
                .reset_index())
    macro = per_repo.groupby("agent")["rate"].mean().rename("macro_isp_risk_rate")
    conc = (per_repo.groupby("agent")
            .apply(lambda g: g.n.max() / g.n.sum(), include_groups=False)
            .rename("top_repo_share"))
    summary = summary.join(macro.round(3)).join(conc.round(3))

    print("\n=== ISP summary per agent (PR-level deltas) ===")
    print(summary.to_string())

    ifaces = results[results.row_type == "interface"].copy()
    if len(ifaces):
        print("\n=== Interface inventory (in-scope, head-state) ===")
        print(ifaces.groupby("kind").agg(
            n=("iface_id", "size"),
            flagged=("isp_risk", "sum"),
            median_abstract=("n_abstract_methods", "median"),
            p90_abstract=("n_abstract_methods", lambda s: s.quantile(0.9)),
            max_abstract=("n_abstract_methods", "max"),
        ).round(1).to_string())

    summary.to_csv(C.REPORT_DIR / "isp_summary.csv")
    ok.drop(columns=["isp_risk_reasons"]).to_csv(
        C.REPORT_DIR / "isp_by_pr.csv", index=False)
    if len(ifaces):
        iface_cols = [c for c in ifaces.columns if c != "hits"]
        ifaces[iface_cols].to_csv(C.REPORT_DIR / "isp_interfaces.csv", index=False)
    print(f"\n[isp-report] wrote {C.REPORT_DIR / 'isp_summary.csv'}")
    print(f"[isp-report] wrote {C.REPORT_DIR / 'isp_by_pr.csv'}")
    print(f"[isp-report] wrote {C.REPORT_DIR / 'isp_interfaces.csv'}")


if __name__ == "__main__":
    main()
