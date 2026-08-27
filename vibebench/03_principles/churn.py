"""
Change frequency (churn) per source file, from the repository's own history.

WHY THIS EXISTS. REDESIGN_V2.md section 5. The supervisor's SRP example -- "many
things import a class and it keeps being edited, so it violates SRP" -- is real
and has a name: high afferent coupling combined with high change frequency, the
CHANGE-MAGNET smell. It is much closer to Martin's actual definition of SRP,
"one reason to change", than any size proxy. The current rubric measures size and
cohesion, which are only correlates of that definition.

It also supplies the width-independent OCP signal that Java's validation asked
for: OCP recall was the weakest validated number, and the diagnosis was that the
false negatives are few-branch author-owned dispatch sets, i.e. OPENNESS IS NOT
A FUNCTION OF BRANCH WIDTH. A class edited across many commits is by definition
not closed for modification, and that says nothing about how wide its switch is,
so the two signals complement rather than replace each other.

THE TRAP THIS MODULE EXISTS TO AVOID, and it is the same one that has bitten
this project three times already:

    Churn is read from the LOCAL clone's history, and the clones are SHALLOW at
    different depths. A repository whose PRs all resolved through gitsnap tier 1
    (a merge SHA) was only ever fetched at --depth 2, while one that needed
    tier 3 was deepened to 500. Tier usage is agent-correlated -- on the V2 Java
    corpus OpenAI_Codex has a merge SHA for 286 of 300 merged PRs against
    Claude_Code's 27 -- so churn measured off the clones AS THEY ARE would be
    deep for one agent and absent for another.

    That is the file-attribution bias all over again, and it would silently
    rewrite the ranking. So depth is EQUALISED first (`ensure_history`), and
    coverage is reported PER AGENT before and after. A repository that cannot be
    deepened is reported as unmeasured, never as zero churn: zero churn and
    unknown churn are opposite claims and must never share an encoding.

WINDOW, not lifetime. Churn is counted over the last N commits of the default
branch, the same N for every repository, so a 15-year-old project and a
6-month-old one are compared on equal terms. Raw lifetime commit counts would
just measure project age.

Self-check: `python vibebench/03_principles/churn.py` (builds a throwaway local
git repo; no network).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

# Commits of the default branch to look back over. 500 matches the depth the
# tier-3 resolver already uses, so most repositories in the corpus need no extra
# fetching at all, and the ones that do are deepened to the same figure.
CHURN_WINDOW = 500

UNMEASURED = None   # explicit: "we could not look", NEVER 0


def _git(args: list[str], cwd: Path):
    try:
        return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=C.GIT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        for lock in (cwd / ".git").glob("**/*.lock"):
            try:
                lock.unlink()
            except OSError:
                pass
        return None


def default_branch(d: Path) -> str | None:
    r = _git(["ls-remote", "--symref", "origin", "HEAD"], d)
    if r is None or r.returncode != 0:
        return None
    for ln in r.stdout.splitlines():
        if ln.startswith("ref:"):
            return ln.split()[1].rsplit("/", 1)[-1]
    return None


def ensure_history(d: Path, window: int = CHURN_WINDOW) -> str | None:
    """Fetch the default branch to `window` commits. Returns the local ref.

    THE EQUALISER. Called for EVERY repository regardless of how its PRs were
    reconstructed, so churn depth does not inherit the agent-correlated tier mix.
    Returns None when the branch cannot be fetched, and the caller must then
    record the repository as unmeasured rather than as zero.
    """
    base = default_branch(d)
    if not base:
        return None
    r = _git(["fetch", "--depth", str(window), "-q", "origin",
              f"{base}:refs/vb/churn"], d)
    if r is None or r.returncode != 0:
        # A forced update is normal when the ref already exists at another depth.
        r = _git(["fetch", "--depth", str(window), "-q", "-f", "origin",
                  f"{base}:refs/vb/churn"], d)
        if r is None or r.returncode != 0:
            return None
    chk = _git(["rev-parse", "--verify", "refs/vb/churn"], d)
    return "refs/vb/churn" if chk is not None and chk.returncode == 0 else None


def file_churn(d: Path, window: int = CHURN_WINDOW,
               ref: str | None = None) -> dict[str, int] | None:
    """{repo-relative path: commits touching it} over the last `window` commits.

    Returns None (UNMEASURED) if the history is unavailable. Merge commits are
    EXCLUDED (`--no-merges`): a merge touches every file of the branch it
    integrates, which would credit an entire release to one "change".
    """
    ref = ref or ensure_history(d, window)
    if ref is None:
        return None
    r = _git(["log", "--no-merges", "--format=%H", "--name-only",
              "-n", str(window), ref], d)
    if r is None or r.returncode != 0:
        return None
    counts: dict[str, int] = {}
    seen_in_commit: set[str] = set()
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            seen_in_commit.clear()
            continue
        if len(line) == 40 and all(c in "0123456789abcdef" for c in line):
            seen_in_commit.clear()
            continue
        if line in seen_in_commit:      # a rename prints two paths for one commit
            continue
        seen_in_commit.add(line)
        counts[line] = counts.get(line, 0) + 1
    return counts


def n_commits_seen(d: Path, window: int = CHURN_WINDOW,
                   ref: str | None = None) -> int | None:
    """How many commits the window actually contained.

    Reported beside every churn figure. A repository whose whole history is 40
    commits and one truncated to 500 are not comparable on raw counts, so the
    denominator has to travel with the numerator.
    """
    ref = ref or ensure_history(d, window)
    if ref is None:
        return None
    r = _git(["rev-list", "--no-merges", "--count", "-n", str(window), ref], d)
    if r is None or r.returncode != 0:
        return None
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


def coverage_by_agent(rows) -> None:
    """Print churn coverage per agent. MANDATORY before and after any backfill.

    `rows` is an iterable of dicts with `agent` and `churn` (None = unmeasured).
    A gap that is uniform across agents costs recall; a gap that is agent-skewed
    silently rewrites the ranking, which is the actual result.
    """
    import pandas as pd
    d = pd.DataFrame(list(rows))
    if not len(d) or "agent" not in d.columns:
        print("[churn] nothing to report")
        return
    t = (d.assign(_ok=d["churn"].notna())
          .groupby("agent")["_ok"].agg(["size", "sum"]))
    t["pct"] = (t["sum"] / t["size"] * 100).round(1)
    print(t.rename(columns={"size": "rows", "sum": "measured"}).to_string())
    spread = float(t["pct"].max() - t["pct"].min())
    print("[churn] per-agent coverage spread: %.1f pp%s" % (
        spread,
        "   <-- NOT USABLE: a >15pp spread means churn is measured for one agent"
        " more than another, so any churn-based metric would encode that gap "
        "as a quality difference. Deepen the missing repos or drop the metric."
        if spread > 15 else "   <-- parity, churn is usable"))


if __name__ == "__main__":
    import shutil
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="vb_churn_"))
    try:
        _git(["init", "-q", "-b", "main"], tmp)
        _git(["config", "user.email", "t@t"], tmp)
        _git(["config", "user.name", "t"], tmp)
        # hot.txt is edited in 3 commits, cold.txt in 1.
        for i in range(3):
            (tmp / "hot.txt").write_text("v%d\n" % i, encoding="utf-8")
            if i == 0:
                (tmp / "cold.txt").write_text("x\n", encoding="utf-8")
            _git(["add", "-A"], tmp)
            _git(["commit", "-q", "-m", "c%d" % i], tmp)

        c = file_churn(tmp, window=50, ref="HEAD")
        assert c is not None, "history is present, churn must not be None"
        assert c["hot.txt"] == 3, c
        assert c["cold.txt"] == 1, c
        print("  [ok  ] per-file commit counts")

        n = n_commits_seen(tmp, window=50, ref="HEAD")
        assert n == 3, n
        print("  [ok  ] window denominator travels with the numerator")

        # A path that was never touched must be ABSENT, not zero: the caller has
        # to be able to tell "not in this window" from "measured as zero".
        assert "never.txt" not in c
        print("  [ok  ] untouched paths are absent, not zero")

        # UNMEASURED must be None and must never be confused with 0.
        empty = Path(tempfile.mkdtemp(prefix="vb_churn_empty_"))
        try:
            _git(["init", "-q"], empty)
            assert file_churn(empty, window=50, ref="refs/does/not/exist") is None
            print("  [ok  ] unmeasurable history returns None, never 0")
        finally:
            shutil.rmtree(empty, ignore_errors=True)

        coverage_by_agent([
            {"agent": "A", "churn": 3}, {"agent": "A", "churn": 1},
            {"agent": "B", "churn": None}, {"agent": "B", "churn": None},
        ])
        print("\nchurn.py self-check ALL PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
