"""
Step 5-ISP-TS - ISP aggregates for the TypeScript corpus.

Mirrors report_isp.py: PR-level delta rates (pooled AND macro per-repo, with
top_repo_share so single-repo domination is visible), plus the head-state
contract inventory.

Counts are printed BESIDE every rate. The per-agent ISP signal is a handful of
PRs; a rate alone invites a leaderboard the data cannot support (audit
2026-07-28, finding 7).

Outputs: 04_report/isp_ts_summary.csv, isp_ts_by_pr.csv, isp_ts_interfaces.csv

Run:  python vibebench/04_report/report_isp_ts.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402


def main() -> None:
    df = pd.read_parquet(C.ISP_RESULTS_TS)
    prs = df[df.row_type == "pr"].copy()
    ifs = df[df.row_type == "interface"].copy()
    ims = df[df.row_type == "implementer"].copy()
    ok = prs[prs.status == "ok"].copy()

    print(f"=== ISP-TS: {len(prs)} PRs ({len(ok)} analyzable), "
          f"{len(ifs)} in-scope contracts, {len(ims)} implementer pairs ===\n")
    print("PR status:")
    print(prs["status"].value_counts().to_string())

    flagged = ok["isp_risk"].notna().any()
    rows = []
    for agent, g in ok.groupby("agent"):
        i = ifs[ifs.agent == agent]
        share = (g.groupby("full_name").size().max() / len(g)) if len(g) else float("nan")
        rows.append({
            "agent": agent,
            "prs": len(g),
            "repos": g.full_name.nunique(),
            "n_flagged": int(g["isp_risk"].fillna(False).sum()) if flagged else None,
            "isp_risk_rate": g["isp_risk"].mean() if flagged else None,
            "macro_isp_risk_rate": (g.groupby("full_name")["isp_risk"].mean().mean()
                                    if flagged else None),
            "new_flagged_iface": (g["isp_risk_reasons"].fillna("")
                                  .str.contains("new_flagged_iface").sum()),
            "widened_fat_iface": (g["isp_risk_reasons"].fillna("")
                                  .str.contains("widened_fat_iface").sum()),
            "new_forced_stub": (g["isp_risk_reasons"].fillna("")
                                .str.contains("new_forced_stub").sum()),
            "contracts_in_scope": len(i),
            "fat_contracts": int(i["flagged"].fillna(False).sum()) if flagged else None,
            "top_repo_share": share,
        })
    summary = pd.DataFrame(rows).sort_values(
        "macro_isp_risk_rate" if flagged else "prs", ascending=False)
    print("\n=== Per-agent summary (macro = repo-mix-controlled; lead with it) ===")
    print("NOTE: read n_flagged first -- these rates rest on single-digit counts.")
    print(summary.round(4).to_string(index=False))

    if len(ifs):
        print("\n=== Contract inventory by kind (head state) ===")
        agg = ifs.groupby("kind")["n_members"].agg(["size", "median", "max"])
        if flagged:
            agg["fat"] = ifs.groupby("kind")["flagged"].sum()
        print(agg.to_string())
        w = ifs["n_members"]
        print("width distribution: " + " ".join(
            f"p{int(p * 100)}={w.quantile(p):.0f}" for p in (0.5, 0.75, 0.9, 0.95)))
        print("\nWidest contracts at head:")
        top = (ifs.sort_values("n_members", ascending=False)
               .drop_duplicates("iface_id")
               .head(12)[["agent", "qualname", "kind", "n_members", "module"]])
        print(top.to_string(index=False))

    if len(ims) and flagged:
        print(f"\nforced-stub implementers: {int(ims.forced_stub.fillna(False).sum())} "
              f"of {len(ims)}")

    out = C.REPORT_DIR
    summary.to_csv(out / "isp_ts_summary.csv", index=False)
    prs.to_csv(out / "isp_ts_by_pr.csv", index=False)
    ifs.to_csv(out / "isp_ts_interfaces.csv", index=False)
    print("\n[report-isp-ts] wrote isp_ts_summary.csv / isp_ts_by_pr.csv / "
          "isp_ts_interfaces.csv")


if __name__ == "__main__":
    main()
