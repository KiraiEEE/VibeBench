"""
Step 1 - Curate the gold set.  V2, OUTCOME-BALANCED (2026-08-25).

Reads the AIDev parquet tables and produces `corpus/gold_set*.parquet`: one row
per in-scope PR, with repo metadata, the AI-touched source files, a before/after
resolution hint for the delta stage, and -- new in V2 -- the PR's OUTCOME and,
when it was not merged, its REJECTION STRATUM.

WHY V2 EXISTS. V1 curated merged PRs only. Merged PRs passed human review, so
the corpus was filtered by the very quality signal the study measures. That is
survivorship bias. V2 draws balanced (agent x outcome) cells so every violation
rate can be reported merged-only, not-merged-only and pooled.

Selection policy lives in config.py:
  - language in the profile's TARGET_LANGUAGES (or by file extension, see below)
  - stars >= MIN_STARS
  - permissive license (compared case-insensitively -- the dataset stores
    "MIT"/"Apache-2.0" and PERMISSIVE_LICENSES is lowercase; a direct isin
    returns ZERO rows, silently)
  - agent in the profile's 3-agent set
  - outcome in the requested set (default config.V2_OUTCOMES)
  - balanced per-(agent, outcome) sampling at CELL_PR_CAP

THE REJECTION CONFOUND, stated here because curation is where it is created.
"Rejected" does NOT mean "rejected for quality". Measured on this eligible pool,
a rejected PR is HALF as likely to carry any recorded review as a merged one
(Java 10.8% vs 25.3%; Python 6.2% vs 12.1%). Most were closed with no review:
superseded, stale, duplicate, abandoned or bulk-closed. `rejection_stratum`
carries that distinction into every downstream parquet so the report can split
on it instead of publishing a bare merged-vs-rejected rate.

Sticky sampling: PRs already in a previous gold set are kept (when still
eligible) and only topped up, so earlier clone caches and label samples stay
valid across re-curation.

Run:  python vibebench/01_curate/curate.py --lang java              # V2 default
      python vibebench/01_curate/curate.py --lang java --dry-run    # pool only
      python vibebench/01_curate/curate.py --lang py --outcomes merged
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
import safeio  # noqa: E402  -- crash-safe writes (power/network cuts)


# Language profiles: everything language-specific in one place. The Python
# profile reproduces the original behaviour byte-for-byte; the TS profile adds
# the .d.ts exclusion (endswith(".ts") would otherwise match declaration files,
# which carry no implementation) and generated/vendored path exclusions.
LANG_PROFILES = {
    "py": {
        "languages": C.TARGET_LANGUAGES,
        "extensions": C.SOURCE_EXTENSIONS,
        "excluded_parts": (),
        "gold_set": C.GOLD_SET,
        "clone_manifest": C.CLONE_MANIFEST,
        # Added 2026-08-17: Python is now restricted to the same 3 agents as
        # Java. Without this the run silently included all 6 agents in the
        # dataset (including the unvalidated Google_Jules).
        "only_agents": C.PY_ONLY_AGENTS,
        # Python's eligible pool is an order of magnitude larger than Java's
        # (smallest cell 3,716 vs 315), so it gets its own, higher cell cap.
        # See the CELL_PR_CAP block in config.py for why 750 and not the ceiling.
        "cell_cap": C.CELL_PR_CAP_PY,
    },
    "ts": {
        "languages": C.TS_TARGET_LANGUAGES,
        "extensions": C.TS_SOURCE_EXTENSIONS,
        "excluded_parts": C.TS_EXCLUDED_PATH_PARTS,
        "gold_set": C.GOLD_SET_TS,
    },
    # Java: see the JAVA_* block in config.py. Since 2026-08-17 this is a
    # 3-agent study like Python (was single-agent under AIDev-full), so it uses
    # the SAME cell cap; `per_repo_cap` is None and within-agent repo
    # concentration is handled by macro-per-repo reporting, not by sampling.
    # `filter_by_file` scopes by .java extension rather than the repo's headline
    # language -- see the language gate in attach_touched_files for how that
    # interacts with git_diff rows.
    "java": {
        "languages": C.JAVA_TARGET_LANGUAGES,
        "extensions": C.JAVA_SOURCE_EXTENSIONS,
        "excluded_parts": C.JAVA_EXCLUDED_PATH_PARTS,
        "gold_set": C.GOLD_SET_JAVA,
        "clone_manifest": C.CLONE_MANIFEST_JAVA,
        "only_agents": C.JAVA_ONLY_AGENTS,
        "per_repo_cap": C.JAVA_PER_REPO_PR_CAP,
        "filter_by_file": C.JAVA_FILTER_BY_FILE,
        "repo_stratified": C.JAVA_REPO_STRATIFIED_DRAW,
    },
}


def is_source_file(path: str, profile: dict) -> bool:
    """True if `path` is an in-scope source file for this language profile."""
    f = path.lower()
    if f.endswith(".d.ts"):
        return False
    if any(part in f for part in profile["excluded_parts"]):
        return False
    return any(f.endswith(ext) for ext in profile["extensions"])


def load_candidates(profile: dict, outcomes: list[str]) -> pd.DataFrame:
    """PRs joined to repo metadata, filtered by outcome/language/stars/license.

    THREE DATASET TRAPS ARE HANDLED HERE, each of which returned a silently
    WRONG result on its first attempt (see REDESIGN_V2.md 2.10):

    1. TABLE TRAP. C.PR_TABLE / C.REPO_TABLE, never a hardcoded filename: in
       AIDev-7.6M the plain `pull_request.parquet` / `repository.parquet` are
       only the >100-star AIDev-pop subset (361,296 of 7,685,281 PRs).
    2. LICENSE CASING. The dataset stores `MIT` / `Apache-2.0`;
       PERMISSIVE_LICENSES is lowercase. A direct `.isin` matches NOTHING and
       raises no error, so the fold to lower case is mandatory.
    3. JOIN DTYPE. `all_repository.id` is int64 but `all_pull_request.repo_id`
       is float64. A naive merge yields an EMPTY frame with no error, so one
       side is cast and the row count is asserted afterwards.
    """
    repo = pd.read_parquet(
        C.DATA_DIR / C.REPO_TABLE,
        columns=["id", "full_name", "url", "language", "stars", "license"],
    )
    pr = pd.read_parquet(
        C.DATA_DIR / C.PR_TABLE,
        columns=["id", "number", "repo_id", "repo_url", "agent", "user", "state",
                 "created_at", "closed_at", "merged_at", "html_url"],
    )
    assert len(pr) and len(repo), "empty master table -- wrong DATA_DIR?"

    # OUTCOME, vectorised. A row-wise .apply over 7.6M rows does not finish in
    # reasonable time; np.where does it in seconds.
    pr["outcome"] = np.where(
        pr["merged_at"].notna(), "merged",
        np.where(pr["state"].eq("closed"), "rejected", "open"))
    pr = pr[pr["outcome"].isin(outcomes)].copy()
    assert len(pr), "no PRs with outcome in %s" % outcomes

    pr["repo_id"] = pr["repo_id"].astype("Int64")
    repo["id"] = repo["id"].astype("Int64")
    merged = pr.merge(
        repo, left_on="repo_id", right_on="id", suffixes=("_pr", "_repo")
    )
    assert len(merged), "PR<->repo join produced zero rows (dtype mismatch?)"

    lic = merged["license"].fillna("").str.lower()
    mask = (merged["stars"] >= C.MIN_STARS) & lic.isin(C.PERMISSIVE_LICENSES)
    # `filter_by_file` profiles skip the repo's headline-language filter: the
    # unit of analysis is the SOURCE FILE, and a repo whose primary language is
    # Kotlin or Go can still contain real Java. attach_touched_files() then does
    # the real filtering by extension, which is the more faithful test.
    if not profile.get("filter_by_file"):
        mask &= merged["language"].isin(profile["languages"])
    out = merged[mask].copy()
    assert len(out), "zero candidates after stars/license/language filter"
    return out


def attach_rejection_stratum(df: pd.DataFrame) -> pd.DataFrame:
    """Stratify NOT-MERGED PRs by how much review evidence exists.

    THE SINGLE BIGGEST JURY RISK IN V2 is publishing a bare merged-vs-rejected
    violation rate as if rejection meant a quality refusal. It usually does not:
    on this eligible pool a rejected PR is half as likely to carry any recorded
    review as a merged one, so most closures are superseded / stale / duplicate /
    abandoned / bulk-closed rather than judged and refused.

    Strata (config.V2_REJECTION_STRATA), strongest quality signal first:
      changes_requested    a CHANGES_REQUESTED review exists
      reviewed_then_closed some review exists, PR closed unmerged
      closed_unreviewed    no review recorded (the majority)
    Merged PRs get "merged"; open PRs get "open".

    `pr_reviews` covers only part of the corpus, so `closed_unreviewed` means
    "no review IN THE DATASET", not "no review on GitHub". The GitHub-API
    backfill (06_enrich/fetch_pr_outcomes.py) resolves that for the SELECTED
    corpus and rewrites this column; the coverage of both passes is printed per
    agent so a gap that hits one agent harder than another is visible rather
    than assumed away.
    """
    ids = set(df["id_pr"].tolist())
    rv = pd.read_parquet(C.DATA_DIR / "pr_reviews.parquet",
                         columns=["pr_id", "state"])
    rv = rv[rv["pr_id"].isin(ids)]
    has_cr = set(rv.loc[rv["state"] == "CHANGES_REQUESTED", "pr_id"])
    has_any = set(rv["pr_id"])

    out = df.copy()
    out["has_review"] = out["id_pr"].isin(has_any)
    out["has_changes_requested"] = out["id_pr"].isin(has_cr)
    out["rejection_stratum"] = np.where(
        out["outcome"] == "merged", "merged",
        np.where(out["outcome"] == "open", "open",
                 np.where(out["id_pr"].isin(has_cr), "changes_requested",
                          np.where(out["id_pr"].isin(has_any),
                                   "reviewed_then_closed", "closed_unreviewed"))))
    return out


def attach_touched_files(cand: pd.DataFrame, profile: dict) -> pd.DataFrame:
    """Attach the list of AI-touched source files per PR.

    TWO SOURCES, in preference order:
      * `files_source="dataset"` -- pr_commit_details lists the PR's files. Free
        and exact, so it is preferred whenever available. Rows are kept only if
        they touch >= 1 in-scope source file, which is a real pre-filter.
      * `files_source="git_diff"` -- no file list in the dataset. The PR is kept
        anyway with an empty list; Step 2 derives the real list from git and
        drops PRs that touch no source file.

    WHY EVERY AGENT NEEDS THE GIT_DIFF FALLBACK, AND WHY IT MUST NEVER BE
    SELECTED BY AGENT NAME. `pr_commit_details` covers only 197,330 of 7,685,281
    PRs (2.6%), and that coverage is AGENT-BIASED BY 25-30x. Measured on this
    eligible pool 2026-08-25:

        agent          Java coverage   Python coverage
        Claude_Code           2.5%            3.1%
        Copilot              73.7%           62.3%
        OpenAI_Codex         77.5%           52.4%

    In the older AIDev-full it was COPILOT that lacked attribution; in
    AIDev-7.6M it is CLAUDE_CODE. The gap MOVES between releases, so any code
    that hardcodes an agent is wrong on the next one -- curate.py carried exactly
    such a hardcode until 2026-08-19. Curating Java from the dataset's own file
    lists would yield Claude_Code 47 merged PRs against Copilot 1,145 and Codex
    1,211: a 40x artificial suppression of one agent caused purely by a metadata
    gap. The LEFT join below routes on DATA, never on identity.
    """
    pr_ids = set(cand["id_pr"].tolist())
    det = pd.read_parquet(
        C.DATA_DIR / "pr_commit_details.parquet",
        columns=["pr_id", "filename", "status"],
    )
    det = det[det["pr_id"].isin(pr_ids)]
    # Keep only added/modified/renamed source files (deletions carry no code to score).
    det = det[det["status"].isin({"added", "modified", "renamed", "changed"})]
    is_src = det["filename"].fillna("").apply(lambda f: is_source_file(f, profile))
    det = det[is_src]

    grouped = (
        det.groupby("pr_id")["filename"]
        .apply(lambda s: sorted(set(s)))
        .rename("touched_files")
    )

    out = cand.merge(grouped, left_on="id_pr", right_index=True, how="left")
    has_files = out["touched_files"].notna()
    out["files_source"] = has_files.map({True: "dataset", False: "git_diff"})
    out.loc[~has_files, "touched_files"] = pd.Series(
        [[] for _ in range((~has_files).sum())],
        index=out.index[~has_files], dtype=object,
    )
    out = out.reset_index(drop=True)

    # LANGUAGE GATE FOR git_diff ROWS (added 2026-08-19 -- fixes an 8x agent bias).
    #
    # A `filter_by_file` profile (Java) deliberately skips the repo's headline-
    # language filter in load_candidates, because the unit of analysis is the
    # SOURCE FILE and real Java lives in Kotlin/Scala/TS-primary repos too. That
    # is correct for `dataset` rows, which are then filtered by extension above.
    #
    # It was NOT correct for `git_diff` rows: they carry no file list at curation
    # time, so nothing constrained them to the language at all. Measured on
    # AIDev-7.6M, the Java candidate pool was 253,523 PRs of which only 6,239
    # (2.46%) were even in a Java repo, so the sampler drew ~1,046 essentially
    # random PRs per short agent. Projected corpus WITHOUT the gate:
    # OpenAI_Codex 500, Copilot 500, Claude_Code ~60 -- an 8x agent bias.
    if profile.get("filter_by_file") and "language" in out.columns:
        langs = set(profile["languages"])
        is_gd = out["files_source"] == "git_diff"
        keep = ~is_gd | out["language"].isin(langs)
        dropped = int((~keep).sum())
        by_agent = out.loc[~keep, "agent"].value_counts().to_dict() if dropped else {}
        out = out[keep].copy()
        print("[curate] language gate on git_diff rows: dropped %d PRs outside %s (%s)"
              % (dropped, sorted(langs), by_agent))

    # PER-AGENT COVERAGE, PRINTED BEFORE EVERY DRAW (V2 rule). A gap that is
    # uniform across agents costs recall; a gap that is agent-skewed silently
    # rewrites the ranking, which IS the result. This table is how the next
    # dataset release's gap gets caught on day one instead of after an analysis.
    cov = (out.assign(ds=out["files_source"] == "dataset")
              .groupby(["agent", "outcome"])["ds"].agg(["size", "sum"]))
    cov["dataset_pct"] = (cov["sum"] / cov["size"] * 100).round(1)
    print("[curate] pr_commit_details file-list coverage per agent x outcome "
          "(the 25x-bias check):")
    print(cov.rename(columns={"size": "eligible", "sum": "with_file_list"}).to_string())

    out["n_touched_files"] = out["touched_files"].apply(len)
    n_ds = int((out["files_source"] == "dataset").sum())
    print("[curate] file lists: %d from dataset, %d deferred to git_diff "
          "(Step 2 resolves + trims)" % (n_ds, len(out) - n_ds))
    return out


def attach_commit_shas(df: pd.DataFrame) -> pd.DataFrame:
    """Attach every PR's own commit SHAs (from pr_commits).

    Consumed by Step 2 and by `gitsnap.delta_pair` tier 2. Attached for ALL
    agents: restricting it to one agent made the no-merge-SHA loss recoverable
    for that agent only, which is an agent-biased data loss.
    """
    pc = pd.read_parquet(C.DATA_DIR / "pr_commits.parquet", columns=["pr_id", "sha"])
    pc = pc[pc["pr_id"].isin(set(df["id_pr"]))]
    grouped = pc.groupby("pr_id")["sha"].apply(list).rename("commit_shas")
    out = df.merge(grouped, left_on="id_pr", right_index=True, how="left")
    out["commit_shas"] = out["commit_shas"].where(out["commit_shas"].notna(), None)
    return out


def attach_merge_sha(df: pd.DataFrame) -> pd.DataFrame:
    """Attach the merge-commit SHA (fallback for clone) from pr_timeline.

    NOT-MERGED PRs HAVE NONE BY CONSTRUCTION, and that is fine: `merge_sha` is
    tier 1 of `gitsnap.delta_pair`, and tier 3 resolves any PR from
    `refs/pull/<N>/head` plus a merge-base against the default branch. GitHub
    serves that ref even after the source branch or fork is deleted (verified
    live on 4 of 4 rejected Java PRs from this dataset, 2026-08-25), so a
    rejected PR reconstructs exactly like a merged one.
    """
    pr_ids = set(df["id_pr"].tolist())
    tl = pd.read_parquet(
        C.DATA_DIR / "pr_timeline.parquet",
        columns=["pr_id", "event", "commit_id"],
    )
    tl = tl[(tl["pr_id"].isin(pr_ids)) & (tl["event"] == "merged")]
    tl = tl.drop_duplicates("pr_id").set_index("pr_id")["commit_id"].rename("merge_sha")
    return df.merge(tl, left_on="id_pr", right_index=True, how="left")


def attach_draw_priority(cand: pd.DataFrame, prev_ids: set) -> pd.DataFrame:
    """Rank every candidate so the stratified draw takes the BEST rows first.

    The repository round-robin in `_draw` controls CONCENTRATION; this controls
    WHICH of a repository's PRs are taken, and it is where three measured jury
    attacks are answered. Order is config.V2_DRAW_PRIORITY:

    `sticky`      already drawn and extracted, so re-curation never discards
                  work already paid for in network time.
    `dataset`     the PR has an exact file list, so Step 2 does not have to
                  derive one and attrition is ~0 rather than ~30%.
    `multi_agent` the repository contains PRs from >= 2 agents. THE FIX FOR THE
                  STRONGEST ATTACK ON THE STUDY: measured on the first V2 draw,
                  only 13 of 730 repositories held all three agents and just
                  6.7% of PRs sat in them, so "agent A violates more than agent
                  B" was almost entirely "agent A's projects differ from agent
                  B's projects". Macro-per-repo controls how much each
                  repository WEIGHS; it cannot control which repositories an
                  agent got. Preferring shared repositories grows the subset on
                  which a genuine within-repository comparison is possible.
    `recent`      newest first. The agents worked in different ERAS -- median PR
                  date OpenAI_Codex 2025-07-10 against Claude_Code 2026-02-21 --
                  so a cross-agent comparison was partly a comparison of
                  repository eras and of older agent versions. Recency narrows
                  that gap and measures the agents as they are now, which is
                  what the study is about.

    Returns the frame with a `_prio` column, lower = drawn first.
    """
    out = cand.copy()
    multi = (out.groupby("full_name")["agent"].transform("nunique") >= 2)
    when = pd.to_datetime(out.get("created_at"), errors="coerce", utc=True)
    # Recency as a rank in [0, 1) so it only ever breaks ties between rows that
    # are equal on the categorical keys above it.
    rec = when.rank(ascending=False, pct=True, na_option="bottom").fillna(1.0)

    prio = pd.Series(0.0, index=out.index)
    weight = 8.0
    for key in C.V2_DRAW_PRIORITY:
        if key == "sticky":
            bad = ~out["id_pr"].isin(prev_ids)
        elif key == "dataset":
            bad = out["files_source"].ne("dataset")
        elif key == "multi_agent":
            bad = ~multi
        elif key == "recent":
            prio = prio + rec          # continuous tiebreak, always last
            continue
        else:
            continue
        prio = prio + weight * bad.astype(float)
        weight /= 2.0
    out["_prio"] = prio
    out["_multi_agent_repo"] = multi
    print("[curate] draw priority: %d sticky, %d dataset-sourced, %d in "
          "multi-agent repos (of %d candidates)"
          % (out["id_pr"].isin(prev_ids).sum(),
             out["files_source"].eq("dataset").sum(), int(multi.sum()), len(out)))
    return out


def _draw(pool: pd.DataFrame, n: int, profile: dict | None = None) -> pd.DataFrame:
    """Take `n` rows from `pool`, repo-stratified when the profile asks for it.

    Default is a uniform random sample (the historic behaviour). When the
    profile sets `repo_stratified`, rows are taken ROUND-ROBIN across
    `full_name` instead: one PR from each repository, then a second from each,
    and so on. That yields the same `n` PRs spread over far more repositories --
    see the JAVA_REPO_STRATIFIED_DRAW note in config.py for the measured effect
    and for what it changes about interpretation.
    """
    n = int(min(n, len(pool)))
    if n <= 0:
        return pool.iloc[:0]
    if not (profile or {}).get("repo_stratified") or "full_name" not in pool.columns:
        return pool.sample(n, random_state=C.RANDOM_SEED)

    rng = np.random.default_rng(C.RANDOM_SEED)
    # Inside a repository bucket, take the highest-priority rows first when a
    # priority has been computed (see attach_draw_priority); otherwise fall back
    # to the seeded shuffle, which is the historic behaviour.
    if "_prio" in pool.columns:
        buckets = {r: list(g.sort_values("_prio", kind="mergesort").index.to_numpy())
                   for r, g in pool.groupby("full_name")}
    else:
        buckets = {r: list(rng.permutation(g.index.to_numpy()))
                   for r, g in pool.groupby("full_name")}
    order = sorted(buckets, key=lambda r: -len(buckets[r]))
    picked: list = []
    depth = 0
    while len(picked) < n:
        progressed = False
        for r in order:
            if depth < len(buckets[r]):
                picked.append(buckets[r][depth])
                progressed = True
                if len(picked) >= n:
                    break
        if not progressed:
            break
        depth += 1
    return pool.loc[picked]


def balanced_sample(df: pd.DataFrame, gold_path: Path,
                    profile: dict | None = None,
                    cell_cap: int = C.CELL_PR_CAP) -> pd.DataFrame:
    """Draw equal (agent x outcome) cells of `cell_cap` PRs each.

    V2 CHANGE: the cell is (agent, OUTCOME), not agent. V1 grouped by agent
    only, which under a two-outcome design would have let one outcome dominate
    an agent's cell and confounded the merged-vs-rejected comparison with n.

    Sticky: PRs already in a previous gold set (for THIS language) are kept
    first (when still eligible), so re-curation only ADDS PRs and earlier clone
    caches and label samples stay valid.

    Draw order inside a cell: sticky rows, then rows whose file list came from
    the DATASET (exact, pre-filtered to touch >=1 source file, ~0% Step-2
    attrition), then git_diff rows. git_diff rows are OVER-DRAWN by
    C.GIT_DIFF_OVERDRAW because Step 2 discovers and drops the ones that touch
    no in-scope source file; under-drawing would land the cell below its cap.
    """
    prev_ids: set = set()
    if gold_path.exists():
        prev = pd.read_parquet(gold_path)
        prev_ids = set(prev["pr_id"])
        print("[curate] sticky sampling against previous gold set (%d PRs)"
              % len(prev_ids))

    # SURVIVAL-AWARE STICKINESS (2026-08-17). A previously drawn PR only holds a
    # slot if Step 2 actually extracted a source file from it. Without this, PRs
    # that FAILED extraction stay sticky forever and consume cap budget, so a
    # re-curation cannot repair an under-filled cell.
    manifest = (profile or {}).get("clone_manifest")
    if manifest is not None and Path(manifest).exists() and prev_ids:
        man = pd.read_parquet(manifest, columns=["pr_id", "extract_status"])
        alive = set(man.loc[man["extract_status"] == "ok", "pr_id"])
        dropped = len(prev_ids) - len(prev_ids & alive)
        prev_ids &= alive
        if dropped:
            print("[curate] survival-aware: %d previously drawn PRs had no "
                  "extractable source file and release their slot (%d keep it)"
                  % (dropped, len(prev_ids)))

    # ---------------- V2.1: WITHIN-AGENT OUTCOME BALANCE (the default) -------
    #
    # Balance merged against rejected INSIDE each agent, at that agent's own
    # rejected ceiling, instead of capping every cell at the smallest cell in
    # the whole corpus. See the V2_WITHIN_AGENT_BALANCE block in config.py for
    # the argument; in short, equal n is needed for the merged-vs-rejected
    # contrast (which is within-agent) and is NOT needed across agents (which is
    # compared on macro-per-repo rates, and a rate is not biased by its
    # denominator). Rejected is the scarce side everywhere, so this maximises
    # the corpus exactly where the study is data-limited, and it captures every
    # `changes_requested` PR that exists rather than sampling for a stratum far
    # too small to sample.
    if getattr(C, "V2_WITHIN_AGENT_BALANCE", False):
        hard = getattr(C, "CELL_HARD_CAP", 1500)
        parts = []
        for agent, ag in df.groupby("agent"):
            rej = ag[ag["outcome"] == "rejected"]
            mer = ag[ag["outcome"] == "merged"]
            opn = ag[ag["outcome"] == "open"]
            n = min(len(rej), hard)
            take_rej = rej if len(rej) <= n else _draw(rej, n, profile)
            # Over-draw merged so within-agent balance survives Step-2 attrition
            # (the rejected pool is exhausted and cannot be over-drawn). See
            # MERGED_OVERDRAW in config.py; balance is then restored on
            # ANALYSABLE PRs, which is the population rates are computed over.
            n_mer = min(int(round(n * getattr(C, "MERGED_OVERDRAW", 1.0))),
                        len(mer), hard)
            take_mer = _draw(mer, n_mer, profile) if len(mer) > n_mer else mer
            parts += [take_rej, take_mer]
            if len(opn):
                parts.append(opn)          # supplementary, never in the headline
            print("[curate]   %-13s rejected %d (whole pool) + merged %d "
                  "(%.1fx over-drawn from %d)%s"
                  % (agent, len(take_rej), len(take_mer),
                     getattr(C, "MERGED_OVERDRAW", 1.0), len(mer),
                     ", + %d open (supplementary)" % len(opn) if len(opn) else ""))
        return pd.concat(parts).reset_index(drop=True)

    parts = []
    for (agent, outcome), grp in df.groupby(["agent", "outcome"]):
        if len(grp) <= cell_cap:
            parts.append(grp)
            print("[curate]   %-13s %-9s pool %d <= cap %d: taking all"
                  % (agent, outcome, len(grp), cell_cap))
            continue
        sticky = grp[grp["id_pr"].isin(prev_ids)]
        pool = grp[~grp["id_pr"].isin(prev_ids)]
        n_new = cell_cap - len(sticky)
        if n_new <= 0:
            # More sticky rows than the cell holds (V1 drew 500/agent, V2 holds
            # 300/cell). Sub-draw them through _draw so the repo-stratification
            # still applies: `.head()` would take whatever the sort order put
            # first, which is alphabetical by repo and therefore a selection
            # effect on repository name.
            parts.append(_draw(sticky, cell_cap, profile))
            print("[curate]   %-13s %-9s %d sticky > cap %d: sub-drawn to %d"
                  % (agent, outcome, len(sticky), cell_cap, cell_cap))
            continue

        exact = pool[pool["files_source"] == "dataset"]
        approx = pool[pool["files_source"] != "dataset"]
        take_exact = _draw(exact, min(n_new, len(exact)), profile)
        short = n_new - len(take_exact)
        chosen = [sticky, take_exact]
        n_gd = 0
        if short > 0 and len(approx):
            want = min(int(round(short * C.GIT_DIFF_OVERDRAW)), len(approx))
            gd = _draw(approx, want, profile)
            chosen.append(gd)
            n_gd = len(gd)
        out_grp = pd.concat(chosen)
        parts.append(out_grp)
        print("[curate]   %-13s %-9s %d sticky + %d dataset + %d git_diff "
              "(over-drawn %sx) = %d, cap %d"
              % (agent, outcome, len(sticky), len(take_exact), n_gd,
                 C.GIT_DIFF_OVERDRAW, len(out_grp), cell_cap))
    return pd.concat(parts).reset_index(drop=True)


def concentration(gold: pd.DataFrame) -> pd.DataFrame:
    """top-1 / top-3 repo share per (agent, outcome) cell, plus repo counts."""
    rows = []
    for (agent, outcome), g in gold.groupby(["agent", "outcome"]):
        rc = g.groupby("full_name").size().sort_values(ascending=False)
        rows.append({
            "agent": agent, "outcome": outcome, "prs": len(g), "repos": len(rc),
            "top1_pct": round(rc.iloc[0] / len(g) * 100, 1),
            "top3_pct": round(rc.head(3).sum() / len(g) * 100, 1),
        })
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=sorted(LANG_PROFILES), default="py",
                    help="language profile (default: py)")
    ap.add_argument("--outcomes", default=",".join(C.V2_OUTCOMES),
                    help="comma list of merged,rejected,open (default: V2_OUTCOMES)")
    ap.add_argument("--include-open", action="store_true",
                    help="also draw the supplementary `open` cells")
    ap.add_argument("--cell-cap", type=int, default=0,
                    help="override the profile's per (agent x outcome) cell cap")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the eligible pool and exit without writing")
    args = ap.parse_args()
    profile = LANG_PROFILES[args.lang]
    gold_path = profile["gold_set"]
    cell_cap = args.cell_cap or profile.get("cell_cap") or C.CELL_PR_CAP

    outcomes = [o.strip() for o in args.outcomes.split(",") if o.strip()]
    if args.include_open and "open" not in outcomes:
        outcomes.append("open")
    bad = set(outcomes) - {"merged", "rejected", "open"}
    assert not bad, "unknown outcome(s): %s" % bad

    C.CORPUS_DIR.mkdir(parents=True, exist_ok=True)

    safeio.check_disk()
    cand = load_candidates(profile, outcomes)
    print("[curate] (%s) candidates after outcome/stars/license: %d   "
          "cell cap %d" % (args.lang, len(cand), cell_cap))

    only = profile.get("only_agents")
    if only:
        before = len(cand)
        cand = cand[cand["agent"].isin(only)]
        assert len(cand), "zero PRs left after restricting to %s" % only
        print("[curate] restricted to %s: %d -> %d candidate PRs"
              % (only, before, len(cand)))

    cand = attach_rejection_stratum(cand)
    cand = attach_touched_files(cand, profile)
    assert len(cand), "zero PRs after attach_touched_files"
    print("[curate] eligible pool: %d PRs" % len(cand))
    print("\n[curate] ELIGIBLE POOL, agent x outcome:")
    print(pd.crosstab(cand["agent"], cand["outcome"]).to_string())
    print("\n[curate] ELIGIBLE POOL, rejection strata (not-merged only):")
    nm = cand[cand["outcome"] != "merged"]
    if len(nm):
        print(pd.crosstab(nm["agent"], nm["rejection_stratum"]).to_string())

    if args.dry_run:
        print("\n[curate] --dry-run: nothing written")
        return

    # Rank candidates before drawing (recency, shared repositories, exact file
    # lists, stickiness). See attach_draw_priority for why each key is there.
    prev_ids: set = set()
    if gold_path.exists():
        prev_ids = set(pd.read_parquet(gold_path, columns=["pr_id"])["pr_id"])
    cand = attach_draw_priority(cand, prev_ids)

    sample = balanced_sample(cand, gold_path, profile, cell_cap)
    sample = attach_merge_sha(sample)
    sample = attach_commit_shas(sample)

    gold = pd.DataFrame({
        "pr_id": sample["id_pr"],
        "pr_number": sample["number"],
        "agent": sample["agent"],
        "outcome": sample["outcome"],                      # V2
        "rejection_stratum": sample["rejection_stratum"],  # V2
        "state": sample["state"],
        "created_at": sample["created_at"],
        "closed_at": sample["closed_at"],
        "merged_at": sample["merged_at"],
        "pr_user": sample["user"],
        "repo_id": sample["repo_id"],
        "full_name": sample["full_name"],       # e.g. "owner/name"
        "repo_url": sample["repo_url"],
        "stars": sample["stars"],
        "license": sample["license"],
        "repo_language": sample["language"],
        "merge_sha": sample["merge_sha"],
        "n_touched_files": sample["n_touched_files"],
        "touched_files": sample["touched_files"],
        "files_source": sample["files_source"],
        "commit_shas": sample["commit_shas"],
        "has_review": sample["has_review"],
        "has_changes_requested": sample["has_changes_requested"],
        # True when this PR's repository holds PRs from >= 2 agents. The
        # within-repository cross-agent comparison is computed on these rows.
        "multi_agent_repo": sample["_multi_agent_repo"],
    }).sort_values(["agent", "outcome", "full_name", "pr_number"]).reset_index(drop=True)

    assert len(gold), "refusing to write an empty gold set"
    safeio.to_parquet(gold, gold_path, index=False)

    print("\n[curate] gold set written to", gold_path)
    print("[curate] total PRs: %d across %d repos"
          % (len(gold), gold["repo_id"].nunique()))
    print("\nDRAWN CORPUS, agent x outcome:")
    print(pd.crosstab(gold["agent"], gold["outcome"], margins=True).to_string())
    print("\nDRAWN CORPUS, rejection strata:")
    print(pd.crosstab(gold["agent"], gold["rejection_stratum"]).to_string())
    print("\nCONCENTRATION per cell (lead with macro-per-repo in every report):")
    print(concentration(gold).to_string(index=False))
    print("\nFile-list source per cell (git_diff rows are resolved in Step 2):")
    print(pd.crosstab([gold["agent"], gold["outcome"]], gold["files_source"]).to_string())
    print("\nmerge_sha present per cell (rejected PRs have none by construction; "
          "gitsnap tier 3 resolves them from refs/pull/N/head):")
    print(gold.assign(has_ms=gold["merge_sha"].notna())
              .groupby(["agent", "outcome"])["has_ms"]
              .agg(["size", "sum"]).to_string())


if __name__ == "__main__":
    main()
