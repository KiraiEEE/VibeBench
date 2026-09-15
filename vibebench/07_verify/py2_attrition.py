"""
Phase 2 attrition for the Python V2 corpus, measured PER PULL REQUEST.

WHY THIS FILE EXISTS. The 20-PR smoke test reported "130 fetch_failed rows, all
on Claude_Code" and that was carried in RESUME_HERE.md as suspected agent-biased
attrition. It was a misreading: those 130 rows are 6 pull requests, and the count
was inflated because Claude_Code's PRs touch 15.6 files on average against
Copilot's 3.4. At PR level the asymmetry ran the OTHER way, and most of it was
the language filter working as intended rather than loss at all.

The lesson is the measurement, not the correction: a per-file-row count answers a
question about FILES, and every claim this study makes is about pull requests. So
attrition is reported per PR here, with the file-row view printed beside it
purely to show the gap between the two.

THE DROP THAT MATTERS is a PR that reaches analysis with nothing to analyse.
`no_source_files` is not that: it means the PR touched no .py file at all, which
is the corpus definition doing its job. `fetch_failed` and `diff_failed` ARE
that, and they are the ones to disclose if they land unevenly.

Never branch on an agent name anywhere in here. Which agent lacks file
attribution CHANGES between AIDev releases, so a hardcoded name is a bug waiting
for the next dataset.

Run:  python vibebench/07_verify/py2_attrition.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

# Statuses that mean "this PR carries no analysable Python", split by whether
# that is the corpus definition working or a genuine failure to retrieve.
OUT_OF_SCOPE = {"no_source_files"}
RETRIEVAL_LOSS = {"fetch_failed", "diff_failed", "file_missing"}


def main() -> None:
    gold = pd.read_parquet(C.GOLD_SET)
    if not C.CLONE_MANIFEST.exists():
        print("no clone manifest yet, phase 2 has not written anything")
        return
    man = pd.read_parquet(C.CLONE_MANIFEST)

    print(f"gold set      : {len(gold):,} PRs over {gold.full_name.nunique():,} repos")
    print(f"manifest      : {len(man):,} file rows over {man.pr_id.nunique():,} PRs")
    reached = man.pr_id.nunique()
    print(f"phase 2 reach : {reached:,} of {len(gold):,} PRs "
          f"({reached/len(gold):.1%})")
    if reached < len(gold):
        print("  NOT COMPLETE. The numbers below describe only what has been "
              "processed so far.")

    print("\n== file rows by status (the view that MISLED the smoke test) ==")
    print(pd.crosstab(man.agent, man.extract_status, margins=True).to_string())

    # PR level: a PR is usable if at least one of its files extracted.
    per_pr = (man.groupby(["pr_id", "agent"])
                 .agg(n_rows=("extract_status", "size"),
                      n_ok=("extract_status", lambda s: (s == "ok").sum()),
                      statuses=("extract_status",
                                lambda s: set(s[s != "ok"])))
                 .reset_index())
    per_pr["usable"] = per_pr.n_ok > 0

    def classify(r) -> str:
        if r.usable:
            return "usable"
        if r.statuses & RETRIEVAL_LOSS:
            return "retrieval_loss"
        if r.statuses & OUT_OF_SCOPE:
            return "out_of_scope"
        return "other"

    per_pr["outcome_class"] = per_pr.apply(classify, axis=1)

    print("\n== PER PULL REQUEST, which is what every claim is about ==")
    tab = pd.crosstab(per_pr.agent, per_pr.outcome_class, margins=True)
    print(tab.to_string())

    print("\n== rates per agent ==")
    rate = pd.crosstab(per_pr.agent, per_pr.outcome_class, normalize="index")
    print((rate * 100).round(1).to_string())

    print("\n== mean files per PR (why the two views disagree) ==")
    print(per_pr.groupby("agent").n_rows.mean().round(1).to_string())

    loss = per_pr[per_pr.outcome_class == "retrieval_loss"]
    print(f"\nRETRIEVAL LOSS: {len(loss)} PRs of {len(per_pr)} reached "
          f"({len(loss)/max(len(per_pr),1):.2%}).")
    if len(loss):
        print("  by agent:")
        print((loss.groupby("agent").size() /
               per_pr.groupby("agent").size()).dropna().mul(100).round(2)
              .to_string())
        print("  THIS is the number to disclose if it is uneven. "
              "`out_of_scope` is not loss, it is the .py filter.")
    else:
        print("  none so far.")


if __name__ == "__main__":
    main()
