"""
Shared git-snapshot helpers for the before/after delta harnesses.

This is the factored version of the block that was copy-pasted into
run_dip/run_isp/run_ocp/run_lsp (PROJECT.md 7.C). It is used by the TypeScript
harnesses from day one; the validated Python harnesses migrate later, under an
output-equality check (never mix refactoring with new-feature work).

Two things it adds over the copy-pasted original:

1. **A per-(repo, sha) tree cache** (`config.TREE_CACHE_DIR`). Every delta
   harness needs the SAME before/after trees, so without a cache each new
   principle re-fetches and re-archives every commit. Trees are stored as one
   gzipped JSON per (repo, sha, extension-set); huge trees are analysed but not
   cached (they are rare and would dominate the disk).

2. **Thread safety.** Git tolerates no concurrent commands in one repo dir, so
   every repo has its own lock; parallel callers may work on distinct repos
   freely (the same rule the clone stage uses).

Everything else is preserved exactly, including the two traps that cost real
data earlier in this project:
  * parents are read via `git cat-file -p`, NEVER `rev-parse ^1` (a later
    shallow fetch can re-graft the boundary and break rev-parse);
  * after a git timeout, stale `.git/**/*.lock` files are cleared (a killed git
    poisons every later command in that repo).

Self-check: `python vibebench/03_principles/gitsnap.py` (offline; exercises the
cache round-trip and the path/extension filters, no network).
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

MAX_FILES = 10_000                 # sentinel guard: absurdly large trees
TOO_LARGE = "__TOO_LARGE__"        # sentinel key (same contract as run_dip)
MAX_CACHE_BYTES = 25 * 1024 * 1024  # don't cache trees bigger than this (raw)

_repo_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def repo_lock(full_name: str) -> threading.Lock:
    """One lock per repo dir; git tolerates no concurrent commands in a repo."""
    with _locks_guard:
        if full_name not in _repo_locks:
            _repo_locks[full_name] = threading.Lock()
        return _repo_locks[full_name]


# --------------------------------------------------------------------------
# NETWORK-OUTAGE HANDLING (shared by every stage, 2026-08-26)
# --------------------------------------------------------------------------
# THE DEFECT THIS FIXES, measured rather than imagined. `git fetch` does not
# hang when the connection drops, it fails in SECONDS, far inside its own
# timeout. So an outage does not pause a harness, it makes the harness race
# through its remaining work marking every item failed. A ~10-minute connection
# drop on 2026-08-26 burned ~527 Java PRs that way.
#
# Nothing was corrupted -- a failed fetch writes a STATUS, never content, and
# git objects are content-addressed so a partial transfer cannot produce a valid
# object. But the work had to be redone, and the same outage against a stage
# whose failure statuses were PERMANENT would have been silent data loss frozen
# into the corpus. That is the real hazard, and it is why this lives in the
# shared layer instead of in one script.
#
# Applies to every consumer of run_git: the clone stage and all five delta
# harnesses, plus the arch and cross-check runs.
# WINDOWS CONSOLE WINDOWS. Every subprocess launched from a non-console parent
# on Windows creates its own console window, which flashes on screen. This
# pipeline spawns two JVMs and several git commands PER PR, i.e. tens of
# thousands of flashes across a run that takes most of a day, on a machine
# somebody is also using. CREATE_NO_WINDOW suppresses that; it is Windows-only,
# so it resolves to an empty dict everywhere else and changes nothing.
#
# `clone_and_extract` imports this by name, so removing it breaks that stage
# with an AttributeError rather than merely restoring the flashing.
NO_WINDOW = ({"creationflags": subprocess.CREATE_NO_WINDOW}
             if hasattr(subprocess, "CREATE_NO_WINDOW") else {})

_net_lock = threading.Lock()
_net_last_ok = [0.0]          # monotonic time of the last successful probe


def network_is_up(timeout: float = 4.0) -> bool:
    """Cheap reachability probe against GitHub (TCP 443, no HTTP round-trip)."""
    try:
        with socket.create_connection(("github.com", 443), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_network(max_wait_s: int = 1800, quiet: bool = False) -> bool:
    """Block until GitHub is reachable, up to `max_wait_s`. Thread-safe.

    Only ONE thread probes at a time; the rest wait on the lock and then see a
    fresh result, so sixteen workers do not open sixteen probes a second during
    an outage. Backs off 5s, 10s, 20s, 40s, capped at 60s, so a brief blip costs
    seconds while a long outage costs one probe a minute.
    """
    with _net_lock:
        if time.monotonic() - _net_last_ok[0] < 5.0:
            return True                      # another thread just confirmed it
        waited, delay = 0, 5
        while waited < max_wait_s:
            if network_is_up():
                if waited and not quiet:
                    print("[net] back after %ds, resuming" % waited, flush=True)
                _net_last_ok[0] = time.monotonic()
                return True
            if waited == 0 and not quiet:
                print("[net] connection DOWN -- pausing instead of burning work "
                      "as failures", flush=True)
            time.sleep(delay)
            waited += delay
            delay = min(delay * 2, 60)
        if not quiet:
            print("[net] still down after %ds -- continuing; failures stay "
                  "retryable" % max_wait_s, flush=True)
        return False


# git subcommands that touch the network. Only these are worth pausing for; a
# local `cat-file` or `archive` failing has nothing to do with connectivity and
# retrying it would just hide a real error.
_NETWORK_SUBCOMMANDS = {"fetch", "ls-remote", "clone", "pull", "push"}


def sweep_stale_locks(clones_dir: Path | None = None) -> int:
    """Clear git locks left by a HARD STOP (reboot / power cut), at start-up.

    The in-run cleanup below only fires when WE time git out; it cannot fire
    when the machine dies mid-fetch. A surviving `.git/shallow.lock` makes every
    later git command in that repository fail at once with "Another git process
    seems to be running", so the repository is not retried but permanently
    broken for the rest of the run, and its PRs are recorded as ordinary fetch
    failures. That is silent, repository-correlated -- and therefore
    agent-correlated -- data loss.

    Call once at the start of any stage that touches git. Safe unconditionally:
    no worker has started yet, so no lock here can belong to a live process of
    this run.
    """
    d = Path(clones_dir) if clones_dir else C.CLONES_DIR
    if not d.exists():
        return 0
    n = 0
    for pat in ("*/.git/*.lock", "*/.git/**/*.lock"):
        for lock in d.glob(pat):
            try:
                lock.unlink(); n += 1
            except OSError:
                pass
    return n


def run_git(args: list[str], cwd: Path, binary: bool = False, _retry: bool = True):
    """Run git in `cwd`. Returns None on timeout (after clearing stale locks).

    NETWORK-AWARE: if a network subcommand fails, this waits for connectivity to
    come back and retries ONCE, rather than recording a failure that was really
    an outage. A non-network subcommand, or a failure that persists once the
    connection is confirmed up, is returned as-is -- the point is to distinguish
    "the internet went away" from "this actually failed", not to mask errors.
    """
    def _run():
        try:
            return subprocess.run(
                ["git", *args], cwd=str(cwd), capture_output=True,
                timeout=C.GIT_TIMEOUT_S, **NO_WINDOW,
                **({} if binary else dict(text=True, encoding="utf-8",
                                          errors="replace")),
            )
        except subprocess.TimeoutExpired:
            for lock in (cwd / ".git").glob("**/*.lock"):
                try:
                    lock.unlink()
                except OSError:
                    pass
            return None

    r = _run()
    if not _retry or not args:
        return r
    sub = next((a for a in args if not a.startswith("-")), "")
    if sub not in _NETWORK_SUBCOMMANDS:
        return r
    if r is not None and r.returncode == 0:
        return r
    if network_is_up():
        return r                              # a real failure, not an outage
    wait_for_network()
    return _run()


def write_parquet_atomic(df, path, **kw) -> None:
    """Write a parquet so a POWER CUT can never leave a corrupt file.

    `DataFrame.to_parquet` writes in place. A machine that loses power partway
    through leaves a truncated parquet, and parquet keeps its footer metadata at
    the END of the file, so a truncated one is not "partially readable" -- it is
    UNREADABLE. Losing a checkpoint that way costs the whole run's bookkeeping:
    the extracted files survive on disk, but the manifest that says which PR each
    belongs to, and with what status, does not.

    Writing to a sibling temp file, flushing to the platter, then `os.replace`
    makes the swap atomic. A reader sees either the previous checkpoint or the
    new one, never a half-written file. This is the counterpart of `write_atomic`
    for extracted source files, and it matters more, because one corrupt parquet
    invalidates thousands of good files while one corrupt source file costs one.
    """
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    df.to_parquet(tmp, **kw)
    # Flush the temp file's bytes before the rename, or a crash right after the
    # rename can still leave the directory entry pointing at unwritten data.
    try:
        fd = os.open(tmp, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass
    os.replace(tmp, path)


def repo_dir(full_name: str) -> Path:
    return C.CLONES_DIR / full_name.replace("/", "__")


def ensure_repo(full_name: str) -> Path:
    d = repo_dir(full_name)
    if not (d / ".git").exists():
        d.mkdir(parents=True, exist_ok=True)
        run_git(["init", "-q"], cwd=d)
        run_git(["remote", "add", "origin", f"https://github.com/{full_name}.git"], cwd=d)
    return d


def have_commit(d: Path, sha: str) -> bool:
    r = run_git(["cat-file", "-e", f"{sha}^{{commit}}"], cwd=d)
    return r is not None and r.returncode == 0


def fetch_merge_pair(d: Path, merge_sha: str) -> str | None:
    """Ensure the merge commit AND its first parent are present; return parent.

    The parent is read from the commit header (`git cat-file -p`), never
    `rev-parse ^1`: a later shallow fetch can re-graft the shallow boundary at
    this commit, so rev-parse fails even though the object is present. That
    trap silently cost ISP 24 PRs before it was diagnosed.
    """
    if not have_commit(d, merge_sha):
        r = run_git(["fetch", "--depth", "2", "-q", "origin", merge_sha], cwd=d)
        if r is None or r.returncode != 0:
            return None
    r = run_git(["cat-file", "-p", f"{merge_sha}^{{commit}}"], cwd=d)
    if r is None or r.returncode != 0:
        return None
    parents = [ln.split()[1] for ln in r.stdout.splitlines()
               if ln.startswith("parent ")]
    if not parents:
        return None   # root commit: no "before" state to diff against
    parent = parents[0]
    if not have_commit(d, parent):
        r = run_git(["fetch", "--depth", "1", "-q", "origin", parent], cwd=d)
        if r is None or r.returncode != 0:
            return None
    return parent if have_commit(d, parent) else None


# --------------------------------------------------------------------------
# Source-file selection (shared with curate/clone so all stages agree)
# --------------------------------------------------------------------------
# Directory-component suffixes that mark generated sources (src-gen, model_gen).
# Kept here as the fallback so this module still self-checks when config has not
# been imported by the caller.
_GEN_SUFFIXES = tuple(getattr(C, "GENERATED_DIR_SUFFIXES", ("-gen", "_gen")))


def path_is_excluded(path: str, excluded_parts=()) -> bool:
    """True when `path` lies in generated, build-output or vendored code.

    ENTRIES ENDING IN "/" ARE DIRECTORY COMPONENTS, matched against whole
    components. Anything else is matched inside the FILE NAME only.

    This used to be a plain `part in path` substring test, which excluded any
    path that merely CONTAINED the fragment: "gradlebuild/" contains "build/"
    and "riksdagen/" contains "gen/". Measured over 304,342 .java files in 133
    repositories, that silently discarded 521 real source files, concentrated in
    whole modules of a handful of large repositories and mildly agent-biased --
    a wrong per-agent rate rather than merely a smaller corpus.
    """
    f = path.lower()
    comps = f.split("/")[:-1]                 # directories only, never the file
    name = f.split("/")[-1]
    for part in excluded_parts:
        if part.endswith("/"):
            if part.strip("/") in comps:
                return True
        elif part in name:
            return True
    return any(c.endswith(_GEN_SUFFIXES) for c in comps)


def is_source_file(path: str, extensions, excluded_parts=()) -> bool:
    f = path.lower()
    if f.endswith(".d.ts"):        # declarations carry no implementation
        return False
    if path_is_excluded(f, excluded_parts):
        return False
    return any(f.endswith(ext) for ext in extensions)


def _cache_key(full_name: str, sha: str, extensions, excluded_parts=()) -> str:
    """Cache key for one snapshot tree.

    `excluded_parts` IS part of the key (added 2026-08-27). It was not, so a
    tree cached under one exclusion rule was served unchanged after the rule
    changed -- the cached file set silently wins over the current filter. That
    turned the substring-vs-component repair into a no-op for every repository
    whose tree had already been cached, which is the worst kind of defect: the
    fix appears to be applied and the data does not change. Including the rule
    makes every stale entry miss and rebuild.
    """
    tag = ",".join(sorted(extensions))
    excl = ",".join(sorted(excluded_parts))
    return hashlib.sha1(f"{full_name}@{sha}@{tag}@{excl}".encode()).hexdigest()


def _cache_path(key: str) -> Path:
    return C.TREE_CACHE_DIR / key[:2] / f"{key}.json.gz"


def delta_pair(d: Path, pr, info: dict | None = None) -> tuple[str, str] | None:
    """(before_sha, after_sha) for a PR's before/after delta, or None.

    `info`, when given, receives `{"tier": 1|2|3}` for the tier that produced
    the pair. RECORD IT AND REPORT IT. The three tiers do not construct the same
    "before": tier 1 takes the merge commit's first parent (the base branch AT
    MERGE TIME), while tiers 2 and 3 take the PR's branch point. A merged PR can
    only use tier 1, and a not-merged PR can never use it, so if the tier mix
    differed sharply by outcome the V2 merged-vs-rejected contrast would be
    partly a comparison of RECONSTRUCTION METHOD rather than of code. On
    AIDev-7.6M most merged PRs also fall through to tier 3 (the timeline covers
    2.6% of the dataset), which makes the two sides more symmetric than they
    look -- but that has to be MEASURED per outcome, not assumed.

    Preferred path: the merge commit and its first parent (`fetch_merge_pair`).

    FALLBACK (added 2026-07-28, a real bias fix): ~15% of TS PRs and ~10% of
    Python PRs have NO merge SHA in the AIDev timeline, and the loss is NOT
    random -- it is concentrated in Copilot (49 of 100 in TypeScript), so
    dropping those PRs systematically under-measures one agent. When the PR's
    own commit SHAs are known we reconstruct the pair from them:
        after  = the tip commit (the one that is no other member's parent)
        before = the first parent of the PR's root commit (its branch point)
    Same two shallow-clone traps as elsewhere: parents are read with
    `cat-file -p` (rev-parse hides them behind the graft), and the branch point
    is fetched explicitly because it lies outside the PR's own commit set.
    """
    def _tier(n: int):
        if info is not None:
            info["tier"] = n

    merge_sha = getattr(pr, "merge_sha", None)
    if isinstance(merge_sha, str) and merge_sha:
        parent = fetch_merge_pair(d, merge_sha)
        if parent is not None:
            _tier(1)
            return parent, merge_sha

    # TIER 3 is reached whenever tier 2 cannot produce a pair -- INCLUDING when
    # commit_shas is absent entirely, which is the COMMON case on AIDev-7.6M
    # (97.4% of its PRs have no commit list). Every failure exit below therefore
    # falls through to it. NOTE: an early `return None` here silently disabled
    # the whole fallback when it was first added, so keep these as `_fb()`.
    def _fb():
        got = pull_ref_pair(d, getattr(pr, "pr_number", None))
        _tier(3 if got is not None else 0)
        return got

    raw = getattr(pr, "commit_shas", None)
    try:
        shas = [s for s in raw] if raw is not None else []
    except TypeError:           # scalar NaN for rows without a commit list
        shas = []
    if not shas:
        return _fb()

    r = run_git(["fetch", "--depth", "2", "-q", "origin", *shas], cwd=d)
    if r is None or r.returncode != 0:
        return _fb()

    parents: dict[str, list[str]] = {}
    for sha in shas:
        cf = run_git(["cat-file", "-p", f"{sha}^{{commit}}"], cwd=d)
        if cf is None or cf.returncode != 0:
            return _fb()
        parents[sha] = [ln.split()[1] for ln in cf.stdout.splitlines()
                        if ln.startswith("parent ")]

    in_set = set(shas)
    all_parents = {p for ps in parents.values() for p in ps}
    tips = [s for s in shas if s not in all_parents]
    if len(tips) != 1:
        return _fb()                    # ambiguous history: don't guess
    after = tips[0]

    # Branch point: the single parent that lies OUTSIDE the PR's commit set.
    externals = [p for ps in parents.values() for p in ps if p not in in_set]
    if not externals:
        return _fb()
    before = externals[0]
    if not have_commit(d, before):
        r = run_git(["fetch", "--depth", "1", "-q", "origin", before], cwd=d)
        if r is None or r.returncode != 0:
            return _fb()
    if have_commit(d, before):
        _tier(2)
        return before, after
    return _fb()


def default_branch(d: Path) -> str | None:
    """The remote's default branch name, via ls-remote --symref (one network call)."""
    r = run_git(["ls-remote", "--symref", "origin", "HEAD"], cwd=d)
    if r is None or r.returncode != 0:
        return None
    for ln in r.stdout.splitlines():
        if ln.startswith("ref:"):
            return ln.split()[1].rsplit("/", 1)[-1]
    return None


def pull_ref_pair(d: Path, pr_number) -> tuple[str, str] | None:
    """(before, after) resolved from GitHub's `refs/pull/<N>/head`. LAST RESORT.

    WHY THIS TIER EXISTS (added 2026-08-17). AIDev-7.6M's per-PR tables cover
    only its ~197k ">100 star" AIDev-pop subset: `pr_commit_details`,
    `pr_commits` and `pr_timeline` all stop there, so 97.4% of the dataset's PRs
    have NO file list, NO commit SHAs and NO merge event. Both tiers above are
    therefore blind on them, and the loss is agent-biased -- restricted to
    dataset-supplied SHAs, Claude_Code has only 45 usable Java PRs against
    Copilot's 1,139 and Codex's 1,207. GitHub serves `refs/pull/<N>/head` for any
    PR regardless, which needs nothing from the dataset but the PR number.

    CORRECTNESS NOTE -- why this does not just take the tip's first parent. For a
    multi-commit PR the tip's parent is the PREVIOUS COMMIT OF THE SAME PR, so
    diffing them would capture only the final commit and MISS violations
    introduced earlier in the PR. That would bias every delta principle toward
    "no new violation" -- which is the very result this benchmark reports, so the
    bias would be self-confirming. The branch point is therefore resolved with
    `merge-base` against the default branch, deepening the shallow history until
    it succeeds. If it cannot be resolved we return None so the PR is recorded
    with a retryable status, rather than silently analysing a wrong pair.
    """
    try:
        n = int(pr_number)
    except (TypeError, ValueError):
        return None

    base = default_branch(d)
    if not base:
        return None

    # Depth ladder. MEASURED 2026-08-17 on codelibs/fess#2998: depth 50 fails
    # (the branch point lies outside both shallow boundaries), depth 500
    # succeeds. Starting low is a false economy -- each failed rung costs two
    # fetches. The cost is also amortised: the clone cache is PER REPO and reused
    # across every PR of that repo, so a repo is deepened once, not per PR.
    for depth in (500, 3000):
        r = run_git(["fetch", "--depth", str(depth), "-q", "origin",
                     f"pull/{n}/head"], cwd=d)
        if r is None or r.returncode != 0:
            return None
        rp = run_git(["rev-parse", "FETCH_HEAD"], cwd=d)
        if rp is None or rp.returncode != 0:
            return None
        after = rp.stdout.strip()

        rb = run_git(["fetch", "--depth", str(depth), "-q", "origin", base], cwd=d)
        if rb is None or rb.returncode != 0:
            return None
        rbp = run_git(["rev-parse", "FETCH_HEAD"], cwd=d)
        if rbp is None or rbp.returncode != 0:
            return None
        base_tip = rbp.stdout.strip()

        mb = run_git(["merge-base", after, base_tip], cwd=d)
        if mb is not None and mb.returncode == 0 and mb.stdout.strip():
            before = mb.stdout.strip()
            # BEFORE == AFTER IS NOT A PAIR (2026-09-01). `merge-base` returns
            # `after` itself whenever the PR head is already an ancestor of the
            # base tip -- the PR's commits landed, or the branch was rebased,
            # or the shallow graft makes it look that way. Diffing a commit
            # against itself yields nothing, and every layer above read that
            # emptiness as the finding "this PR touches no Java", cached it,
            # and dropped the PR.
            #
            # THIS IS THE ROOT CAUSE of the 2,819 empty `_touched.json` files.
            # Confirmed on amikos-tech/chromadb-java-client#116 and
            # jenkinsci/chucknorris-plugin#249: both resolve to (X, X), both
            # have added/modified Java on GitHub, both were dropped. Sampling
            # 40 such PRs, 25% have real Java, so ~700 analysable PRs (about
            # 18% of the corpus) were lost to a pair that was never valid.
            #
            # Deepening can genuinely change the answer, so retry the next rung
            # before giving up, then fail honestly rather than guess a parent:
            # for a MULTI-COMMIT PR, `after^1` is the PR's own previous commit,
            # and diffing that would capture only the last commit and miss
            # violations introduced earlier -- biasing every delta principle
            # toward "no new violation", which is the very result this
            # benchmark reports.
            if before != after and have_commit(d, before):
                return before, after
        # merge-base failed, or returned a degenerate pair: the branch point
        # lies outside both shallow boundaries. Deepen and retry rather than
        # guessing.
    return None


def src_tree(d: Path, sha: str, extensions, excluded_parts=(),
             full_name: str | None = None, use_cache: bool = True
             ) -> dict[str, str] | None:
    """{repo-relative path: source} for every in-scope file at `sha`.

    Returns None on git failure, {} when the tree has no matching file, and
    {TOO_LARGE: ""} when the tree exceeds MAX_FILES. Results are cached per
    (repo, sha, extension-set) so later harnesses never re-fetch the same tree.
    """
    key = (_cache_key(full_name or str(d), sha, extensions, excluded_parts)
           if use_cache else None)
    if key:
        p = _cache_path(key)
        if p.exists():
            try:
                with gzip.open(p, "rt", encoding="utf-8") as fh:
                    return json.load(fh)
            except (OSError, json.JSONDecodeError):
                pass  # corrupt cache entry: fall through and re-fetch

    # `git archive` with no pathspec: filter in Python so one cache entry can
    # serve any extension set and the .d.ts / vendored rules stay in one place.
    #
    # `-c core.protectNTFS=false` is REQUIRED on Windows and is not optional
    # hygiene: git refuses to unpack an entire tree if ANY path in it is illegal
    # on NTFS, even when we want none of those files. mochilang/mochi carries
    # `.../Eulers-constant-0.5772....py` (trailing dots), which killed all 84 of
    # its Java PRs -- 9.7% of the whole corpus, concentrated in ONE repo, so the
    # loss was severely biased rather than random. A pathspec does NOT help:
    # git validates the full tree before applying the filter (measured).
    #
    # Safe here because we never let git check out to disk: the tar is streamed
    # and parsed in memory, filtered to source extensions, and our own writer
    # rejects absolute paths and `..` traversal before touching the filesystem.
    r = run_git(["-c", "core.protectNTFS=false", "archive", "--format=tar", sha],
                cwd=d, binary=True)
    if r is None:
        return None
    if r.returncode != 0:
        err = (r.stderr or b"").decode("utf-8", "replace")
        return {} if "did not match any files" in err else None

    files: dict[str, str] = {}
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tar:
            for member in tar:
                if not member.isfile():
                    continue
                if not is_source_file(member.name, extensions, excluded_parts):
                    continue
                if len(files) >= MAX_FILES:
                    return {TOO_LARGE: ""}
                fh = tar.extractfile(member)
                if fh is not None:
                    files[member.name] = fh.read().decode("utf-8", "replace")
                    total += member.size
    except tarfile.TarError:
        return None

    if key and total <= MAX_CACHE_BYTES:
        p = _cache_path(key)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            with gzip.open(tmp, "wt", encoding="utf-8") as fh:
                json.dump(files, fh)
            tmp.replace(p)          # atomic: concurrent readers never see a partial file
        except OSError:
            pass                    # cache is an optimisation, never a requirement
    return files


def touched_files(pr, extensions, excluded_parts=(), extract_dir: Path | None = None
                  ) -> list[str]:
    """AI-touched in-scope paths: gold-set list, or Step 2's derived cache (Copilot)."""
    if getattr(pr, "files_source", "dataset") == "git_diff":
        base = extract_dir or C.EXTRACT_DIR
        cache = base / str(pr.pr_id) / "_touched.json"
        raw = json.loads(cache.read_text()) if cache.exists() else []
    else:
        raw = list(pr.touched_files)
    return [f for f in raw if is_source_file(f, extensions, excluded_parts)]


if __name__ == "__main__":
    # Offline self-check: filters + cache round-trip (no network, no repos).
    exts = C.TS_SOURCE_EXTENSIONS
    excl = C.TS_EXCLUDED_PATH_PARTS
    assert is_source_file("src/a.ts", exts, excl)
    assert is_source_file("src/a.tsx", exts, excl)
    assert not is_source_file("src/a.d.ts", exts, excl), "declaration files excluded"
    assert not is_source_file("node_modules/x/a.ts", exts, excl), "vendored excluded"
    assert not is_source_file("dist/a.ts", exts, excl), "build output excluded"
    # The substring-vs-component regression (2026-08-27). Each of these paths
    # CONTAINS an excluded fragment but is real hand-written source.
    jexcl = C.JAVA_EXCLUDED_PATH_PARTS
    j = {".java"}
    assert is_source_file("gradlebuild/docs/Doc.java", j, jexcl), "gradlebuild is not build/"
    assert is_source_file("com/riksdagen/Vote.java", j, jexcl), "riksdagen is not gen/"
    assert is_source_file("codegen/Generator.java", j, jexcl), "codegen holds the generator"
    assert is_source_file("mytarget/A.java", j, jexcl), "mytarget is not target/"
    assert not is_source_file("build/A.java", j, jexcl), "real build output excluded"
    assert not is_source_file("a/target/A.java", j, jexcl), "real maven output excluded"
    assert not is_source_file("vavr/src-gen/io/API.java", j, jexcl), "src-gen is generated"
    assert not is_source_file("a/generated/A.java", j, jexcl), "generated/ excluded"
    assert not is_source_file("a/out/A.java", j, jexcl), "out/ excluded"
    assert not is_source_file("a/X.generated.java", j, jexcl), "filename marker excluded"
    assert not is_source_file("src/a.py", exts, excl)
    assert is_source_file("src/a.py", {".py"}), "python profile still works"

    k1 = _cache_key("o/r", "abc", exts)
    assert k1 == _cache_key("o/r", "abc", exts), "cache key is deterministic"
    assert k1 != _cache_key("o/r", "abd", exts), "sha changes the key"
    assert k1 != _cache_key("o/r", "abc", {".py"}), "extension set changes the key"
    assert k1 != _cache_key("o/r", "abc", exts, ("build/",)), (
        "the EXCLUSION RULE must change the key, or a filter repair silently "
        "serves trees built under the old rule")

    sample = {"src/a.ts": "export class A {}", "src/b.tsx": "export const B = 1;"}
    p = _cache_path(k1)
    p.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        json.dump(sample, fh)
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        assert json.load(fh) == sample, "cache round-trip"
    p.unlink()

    lock_a, lock_b = repo_lock("o/r"), repo_lock("o/r")
    assert lock_a is lock_b, "one lock per repo"
    assert repo_lock("o/other") is not lock_a

    print("gitsnap.py self-check OK: filters, cache key/round-trip, repo locks")
