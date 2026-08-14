"""
Step 5-LSP-TS - LSP aggregates for the TypeScript corpus.

Mirrors report_lsp.py: PR-level delta rates (pooled AND macro per-repo) plus the
head-state override inventory and a breakdown of which signals fire.

Counts are printed BESIDE every rate (audit 2026-07-28, finding 7).

Outputs: 04_report/lsp_ts_summary.csv, lsp_ts_by_pr.csv, lsp_ts_overrides.csv

Run:  python vibebench/04_report/report_lsp_ts.py
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402


def main() -> None:
    df = pd.read_parquet(C.LSP_RESULTS_TS)
    prs = df[df.row_type == "pr"].copy()
    ovr = df[df.row_type == "override"].copy()
    ok = prs[prs.status == "ok"].copy()

    print(f"=== LSP-TS: {len(prs)} PRs ({len(ok)} analyzable), "
          f"{len(ovr)} in-scope override pairs ===\n")
    print("PR status:")
    print(prs["status"].value_counts().to_string())

    flagged = ok["lsp_risk"].notna().any()
    rows = []
    for agent, g in ok.groupby("agent"):
        o = ovr[ovr.agent == agent]
        share = (g.groupby("full_name").size().max() / len(g)) if len(g) else float("nan")
        rows.append({
            "agent": agent,
            "prs": len(g),
            "repos": g.full_name.nunique(),
            "n_flagged": int(g["lsp_risk"].fillna(False).sum()) if flagged else None,
            "lsp_risk_rate": g["lsp_risk"].mean() if flagged else None,
            "macro_lsp_risk_rate": (g.groupby("full_name")["lsp_risk"].mean().mean()
                                    if flagged else None),
            "overrides_in_scope": len(o),
            "violations_in_scope": int(o["violation"].fillna(False).sum()) if flagged else None,
            "top_repo_share": share,
        })
    summary = pd.DataFrame(rows).sort_values(
        "macro_lsp_risk_rate" if flagged else "prs", ascending=False)
    print("\n=== Per-agent summary (macro = repo-mix-controlled; lead with it) ===")
    print("NOTE: read n_flagged first -- these rates rest on single-digit counts.")
    print(summary.round(4).to_string(index=False))

    if len(ovr) and flagged:
        viol = ovr[ovr["violation"].fillna(False)]
        print(f"\n=== Head-state violations: {len(viol)} of {len(ovr)} pairs ===")
        sig = Counter()
        for s in viol["signals"].fillna(""):
            for part in str(s).split(";"):
                if part:
                    sig[part] += 1
        for name, n in sig.most_common():
            print(f"  {name:<28} {n}")
        print("\nViolating pairs by agent:")
        print(viol.groupby("agent").size().to_string())
        print("\nExamples:")
        print(viol.head(12)[["agent", "qualname", "method", "base_id", "signals"]]
              .to_string(index=False))

    out = C.REPORT_DIR
    summary.to_csv(out / "lsp_ts_summary.csv", index=False)
    prs.to_csv(out / "lsp_ts_by_pr.csv", index=False)
    ovr.to_csv(out / "lsp_ts_overrides.csv", index=False)
    print("\n[report-lsp-ts] wrote lsp_ts_summary.csv / lsp_ts_by_pr.csv / "
          "lsp_ts_overrides.csv")


if __name__ == "__main__":
    main()
