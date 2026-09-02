"""
Step 2 - Clone harness + AI-touched-file extraction.

For each PR in the gold set we need the AI's version of each touched source file.
We fetch the PR head commit (`refs/pull/<n>/head`, which GitHub retains even after
the branch is deleted; fallback: the merge SHA) into a per-repo local git dir, then
extract each touched file's blob with `git show <sha>:<path>` -- no full working-tree
checkout, so disk stays light.

Outputs (per language profile):
  corpus/ai_files[_ts]/<pr_id>/<original/relative/path>   extracted file content
  corpus/clone_manifest[_ts].parquet                      one row per (pr, file)

files_source == "git_diff" (NO AGENT IS NAMED HERE, and that is deliberate):
when the dataset carries no usable file list for a PR, the touched-file list is
derived from git itself. Three routes are tried in order, the first that works
wins, and the result is cached in <out_dir>/_touched.json so re-runs stay
resumable without network:
  1. merge commit at --depth 2, `git diff --name-only merge_sha^1 merge_sha`
     (merge commits diff against the base branch at merge time, squash merges
     against their single parent; both yield exactly the PR's landed change);
  2. the PR's own commit set, unioning each non-merge commit's diff;
  3. `refs/pull/<N>/head` versus its merge-base with the default branch, which
     is the ONLY route available to a PR that was never merged, and which
     GitHub serves even after the source branch or fork is deleted.
Route 3 is what makes V2's rejected and open cells possible at all.

WHICH AGENT NEEDS THIS CHANGES BETWEEN DATASET RELEASES. Under AIDev-full it
was Copilot that had no filenames; under AIDev-7.6M Copilot is 97.3% non-null
and CLAUDE_CODE is the one at 2.3%. Selecting the fallback by agent name was a
real defect in this pipeline (removed 2026-08-19) and it cost a 40x artificial
suppression of one agent in Java. Route on the data, never on identity.

Parallel (2026-07 perf pass): repos are fetched concurrently
(config.FETCH_WORKERS threads -- network-bound, so threads are the right tool),
but all PRs of ONE repo run sequentially on one thread, because a git dir does
not tolerate concurrent git commands. Rows are collected under a lock and
checkpointed periodically, so the run stays resumable exactly like before.

Resumable: PRs whose files are already extracted are skipped.

Run:  python vibebench/02_clone/clone_and_extract.py               # Python corpus
      python vibebench/02_clone/clone_and_extract.py --lang ts     # TypeScript corpus
      python vibebench/02_clone/clone_and_extract.py --limit 5     # smoke test
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "03_principles"))
import config as C  # noqa: E402
import safeio  # noqa: E402  -- crash-safe writes (power/network cuts)
import gitsnap  # noqa: E402

# Seconds per git command -- a single slow/huge repo must not stall the batch,
# but 60s proved too short for mlflow-sized PR-head fetches (see config.py).
GIT_TIMEOUT = C.GIT_TIMEOUT_S

# Language profiles (mirrors 01_curate/curate.py). "py" reproduces the original
# behaviour; "ts" adds the .d.ts + generated/vendored-path exclusions.
LANG_PROFILES = {
    "py": {
        "extensions": C.SOURCE_EXTENSIONS,
        "excluded_parts": (),
        "gold_set": C.GOLD_SET,
        "extract_dir": C.EXTRACT_DIR,
        "manifest": C.CLONE_MANIFEST,
        "label": "py",
    },
    "ts": {
        "extensions": C.TS_SOURCE_EXTENSIONS,
        "excluded_parts": C.TS_EXCLUDED_PATH_PARTS,
        "gold_set": C.GOLD_SET_TS,
        "extract_dir": C.EXTRACT_DIR_TS,
        "manifest": C.CLONE_MANIFEST_TS,
        "label": "ts",
    },
    "java": {
        "extensions": C.JAVA_SOURCE_EXTENSIONS,
        "excluded_parts": C.JAVA_EXCLUDED_PATH_PARTS,
        "gold_set": C.GOLD_SET_JAVA,
        "extract_dir": C.EXTRACT_DIR_JAVA,
        "manifest": C.CLONE_MANIFEST_JAVA,
        "label": "java",
    },
}


def is_source_file(path: str, profile: dict) -> bool:
    """In-scope source file for this profile (.d.ts + vendored paths excluded).

    DELEGATES to gitsnap so extraction and the five delta harnesses can never
    disagree about what is in scope. It was duplicated here, and both copies
    carried the same substring-matching defect; a divergence between them would
    have been far worse than the defect itself, because analysis would then be
    reading files extraction never wrote.
    """
    return gitsnap.is_source_file(path, profile["extensions"],
                                  profile["excluded_parts"])


def write_atomic(path: Path, blob: bytes) -> None:
    """Write `blob` to `path` so a crash can never leave a TRUNCATED file.

    `write_bytes` is not atomic: a power cut or a killed process mid-write
    leaves a short file that is perfectly valid to every later reader, so the
    corpus would silently contain half-files and every metric computed on them
    would be wrong with no error anywhere. Writing to a temp file in the same
    directory and then `os.replace` makes the swap atomic on NTFS and POSIX
    alike -- a reader sees either the old file or the whole new one.
    """
    tmp = path.with_name(path.name + ".part")
    with open(tmp, "wb") as fh:
        fh.write(blob)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


# Network-outage handling lives in gitsnap (the shared git layer) so the clone
# stage and all five delta harnesses behave identically during an outage.
network_is_up = gitsnap.network_is_up
wait_for_network = gitsnap.wait_for_network


class _TimedOut:
    """Minimal CompletedProcess stand-in so callers can just check .returncode."""
    returncode = 124
    stdout = b""
    stderr = b"timeout"


def _clear_stale_locks(cwd: Path | None) -> None:
    """Remove git lock files left behind by a timeout-killed git process.

    When we kill git on timeout (esp. on Windows, where child transport
    processes may linger), .git/shallow.lock or similar survives and makes
    EVERY later git command in that repo fail instantly with "Another git
    process seems to be running". This silently poisoned all subsequent
    mlflow/langflow fetches in the first full run. Safe here because each
    clone dir is owned by exactly one worker thread at a time (repos are the
    unit of parallelism).
    """
    if cwd is None:
        return
    git_dir = Path(cwd) / ".git"
    if git_dir.exists():
        for lock in git_dir.rglob("*.lock"):
            try:
                lock.unlink()
            except OSError:
                pass


def sweep_stale_locks(clones_dir: Path) -> int:
    """Clear git lock files left behind by a HARD STOP, at start-up.

    `_clear_stale_locks` only fires when WE time a git process out. It cannot
    fire when the machine loses power or is rebooted mid-fetch, and this project
    runs on a desktop where both happen. A `.git/shallow.lock` that survives a
    reboot makes every later git command in that repository fail instantly with
    "Another git process seems to be running", so the repo is not retried, it is
    permanently broken for the rest of the run -- and the failures are recorded
    as ordinary fetch failures, which is the silent, agent-biased data loss this
    pipeline keeps having to hunt down.

    Safe to run unconditionally at start-up: if a lock were genuinely held by a
    live git process, that process belongs to a run that is no longer going,
    because this sweep happens before any worker starts.
    """
    n = 0
    if not clones_dir.exists():
        return 0
    for lock in clones_dir.glob("*/.git/**/*.lock"):
        try:
            lock.unlink(); n += 1
        except OSError:
            pass
    for lock in clones_dir.glob("*/.git/*.lock"):
        try:
            lock.unlink(); n += 1
        except OSError:
            pass
    return n


def run_git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", *args], **gitsnap.NO_WINDOW,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        _clear_stale_locks(cwd)
        return _TimedOut()


def run_git_net(args: list[str], cwd: Path | None = None):
    """`run_git` for a NETWORK subcommand: pause on an outage, then retry once.

    See gitsnap's network-outage block for why. In short, git fails in seconds
    when the connection drops rather than hanging, so without this an outage
    does not pause the run, it races through the queue marking everything
    failed. Distinguishing "the internet went away" from "this genuinely
    failed" is the whole point; a failure that persists once connectivity is
    confirmed is returned untouched.
    """
    r = run_git(args, cwd)
    if r.returncode == 0 or network_is_up():
        return r
    wait_for_network()
    return run_git(args, cwd)


def repo_dir(full_name: str) -> Path:
    return C.CLONES_DIR / full_name.replace("/", "__")


def ensure_repo(full_name: str) -> Path:
    """Init a local git dir with the GitHub remote (no history fetched yet)."""
    d = repo_dir(full_name)
    if not (d / ".git").exists():
        d.mkdir(parents=True, exist_ok=True)
        run_git(["init", "-q"], cwd=d)
        run_git(["remote", "add", "origin", f"https://github.com/{full_name}.git"], cwd=d)
    return d


def fetch_pr_head(d: Path, pr_number: int, merge_sha: str | None) -> str | None:
    """Fetch the PR head commit; return the resolved SHA or None on failure."""
    ref = f"refs/pull/{int(pr_number)}/head"
    local = f"refs/vb/pr{int(pr_number)}"
    r = run_git_net(["fetch", "--depth", "1", "-q", "origin", f"{ref}:{local}"], cwd=d)
    if r.returncode == 0:
        rev = run_git(["rev-parse", local], cwd=d)
        if rev.returncode == 0:
            return rev.stdout.strip()
    # Fallback: fetch the merge commit directly.
    if isinstance(merge_sha, str) and merge_sha:
        r2 = run_git_net(["fetch", "--depth", "1", "-q", "origin", merge_sha], cwd=d)
        if r2.returncode == 0:
            return merge_sha
    return None


def derive_touched_files(d: Path, merge_sha: str | None,
                         profile: dict) -> list[str] | None:
    """ROUTE 1: source files changed by the PR, from the merge-commit diff.

    Merged PRs only. Returns None when there is no merge SHA, which is the case
    for every rejected and open PR and for most merged PRs on AIDev-7.6M (whose
    pr_timeline covers 2.6% of the dataset), so the caller falls through.

    Fetches the merge commit at depth 2 (tip + parents) and diffs against the
    first parent. Returns None if the merge commit can't be fetched/diffed.
    """
    if not (isinstance(merge_sha, str) and merge_sha):
        return None
    r = run_git_net(["fetch", "--depth", "2", "-q", "origin", merge_sha], cwd=d)
    if r.returncode != 0:
        return None
    diff = run_git(["diff", "--name-only", "--diff-filter=ACMR",
                    f"{merge_sha}^1", merge_sha], cwd=d)
    if diff.returncode != 0:
        return None
    files = [ln.strip() for ln in diff.stdout.splitlines() if ln.strip()]
    if not files:
        # Third instance of the same defect (all fixed 2026-09-01). A merge
        # commit whose first-parent diff is empty is a resolution problem (a
        # graft artefact of the depth-2 fetch, or a merge SHA that does not
        # belong to this PR), never a description of the PR's content. Return
        # None so the caller falls through to routes 2 and 3 instead of caching
        # "no files" and dropping the PR.
        return None
    return sorted(f for f in files if is_source_file(f, profile))


def derive_via_pull_ref(d: Path, pr, profile: dict) -> tuple[str, list[str]] | None:
    """TIER 3 (added 2026-08-17): resolve the PR from `refs/pull/<N>/head`.

    WHY. The two tiers above need a merge SHA or the PR's own commit SHAs, and
    AIDev-7.6M supplies NEITHER for 97.4% of its PRs -- `pr_timeline` and
    `pr_commits` both stop at the ~197k ">100 star" AIDev-pop subset. Copilot is
    hit hardest because it has no dataset file list at all and must resolve
    everything from git.

    MEASURED CONSEQUENCE, and the reason this is not optional: on the first pass
    Copilot lost 43.4% of its PRs while OpenAI_Codex lost 0.2%, AND the loss was
    NOT random -- failed Copilot PRs had a median 93 stars against 455 for
    survivors, shifting Copilot's median repo popularity 246 -> 455 (+85%) while
    Codex's did not move at all (+0%). That is a DIFFERENTIAL, popularity-linked
    selection effect introduced by the pipeline itself, on top of the agent
    imbalance this whole re-run exists to remove.

    gitsnap.delta_pair already implements all three tiers (merge pair -> commit
    set -> pull ref, the last resolving the branch point with `merge-base` so a
    multi-commit PR is not truncated to its final commit). Reusing it here keeps
    the clone stage and the delta harnesses on ONE resolution path instead of two
    that can silently disagree.
    """
    pair = gitsnap.delta_pair(d, pr)
    if pair is None:
        return None
    before, after = pair
    diff = run_git(["diff", "--name-only", "--diff-filter=ACMR", before, after], cwd=d)
    if diff is None or diff.returncode != 0:
        return None
    files = [ln.strip() for ln in diff.stdout.splitlines() if ln.strip()]
    if not files:
        # AN EMPTY RAW DIFF IS A RESOLUTION FAILURE, NOT AN ANSWER (2026-09-01).
        # `git diff A B` exiting 0 with no output means A and B are the same
        # tree, i.e. the pair we resolved does not describe this PR. Returning
        # `(after, [])` here made that indistinguishable from "this PR really
        # touches no Java", and the caller caches any non-None result, so the
        # PR was recorded as `no_touched_files` and silently dropped.
        #
        # MEASURED COST of getting this wrong: 2,819 PRs cached an empty list.
        # Sampling 40 of them against the GitHub API, 10 (25%) DO change Java
        # files -- qaiu/netdisk-fast-download#162 changes 208 of them and the
        # local diff for our resolved pair returns zero. That is roughly 700
        # analysable PRs, about 18% of the corpus, lost to a `[]` that looked
        # like data.
        #
        # A real "no Java in this PR" still returns an empty list below, because
        # `files` is non-empty in that case and only the .java filter empties it.
        # The two are now distinguishable, which is the whole point.
        return None
    return after, sorted(f for f in files if is_source_file(f, profile))


def derive_from_commit_set(d: Path, shas: list[str],
                           profile: dict) -> tuple[str, list[str]] | None:
    """ROUTE 2: reconstruct the PR's touched files from its own commit SHAs.

    Used when the timeline has no merge SHA. Applies to any agent; the count
    that used to be quoted here ("48 of 150 Copilot PRs") described AIDev-full
    and does not describe this dataset.

    Two shallow-clone traps learned the hard way:
      * Parent SHAs must be read with `git cat-file -p` -- `git rev-list`
        applies the shallow graft and reports NO parents at depth 1, which
        made every commit look like both a tip and a root.
      * A base..head TREE diff is wrong whenever the agent merged main into
        its branch mid-PR (common for Copilot): the diff would then include
        everything main gained meanwhile. Instead, union each NON-MERGE
        commit's diff against its first parent -- merge commits are skipped,
        so main's concurrent changes never pollute the attribution.

    head (for extraction) = the unique commit that is no other member's
    parent. Returns (head_sha, touched_source_files) or None if the commit
    set is unfetchable (force-pushed/stale SHAs) or the tip is ambiguous.
    """
    if not shas:
        return None
    r = run_git_net(["fetch", "--depth", "1", "-q", "origin", *shas], cwd=d)
    if r.returncode != 0:
        return None

    parents: dict[str, list[str]] = {}
    for sha in shas:
        cf = run_git(["cat-file", "-p", sha], cwd=d)
        if cf.returncode != 0:
            return None
        parents[sha] = [ln.split()[1] for ln in cf.stdout.splitlines()
                        if ln.startswith("parent ")]

    in_set = set(shas)
    all_parents = {p for ps in parents.values() for p in ps}
    tips = [s for s in shas if s not in all_parents]
    if len(tips) != 1:
        return None
    head = tips[0]

    files: set[str] = set()

    any_diff_ran = False
    for sha in shas:
        if len(parents[sha]) != 1:
            continue  # merge commit (or orphan root): not the agent's own change
        p = parents[sha][0]
        if p not in in_set:
            # External parent (the PR's branch point): fetch it for the diff.
            if run_git(["cat-file", "-e", f"{p}^{{commit}}"], cwd=d).returncode != 0:
                if run_git(["fetch", "--depth", "1", "-q", "origin", p],
                           cwd=d).returncode != 0:
                    continue  # unfetchable ancient base: skip this commit's diff
        diff = run_git(["diff", "--name-only", "--diff-filter=ACMR", p, sha], cwd=d)
        if diff.returncode != 0:
            continue
        raw = [f.strip() for f in diff.stdout.splitlines() if f.strip()]
        any_diff_ran = any_diff_ran or bool(raw)
        files.update(f for f in raw if is_source_file(f, profile))
    if not any_diff_ran:
        # SAME DEFECT AS derive_via_pull_ref (fixed 2026-09-01). Every commit
        # here can `continue` -- merge commit, unfetchable parent, non-zero
        # diff -- and the function would still return an empty list, which the
        # caller caches as the authoritative answer "this PR touches no Java".
        # If not one diff produced a single path, we learned nothing about this
        # PR and must say so rather than assert an empty result.
        return None
    return head, sorted(files)


def commit_shas(pr) -> list[str]:
    """The PR's own commit SHAs, as a plain list.

    Parquet list columns come back as numpy arrays -- never truth-test them
    directly ("ambiguous" ValueError) -- and the column is a scalar NaN for
    rows the dataset had no commit detail for.
    """
    raw = getattr(pr, "commit_shas", None)
    try:
        return [s for s in raw] if raw is not None else []
    except TypeError:
        return []


def extract_file(d: Path, sha: str, path: str) -> bytes | None:
    """Return the file blob at <sha>:<path>, or None if absent/timed out."""
    try:
        r = subprocess.run(
            ["git", "show", f"{sha}:{path}"],
            cwd=str(d), capture_output=True, timeout=GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return None
    return r.stdout if r.returncode == 0 else None


def process_pr(pr, profile: dict, tag: str) -> list[dict]:
    """All manifest rows for one PR. Never raises: errors become status rows."""
    out_dir = profile["extract_dir"] / str(pr.pr_id)
    needs_diff = getattr(pr, "files_source", "dataset") == "git_diff"
    touched_cache = out_dir / "_touched.json"

    if needs_diff:
        # No dataset file list: it comes from git, cached across runs.
        touched = (json.loads(touched_cache.read_text())
                   if touched_cache.exists() else None)
    else:
        touched = [f for f in pr.touched_files if is_source_file(f, profile)]

    done = (touched is not None and out_dir.exists()
            and all((out_dir / f).exists() for f in touched))

    n_desc = "?" if touched is None else len(touched)
    print(f"{tag} {pr.agent:<12} {pr.full_name}#{pr.pr_number} "
          f"({n_desc} {profile['label']}){' [cached]' if done else ''}", flush=True)

    rows: list[dict] = []
    try:
        if done:
            head_sha = "cached"
        else:
            d = ensure_repo(pr.full_name)
            head_sha = fetch_pr_head(d, pr.pr_number, pr.merge_sha)
            if needs_diff and touched is None:
                touched = derive_touched_files(d, pr.merge_sha, profile)
                if touched is None:
                    got = derive_from_commit_set(d, commit_shas(pr), profile)
                    if got is None:
                        got = derive_via_pull_ref(d, pr, profile)   # tier 3
                    if got is not None:
                        derived_head, touched = got
                        head_sha = head_sha or derived_head
                if touched is not None:
                    out_dir.mkdir(parents=True, exist_ok=True)
                    safeio.write_json(touched_cache, touched)
            elif head_sha is None:
                # The PR ref and the merge SHA are both gone (branch deleted,
                # force-pushed, or the timeline never recorded a merge). The
                # PR's own commits still pin the head, and curate.py now
                # attaches them for EVERY agent -- without this fallback the
                # loss is agent-biased (Devin 7.8% / Cursor 7.4% / Codex 0%),
                # which is exactly the bias the merge-SHA fix removed from the
                # delta harnesses. The touched list stays the dataset's.
                got = derive_from_commit_set(d, commit_shas(pr), profile)
                if got is None:
                    got = derive_via_pull_ref(d, pr, profile)       # tier 3
                if got is not None:
                    head_sha = got[0]

        if needs_diff and touched is None:
            return [{
                "pr_id": pr.pr_id, "agent": pr.agent, "full_name": pr.full_name,
                "pr_number": pr.pr_number,
                "outcome": getattr(pr, "outcome", "merged"),
                "rejection_stratum": getattr(pr, "rejection_stratum", "merged"),
                "head_sha": head_sha or "",
                "file_path": "", "local_path": "",
                "extract_status": "diff_failed", "n_bytes": 0,
            }]
        # `not touched` is checked for BOTH routes (2026-08-19). It used to be
        # guarded only under `needs_diff`, so a `dataset` row that filtered down
        # to zero in-scope files fell through to the loop below, produced no
        # manifest rows at all, and vanished from the run with no status to count
        # it by. Curation pre-filters dataset rows so this should be empty, but a
        # silent disappearance is not an acceptable failure mode for the one
        # table the whole pipeline reconciles against.
        if not touched:
            return [{
                "pr_id": pr.pr_id, "agent": pr.agent, "full_name": pr.full_name,
                "pr_number": pr.pr_number,
                "outcome": getattr(pr, "outcome", "merged"),
                "rejection_stratum": getattr(pr, "rejection_stratum", "merged"),
                "head_sha": head_sha or "",
                "file_path": "", "local_path": "",
                "extract_status": "no_source_files", "n_bytes": 0,
            }]

        for path in touched:
            local_path = out_dir / path
            status, nbytes = "ok", 0
            if done or local_path.exists():
                status, nbytes = "ok", local_path.stat().st_size
            elif head_sha in (None, "cached"):
                status = "fetch_failed"
            else:
                blob = extract_file(repo_dir(pr.full_name), head_sha, path)
                if blob is None:
                    status = "file_missing"
                else:
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    write_atomic(local_path, blob)
                    status, nbytes = "ok", len(blob)

            rows.append({
                "pr_id": pr.pr_id, "agent": pr.agent, "full_name": pr.full_name,
                "pr_number": pr.pr_number,
                "outcome": getattr(pr, "outcome", "merged"),
                "rejection_stratum": getattr(pr, "rejection_stratum", "merged"),
                "head_sha": head_sha or "",
                "file_path": path, "local_path": str(local_path),
                "extract_status": status, "n_bytes": nbytes,
            })
    except Exception as e:  # noqa: BLE001 -- a single bad repo must not abort the batch
        print(f"  !! error on {pr.full_name}#{pr.pr_number}: {e}", flush=True)
        for path in (touched or [""]):
            rows.append({
                "pr_id": pr.pr_id, "agent": pr.agent, "full_name": pr.full_name,
                "pr_number": pr.pr_number,
                "outcome": getattr(pr, "outcome", "merged"),
                "rejection_stratum": getattr(pr, "rejection_stratum", "merged"),
                "head_sha": "",
                "file_path": path, "local_path": str(out_dir / path),
                "extract_status": "error", "n_bytes": 0,
            })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=sorted(LANG_PROFILES), default="py",
                    help="language profile (default: py)")
    ap.add_argument("--limit", type=int, default=0, help="process only first N PRs")
    ap.add_argument("--workers", type=int, default=C.FETCH_WORKERS,
                    help="concurrent repos (1 = sequential, original behaviour)")
    ap.add_argument("--outcome", default="",
                    help="V2: restrict to one outcome (merged|rejected|open). "
                         "Rows for PRs NOT processed this run are carried over "
                         "from the existing manifest, never dropped.")
    ap.add_argument("--recheck", action="store_true",
                    help="ignore the manifest and re-derive every PR's state from "
                         "the filesystem (slow; use if files changed on disk)")
    ap.add_argument("--sample", type=int, default=0,
                    help="seeded random N PRs (smoke test; --limit takes the head, "
                         "which is sorted by agent and would test one agent only)")
    args = ap.parse_args()
    profile = LANG_PROFILES[args.lang]

    # A full disk turns every write into a silent failure while the run keeps
    # going, and the symptom is indistinguishable from a network fault. Stop
    # up-front instead, with the corpus still complete and resumable.
    print("[clone] free disk: %.1f GB" % safeio.check_disk(), flush=True)
    gold_all = pd.read_parquet(profile["gold_set"])
    gold = gold_all
    if args.outcome:
        assert "outcome" in gold.columns, "gold set predates V2: no `outcome` column"
        gold = gold[gold["outcome"] == args.outcome]
        assert len(gold), "no PRs with outcome=%s" % args.outcome
    if args.sample:
        gold = gold.sample(min(args.sample, len(gold)), random_state=C.RANDOM_SEED)
    if args.limit:
        gold = gold.head(args.limit)

    C.CLONES_DIR.mkdir(parents=True, exist_ok=True)
    profile["extract_dir"].mkdir(parents=True, exist_ok=True)
    # Clear locks left by a previous run that was killed hard (reboot, power
    # cut, task kill). Must happen BEFORE any worker starts -- see
    # sweep_stale_locks.
    swept = sweep_stale_locks(C.CLONES_DIR)
    if swept:
        print("[clone] swept %d stale git lock(s) from a previous hard stop"
              % swept, flush=True)

    # Repos are the unit of parallelism: one thread owns one git dir at a time.
    groups = list(gold.groupby("full_name", sort=False))
    n_prs = len(gold)
    print(f"[clone] ({args.lang}) {n_prs} PRs across {len(groups)} repos, "
          f"{args.workers} workers", flush=True)

    # CARRY-OVER (V2). main() used to rebuild `rows` from scratch and overwrite
    # the manifest wholesale, which is safe only when every PR of the gold set is
    # processed in one run. With --outcome / --sample it is NOT: a partial run
    # would silently delete the manifest rows of every PR it did not visit, and
    # the manifest is the one table the whole pipeline reconciles against.
    # Rows for PRs outside this run's selection are therefore preserved.
    # Scoped to the CURRENT gold set on both sides: a row is carried only if its
    # PR is still in the corpus AND was not visited this run. Without the first
    # condition a re-curation would leave the previous corpus's PRs in the
    # manifest forever, and every downstream count would be taken over a corpus
    # that no longer exists.
    carried: list[dict] = []
    settled: set = set()
    if Path(profile["manifest"]).exists():
        prev = pd.read_parquet(profile["manifest"])
        in_corpus = set(gold_all["pr_id"])
        this_run = set(gold["pr_id"])
        keep = prev[prev["pr_id"].isin(in_corpus) & ~prev["pr_id"].isin(this_run)]
        carried = keep.to_dict("records")

        # FAST RESUME (2026-08-26). Previously a re-run re-derived every PR's
        # state from the FILESYSTEM, stat-ing all ~80,000 extracted files to
        # discover the few hundred that still needed work. That made a pass whose
        # real workload was ~900 PRs take hours, and it is why "fixing 500 PRs"
        # looked like "downloading everything again".
        #
        # The manifest already records the answer. A PR is SETTLED when it has at
        # least one successfully extracted file, or when its only status is
        # `no_source_files` (it genuinely touches none of this language, which is
        # a real result, not a failure). Settled PRs are carried over untouched
        # and never visited.
        #
        # NOT settled, and therefore retried: fetch_failed, diff_failed, error,
        # and file_missing-only PRs. Those are exactly the transient states this
        # project refuses to freeze, because freezing them has repeatedly turned
        # into silent, agent-biased data loss.
        #
        # `--recheck` forces the old filesystem-verifying behaviour, for when the
        # files on disk may have changed under the manifest.
        if not args.recheck:
            mine = prev[prev["pr_id"].isin(this_run)]
            if len(mine):
                agg = mine.groupby("pr_id")["extract_status"].agg(
                    ok=lambda s: (s == "ok").any(),
                    only_none=lambda s: set(s) == {"no_source_files"})
                # `no_source_files` COUNTS AS SETTLED ONLY IF ITS EVIDENCE STILL
                # EXISTS (2026-09-01). The claim "this PR touches no Java" rests
                # entirely on the derived file list in `_touched.json`. When the
                # degenerate-pair bug was found, those caches were deleted
                # precisely because the lists in them were not trustworthy -- yet
                # fast resume kept carrying the CONCLUSION forward and never
                # revisited the PR, so 2,855 of 2,948 recovered PRs were skipped
                # by a run whose whole purpose was to recover them.
                #
                # A conclusion whose evidence has been withdrawn is not settled.
                # For a `git_diff` PR the cache file IS the evidence, so require
                # it; dataset-sourced PRs carry their list in the gold set and
                # are unaffected.
                have_cache = {
                    pid for pid in agg.index[agg["only_none"]]
                    if (Path(profile["extract_dir"]) / str(pid) /
                        "_touched.json").exists()}
                only_none_ok = agg["only_none"] & agg.index.isin(have_cache)
                settled = set(agg.index[agg["ok"] | only_none_ok])
                if settled:
                    carried += mine[mine["pr_id"].isin(settled)].to_dict("records")
                    print("[clone] fast resume: %d PRs already settled in the "
                          "manifest, skipping them entirely (use --recheck to "
                          "re-verify against disk)" % len(settled), flush=True)
        if carried:
            print("[clone] carrying over %d manifest rows for %d PRs outside this "
                  "run's selection" % (len(carried), keep["pr_id"].nunique()),
                  flush=True)

    rows: list[dict] = list(carried)
    rows_lock = threading.Lock()
    done_prs = 0

    # PENDING ROWS FROM THE PREVIOUS MANIFEST (2026-08-26 -- fixes real data loss).
    #
    # Checkpoints used to write ONLY the rows produced so far in this run. So a
    # run killed at PR 1,400 of 6,956 wrote a manifest containing 1,400 PRs and
    # SILENTLY DISCARDED the record of the other 4,700 that a previous pass had
    # already completed. The extracted files survived on disk, but the table that
    # says which PR they belong to did not, so the next run had to rediscover all
    # of them -- which is exactly the "why are we doing it all again" problem.
    #
    # Every checkpoint now writes this run's rows PLUS the previous manifest's
    # rows for PRs this run has not yet reached. The manifest is then a superset
    # at every instant, and killing the process at any moment loses nothing.
    pending: dict = {}
    if Path(profile["manifest"]).exists():
        _prev = pd.read_parquet(profile["manifest"])
        for _pid, _grp in _prev[_prev["pr_id"].isin(set(gold["pr_id"]))].groupby("pr_id"):
            pending[_pid] = _grp.to_dict("records")
        for _pid in settled:
            pending.pop(_pid, None)          # already in `carried`
        if pending:
            print("[clone] %d PRs carried from the previous manifest until this "
                  "run reaches them (a kill mid-run now loses nothing)"
                  % len(pending), flush=True)

    def snapshot_rows() -> list[dict]:
        """This run's rows + previous rows for PRs not yet re-processed."""
        out = list(rows)
        for _v in pending.values():
            out.extend(_v)
        return out

    def process_repo(full_name: str, prs: pd.DataFrame) -> list[dict]:
        out: list[dict] = []
        for pr in prs.itertuples(index=False):
            if pr.pr_id in settled:
                continue                      # already carried over
            out.extend(process_pr(pr, profile, tag=f"[{full_name}]"))
        return out

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futures = {ex.submit(process_repo, name, grp): (name, len(grp))
                   for name, grp in groups}
        for fut in as_completed(futures):
            name, n_grp = futures[fut]
            try:
                got = fut.result()
            except Exception as e:  # noqa: BLE001 -- belt and braces; process_pr already catches
                print(f"  !! repo-level error on {name}: {e}", flush=True)
                got = []
            with rows_lock:
                for _r in got:
                    pending.pop(_r["pr_id"], None)   # superseded by a fresh row
                rows.extend(got)
                done_prs += n_grp
                # Checkpoint so a crash doesn't lose earlier repos' progress.
                if done_prs % 25 < n_grp:
                    gitsnap.write_parquet_atomic(pd.DataFrame(snapshot_rows()), profile["manifest"], index=False)
            print(f"[clone] progress: {done_prs}/{n_prs} PRs", flush=True)

    manifest = pd.DataFrame(snapshot_rows())
    gitsnap.write_parquet_atomic(manifest, profile["manifest"], index=False)

    print("\n[clone] manifest written to", profile["manifest"])
    print(f"[clone] files: {len(manifest)}  ok: {(manifest.extract_status=='ok').sum()}")
    print("\nExtract status counts:")
    print(manifest["extract_status"].value_counts().to_string())
    print("\nOK files per agent:")
    print(manifest[manifest.extract_status == "ok"].groupby("agent").size().to_string())

    # STATUS x AGENT, AND STATUS x OUTCOME (V2 acceptance criterion). A status
    # that hits one agent or one outcome disproportionately is a BIAS, not an
    # inconvenience: it silently rewrites the ranking or the merged-vs-rejected
    # contrast, which are the two results this corpus exists to produce.
    per_pr = (manifest.sort_values("extract_status")
                      .groupby("pr_id")
                      .agg(agent=("agent", "first"),
                           outcome=("outcome", "first"),
                           any_ok=("extract_status", lambda s: (s == "ok").any())))
    print("\nPRs with >=1 extracted file, per agent x outcome "
          "(THE per-agent parity check the 25x file-attribution gap demands):")
    t = per_pr.groupby(["agent", "outcome"])["any_ok"].agg(["size", "sum"])
    t["pct"] = (t["sum"] / t["size"] * 100).round(1)
    print(t.rename(columns={"size": "drawn", "sum": "usable"}).to_string())
    spread = t["pct"].max() - t["pct"].min()
    print("[clone] per-cell usable-rate spread: %.1f pp%s"
          % (spread, "  <-- WARNING: >15pp is an agent/outcome-biased loss"
             if spread > 15 else ""))
    print("\nstatus x agent cross-tab (file rows):")
    print(pd.crosstab(manifest["extract_status"], manifest["agent"]).to_string())
    print("\nstatus x outcome cross-tab (file rows):")
    print(pd.crosstab(manifest["extract_status"], manifest["outcome"]).to_string())


if __name__ == "__main__":
    main()
