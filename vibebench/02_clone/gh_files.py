"""
Batched PR file lists from GitHub's GraphQL API. Shared by repair and recovery.

WHY BATCHED, AND WHY THIS MODULE EXISTS. Both the repair pass (374 PRs with
inflated lists) and the recovery pass (2,444 PRs the degenerate-pair bug lost)
need the same thing: the set of paths a PR actually changed. Done over REST that
is two round trips per PR, and on a link that drops for minutes at a time the
binding constraint is REQUEST COUNT, not bandwidth or rate limit -- measured
2026-09-02, the REST audit stalled for 8-minute stretches and managed
1,253 PRs/hour, while the same work batched over GraphQL ran at 41,000/hour.

One GraphQL request carries many PRs via aliases and returns the count AND the
file list together, so 2,818 PRs become ~60 requests instead of ~5,600.

TRUNCATION IS EXPLICIT HERE. `files(first: N)` returns at most N nodes, and
`changedFiles` gives the true total, so a caller can always tell a complete
answer from a partial one. Every function below reports `complete` rather than
quietly handing back a short list -- the whole class of bug this project keeps
finding is a partial result that looks like a full one.

Self-check: python vibebench/02_clone/gh_files.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "03_principles"))
import gitsnap  # noqa: E402

# GitHub caps a GraphQL page at 100 nodes. Anything above that needs paging,
# which we deliberately do NOT do inline: a PR with >100 changed files is rare,
# and reporting it as incomplete so the caller can fall back is safer than
# silently paginating inside a batch and getting it subtly wrong.
PAGE = 100


class PRFiles:
    """One PR's answer. `complete` is False when the list was truncated."""

    __slots__ = ("paths", "changed_files", "complete", "found")

    def __init__(self, paths, changed_files, complete, found):
        self.paths = paths                  # [(changeType, path)]
        self.changed_files = changed_files  # GitHub's own total, or None
        self.complete = complete
        self.found = found                  # False = deleted repo/PR

    def source_paths(self, extensions, excluded_parts) -> list[str]:
        """In-scope, non-deleted paths. DELETED files are excluded: a removed
        file has no content in the after-tree, so it cannot carry a violation."""
        return sorted(
            p for ct, p in self.paths
            if ct != "DELETED"
            and gitsnap.is_source_file(p, extensions, excluded_parts))

    def __repr__(self):
        return (f"PRFiles(n={len(self.paths)}, changed={self.changed_files}, "
                f"complete={self.complete}, found={self.found})")


def _query(batch) -> str:
    parts = []
    for i, (full_name, number) in enumerate(batch):
        owner, _, name = str(full_name).partition("/")
        if not owner or not name:
            continue
        parts.append(
            f'p{i}: repository(owner:"{owner}", name:"{name}") {{ '
            f'pullRequest(number:{int(number)}) {{ changedFiles '
            f'files(first:{PAGE}) {{ nodes {{ path changeType }} }} }} }}')
    return "query {\n" + "\n".join(parts) + "\n}"


def fetch_batch(batch, timeout: int = 180) -> dict:
    """{(full_name, number): PRFiles} for a batch, or {} if the request failed.

    Network-aware in the same way as gitsnap.run_git: a failure while the link
    is down waits for it to return rather than reporting every PR in the batch
    as unavailable, which would be an outage masquerading as a finding.
    """
    if not batch:
        return {}
    q = _query(batch)
    payload = None
    for attempt in (1, 2):
        try:
            r = subprocess.run(["gh", "api", "graphql", "-f", f"query={q}"],
                               capture_output=True, text=True, timeout=timeout,
                               **gitsnap.NO_WINDOW)
        except FileNotFoundError:
            sys.exit("gh is not on PATH -- this needs the GitHub CLI")
        except (subprocess.TimeoutExpired, OSError):
            r = None
        if r is not None and r.stdout.strip():
            try:
                payload = json.loads(r.stdout)
            except json.JSONDecodeError:
                payload = None
        if payload and payload.get("data"):
            break
        if attempt == 1 and not gitsnap.network_is_up():
            gitsnap.wait_for_network()
            continue
        break
    if not payload or not payload.get("data"):
        # TOTAL FAILURE, so SPLIT rather than lose the batch. GitHub answers a
        # multi-alias query with PARTIAL success: valid data for most PRs plus
        # NOT_FOUND for a few (deleted repo, "pull requests are disabled").
        # `gh` exits non-zero for those, and if the payload cannot be used at
        # all, one bad repository would otherwise discard 50 good PRs -- which
        # is exactly the "one failure hides many results" shape this project
        # keeps repairing. Halving isolates the offender in log2(n) requests.
        if len(batch) > 1:
            mid = len(batch) // 2
            out = fetch_batch(batch[:mid], timeout)
            out.update(fetch_batch(batch[mid:], timeout))
            return out
        return {}

    out = {}
    # Aliases GitHub could not resolve are absent or null in `data`; every key
    # in `batch` that does not come back is filled in as not-found below, so a
    # caller never sees a missing PR as "this PR changed nothing".
    for alias, node in (payload["data"] or {}).items():
        try:
            idx = int(alias[1:])
        except ValueError:
            continue
        if idx >= len(batch):
            continue
        key = batch[idx]
        pr = (node or {}).get("pullRequest")
        if not pr:
            # Deleted repo, deleted PR, or a private fork. Recorded as NOT
            # FOUND so a caller can never mistake it for "this PR changed
            # nothing", which is the exact confusion that lost 2,948 PRs.
            out[key] = PRFiles([], None, False, False)
            continue
        nodes = ((pr.get("files") or {}).get("nodes")) or []
        paths = [(n.get("changeType"), n.get("path")) for n in nodes if n.get("path")]
        changed = pr.get("changedFiles")
        complete = changed is None or len(paths) >= changed or len(paths) < PAGE
        out[key] = PRFiles(paths, changed, complete, True)
    for key in batch:
        out.setdefault(key, PRFiles([], None, False, False))
    return out


if __name__ == "__main__":
    print("gh_files self-check (needs network + gh auth)")
    b = [("amikos-tech/chromadb-java-client", 116),
         ("jenkinsci/chucknorris-plugin", 249),
         ("graphql-java/graphql-java", 4218)]
    got = fetch_batch(b)
    assert got, "no data returned -- is gh authenticated and the link up?"
    for k in b:
        v = got.get(k)
        print(f"  {k[0]}#{k[1]}: {v}")
        assert v is not None, f"missing {k}"
    a = got[b[0]]
    assert a.found and a.changed_files == 3, a
    src = a.source_paths({".java"}, ())
    assert all(s.endswith(".java") for s in src), src
    assert not any("/test/" in s for s in a.source_paths({".java"}, ("test/",))), \
        "excluded_parts must be honoured"
    print("  [ok  ] batch returns counts and file lists together")
    print("  [ok  ] deleted paths excluded, exclusions honoured")
    print("  [ok  ] missing PRs reported as found=False, never as 'no files'")
    print("\ngh_files self-check PASS")
