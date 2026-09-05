"""
Step 5-OCP - Aggregate + report OCP delta results per agent.

Reads corpus/ocp_results.parquet (Step 3-OCP output) and produces:
  - a console summary (pooled + per-repo macro-averaged, same repo-mix
    controls as the SRP/DIP/ISP reports)
  - 04_report/ocp_summary.csv    (per-agent PR-level delta aggregates)
  - 04_report/ocp_by_pr.csv      (one row per analyzable PR)
  - 04_report/ocp_dispatch.csv   (head-state flagged dispatch chains, context)

All PR-level OCP metrics are BEFORE/AFTER DELTAS around the merge commit -- an
agent is only debited for dispatch it created or widened, never for the repo's
pre-existing switch statements. The add-vs-modify def profile is descriptive
(never a flag); it is summed per agent for context on extension vs modification
style.

The dispatch-chain inventory also prints the branch-count distribution, which
is what the OCP thresholds should be CALIBRATED against before any labeling
(mirrors the ISP n_abstract_methods percentile calibration).

Run:  python vibebench/04_report/report_ocp.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402


def main() -> None:
    C.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    results = pd.read_parquet(C.OCP_RESULTS)
    pr_rows = results[results.row_type == "pr"].copy()

    print("[ocp-report] PR status counts:")
    print(pr_rows["status"].value_counts().to_string())

    ok = pr_rows[pr_rows.status == "ok"].copy()
    if ok.empty:
        print("[ocp-report] no analyzable PRs yet")
        return

    def reason_rate(reason: str):
        return ("ocp_risk_reasons",
                lambda s: s.apply(lambda r: reason in list(r)).mean())

    summary = ok.groupby("agent").agg(
        n_prs=("pr_id", "size"),
        n_repos=("full_name", "nunique"),
        ocp_risk_rate=("ocp_risk", "mean"),
        new_dispatch_smell_rate=reason_rate("new_dispatch_smell"),
        dispatch_widened_rate=reason_rate("dispatch_widened"),
        smells_in_scope=("n_smells_in_scope", "sum"),
        defs_added=("n_defs_added", "sum"),
        defs_modified=("n_defs_modified", "sum"),
    ).round(3).sort_values("ocp_risk_rate", ascending=False)

    # Modify:add ratio -- descriptive "extension vs modification" style signal.
    summary["modify_add_ratio"] = (
        summary["defs_modified"] / summary["defs_added"].replace(0, pd.NA)
    ).round(2)

    # Repo-mix control: every repo weighs the same (see SRP report rationale).
    per_repo = (ok.groupby(["agent", "full_name"])
                .agg(n=("pr_id", "size"), rate=("ocp_risk", "mean"))
                .reset_index())
    macro = per_repo.groupby("agent")["rate"].mean().rename("macro_ocp_risk_rate")
    conc = (per_repo.groupby("agent")
            .apply(lambda g: g.n.max() / g.n.sum(), include_groups=False)
            .rename("top_repo_share"))
    summary = summary.join(macro.round(3)).join(conc.round(3))

    print("\n=== OCP summary per agent (PR-level deltas) ===")
    print(summary.to_string())

    disp = results[results.row_type == "dispatch"].copy()
    if len(disp):
        type_based = disp["kind"].isin(["isinstance", "type_eq", "match_type"])
        print("\n=== Dispatch inventory (ALL in-scope chains, head-state) ===")
        print(disp.groupby("kind").agg(
            n=("subject", "size"),
            smells=("smell", "sum"),
            median_branches=("n_branches", "median"),
            p90_branches=("n_branches", lambda s: s.quantile(0.9)),
            p95_branches=("n_branches", lambda s: s.quantile(0.95)),
            max_branches=("n_branches", "max"),
        ).round(1).to_string())
        print("\nCALIBRATION (set OCP_THRESHOLDS from these BEFORE labeling):")
        for label, mask in [("type-based (isinstance/type_eq/match_type)",
                             type_based),
                            ("value-based (value_switch/match_value)",
                             ~type_based)]:
            b = disp.loc[mask, "n_branches"]
            if len(b):
                qs = b.quantile([0.5, 0.9, 0.95]).round(1).tolist()
                print(f"  {label}: n={len(b)}, "
                      f"p50={qs[0]} p90={qs[1]} p95={qs[2]} max={b.max()}")

    summary.to_csv(C.REPORT_DIR / "ocp_summary.csv")
    ok.drop(columns=["ocp_risk_reasons"]).to_csv(
        C.REPORT_DIR / "ocp_by_pr.csv", index=False)
    if len(disp):
        disp.to_csv(C.REPORT_DIR / "ocp_dispatch.csv", index=False)
    print(f"\n[ocp-report] wrote {C.REPORT_DIR / 'ocp_summary.csv'}")
    print(f"[ocp-report] wrote {C.REPORT_DIR / 'ocp_by_pr.csv'}")
    if len(disp):
        print(f"[ocp-report] wrote {C.REPORT_DIR / 'ocp_dispatch.csv'}")


if __name__ == "__main__":
    main()
