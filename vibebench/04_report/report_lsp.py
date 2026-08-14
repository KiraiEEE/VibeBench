"""
Step 5-LSP - Aggregate + report LSP delta results per agent.

Reads corpus/lsp_results.parquet (Step 3-LSP output) and produces:
  - a console summary (pooled + per-repo macro-averaged, same repo-mix controls
    as the SRP/DIP/ISP/OCP reports)
  - 04_report/lsp_summary.csv    (per-agent PR-level delta aggregates)
  - 04_report/lsp_by_pr.csv      (one row per analyzable PR)
  - 04_report/lsp_overrides.csv  (head-state override pairs, context + inventory)

All PR-level LSP metrics are BEFORE/AFTER DELTAS around the merge commit -- an
agent is only debited for override incompatibilities it created, never for the
repo's pre-existing bad overrides. The override inventory also prints the
per-signal frequency, which is the LSP analogue of OCP's branch-count
calibration: LSP signals are categorical (each is individually a recognized
substitutability break), so there is no continuous threshold to set -- the
inventory instead shows how often each signal occurs, and the label loop
measures its precision.

Run:  python vibebench/04_report/report_lsp.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

_SIGNALS = [
    "refused_bequest", "param_required_added", "param_removed",
    "param_type_narrowed", "return_type_widened",
]


def main() -> None:
    C.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    results = pd.read_parquet(C.LSP_RESULTS)
    pr_rows = results[results.row_type == "pr"].copy()

    print("[lsp-report] PR status counts:")
    print(pr_rows["status"].value_counts().to_string())

    ok = pr_rows[pr_rows.status == "ok"].copy()
    if ok.empty:
        print("[lsp-report] no analyzable PRs yet")
        return

    def reason_rate(reason: str):
        return ("lsp_risk_reasons",
                lambda s: s.apply(lambda r: reason in list(r)).mean())

    summary = ok.groupby("agent").agg(
        n_prs=("pr_id", "size"),
        n_repos=("full_name", "nunique"),
        lsp_risk_rate=("lsp_risk", "mean"),
        refused_bequest_rate=reason_rate("new_refused_bequest"),
        incompatible_sig_rate=reason_rate("new_incompatible_signature"),
        type_variance_rate=reason_rate("new_type_variance"),
        overrides_in_scope=("n_overrides_in_scope", "sum"),
        violations_in_scope=("n_violations_in_scope", "sum"),
    ).round(3).sort_values("lsp_risk_rate", ascending=False)

    # Repo-mix control: every repo weighs the same (see SRP report rationale).
    per_repo = (ok.groupby(["agent", "full_name"])
                .agg(n=("pr_id", "size"), rate=("lsp_risk", "mean"))
                .reset_index())
    macro = per_repo.groupby("agent")["rate"].mean().rename("macro_lsp_risk_rate")
    conc = (per_repo.groupby("agent")
            .apply(lambda g: g.n.max() / g.n.sum(), include_groups=False)
            .rename("top_repo_share"))
    summary = summary.join(macro.round(3)).join(conc.round(3))

    print("\n=== LSP summary per agent (PR-level deltas) ===")
    print(summary.to_string())

    ov = results[results.row_type == "override"].copy()
    if len(ov):
        ov["signal_list"] = ov["signals"].fillna("").apply(
            lambda s: [x for x in s.split(";") if x])
        print("\n=== Override inventory (ALL in-scope pairs, head-state) ===")
        print(f"total in-scope override pairs: {len(ov)}")
        print(f"violating pairs: {int(ov['violation'].sum())}")
        print("\nper-signal frequency (a pair may carry >1 signal):")
        for sig in _SIGNALS:
            n = int(ov["signal_list"].apply(lambda L: sig in L).sum())
            print(f"  {sig:<22} {n}")
        print("\nviolating pairs by agent:")
        print(ov[ov.violation].groupby("agent").size().to_string())

    summary.to_csv(C.REPORT_DIR / "lsp_summary.csv")
    ok.drop(columns=["lsp_risk_reasons"]).to_csv(
        C.REPORT_DIR / "lsp_by_pr.csv", index=False)
    if len(ov):
        ov.drop(columns=["signal_list"]).to_csv(
            C.REPORT_DIR / "lsp_overrides.csv", index=False)
    print(f"\n[lsp-report] wrote {C.REPORT_DIR / 'lsp_summary.csv'}")
    print(f"[lsp-report] wrote {C.REPORT_DIR / 'lsp_by_pr.csv'}")
    if len(ov):
        print(f"[lsp-report] wrote {C.REPORT_DIR / 'lsp_overrides.csv'}")


if __name__ == "__main__":
    main()
