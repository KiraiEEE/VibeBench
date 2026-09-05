"""
Recover the touched-file LIST for PRs the degenerate-pair bug dropped, using
the GitHub API only. No git, no clone, no tree download.

WHY THIS IS THE RIGHT TOOL, measured 2026-09-02.
`clone_and_extract` does two jobs: it derives each PR's touched-file list, and
it downloads the CONTENT of those files into `corpus/ai_files_java/`. The
recovery pass only needs the first. Grepping every consumer of
`EXTRACT_DIR_JAVA` shows all of them go through `gitsnap.touched_files`, which
reads exactly one thing:

    <extract_dir>/<pr_id>/_touched.json

Nothing in the pipeline reads the extracted file CONTENT. The analysis harness
(`run_java.py`) gets its source from `gitsnap.src_tree`, which fetches and
caches trees itself.

So the recovery pass was saturating a 3.5 MB/s connection downloading file
bodies that nothing ever opens. Measured cost of the two approaches per PR:

    clone_and_extract (fetch 2 commits + write files) : ~15 s, megabytes
    gh api pulls/N/files                              : ~1 s,  ~2 KB

At 2,350 PRs remaining that is the difference between ~9 hours and well under
one. It is also STRICTLY more accurate: GitHub's own file list is the ground
truth for what a PR changed, where the local derivation has to infer a branch
point and returns a degenerate pair whenever the head is already an ancestor of
the base tip -- the bug that lost these PRs in the first place.

DELETIONS ARE EXCLUDED. A file the PR removed has no content in the after-tree,
so it cannot carry a violation and must not count as a touched source file.
This is why an earlier estimate of the recoverable population was wrong: it
counted `.java` filenames without checking their status, and a revert PR that
deletes 208 Java files looked recoverable when it has nothing to analyse.

Run:  python vibebench/02_clone/recover_file_lists.py [--limit N] [--workers N]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "03_principles"))
import config as C          # noqa: E402
import gitsnap              # noqa: E402
import safeio              # noqa: E402

NO_WINDOW = gitsnap.NO_WINDOW
_lock = threading.Lock()
_stats = {"ok": 0, "empty": 0, "failed": 0, "n": 0}


def api_changed_count(full_name: str, pr_number: int) -> int | None:
    """GitHub's own count of files the PR changed. The truncation detector.

    The /files endpoint CAPS how many entries it will return, but the PR object
    itself reports the true total in `changed_files`. Comparing the two is the
    only way to tell a complete list from a silently truncated one.
    """
    try:
        r = subprocess.run(
            ["gh", "api", f"repos/{full_name}/pulls/{pr_number}",
             "-q", ".changed_files"],
            capture_output=True, text=True, timeout=90, **NO_WINDOW)
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        return None
    if r.returncode != 0:
        return None
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


def api_files(full_name: str, pr_number: int) -> list[str] | None:
    """Every path the PR added/modified/renamed, or None if the call failed.

    TRUNCATION IS REAL AND IT IS SIZE-CORRELATED (measured 2026-09-02). Against
    the git-derived list on 25 already-analysed PRs: 18 agreed exactly, and 2
    disagreed with the API returning FEWER files --
        microsphere-projects/microsphere-spring#180 : git 530 -> api 31
        Talend/component-runtime#1166               : git  58 -> api  0
    `--paginate` does not save you; the endpoint caps the total it will serve.
    Using this list unguarded would under-report files on precisely the largest
    PRs, i.e. the ones most likely to contain a violation -- a systematic,
    size-correlated bias, not noise. The caller MUST verify the count.
    """
    for attempt in (1, 2):
        try:
            r = subprocess.run(
                ["gh", "api", f"repos/{full_name}/pulls/{pr_number}/files",
                 "--paginate", "-q",
                 '.[] | .status + "	" + .filename'],
                capture_output=True, text=True, timeout=120, **NO_WINDOW)
        except FileNotFoundError:
            sys.exit("gh is not on PATH -- this script needs the GitHub CLI")
        except (subprocess.TimeoutExpired, OSError):
            r = None
        if r is not None and r.returncode == 0:
            out = []
            for ln in r.stdout.splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                st, _, name = ln.partition("	")
                out.append((st, name))
            return out
        # Distinguish an outage from a real API error, exactly as run_git does:
        # failing fast during a connection drop would mark thousands of PRs
        # unrecoverable for a reason that has nothing to do with the PRs.
        if attempt == 1 and not gitsnap.network_is_up():
            gitsnap.wait_for_network()
            continue
        break
    return None


def _git_fallback(row) -> list[str] | None:
    """Authoritative but slow: resolve the pair locally and diff it.

    Used ONLY when the API list is provably incomplete, so the corpus never
    contains a truncated list, and the slow path costs its ~15s on the minority
    of PRs that need it instead of on all of them.
    """
    d = gitsnap.ensure_repo(row.full_name)
    if d is None:
        return None
    pair = gitsnap.delta_pair(d, row)
    if pair is None:
        return None
    before, after = pair
    if before == after:
        return None
    r = gitsnap.run_git(["diff", "--name-only", "--diff-filter=ACMR",
                         before, after], cwd=d)
    if r is None or r.returncode != 0:
        return None
    raw = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    if not raw:
        return None                   # empty raw diff == resolution failure
    return [f for f in raw
            if gitsnap.is_source_file(f, C.JAVA_SOURCE_EXTENSIONS,
                                      C.JAVA_EXCLUDED_PATH_PARTS)]


def process(row) -> str:
    pid = str(row.pr_id)
    out_dir = Path(C.EXTRACT_DIR_JAVA) / pid
    cache = out_dir / "_touched.json"
    if cache.exists():
        return "skip"
    files = api_files(row.full_name, int(row.pr_number))
    if files is None:
        return "failed"
    # TRUNCATION GUARD, comparing LIKE WITH LIKE. `changed_files` counts every
    # changed path including deletions, so the comparison must use the FULL
    # returned list, not the deletion-filtered one -- otherwise every PR that
    # deletes anything looks truncated and takes the slow path for nothing.
    n_changed = api_changed_count(row.full_name, int(row.pr_number))
    truncated = n_changed is not None and len(files) < n_changed
    files = [name for st, name in files if st != "removed"]
    if truncated:
        got = _git_fallback(row)
        if got is None:
            return "failed"           # never write a list we could not verify
        keep = got
    else:
        keep = [f for f in files
                if gitsnap.is_source_file(f, C.JAVA_SOURCE_EXTENSIONS,
                                          C.JAVA_EXCLUDED_PATH_PARTS)]
    out_dir.mkdir(parents=True, exist_ok=True)
    safeio.write_json(cache, keep)      # atomic: a power cut cannot half-write
    return "ok" if keep else "empty"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()

    pr = pd.read_parquet(C.CORPUS_DIR / "java_pr_results.parquet")
    gold = pd.read_parquet(C.CORPUS_DIR / "gold_set_java.parquet")
    lost = pr[pr.status == "no_touched_files"][["pr_id", "full_name", "pr_number"]]
    lost = lost.merge(gold[["pr_id"]].drop_duplicates(), on="pr_id", how="inner")

    base = Path(C.EXTRACT_DIR_JAVA)
    todo = [r for r in lost.itertuples()
            if not (base / str(r.pr_id) / "_touched.json").exists()]
    if a.limit:
        todo = todo[:a.limit]
    print(f"[recover] {len(lost):,} PRs dropped by the bug, {len(todo):,} still "
          f"need a file list", flush=True)
    if not todo:
        print("[recover] nothing to do", flush=True)
        return

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(process, r): r for r in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                res = fut.result()
            except Exception as e:                    # noqa: BLE001
                res = "failed"
                print(f"  !! {type(e).__name__}: {e}"[:160], flush=True)
            with _lock:
                _stats[res] = _stats.get(res, 0) + 1
                _stats["n"] += 1
            if i % 100 == 0 or i == len(todo):
                el = time.time() - t0
                rate = i / el * 3600
                left = (len(todo) - i) / max(rate / 3600, 1e-9)
                print(f"[recover] {i}/{len(todo)}  recovered={_stats['ok']} "
                      f"no-java={_stats['empty']} failed={_stats['failed']}  "
                      f"{rate:,.0f}/h  eta {left/60:.0f} min", flush=True)

    print(f"[recover] DONE in {(time.time()-t0)/60:.1f} min: "
          f"recovered={_stats['ok']} no-java={_stats['empty']} "
          f"failed={_stats['failed']}", flush=True)
    print("[recover] next: re-run run_java.py so the newly analysable PRs are "
          "measured", flush=True)


if __name__ == "__main__":
    main()
