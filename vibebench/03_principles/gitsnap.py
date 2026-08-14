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
import subprocess
import sys
import tarfile
import threading
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


def run_git(args: list[str], cwd: Path, binary: bool = False):
    """Run git in `cwd`. Returns None on timeout (after clearing stale locks)."""
    try:
        return subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True,
            timeout=C.GIT_TIMEOUT_S,
            **({} if binary else dict(text=True, encoding="utf-8", errors="replace")),
        )
    except subprocess.TimeoutExpired:
        for lock in (cwd / ".git").glob("**/*.lock"):
            try:
                lock.unlink()
            except OSError:
                pass
        return None


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
def is_source_file(path: str, extensions, excluded_parts=()) -> bool:
    f = path.lower()
    if f.endswith(".d.ts"):        # declarations carry no implementation
        return False
    if any(part in f for part in excluded_parts):
        return False
    return any(f.endswith(ext) for ext in extensions)


def _cache_key(full_name: str, sha: str, extensions) -> str:
    tag = ",".join(sorted(extensions))
    return hashlib.sha1(f"{full_name}@{sha}@{tag}".encode()).hexdigest()


def _cache_path(key: str) -> Path:
    return C.TREE_CACHE_DIR / key[:2] / f"{key}.json.gz"


def delta_pair(d: Path, pr) -> tuple[str, str] | None:
    """(before_sha, after_sha) for a PR's before/after delta, or None.

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
    merge_sha = getattr(pr, "merge_sha", None)
    if isinstance(merge_sha, str) and merge_sha:
        parent = fetch_merge_pair(d, merge_sha)
        if parent is not None:
            return parent, merge_sha

    raw = getattr(pr, "commit_shas", None)
    try:
        shas = [s for s in raw] if raw is not None else []
    except TypeError:           # scalar NaN for rows without a commit list
        shas = []
    if not shas:
        return None

    r = run_git(["fetch", "--depth", "2", "-q", "origin", *shas], cwd=d)
    if r is None or r.returncode != 0:
        return None

    parents: dict[str, list[str]] = {}
    for sha in shas:
        cf = run_git(["cat-file", "-p", f"{sha}^{{commit}}"], cwd=d)
        if cf is None or cf.returncode != 0:
            return None
        parents[sha] = [ln.split()[1] for ln in cf.stdout.splitlines()
                        if ln.startswith("parent ")]

    in_set = set(shas)
    all_parents = {p for ps in parents.values() for p in ps}
    tips = [s for s in shas if s not in all_parents]
    if len(tips) != 1:
        return None                     # ambiguous history: don't guess
    after = tips[0]

    # Branch point: the single parent that lies OUTSIDE the PR's commit set.
    externals = [p for ps in parents.values() for p in ps if p not in in_set]
    if not externals:
        return None
    before = externals[0]
    if not have_commit(d, before):
        r = run_git(["fetch", "--depth", "1", "-q", "origin", before], cwd=d)
        if r is None or r.returncode != 0:
            return None
    return (before, after) if have_commit(d, before) else None


def src_tree(d: Path, sha: str, extensions, excluded_parts=(),
             full_name: str | None = None, use_cache: bool = True
             ) -> dict[str, str] | None:
    """{repo-relative path: source} for every in-scope file at `sha`.

    Returns None on git failure, {} when the tree has no matching file, and
    {TOO_LARGE: ""} when the tree exceeds MAX_FILES. Results are cached per
    (repo, sha, extension-set) so later harnesses never re-fetch the same tree.
    """
    key = _cache_key(full_name or str(d), sha, extensions) if use_cache else None
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
    assert not is_source_file("src/a.py", exts, excl)
    assert is_source_file("src/a.py", {".py"}), "python profile still works"

    k1 = _cache_key("o/r", "abc", exts)
    assert k1 == _cache_key("o/r", "abc", exts), "cache key is deterministic"
    assert k1 != _cache_key("o/r", "abd", exts), "sha changes the key"
    assert k1 != _cache_key("o/r", "abc", {".py"}), "extension set changes the key"

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
