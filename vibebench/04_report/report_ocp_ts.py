"""
Step 5-OCP-TS - OCP aggregates for the TypeScript corpus.

Mirrors report_ocp.py: PR-level delta rates (pooled AND macro per-repo, with
top_repo_share so single-repo domination is visible), plus the head-state
dispatch inventory and the calibration distribution.

Outputs: 04_report/ocp_ts_summary.csv, ocp_ts_by_pr.csv, ocp_ts_dispatch.csv

Run:  python vibebench/04_report/report_ocp_ts.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402


def main() -> None:
    df = pd.read_parquet(C.OCP_RESULTS_TS)
    prs = df[df.row_type == "pr"].copy()
    disp = df[df.row_type == "dispatch"].copy()
    ok = prs[prs.status == "ok"].copy()

    print(f"=== OCP-TS: {len(prs)} PRs ({len(ok)} analyzable), "
          f"{len(disp)} in-scope dispatch chains ===\n")
    print("PR status:")
    print(prs["status"].value_counts().to_string())

    flagged = ok["ocp_risk"].notna().any()
    rows = []
    for agent, g in ok.groupby("agent"):
        d = disp[disp.agent == agent]
        share = (g.groupby("full_name").size().max() / len(g)) if len(g) else float("nan")
        rows.append({
            "agent": agent,
            "prs": len(g),
            "repos": g.full_name.nunique(),
            "ocp_risk_rate": g["ocp_risk"].mean() if flagged else None,
            "macro_ocp_risk_rate": (g.groupby("full_name")["ocp_risk"].mean().mean()
                                    if flagged else None),
            "new_dispatch_smell": (g["ocp_risk_reasons"].fillna("")
                                   .str.contains("new_dispatch_smell").mean()),
            "dispatch_widened": (g["ocp_risk_reasons"].fillna("")
                                 .str.contains("dispatch_widened").mean()),
            "chains_in_scope": len(d),
            "smells_in_scope": int(d["smell"].sum()) if flagged else None,
            "top_repo_share": share,
        })
    summary = pd.DataFrame(rows).sort_values(
        "macro_ocp_risk_rate" if flagged else "prs", ascending=False)
    print("\n=== Per-agent summary (macro = repo-mix-controlled; lead with it) ===")
    print(summary.round(4).to_string(index=False))

    print("\n=== Dispatch inventory by kind (head state) ===")
    if len(disp):
        agg = disp.groupby("kind")["n_branches"].agg(["size", "median", "max"])
        if flagged:
            agg["smells"] = disp.groupby("kind")["smell"].sum()
        print(agg.to_string())
        print(f"\nclosed_set chains: {int(disp.closed_set.sum())} of {len(disp)}"
              + (f"  (suppressed from smell: "
                 f"{int((disp.closed_set & ~disp.smell.fillna(False)).sum())})"
                 if flagged else ""))

    out = C.REPORT_DIR
    summary.to_csv(out / "ocp_ts_summary.csv", index=False)
    prs.to_csv(out / "ocp_ts_by_pr.csv", index=False)
    disp.to_csv(out / "ocp_ts_dispatch.csv", index=False)
    print("\n[report-ocp-ts] wrote ocp_ts_summary.csv / ocp_ts_by_pr.csv / "
          "ocp_ts_dispatch.csv")


if __name__ == "__main__":
    main()
