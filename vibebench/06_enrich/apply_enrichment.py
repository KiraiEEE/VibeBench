"""
Step 6-ENRICH (part 2) - fold the GitHub API facts back into the gold set.

`fetch_pr_outcomes.py` does the network work and writes
`corpus/pr_outcomes_<lang>.parquet`. This script is pure local computation: it
rewrites `rejection_stratum` from the API's review evidence, adds
`outcome_final` and `closed_by_author`, and REPORTS THE PER-AGENT COVERAGE
BEFORE AND AFTER so the gap closure is measured rather than asserted.

THE RULE IT ENFORCES (REDESIGN_V2.md 7). A gap that is uniform across agents
costs recall. A gap that is agent-skewed silently rewrites the ranking, which is
the actual result. So every backfilled column is printed per agent twice, and a
column that still shows a >15pp per-cell spread after the backfill is reported
as NOT CLOSED, which means it must be dropped from the analysis rather than
reported biased.

TWO OUTCOME COLUMNS, AND THEY ARE NOT REDUNDANT:
  `outcome`       the dataset's outcome at its 2026-03-31 cutoff. This DEFINES
                  THE SAMPLING FRAME and must never be rewritten, or the corpus
                  stops being reproducible from the dataset.
  `outcome_final` the PR's state when the API was queried. A PR that was `open`
                  at the cutoff has usually resolved since; a `rejected` PR can
                  be reopened and merged. Analysis of the merged-vs-not-merged
                  contrast uses this one, because it is the true label.
Drift between them is printed. If it is large inside the balanced cells, the
report must say which column each number was computed on.

Run:  python vibebench/06_enrich/apply_enrichment.py --lang java
      python vibebench/06_enrich/apply_enrichment.py --lang java --dry-run
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
import safeio  # noqa: E402  -- crash-safe writes (power/network cuts)

GOLD = {"java": C.GOLD_SET_JAVA, "py": C.GOLD_SET, "ts": C.GOLD_SET_TS}


def coverage(df: pd.DataFrame, col: str, label: str,
             applicable: pd.Series | None = None) -> float:
    """Per (agent x outcome) coverage of `col`; returns the spread in pp.

    `applicable` marks the rows where the column CAN exist. Without it the check
    reports a false gap and tells the reader to drop a good column: an OPEN PR
    has no close event, so `api_closed_by` is legitimately null for ~55% of the
    open cells, and pooling those with the closed cells produced a 59pp
    "spread" on a column that is actually 99.4-100% complete wherever it
    applies. A coverage check that cannot tell "missing" from "not applicable"
    is worse than no check, because it manufactures a bias finding.
    """
    d = df if applicable is None else df[applicable]
    if not len(d):
        print("\n%s\n  (no applicable rows)" % label)
        return 0.0
    t = (d.assign(_have=d[col].notna())
          .groupby(["agent", "outcome"])["_have"].agg(["size", "sum"]))
    t["pct"] = (t["sum"] / t["size"] * 100).round(1)
    print("\n%s" % label)
    print(t.rename(columns={"size": "prs", "sum": "known"}).to_string())
    spread = float(t["pct"].max() - t["pct"].min())
    print("  per-cell spread: %.1f pp   (over APPLICABLE rows only: %d of %d)"
          % (spread, len(d), len(df)))
    return spread


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=sorted(GOLD), default="java")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    gold_path = GOLD[args.lang]
    enr_path = C.CORPUS_DIR / ("pr_outcomes_%s.parquet" % args.lang)
    assert enr_path.exists(), "run fetch_pr_outcomes.py first: %s missing" % enr_path

    gold = pd.read_parquet(gold_path)
    enr = pd.read_parquet(enr_path)
    assert len(gold) and len(enr), "empty input"
    print("[apply] gold %d PRs, enrichment %d PRs" % (len(gold), len(enr)))

    cols = ["pr_id", "api_available", "api_state", "api_merged", "api_closed_at",
            "api_author", "api_n_reviews", "api_review_states",
            "api_has_changes_requested", "api_has_approval",
            "api_closed_by", "api_closed_by_author",
            "api_additions", "api_deletions", "api_changed_files",
            "api_n_comments"]
    cols = [c for c in cols if c in enr.columns]
    m = gold.merge(enr[cols].drop_duplicates("pr_id"), on="pr_id", how="left")
    assert len(m) == len(gold), "enrichment join changed the row count"
    hit = m["api_available"].fillna(False)
    print("[apply] API facts attached to %d of %d PRs (%.1f%%)"
          % (hit.sum(), len(m), 100 * hit.mean()))

    # ---------------- BEFORE: what the dataset alone could tell us
    m["_ds_review"] = np.where(m["has_review"], True, np.nan)
    coverage(m, "_ds_review",
             "BEFORE -- review evidence from the DATASET only "
             "(pr_reviews, 6-11% coverage on rejected PRs):")
    print("\nBEFORE -- rejection strata from the dataset:")
    print(pd.crosstab(m["agent"], m["rejection_stratum"]).to_string())

    # ---------------- AFTER: the API's answer
    # Only CLOSED PRs can have a close actor, so an open PR's null is "not
    # applicable", not "missing". Scoping the check to closed PRs is what makes
    # it a bias test rather than a restatement of which PRs are still open.
    is_closed_now = ~m["api_state"].fillna("").eq("OPEN")
    spread = coverage(m, "api_closed_by",
                      "AFTER -- close-actor coverage from the GitHub API, over "
                      "CLOSED PRs only (the dataset's own version was "
                      "agent-biased 20x: Claude_Code 2.8% vs Codex 64.4%):",
                      applicable=is_closed_now)
    if spread > 15:
        print("  !! NOT CLOSED. A >15pp per-cell spread means `closed_by` still")
        print("     carries an agent bias and must NOT be used as an analysis")
        print("     variable. Report it as unavailable instead of biased.")
    else:
        print("  parity reached -- `closed_by` is usable as an analysis variable.")

    # `outcome_final`: the PR's state now, which is the true label. Falls back to
    # the dataset outcome for PRs the API could not resolve (deleted or private
    # repos), and those are counted so the fallback is never invisible.
    api_state = m["api_state"].fillna("")
    m["outcome_final"] = np.where(
        m["api_merged"].fillna(False), "merged",
        np.where(api_state.eq("CLOSED"), "rejected",
                 np.where(api_state.eq("OPEN"), "open", m["outcome"])))
    n_fallback = int((~hit).sum())
    print("\n`outcome_final` fell back to the dataset outcome for %d PRs "
          "(API could not resolve them)" % n_fallback)
    print("\nOUTCOME DRIFT, dataset (rows) vs API now (columns). The dataset's "
          "cutoff is %s:" % C.DATASET_CUTOFF)
    print(pd.crosstab(m["outcome"], m["outcome_final"]).to_string())
    bal = m[m["outcome"].isin(C.V2_OUTCOMES)]
    moved = int((bal["outcome"] != bal["outcome_final"]).sum())
    print("  inside the BALANCED cells, %d of %d PRs changed label (%.1f%%)"
          % (moved, len(bal), 100 * moved / max(len(bal), 1)))
    if moved / max(len(bal), 1) > 0.05:
        print("  !! >5%% drift. Say explicitly which column every rate uses.")

    # `rejection_stratum`, recomputed from the API's review evidence where it
    # exists. Self-reviews were already excluded upstream: an agent reviewing its
    # own PR is not external quality evidence, and counting it would inflate the
    # reviewed strata for whichever agent does it most.
    has_api = hit & m["api_n_reviews"].notna()
    api_stratum = np.where(
        m["api_has_changes_requested"].fillna(False), "changes_requested",
        np.where(m["api_n_reviews"].fillna(0) > 0,
                 "reviewed_then_closed", "closed_unreviewed"))
    not_merged_final = ~m["outcome_final"].eq("merged")
    m["rejection_stratum_api"] = np.where(
        ~not_merged_final, m["outcome_final"],
        np.where(has_api, api_stratum, "unknown_no_api"))
    m["stratum_source"] = np.where(~not_merged_final, "n/a",
                                   np.where(has_api, "github_api", "dataset_only"))

    print("\nAFTER -- rejection strata from the API "
          "(not-merged by `outcome_final`):")
    nm = m[not_merged_final]
    if len(nm):
        print(pd.crosstab(nm["agent"], nm["rejection_stratum_api"]).to_string())
        print("\n  stratum reclassification, dataset (rows) vs API (columns):")
        print(pd.crosstab(nm["rejection_stratum"],
                          nm["rejection_stratum_api"]).to_string())
        cr = nm["rejection_stratum_api"].eq("changes_requested")
        print("\n  changes_requested: %d PRs (dataset found %d). THIS IS THE ONLY "
              "STRATUM" % (int(cr.sum()),
                           int(nm["rejection_stratum"].eq("changes_requested").sum())))
        print("  that carries an explicit maintainer objection. Print its COUNT "
              "beside every rate computed on it.")
        if int(cr.sum()) < 30:
            print("  !! under 30 PRs. Report it as descriptive; a violation-rate")
            print("     comparison on this stratum has no power.")

        # AUTHOR-CLOSED IS **NOT** AN ABANDONMENT MEASURE, AND USING IT AS ONE
        # WOULD BE A BADLY WRONG HEADLINE. Measured 2026-08-25 it reads
        # Claude_Code 78.2%, OpenAI_Codex 80.6%, Copilot 0.5% -- a 160x spread
        # that looks like a spectacular behavioural finding and is an artefact
        # of WHO OWNS THE PR ACCOUNT:
        #     Copilot      941 of 964 PRs authored by `copilot-swe-agent`, a bot
        #     Claude_Code  426 distinct human authors, the developer's own account
        #     OpenAI_Codex 167 distinct human authors, likewise
        # So for Copilot a human closing the PR can never be "the author", while
        # for the other two the developer closing their own PR always is. The
        # column measures each agent's PR-AUTHORSHIP MODEL, not whether anyone
        # gave up. It is printed as a corpus property and is NOT available as an
        # analysis variable, per the V2 rule: where a gap cannot be closed
        # evenly, drop the column rather than report a biased one.
        if "api_closed_by_author" in nm.columns:
            ac = nm.groupby("agent")["api_closed_by_author"].agg(
                ["size", "sum", "mean"])
            print("\n  self-closed share among not-merged PRs -- A CORPUS "
                  "PROPERTY, NOT A RESULT:")
            print(ac.rename(columns={"size": "prs", "sum": "self_closed",
                                     "mean": "share"}).to_string())
            print("  ^ NOT comparable across agents and NOT an abandonment "
                  "measure. Copilot's PRs are")
            print("    authored by a bot account and every other agent's by the "
                  "developer's own account,")
            print("    so this column tracks account ownership. Do not report it "
                  "as behaviour.")
            print("  PR-author identity per agent (the reason):")
            for a_, d_ in m.groupby("agent"):
                top = d_["api_author"].value_counts()
                if not len(top):
                    continue
                print("    %-13s %d distinct authors, top: %s (%d of %d)"
                      % (a_, d_["api_author"].nunique(), top.index[0],
                         top.iloc[0], len(d_)))

        # THE COMPARABLE REPLACEMENT: was the PR closed by an automation? A bot
        # or policy service closing a PR is a bulk/stale closure by definition
        # and carries no quality judgement, and "is the closer a bot" does not
        # depend on who authored the PR, so it IS comparable across agents.
        bot = nm["api_closed_by"].fillna("").str.lower().str.contains(
            r"bot$|\[bot\]|github-actions|policy-service|stale", regex=True)
        m.loc[nm.index, "closed_by_automation"] = bot
        ba = nm.assign(_b=bot).groupby("agent")["_b"].agg(["size", "sum", "mean"])
        print("\n  closed by an AUTOMATION (bot / policy service / stale bot) -- "
              "comparable across")
        print("  agents because it does not depend on PR-author identity. These "
              "closures carry")
        print("  no quality judgement at all:")
        print(ba.rename(columns={"size": "prs", "sum": "bot_closed",
                                 "mean": "share"}).to_string())

    if args.dry_run:
        print("\n[apply] --dry-run: gold set NOT modified")
        return

    backup = gold_path.with_suffix(".pre_enrichment.parquet")
    if not backup.exists():
        shutil.copy2(gold_path, backup)
        print("\n[apply] pre-enrichment gold set copied to %s" % backup.name)
    safeio.to_parquet(m.drop(columns=["_ds_review"]), gold_path, index=False)
    print("[apply] gold set rewritten with outcome_final, rejection_stratum_api, "
          "stratum_source and the API columns (%d rows)" % len(m))


if __name__ == "__main__":
    main()
