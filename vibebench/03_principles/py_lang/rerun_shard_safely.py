"""
Re-run one shard's RETRYABLE PRs under safe, deterministic git conditions.

WHY THIS EXISTS (2026-09-14). The six-shard flagged pass left 18 PRs as
`archive_failed`, and diagnosing them one by one found three separate causes,
none of them a property of the pull request:

  1. GIT LFS. For microsoft/onnxscript and splunk/attack_data, `git archive`
     spawned `git-lfs filter-process`, which DOWNLOADS every LFS object over the
     network while building the tar, and ran into the 300 s git timeout. The
     analysis reads `.py` only, and no LFS pattern in any affected repository
     matches a `.py` file (checked in each repository's .gitattributes), so LFS
     content can never reach a detector. Setting GIT_LFS_SKIP_SMUDGE makes git
     emit the pointer files instead, which are then discarded as non-`.py`.
  2. LOAD. ray-project/ray, home-assistant/core, huggingface/transformers and
     similar archived in seconds on an idle machine (ray: 4,543 files in 6 s)
     but exceeded 300 s while six shards ran. The timeout is raised here for this
     process only. A timeout decides WHETHER a PR is analysed, never WHAT is
     computed, so it cannot bias a flag.
  3. SIZE. `gitsnap.src_tree` reads the whole uncompressed tar into memory, and
     one tree (LukePrior/nbn-upgrade-map) is 4.86 GB, roughly 10 GB at peak.
     Windows does not overcommit, so an allocation that size can make OTHER
     programs' allocations fail. A tree above MAX_TREE_BYTES is therefore
     refused before `git archive` runs and stays `archive_failed`, which is the
     status it already had. Measured with `git ls-tree -l`, which reads sizes
     without reading content.

NOTHING IN SHARED CODE IS CHANGED. config.GIT_TIMEOUT_S and gitsnap.src_tree are
also used by the finished Java stages, so both adjustments are applied at
runtime inside this process alone, and each is logged.

Run:  python vibebench/03_principles/py_lang/rerun_shard_safely.py --shard 0/6
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Must be set before any git subprocess exists; children inherit it.
os.environ["GIT_LFS_SKIP_SMUDGE"] = "1"

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))
import run_python as R  # noqa: E402

TIMEOUT_S = 1800
MAX_TREE_BYTES = int(1.5 * 2 ** 30)


def tree_bytes(d: Path, sha: str) -> int | None:
    try:
        p = subprocess.run(["git", "-C", str(d), "ls-tree", "-r", "-l", sha],
                           capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return None
    if p.returncode != 0:
        return None
    total = 0
    for line in p.stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) >= 4 and parts[3].isdigit():
            total += int(parts[3])
    return total


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", required=True)
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()

    R.C.GIT_TIMEOUT_S = TIMEOUT_S
    original = R.gitsnap.src_tree

    def guarded(d, sha, extensions, excluded_parts=(), full_name=None, use_cache=True):
        key = R.gitsnap._cache_key(full_name or str(d), sha, extensions, excluded_parts)
        if use_cache and R.gitsnap._cache_path(key).exists():
            return original(d, sha, extensions, excluded_parts, full_name, use_cache)
        size = tree_bytes(d, sha)
        if size is not None and size > MAX_TREE_BYTES:
            R.log(f"  SIZE GUARD: {full_name} {sha[:10]} tree is "
                  f"{size / 2 ** 30:.2f} GB > {MAX_TREE_BYTES / 2 ** 30:.1f} GB, "
                  f"not archiving (stays archive_failed)")
            return None
        return original(d, sha, extensions, excluded_parts, full_name, use_cache)

    R.gitsnap.src_tree = guarded
    R.log(f"SAFE RERUN: GIT_LFS_SKIP_SMUDGE=1, GIT_TIMEOUT_S={TIMEOUT_S}, "
          f"tree size guard {MAX_TREE_BYTES / 2 ** 30:.1f} GB (this process only)")
    sys.argv = [str(_HERE.parent / "run_python.py"),
                "--shard", a.shard, "--workers", str(a.workers)]
    R.main()


if __name__ == "__main__":
    main()
