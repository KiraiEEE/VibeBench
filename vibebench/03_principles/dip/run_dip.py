"""
Step 3-DIP - Dependency Inversion evaluator: before/after PR delta.

For each gold-set PR, compare the repo's internal import architecture at the
merge commit's first parent (state just before the PR landed) vs the merge
commit itself (state just after). Scoring the DELTA -- not the absolute repo
state -- is what isolates the agent's contribution from pre-existing
human-written architecture (see PROJECT.md section 3).

Per PR, computed over the AI-touched modules (see graph.py for definitions):
  touched_fan_out       -- internal import edges leaving touched modules
  touched_cycles        -- import cycles (SCCs) involving a touched module
  abstract_dep_ratio    -- share of touched modules' internal imports that land
                           on modules defining an abstraction (ABC/Protocol)
  mean_D                -- mean Martin distance-from-main-sequence |A + I - 1|
                           of the top-level packages containing touched modules
  repo_cycles           -- total cycles in the whole repo graph (context)

DIP-risk flag (thresholds in config.py): the PR creates a new cycle through a
touched module, or worsens mean_D beyond DIP_THRESHOLDS["delta_mean_d"].

Needs the merge commit at depth 2 (tip + parents); fetched on demand into the
same corpus/clones/<repo> dirs Step 2 uses. Resumable: PRs already present in
corpus/dip_results.parquet are skipped, and the parquet is checkpointed every
25 PRs (lesson learned from the Step 2 batch crash, see PROJECT.md).

Run:  python vibebench/03_principles/dip/run_dip.py            # full gold set
      python vibebench/03_principles/dip/run_dip.py --limit 5  # smoke test
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import pathlib
import sys
import tarfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dip"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import config as C  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 03_principles
import gitsnap  # noqa: E402
from graph import (
    cyclic_pairs,  # noqa: E402
    abstract_dep_ratio, build_module_graph, find_cycles, martin_metrics,
    path_to_module, top_package,
)

# Repos with more than this many .py files at either snapshot are skipped
# (status repo_too_large) to bound runtime; recorded, not silently dropped.
MAX_PY_FILES = 10_000



def _atomic_parquet(df, path):
    """Write a checkpoint so a power cut can never leave a CORRUPT file.

    `to_parquet(path)` overwrites in place, so a crash mid-write truncates the
    file. Writing to a sibling temp and then `os.replace` (atomic on NTFS and
    POSIX) means a reader always sees either the previous complete checkpoint or
    the new one -- never a half-written one. Matters most for the long overnight
    delta runs, which checkpoint every 25 PRs and so have many more write windows
    than a single end-of-run write.
    """
    import os
    tmp = pathlib.Path(str(path) + ".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)

def run_git(args: list[str], cwd: Path, binary: bool = False):
    try:
        return subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True,
            timeout=C.GIT_TIMEOUT_S,
            **({} if binary else dict(text=True, encoding="utf-8", errors="replace")),
        )
    except subprocess.TimeoutExpired:
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
    """Ensure merge commit + its first parent are present; return parent SHA."""
    if not have_commit(d, merge_sha):
        r = run_git(["fetch", "--depth", "2", "-q", "origin", merge_sha], cwd=d)
        if r is None or r.returncode != 0:
            return None
    rev = run_git(["rev-parse", f"{merge_sha}^1"], cwd=d)
    if rev is None or rev.returncode != 0:
        return None
    parent = rev.stdout.strip()
    return parent if have_commit(d, parent) else None


def py_tree(d: Path, sha: str) -> dict[str, str] | None:
    """{repo-relative path: source} for every .py file at `sha`."""
    r = run_git(["archive", "--format=tar", sha, "--", "*.py"], cwd=d, binary=True)
    if r is None:
        return None
    if r.returncode != 0:
        # A pathspec that matches nothing is a legitimate "no python files".
        err = (r.stderr or b"").decode("utf-8", "replace")
        return {} if "did not match any files" in err else None
    files: dict[str, str] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tar:
            for member in tar:
                if not (member.isfile() and member.name.endswith(".py")):
                    continue
                if len(files) >= MAX_PY_FILES:
                    return {"__TOO_LARGE__": ""}
                f = tar.extractfile(member)
                if f is not None:
                    files[member.name] = f.read().decode("utf-8", "replace")
    except tarfile.TarError:
        return None
    return files


def snapshot_metrics(files: dict[str, str], touched: set[str]) -> dict:
    g = build_module_graph(files)
    scope = {m for m in touched if m in g}
    cycles = find_cycles(g)
    touched_cycles = sum(1 for c in cycles if c & scope)
    mm = martin_metrics(g)
    pkgs = {top_package(m) for m in scope}
    ds = [mm[p]["D"] for p in pkgs if p in mm and mm[p]["D"] is not None]
    return {
        "n_modules": len(g),
        "touched_in_graph": len(scope),
        "touched_fan_out": sum(len(g[m].imports) for m in scope),
        "touched_cycles": touched_cycles,
        "repo_cycles": len(cycles),
        "abstract_dep_ratio": abstract_dep_ratio(g, scope),
        "mean_d": (sum(ds) / len(ds)) if ds else None,
        # NOVELTY IS DECIDED ON THE WHOLE GRAPH, then attributed by touch.
        # Comparing COUNTS of touched cycles has two failure modes: a
        # pre-existing cycle that merely enters the touched scope reads as new
        # (this is the defect that produced 40 spurious Java flags before it was
        # caught by labelling), and one cycle vanishing while another appears
        # nets to zero and reads as no change. Sets fix both.
        # Pair-level, not SCC-level: see graph.cyclic_pairs. Comparing whole
        # SCCs makes ANY membership change in a large pre-existing component
        # look new -- including a PR that REMOVES a module from the cycle.
        "cyclic_pairs": cyclic_pairs(cycles),
    }


def touched_py_files(pr) -> list[str]:
    """AI-touched .py paths: gold-set list, or Step 2's derived cache (Copilot)."""
    if getattr(pr, "files_source", "dataset") == "git_diff":
        cache = C.EXTRACT_DIR / str(pr.pr_id) / "_touched.json"
        return json.loads(cache.read_text()) if cache.exists() else []
    return [f for f in pr.touched_files if f.lower().endswith(".py")]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    gold = pd.read_parquet(C.GOLD_SET)
    if args.limit:
        gold = gold.head(args.limit)

    # Resume: keep rows with a PERMANENT outcome; retry transient failures
    # (fetch_failed / archive_failed / error) so one network hiccup doesn't
    # permanently exclude a PR from the benchmark.
    permanent = {"ok", "no_touched_modules", "repo_too_large"}  # AUDIT FIX: no_merge_sha now retryable
    done_ids: set = set()
    rows: list[dict] = []
    if C.DIP_RESULTS.exists():
        prev = pd.read_parquet(C.DIP_RESULTS)
        keep = prev[prev.status.isin(permanent)]
        rows = keep.to_dict("records")
        done_ids = set(keep.pr_id)
        n_retry = len(prev) - len(keep)
        print(f"[dip] resuming: {len(done_ids)} PRs done, {n_retry} failures to retry")

    n = len(gold)
    for i, pr in enumerate(gold.itertuples(index=False), 1):
        if pr.pr_id in done_ids:
            continue
        # V2: `outcome` and `rejection_stratum` ride on EVERY row of EVERY
        # parquet (REDESIGN_V2.md 3), so any report can split on them without a
        # join and no analysis can accidentally pair results with the wrong
        # gold set. Defaulted, because this harness must also read a pre-V2
        # gold set.
        #
        # `merge_sha` is normalised here rather than left as `pr.merge_sha or
        # ""`: a NOT-MERGED PR has no merge SHA, the parquet stores that as a
        # float NaN, and `nan or ""` evaluates to NaN (NaN is truthy), so the
        # old expression put a float into a string column for every rejected
        # and open PR. gitsnap.delta_pair already type-checks it, but the
        # column itself would have been unusable.
        _ms = getattr(pr, "merge_sha", None)
        base = {"pr_id": pr.pr_id, "agent": pr.agent, "full_name": pr.full_name,
                "pr_number": pr.pr_number,
                "merge_sha": _ms if isinstance(_ms, str) else "",
                "outcome": getattr(pr, "outcome", "merged"),
                "rejection_stratum": getattr(pr, "rejection_stratum", "merged")}

        def emit(status: str, **extra) -> None:
            rows.append({**base, "status": status, **extra})

        try:
            touched = [m for m in (path_to_module(p) for p in touched_py_files(pr)) if m]
            if not touched:
                emit("no_touched_modules")
                continue
            if not (isinstance(pr.merge_sha, str) and pr.merge_sha):
                pass  # AUDIT FIX: no longer fatal, see gitsnap.delta_pair below

            d = gitsnap.ensure_repo(pr.full_name)
            # AUDIT FIX 2026-07-28: shared resolver -- graft-safe parent read,
            # PLUS reconstruction from the PR's own commit SHAs when the timeline
            # has no merge SHA, PLUS a per-(repo,sha) tree cache. Previously ~57
            # PRs per principle were dropped as `no_merge_sha`, and that loss was
            # heavily agent-biased (Copilot lost 22.7% of its PRs vs Codex 1.3%).
            pair = gitsnap.delta_pair(d, pr)
            print(f"[{i}/{n}] {pr.agent:<12} {pr.full_name}#{pr.pr_number} "
                  f"({len(touched)} modules)", flush=True)
            if pair is None:
                emit("fetch_failed" if (isinstance(pr.merge_sha, str) and pr.merge_sha)
                     else "no_merge_sha")
                continue
            before_sha, after_sha = pair

            before_files = gitsnap.src_tree(d, before_sha, {".py"}, (), pr.full_name)
            after_files = gitsnap.src_tree(d, after_sha, {".py"}, (), pr.full_name)
            if before_files is None or after_files is None:
                emit("archive_failed")
                continue
            if "__TOO_LARGE__" in before_files or "__TOO_LARGE__" in after_files:
                emit("repo_too_large")
                continue

            tset = set(touched)
            before = snapshot_metrics(before_files, tset)
            after = snapshot_metrics(after_files, tset)

            row = {**base, "status": "ok",
                   "n_touched_modules": len(tset)}
            for k in ("n_modules", "touched_in_graph", "touched_fan_out",
                      "touched_cycles", "repo_cycles", "abstract_dep_ratio", "mean_d"):
                row[f"before_{k}"] = before[k]
                row[f"after_{k}"] = after[k]

            d_cycles = after["touched_cycles"] - before["touched_cycles"]
            # A cycle is NEW only if the BEFORE graph did not contain it at all;
            # it is charged to this PR only if it also involves a touched module.
            # A pair (u, v) is newly cyclic iff the two are mutually reachable
            # after and were not before; charged only if a touched module is in it.
            new_pairs = after["cyclic_pairs"] - before["cyclic_pairs"]
            new_cycles = {pr_ for pr_ in new_pairs if pr_[0] in tset or pr_[1] in tset}
            d_mean_d = (after["mean_d"] - before["mean_d"]
                        if (after["mean_d"] is not None and before["mean_d"] is not None)
                        else None)
            d_adr = (after["abstract_dep_ratio"] - before["abstract_dep_ratio"]
                     if (after["abstract_dep_ratio"] is not None
                         and before["abstract_dep_ratio"] is not None)
                     else None)
            row["delta_touched_cycles"] = d_cycles
            row["delta_mean_d"] = d_mean_d
            row["delta_abstract_dep_ratio"] = d_adr

            row["n_new_cyclic_pairs"] = len(new_cycles)

            reasons = []
            if len(new_cycles) > C.DIP_THRESHOLDS["new_touched_cycles"]:
                reasons.append("new_cycle")
            # mean_d_worse is COMPUTED AND STORED but no longer drives dip_risk
            # (2026-08-17). It validated at precision 0.000 in BOTH languages
            # (Python 0/7, TypeScript 0/10 -- 17 flags, zero true positives), and
            # 4 of 7 Python FPs had ZERO new dependencies: it fires on graph
            # RECOMPOSITION, not on real inversion. Keeping it in the composite
            # dragged Python DIP to 13/20 = 0.650 precision when `new_cycle`
            # alone is 13/13 = 1.000 -- i.e. the composite UNDERSTATED the
            # project's own best detector. Dropping it loses no recall: the 7
            # were not violations at all.
            # It is still recorded as a descriptor so the finding stays auditable
            # and the decision is reversible from the parquet alone.
            if d_mean_d is not None and d_mean_d > C.DIP_THRESHOLDS["delta_mean_d"]:
                row["mean_d_worse"] = True
                if C.DIP_MEAN_D_COUNTS_AS_RISK:
                    reasons.append("mean_d_worse")
            else:
                row["mean_d_worse"] = False
            row["dip_risk_hits"] = reasons
            row["dip_risk"] = bool(reasons)
            rows.append(row)
        except Exception as e:  # noqa: BLE001 -- one bad repo must not abort the batch
            print(f"  !! error on {pr.full_name}#{pr.pr_number}: {e}", flush=True)
            emit("error")

        if i % 25 == 0:
            _atomic_parquet(pd.DataFrame(rows), C.DIP_RESULTS)

    results = pd.DataFrame(rows)
    _atomic_parquet(results, C.DIP_RESULTS)
    print(f"\n[dip] results written to {C.DIP_RESULTS}")
    print(results["status"].value_counts().to_string())
    ok = results[results.status == "ok"]
    if len(ok):
        print("\nDIP-risk rate per agent (PR-level):")
        print(ok.groupby("agent").agg(
            prs=("pr_id", "size"),
            dip_risk_rate=("dip_risk", "mean"),
            mean_delta_cycles=("delta_touched_cycles", "mean"),
            mean_delta_d=("delta_mean_d", "mean"),
            mean_delta_adr=("delta_abstract_dep_ratio", "mean"),
        ).round(3).to_string())


if __name__ == "__main__":
    main()
