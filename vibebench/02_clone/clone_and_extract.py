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

Copilot (files_source == "git_diff"): the dataset has no filenames for Copilot,
so the touched-file list is derived from git itself: fetch the merge commit at
--depth 2 and take `git diff --name-only merge_sha^1 merge_sha`. For merge
commits this diffs against the first parent (= the base branch at merge time)
and for squash merges against the single parent -- both yield exactly the PR's
landed change. (Rebase-merges would undercount; they are rare and any
derivation failure is recorded as its own status.) The derived list is cached
in <out_dir>/_touched.json so re-runs stay resumable without network.

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
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

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
    """In-scope source file for this profile (.d.ts + vendored paths excluded)."""
    f = path.lower()
    if f.endswith(".d.ts"):
        return False
    if any(part in f for part in profile["excluded_parts"]):
        return False
    return any(f.endswith(ext) for ext in profile["extensions"])


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


def run_git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", *args],
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
    r = run_git(["fetch", "--depth", "1", "-q", "origin", f"{ref}:{local}"], cwd=d)
    if r.returncode == 0:
        rev = run_git(["rev-parse", local], cwd=d)
        if rev.returncode == 0:
            return rev.stdout.strip()
    # Fallback: fetch the merge commit directly.
    if isinstance(merge_sha, str) and merge_sha:
        r2 = run_git(["fetch", "--depth", "1", "-q", "origin", merge_sha], cwd=d)
        if r2.returncode == 0:
            return merge_sha
    return None


def derive_touched_files(d: Path, merge_sha: str | None,
                         profile: dict) -> list[str] | None:
    """Copilot path: list source files changed by the PR, from the merge diff.

    Fetches the merge commit at depth 2 (tip + parents) and diffs against the
    first parent. Returns None if the merge commit can't be fetched/diffed.
    """
    if not (isinstance(merge_sha, str) and merge_sha):
        return None
    r = run_git(["fetch", "--depth", "2", "-q", "origin", merge_sha], cwd=d)
    if r.returncode != 0:
        return None
    diff = run_git(["diff", "--name-only", "--diff-filter=ACMR",
                    f"{merge_sha}^1", merge_sha], cwd=d)
    if diff.returncode != 0:
        return None
    files = [ln.strip() for ln in diff.stdout.splitlines() if ln.strip()]
    return sorted(f for f in files if is_source_file(f, profile))


def derive_from_commit_set(d: Path, shas: list[str],
                           profile: dict) -> tuple[str, list[str]] | None:
    """Fallback when the timeline has no merge SHA (48 of 150 Copilot PRs):
    reconstruct the PR's touched files from its own commit SHAs (pr_commits).

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
    r = run_git(["fetch", "--depth", "1", "-q", "origin", *shas], cwd=d)
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
        files.update(f.strip() for f in diff.stdout.splitlines()
                     if f.strip() and is_source_file(f.strip(), profile))
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
        # Copilot: touched list comes from git, cached across runs.
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
                    if got is not None:
                        derived_head, touched = got
                        head_sha = head_sha or derived_head
                if touched is not None:
                    out_dir.mkdir(parents=True, exist_ok=True)
                    touched_cache.write_text(json.dumps(touched))
            elif head_sha is None:
                # The PR ref and the merge SHA are both gone (branch deleted,
                # force-pushed, or the timeline never recorded a merge). The
                # PR's own commits still pin the head, and curate.py now
                # attaches them for EVERY agent -- without this fallback the
                # loss is agent-biased (Devin 7.8% / Cursor 7.4% / Codex 0%),
                # which is exactly the bias the merge-SHA fix removed from the
                # delta harnesses. The touched list stays the dataset's.
                got = derive_from_commit_set(d, commit_shas(pr), profile)
                if got is not None:
                    head_sha = got[0]

        if needs_diff and touched is None:
            return [{
                "pr_id": pr.pr_id, "agent": pr.agent, "full_name": pr.full_name,
                "pr_number": pr.pr_number, "head_sha": head_sha or "",
                "file_path": "", "local_path": "",
                "extract_status": "diff_failed", "n_bytes": 0,
            }]
        if needs_diff and not touched:
            return [{
                "pr_id": pr.pr_id, "agent": pr.agent, "full_name": pr.full_name,
                "pr_number": pr.pr_number, "head_sha": head_sha or "",
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
                    local_path.write_bytes(blob)
                    status, nbytes = "ok", len(blob)

            rows.append({
                "pr_id": pr.pr_id, "agent": pr.agent, "full_name": pr.full_name,
                "pr_number": pr.pr_number, "head_sha": head_sha or "",
                "file_path": path, "local_path": str(local_path),
                "extract_status": status, "n_bytes": nbytes,
            })
    except Exception as e:  # noqa: BLE001 -- a single bad repo must not abort the batch
        print(f"  !! error on {pr.full_name}#{pr.pr_number}: {e}", flush=True)
        for path in (touched or [""]):
            rows.append({
                "pr_id": pr.pr_id, "agent": pr.agent, "full_name": pr.full_name,
                "pr_number": pr.pr_number, "head_sha": "",
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
    args = ap.parse_args()
    profile = LANG_PROFILES[args.lang]

    gold = pd.read_parquet(profile["gold_set"])
    if args.limit:
        gold = gold.head(args.limit)

    C.CLONES_DIR.mkdir(parents=True, exist_ok=True)
    profile["extract_dir"].mkdir(parents=True, exist_ok=True)

    # Repos are the unit of parallelism: one thread owns one git dir at a time.
    groups = list(gold.groupby("full_name", sort=False))
    n_prs = len(gold)
    print(f"[clone] ({args.lang}) {n_prs} PRs across {len(groups)} repos, "
          f"{args.workers} workers", flush=True)

    rows: list[dict] = []
    rows_lock = threading.Lock()
    done_prs = 0

    def process_repo(full_name: str, prs: pd.DataFrame) -> list[dict]:
        out: list[dict] = []
        for pr in prs.itertuples(index=False):
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
                rows.extend(got)
                done_prs += n_grp
                # Checkpoint so a crash doesn't lose earlier repos' progress.
                if done_prs % 25 < n_grp:
                    pd.DataFrame(rows).to_parquet(profile["manifest"], index=False)
            print(f"[clone] progress: {done_prs}/{n_prs} PRs", flush=True)

    manifest = pd.DataFrame(rows)
    manifest.to_parquet(profile["manifest"], index=False)

    print("\n[clone] manifest written to", profile["manifest"])
    print(f"[clone] files: {len(manifest)}  ok: {(manifest.extract_status=='ok').sum()}")
    print("\nExtract status counts:")
    print(manifest["extract_status"].value_counts().to_string())
    print("\nOK files per agent:")
    print(manifest[manifest.extract_status == "ok"].groupby("agent").size().to_string())


if __name__ == "__main__":
    main()
