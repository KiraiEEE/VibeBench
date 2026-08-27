"""
Step 6-ENRICH - backfill the CLOSE REASON for the selected corpus, from GitHub.

WHY THIS EXISTS (REDESIGN_V2.md 2.4 and 7.1). V2 compares violation rates in
merged vs not-merged PRs. That comparison is worthless, and actively misleading,
if "rejected" is read as "refused on quality". Measured on the eligible pool,
it usually is not: a rejected PR is HALF as likely to carry any recorded review
as a merged one (Java 10.8% vs 25.3%, Python 6.2% vs 12.1%), because most
closures are superseded, stale, duplicate, abandoned or bulk-closed.

The dataset cannot settle this on its own. `pr_reviews` covers 6-11% of rejected
PRs, and `pr_timeline`'s `closed` event is worse than partial, it is AGENT-BIASED:

    closed-event coverage, eligible rejected PRs, measured 2026-08-25
    lang    Claude_Code   Copilot   OpenAI_Codex
    Java           2.8%     58.5%          64.4%
    Python         3.5%     54.8%          26.8%

A 20x per-agent spread on the column that defines the rejection strata would
have decided the merged-vs-rejected result by metadata coverage rather than by
code. Under the V2 enrichment rule that column may not be used until the gap is
closed and the closure is VERIFIED per agent, or the column is dropped.

WHAT THIS DOES. One GraphQL pass over the SELECTED corpus only (low thousands of
PRs, never a bulk crawl), fetching per PR: state, merged, closedAt, every review
state, the CLOSED_EVENT actor, and comment counts. GraphQL is used rather than
REST because REST needs 3 calls per PR (~8,000 calls for a 2,700-PR corpus,
close to the 5,000/hour ceiling) while GraphQL aliases 40 PRs into one request
at a handful of rate-limit points each.

Outputs `corpus/pr_outcomes_<lang>.parquet`, one row per PR, and prints per-agent
coverage BEFORE and AFTER so the closure of the gap is measured, not assumed.
It does NOT modify the gold set; `apply_enrichment.py` does that, so this
network pass can be re-run freely.

Auth: uses `gh auth token` (the GitHub CLI's stored token). No token is written
anywhere and none is passed on a command line.

Run:  python vibebench/06_enrich/fetch_pr_outcomes.py --lang java
      python vibebench/06_enrich/fetch_pr_outcomes.py --lang java --limit 40
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
import safeio  # noqa: E402  -- crash-safe writes (power/network cuts)

GOLD = {"java": C.GOLD_SET_JAVA, "py": C.GOLD_SET, "ts": C.GOLD_SET_TS}
OUT = {lang: C.CORPUS_DIR / ("pr_outcomes_%s.parquet" % lang) for lang in GOLD}

# 40 PRs per GraphQL request. The limit is not the rate budget (a batch this
# size costs a few points of the 5,000/hour) but the 10-second server-side query
# timeout: aliasing hundreds of repository lookups into one document makes
# GitHub abort the whole batch, losing 400 PRs instead of 40.
BATCH = 40
MAX_RETRIES = 4

PR_FRAGMENT = """
  a%(i)d: repository(owner: %(owner)s, name: %(name)s) {
    pullRequest(number: %(number)d) {
      number
      state
      merged
      closedAt
      mergedAt
      author { login }
      additions
      deletions
      changedFiles
      comments { totalCount }
      reviews(first: 30) { nodes { state author { login } } }
      timelineItems(last: 20, itemTypes: [CLOSED_EVENT]) {
        nodes { ... on ClosedEvent { createdAt actor { login } } }
      }
    }
  }
"""


def gh_token() -> str:
    r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip(), (
        "no GitHub token: run `gh auth login` first (stderr: %s)" % r.stderr[:200])
    return r.stdout.strip()


def graphql(query: str, token: str) -> dict:
    """POST one GraphQL document, retrying on transient failures and rate limits.

    Retries on 403/429/5xx and on GitHub's secondary rate limit, which arrives as
    a 200 with an `errors` block rather than an HTTP error status. A batch that
    still fails after MAX_RETRIES returns its `errors` so the caller can record
    the PRs as unresolved instead of silently dropping them.
    """
    body = json.dumps({"query": query}).encode()
    for attempt in range(MAX_RETRIES):
        req = urllib.request.Request(
            "https://api.github.com/graphql", data=body,
            headers={"Authorization": "bearer %s" % token,
                     "Content-Type": "application/json",
                     "User-Agent": "vibebench-enrich"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                got = json.loads(resp.read().decode())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            status = getattr(e, "code", None)
            if attempt == MAX_RETRIES - 1:
                return {"errors": [{"message": "http %s: %s" % (status, e)}]}
            time.sleep(5 * (attempt + 1))
            continue
        msgs = " ".join(str(e.get("message", "")) for e in got.get("errors", []))
        if "rate limit" in msgs.lower() and attempt < MAX_RETRIES - 1:
            time.sleep(60)
            continue
        return got
    return {"errors": [{"message": "exhausted retries"}]}


def build_query(chunk: pd.DataFrame) -> str:
    parts = []
    for i, r in enumerate(chunk.itertuples()):
        owner, _, name = str(r.full_name).partition("/")
        parts.append(PR_FRAGMENT % {
            "i": i, "owner": json.dumps(owner), "name": json.dumps(name),
            "number": int(r.pr_number)})
    return "query {%s}" % "".join(parts)


def parse_pr(node: dict | None, row) -> dict:
    """One PR's API facts. A missing node means the repo or PR is gone."""
    out = {
        "pr_id": row.pr_id, "agent": row.agent, "full_name": row.full_name,
        "pr_number": int(row.pr_number),
        "outcome_dataset": getattr(row, "outcome", None),
        "api_available": node is not None,
        "api_state": None, "api_merged": None, "api_closed_at": None,
        "api_author": None, "api_additions": None, "api_deletions": None,
        "api_changed_files": None, "api_n_comments": None,
        "api_n_reviews": 0, "api_review_states": "",
        "api_has_changes_requested": False, "api_has_approval": False,
        "api_closed_by": None, "api_closed_by_author": None,
    }
    if node is None:
        return out
    author = (node.get("author") or {}).get("login")
    reviews = [n for n in (node.get("reviews") or {}).get("nodes", []) or [] if n]
    states = [n.get("state") for n in reviews if n.get("state")]
    # A review the PR's own author left is not external quality evidence. The
    # agents post reviews on their own PRs often enough that counting them would
    # inflate `reviewed_then_closed` for exactly the agents that do it most.
    ext_states = [n.get("state") for n in reviews
                  if n.get("state") and (n.get("author") or {}).get("login") != author]
    closed_ev = [n for n in (node.get("timelineItems") or {}).get("nodes", []) or [] if n]
    closer = (closed_ev[-1].get("actor") or {}).get("login") if closed_ev else None
    out.update(
        api_state=node.get("state"), api_merged=node.get("merged"),
        api_closed_at=node.get("closedAt"), api_author=author,
        api_additions=node.get("additions"), api_deletions=node.get("deletions"),
        api_changed_files=node.get("changedFiles"),
        api_n_comments=(node.get("comments") or {}).get("totalCount"),
        api_n_reviews=len(ext_states),
        api_review_states="|".join(sorted(set(ext_states))),
        api_has_changes_requested="CHANGES_REQUESTED" in ext_states,
        api_has_approval="APPROVED" in ext_states,
        api_closed_by=closer,
        api_closed_by_author=(None if closer is None or author is None
                              else closer.lower() == author.lower()),
    )
    _ = states  # kept deliberately: self-reviews are excluded above, not ignored
    return out


def coverage_table(df: pd.DataFrame, col: str, label: str) -> None:
    """Per-agent x outcome coverage of `col`. THE gate for every backfill."""
    t = (df.assign(_have=df[col].notna())
           .groupby(["agent", "outcome_dataset"])["_have"].agg(["size", "sum"]))
    t["pct"] = (t["sum"] / t["size"] * 100).round(1)
    print("\n%s" % label)
    print(t.rename(columns={"size": "prs", "sum": "known"}).to_string())
    spread = t["pct"].max() - t["pct"].min()
    print("per-cell spread: %.1f pp%s" % (
        spread, "   <-- NOT CLOSED: >2x/15pp spread means this column must be "
                "dropped from the analysis, not reported" if spread > 15 else
                "   <-- parity reached"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=sorted(GOLD), default="java")
    ap.add_argument("--limit", type=int, default=0, help="first N PRs (smoke test)")
    ap.add_argument("--from-pool", default=None,
                    help="parquet of candidate PRs (pr_id, pr_number, agent, "
                         "full_name) to enrich instead of the gold set")
    ap.add_argument("--out", default=None, help="override the output parquet")
    ap.add_argument("--refresh", action="store_true",
                    help="re-fetch PRs already present in the output parquet")
    args = ap.parse_args()

    # `--from-pool` enriches a CANDIDATE list rather than the selected corpus.
    # Needed because the rejection strata cannot be known before selection, and
    # the strongest stratum (`changes_requested`) is far too small to draw
    # blindly: the dataset finds 25 of them in the whole Java corpus. Enriching
    # the ~1,900 eligible rejected candidates first costs ~50 GraphQL requests
    # and lets the draw OVERSAMPLE the informative stratum, which is a stratified
    # sample with known weights, not a bias -- provided every rate is reported
    # per stratum, which the report already enforces.
    gold = pd.read_parquet(args.from_pool if args.from_pool else GOLD[args.lang])
    assert len(gold), "empty input"
    if "outcome" not in gold.columns:
        gold["outcome"] = "merged"
    if args.limit:
        gold = gold.head(args.limit)

    out_path = Path(args.out) if args.out else OUT[args.lang]
    done: dict = {}
    if out_path.exists() and not args.refresh:
        prev = pd.read_parquet(out_path)
        # Only rows that actually resolved are kept: an unavailable PR is retried,
        # because "repo was briefly unreachable" and "repo is deleted" arrive as
        # the same empty node and freezing the first as permanent is the same
        # failure family as the no_delta_pair caching defect.
        prev = prev[prev["api_available"]]
        done = {r["pr_id"]: r for r in prev.to_dict("records")}
        print("[enrich] resuming: %d PRs already fetched" % len(done))

    todo = gold[~gold["pr_id"].isin(done)]
    print("[enrich] %d PRs to fetch, %d per request" % (len(todo), BATCH))

    token = gh_token()
    rows: list[dict] = list(done.values())
    t0 = time.time()
    for start in range(0, len(todo), BATCH):
        chunk = todo.iloc[start:start + BATCH]
        got = graphql(build_query(chunk), token)
        data = got.get("data") or {}
        if got.get("errors") and not data:
            print("  !! batch %d-%d failed: %s"
                  % (start, start + len(chunk),
                     str(got["errors"])[:200]), flush=True)
        for i, r in enumerate(chunk.itertuples()):
            repo_node = data.get("a%d" % i)
            node = (repo_node or {}).get("pullRequest") if repo_node else None
            rows.append(parse_pr(node, r))
        el = time.time() - t0
        n_done = start + len(chunk)
        print("[enrich] %d/%d  %.1f min elapsed, ~%.1f min left"
              % (n_done, len(todo), el / 60,
                 (el / max(n_done, 1)) * (len(todo) - n_done) / 60), flush=True)
        safeio.to_parquet(pd.DataFrame(rows), out_path, index=False)

    df = pd.DataFrame(rows)
    assert len(df), "refusing to write an empty enrichment table"
    safeio.to_parquet(df, out_path, index=False)
    print("\n[enrich] wrote %s (%d PRs)" % (out_path, len(df)))

    print("\nAPI availability: %d of %d PRs resolved (%.1f%%)"
          % (df["api_available"].sum(), len(df),
             df["api_available"].mean() * 100))
    print(df.groupby(["agent", "outcome_dataset"])["api_available"]
            .agg(["size", "sum"]).to_string())

    # THE GAP-CLOSURE MEASUREMENT. Before = the dataset's own timeline coverage
    # (agent-biased 20x); after = what the API returned. Both printed, per agent,
    # because an average that hides a per-agent spread is exactly how the 40x
    # file-attribution bias got in.
    coverage_table(df, "api_closed_by",
                   "CLOSE ACTOR coverage AFTER the API backfill:")
    print("\nReview evidence found by the API (external reviews only, "
          "self-reviews excluded):")
    ev = df.groupby(["agent", "outcome_dataset"]).agg(
        prs=("pr_id", "size"),
        any_review=("api_n_reviews", lambda s: int((s > 0).sum())),
        changes_requested=("api_has_changes_requested", "sum"),
        approved=("api_has_approval", "sum"))
    print(ev.to_string())

    print("\nDataset outcome vs API state (a mismatch means the PR moved after "
          "the 2026-03-31 dataset cutoff):")
    print(pd.crosstab(df["outcome_dataset"], df["api_state"].fillna("UNAVAILABLE"))
            .to_string())


if __name__ == "__main__":
    main()
