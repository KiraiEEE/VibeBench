"""
Step 5 - Aggregate + report SRP results per agent.

Reads corpus/srp_results.parquet (Step 3 output) and produces:
  - a console summary table
  - 04_report/srp_summary.csv (per-agent aggregate metrics)
  - 04_report/srp_by_class.csv (flat class-level table for further analysis / plotting)

Run:  python vibebench/04_report/report.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402


def main() -> None:
    C.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    results = pd.read_parquet(C.SRP_RESULTS)
    class_rows = results[results.class_name.notna()].copy()

    print(f"[report] total analyzed files: {results.file_path.nunique()} "
          f"across {results.pr_id.nunique()} PRs")
    print(f"[report] total classes: {len(class_rows)}")
    print(f"[report] parse errors: {(~results.parse_ok).sum()}")

    # --- AUDIT FIX 2026-07-28: separate test code from production code -------
    # Agents differ ~4x in how much test code they write, and test classes are
    # flagged at ~1/3 the production rate, so pooling them DILUTES heavy-test
    # agents and changes the ranking. Production code is now the headline;
    # test code is reported alongside for transparency. (See config.is_test_path.)
    class_rows["is_test"] = class_rows["file_path"].apply(
        lambda p: C.is_test_path(p, "py"))
    prod = class_rows[~class_rows.is_test]
    print(f"[report] production classes: {len(prod)}  "
          f"test classes: {int(class_rows.is_test.sum())} "
          f"({class_rows.is_test.mean()*100:.1f}%)")
    print("\n[report] test-code share and its effect, per agent:")
    eff = class_rows.groupby("agent").agg(
        pct_test=("is_test", "mean"), rate_all=("srp_risk", "mean"))
    eff["rate_production"] = prod.groupby("agent")["srp_risk"].mean()
    eff["rate_tests"] = (class_rows[class_rows.is_test]
                         .groupby("agent")["srp_risk"].mean())
    print(eff.round(4).to_string())

    summary = class_rows.groupby("agent").agg(
        n_prs=("pr_id", "nunique"),
        n_repos=("full_name", "nunique"),
        n_classes=("class_name", "size"),
        srp_risk_rate=("srp_risk", "mean"),
        srp_risk_strict_rate=("srp_risk_strict", "mean"),
        median_class_loc=("class_loc", "median"),
        median_wmc=("wmc", "median"),
        median_method_count=("method_count", "median"),
        median_lcom=("lcom", "median"),
        median_c3=("c3", "median"),
        mean_maintainability=("maintainability_index", "mean"),
    ).round(3).sort_values("srp_risk_rate", ascending=False)

    # --- Repo-mix controls -------------------------------------------------
    # The pooled risk rate conflates agent quality with WHICH repos an agent's
    # sample happens to come from (e.g. one repo contributing most classes).
    # Two controls:
    #   macro_risk_rate  -- average of per-repo risk rates (every repo weighs
    #                       the same, so a single giant repo can't dominate)
    #   top_repo_share   -- fraction of the agent's classes from its single
    #                       most-represented repo (concentration diagnostic)
    per_repo = (class_rows.groupby(["agent", "full_name"])
                .agg(n=("class_name", "size"), rate=("srp_risk", "mean"))
                .reset_index())
    macro = per_repo.groupby("agent")["rate"].mean().rename("macro_risk_rate")
    # Production-only twins of the two headline numbers (the confound-free view).
    prod_pooled = prod.groupby("agent")["srp_risk"].mean().rename("prod_risk_rate")
    prod_macro = (prod.groupby(["agent", "full_name"])["srp_risk"].mean()
                  .groupby("agent").mean().rename("prod_macro_risk_rate"))
    conc = (per_repo.groupby("agent")
            .apply(lambda g: g.n.max() / g.n.sum(), include_groups=False)
            .rename("top_repo_share"))
    summary = (summary.join(macro.round(3)).join(prod_pooled.round(3))
               .join(prod_macro.round(3)).join(conc.round(3)))

    print("\n=== SRP summary per agent (class-level) ===")
    print(summary.to_string())
    print("\n=== HEADLINE: production code only, repo-mix-controlled ===")
    print("(prod_macro_risk_rate is the number to lead with -- it removes both "
          "the repo-concentration and the test-writing confounds)")
    print(summary[["n_classes", "srp_risk_rate", "prod_risk_rate",
                   "macro_risk_rate", "prod_macro_risk_rate", "top_repo_share"]]
          .sort_values("prod_macro_risk_rate", ascending=False).to_string())

    # Within-repo comparison: only repos where >=2 agents authored classes.
    # This is the cleanest agent-vs-agent signal (same codebase, same
    # conventions), at the cost of a much smaller sample.
    multi = per_repo[per_repo.groupby("full_name")["agent"].transform("nunique") >= 2]
    if len(multi):
        shared = multi.pivot(index="full_name", columns="agent", values="rate").round(3)
        shared_n = multi.pivot(index="full_name", columns="agent", values="n")
        print("\n=== Within-repo SRP-risk rates (repos with >=2 agents; n in parens) ===")
        for repo in shared.index:
            cells = []
            for agent in shared.columns:
                r = shared.loc[repo, agent]
                if pd.notna(r):
                    cells.append(f"{agent}={r:.3f} (n={int(shared_n.loc[repo, agent])})")
            print(f"  {repo}: " + ", ".join(cells))
        shared.to_csv(C.REPORT_DIR / "srp_within_repo.csv")
    else:
        print("\n[report] no repos shared by >=2 agents in this sample")

    # Which rubric metric fires most often, per agent -- useful for threshold tuning.
    exploded = class_rows.explode("srp_risk_hits")
    hit_counts = (
        exploded[exploded.srp_risk_hits.notna()]
        .groupby(["agent", "srp_risk_hits"]).size().unstack(fill_value=0)
    )
    print("\n=== Rubric metric hit counts per agent ===")
    print(hit_counts.to_string())

    summary.to_csv(C.REPORT_DIR / "srp_summary.csv")
    class_rows.drop(columns=["srp_risk_hits"]).to_csv(
        C.REPORT_DIR / "srp_by_class.csv", index=False
    )
    print(f"\n[report] wrote {C.REPORT_DIR / 'srp_summary.csv'}")
    print(f"[report] wrote {C.REPORT_DIR / 'srp_by_class.csv'}")


if __name__ == "__main__":
    main()
