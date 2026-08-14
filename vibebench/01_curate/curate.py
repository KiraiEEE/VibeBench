"""
Step 1 - Curate the gold set.

Reads the AIDev parquet tables and produces `corpus/gold_set.parquet`: one row
per in-scope merged PR, with the repo metadata, the AI-touched source files, and
a merge-commit SHA fallback for the clone stage.

Selection policy lives in config.py:
  - language in TARGET_LANGUAGES
  - stars >= MIN_STARS
  - permissive license
  - PR is merged and touches >= 1 source file (SOURCE_EXTENSIONS)
  - balanced per-agent sampling (keep small agents whole, cap large ones)

Copilot special case: pr_commit_details carries NO filenames for Copilot (an
AIDev collection gap), so the "touches >= 1 source file" pre-filter cannot be
applied. Copilot PRs are sampled on repo criteria alone with
`files_source="git_diff"`; Step 2 derives their touched files from the merge
commit's diff and drops PRs that touch no source file.

Sticky sampling: if a previous gold_set.parquet exists, PRs already in it are
kept (when still eligible) and only topped up to the cap with new draws. This
keeps earlier clone caches and labeling samples valid across re-curation.

Run:  python vibebench/01_curate/curate.py             (Python corpus, default)
      python vibebench/01_curate/curate.py --lang ts   (TypeScript corpus)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402


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
    },
    "ts": {
        "languages": C.TS_TARGET_LANGUAGES,
        "extensions": C.TS_SOURCE_EXTENSIONS,
        "excluded_parts": C.TS_EXCLUDED_PATH_PARTS,
        "gold_set": C.GOLD_SET_TS,
    },
    # Java samples differently from the other two on purpose -- see the
    # JAVA_* block in config.py. Only one agent has enough Java to measure, and
    # the imbalance to control is across REPOSITORIES rather than agents.
    "java": {
        "languages": C.JAVA_TARGET_LANGUAGES,
        "extensions": C.JAVA_SOURCE_EXTENSIONS,
        "excluded_parts": C.JAVA_EXCLUDED_PATH_PARTS,
        "gold_set": C.GOLD_SET_JAVA,
        "only_agents": C.JAVA_ONLY_AGENTS,
        "per_repo_cap": C.JAVA_PER_REPO_PR_CAP,
        "filter_by_file": C.JAVA_FILTER_BY_FILE,
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


def load_candidates(profile: dict) -> pd.DataFrame:
    """Merged PRs joined to repo metadata, filtered by language/stars/license."""
    repo = pd.read_parquet(
        C.DATA_DIR / "repository.parquet",
        columns=["id", "full_name", "url", "language", "stars", "license"],
    )
    pr = pd.read_parquet(
        C.DATA_DIR / "pull_request.parquet",
        columns=["id", "number", "repo_id", "repo_url", "agent", "merged_at", "html_url"],
    )
    pr = pr[pr["merged_at"].notna()].copy()

    merged = pr.merge(
        repo, left_on="repo_id", right_on="id", suffixes=("_pr", "_repo")
    )
    lic = merged["license"].fillna("").str.lower()
    mask = (merged["stars"] >= C.MIN_STARS) & lic.isin(C.PERMISSIVE_LICENSES)
    # `filter_by_file` profiles skip the repo's headline-language filter: the
    # unit of analysis is the SOURCE FILE, and a repo whose primary language is
    # Kotlin or Go can still contain real Java. attach_touched_files() then does
    # the real filtering by extension, which is the more faithful test.
    if not profile.get("filter_by_file"):
        mask &= merged["language"].isin(profile["languages"])
    return merged[mask].copy()


def attach_touched_files(cand: pd.DataFrame, profile: dict) -> pd.DataFrame:
    """Attach the list of AI-touched source files per PR from pr_commit_details.

    Reads only the light columns (not the huge `patch`) and filters to the
    candidate PR ids to keep memory bounded.

    Non-Copilot agents: require >= 1 touched source file (inner join), and set
    files_source="dataset". Copilot: pr_commit_details filenames are 100% null,
    so all candidate PRs are kept with an empty touched list and
    files_source="git_diff" -- Step 2 resolves the real list from git.
    """
    non_cop = cand[cand["agent"] != "Copilot"].copy()
    cop = cand[cand["agent"] == "Copilot"].copy()

    pr_ids = set(non_cop["id_pr"].tolist())
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
    non_cop = non_cop.merge(grouped, left_on="id_pr", right_index=True, how="inner")
    non_cop["files_source"] = "dataset"

    cop["touched_files"] = [[] for _ in range(len(cop))]
    cop["files_source"] = "git_diff"

    out = pd.concat([non_cop, cop], ignore_index=True)
    out["n_touched_files"] = out["touched_files"].apply(len)
    return out


def attach_commit_shas(df: pd.DataFrame) -> pd.DataFrame:
    """Attach every PR's own commit SHAs (from pr_commits).

    Two consumers:
      * Step 2 uses them to derive Copilot's touched files when the timeline
        has no merge SHA.
      * The delta harnesses use them (gitsnap.delta_pair) to reconstruct a
        before/after pair when the timeline has no merge SHA at all: the tip
        of this commit set is the "after", and the branch point (the parent
        lying outside the set) is the "before".

    Attached for ALL agents since 2026-07-28. Previously only Copilot rows got
    them, which meant the no-merge-SHA loss (~15% of TS PRs, ~10% of Python)
    was only recoverable for Copilot -- an avoidable, agent-biased data loss.
    """
    pc = pd.read_parquet(C.DATA_DIR / "pr_commits.parquet", columns=["pr_id", "sha"])
    pc = pc[pc["pr_id"].isin(set(df["id_pr"]))]
    grouped = pc.groupby("pr_id")["sha"].apply(list).rename("commit_shas")
    out = df.merge(grouped, left_on="id_pr", right_index=True, how="left")
    out["commit_shas"] = out["commit_shas"].where(out["commit_shas"].notna(), None)
    return out


def attach_merge_sha(df: pd.DataFrame) -> pd.DataFrame:
    """Attach the merge-commit SHA (fallback for clone) from pr_timeline."""
    pr_ids = set(df["id_pr"].tolist())
    tl = pd.read_parquet(
        C.DATA_DIR / "pr_timeline.parquet",
        columns=["pr_id", "event", "commit_id"],
    )
    tl = tl[(tl["pr_id"].isin(pr_ids)) & (tl["event"] == "merged")]
    tl = tl.drop_duplicates("pr_id").set_index("pr_id")["commit_id"].rename("merge_sha")
    return df.merge(tl, left_on="id_pr", right_index=True, how="left")


def balanced_sample(df: pd.DataFrame, gold_path: Path,
                    profile: dict | None = None) -> pd.DataFrame:
    """Keep every PR from small agents; cap large agents at PER_AGENT_PR_CAP.

    Sticky: PRs already in a previous gold set (for THIS language) are kept
    first (when still eligible), so re-curation with wider filters only ADDS
    PRs -- earlier clone caches and labeling samples stay valid.

    PER-REPO MODE (`profile["per_repo_cap"]`, used by Java): caps by repository
    instead of by agent. Java has only one agent with usable data, so the
    imbalance worth controlling is across repositories -- uncapped, 53% of the
    Java corpus sits in three repos. Stickiness works the same way.
    """
    prev_ids: set = set()
    if gold_path.exists():
        prev_ids = set(pd.read_parquet(gold_path, columns=["pr_id"])["pr_id"])
        print(f"[curate] sticky sampling against previous gold set ({len(prev_ids)} PRs)")

    cap = (profile or {}).get("per_repo_cap")
    if (profile or {}).get("only_agents") and not cap:
        rc = df.groupby("full_name").size()
        print(f"[curate] no cap: {len(df)} PRs across {len(rc)} repos; "
              f"top-repo share {rc.max()/len(df):.1%} "
              f"(controlled by macro-per-repo averaging in the report)")
        return df.reset_index(drop=True)
    if cap:
        parts = []
        for _, grp in df.groupby("full_name"):
            if len(grp) > cap:
                keep = grp[grp["id_pr"].isin(prev_ids)]
                rest = grp[~grp["id_pr"].isin(prev_ids)]
                n_new = cap - len(keep)
                grp = (pd.concat([keep, rest.sample(n_new, random_state=C.RANDOM_SEED)])
                       if n_new > 0 else keep.head(cap))
            parts.append(grp)
        out = pd.concat(parts).reset_index(drop=True)
        rc = out.groupby("full_name").size()
        print(f"[curate] per-repo cap {cap}: {len(out)} PRs across {len(rc)} repos; "
              f"top-repo share {rc.max()/len(out):.1%}, top-3 {rc.nlargest(3).sum()/len(out):.1%}")
        return out

    parts = []
    for agent, grp in df.groupby("agent"):
        if len(grp) > C.PER_AGENT_PR_CAP:
            keep = grp[grp["id_pr"].isin(prev_ids)]
            rest = grp[~grp["id_pr"].isin(prev_ids)]
            n_new = C.PER_AGENT_PR_CAP - len(keep)
            if n_new > 0:
                grp = pd.concat([keep, rest.sample(n_new, random_state=C.RANDOM_SEED)])
            else:
                grp = keep.head(C.PER_AGENT_PR_CAP)
        parts.append(grp)
    return pd.concat(parts).reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=sorted(LANG_PROFILES), default="py",
                    help="language profile (default: py -- original behaviour)")
    args = ap.parse_args()
    profile = LANG_PROFILES[args.lang]
    gold_path = profile["gold_set"]

    C.CORPUS_DIR.mkdir(parents=True, exist_ok=True)

    cand = load_candidates(profile)
    print(f"[curate] ({args.lang}) candidate merged PRs after lang/stars/license: {len(cand)}")

    only = profile.get("only_agents")
    if only:
        before = len(cand)
        cand = cand[cand["agent"].isin(only)]
        print(f"[curate] restricted to {only}: {before} -> {len(cand)} candidate PRs")
    cand = attach_touched_files(cand, profile)
    print(f"[curate] PRs touching >=1 source file: {len(cand)}")

    sample = balanced_sample(cand, gold_path, profile)
    sample = attach_merge_sha(sample)
    sample = attach_commit_shas(sample)

    gold = pd.DataFrame({
        "pr_id": sample["id_pr"],
        "pr_number": sample["number"],
        "agent": sample["agent"],
        "merged_at": sample["merged_at"],
        "repo_id": sample["repo_id"],
        "full_name": sample["full_name"],       # e.g. "owner/name"
        "repo_url": sample["repo_url"],
        "stars": sample["stars"],
        "license": sample["license"],
        "merge_sha": sample["merge_sha"],
        "n_touched_files": sample["n_touched_files"],
        "touched_files": sample["touched_files"],
        "files_source": sample["files_source"],
        "commit_shas": sample["commit_shas"],
    }).sort_values(["agent", "full_name", "pr_number"]).reset_index(drop=True)

    gold.to_parquet(gold_path, index=False)

    print("\n[curate] gold set written to", gold_path)
    print(f"[curate] total PRs: {len(gold)} across {gold['repo_id'].nunique()} repos")
    print("\nPer-agent breakdown:")
    summary = gold.groupby("agent").agg(
        prs=("pr_id", "size"),
        repos=("repo_id", "nunique"),
        median_files=("n_touched_files", "median"),
    )
    print(summary.to_string())


if __name__ == "__main__":
    main()
