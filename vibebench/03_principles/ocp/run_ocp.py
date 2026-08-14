"""
Step 3-OCP - Open/Closed evaluator: before/after PR delta.

For each gold-set PR, compare the repo's type-dispatch landscape at the merge
commit's first parent vs the merge commit itself. Like DIP/ISP, only the DELTA
is debited to the agent -- pre-existing switch statements are never counted.

OCP's canonical violation (Robert Martin) is the "switch statement": a
type-/tag-discriminating if/elif or match ladder that must be edited to add a
variant. The delta harness is the natural instrument -- a PR IS a requirements
change, so we can directly see whether the agent added behavior by EXTENDING
(new class/function) or by MODIFYING a closed conditional. See dispatch.py.

Output: corpus/ocp_results.parquet with two row types --
  pr        -- one per gold-set PR: status, delta reasons, ocp_risk flag, and
               the raw add-vs-modify def profile (descriptive, not a flag).
  dispatch  -- head-state dispatch chains in AI-touched modules that are a smell
               (for inventory + labeling).

Scope (attribution): a dispatch chain is in scope iff its module is AI-touched.
Entity rows come from the AFTER snapshot only. PR-level delta reasons:
  new_dispatch_smell -- PR introduced a flagged chain absent (by
                        module+qualname+subject key) from the parent snapshot.
  dispatch_widened   -- a chain present in both grew its branch count.

Resumable exactly like run_isp.py: permanent statuses kept, transient failures
retried, parquet checkpointed every 25 PRs.

Run:  python vibebench/03_principles/ocp/run_ocp.py            # full gold set
      python vibebench/03_principles/ocp/run_ocp.py --limit 5  # smoke test
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ocp"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import config as C  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 03_principles
import gitsnap  # noqa: E402
from dispatch import (  # noqa: E402
    def_delta, def_index, find_dispatch_chains, is_dispatch_smell,
)
from graph import path_to_module  # noqa: E402

# --- Git snapshot helpers: duplicated from run_isp.py on purpose (same reason:
# never mix refactoring risk with new-feature correctness, and each harness
# stays self-contained). Uses the graft-safe cat-file parent read.
# TODO: factor into 03_principles/gitsnap.py once OCP is label-validated.
MAX_PY_FILES = 10_000


def run_git(args: list[str], cwd: Path, binary: bool = False):
    try:
        return subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True,
            timeout=C.GIT_TIMEOUT_S,
            **({} if binary else dict(text=True, encoding="utf-8", errors="replace")),
        )
    except subprocess.TimeoutExpired:
        # A killed git process can leave .git/*.lock behind and poison every
        # later command in this repo (PROJECT.md section 6). Clone dirs are
        # exclusively pipeline-owned, so clearing locks is safe.
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
    """Ensure merge commit + its first parent are present; return parent SHA.
    Parent is read from the commit header (`git cat-file -p`), never
    `rev-parse ^1`: a later shallow fetch can re-graft the boundary at this
    commit and break rev-parse even though the object is present (the trap in
    PROJECT.md section 2 that cost ISP 24 PRs)."""
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


def py_tree(d: Path, sha: str) -> dict[str, str] | None:
    """{repo-relative path: source} for every .py file at `sha`."""
    r = run_git(["archive", "--format=tar", sha, "--", "*.py"], cwd=d, binary=True)
    if r is None:
        return None
    if r.returncode != 0:
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


def touched_py_files(pr) -> list[str]:
    """AI-touched .py paths: gold-set list, or Step 2's derived cache (Copilot)."""
    if getattr(pr, "files_source", "dataset") == "git_diff":
        cache = C.EXTRACT_DIR / str(pr.pr_id) / "_touched.json"
        return json.loads(cache.read_text()) if cache.exists() else []
    return [f for f in pr.touched_files if f.lower().endswith(".py")]
# --- end of duplicated harness helpers.


def _branch_cohesion_suppresses(files_by_module: dict[str, str], ch) -> bool:
    """OPTIONAL semantic suppressor (config.OCP_BRANCH_COHESION_SUPPRESS): True
    if the chain's branch bodies are semantically homogeneous (a CLOSED set --
    the dominant OCP false positive) per a local code encoder. Off / no-op unless
    the threshold is set AND the encoder is available."""
    thr = getattr(C, "OCP_BRANCH_COHESION_SUPPRESS", None)
    if thr is None:
        return False
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 03_principles
    import embeddings as E  # noqa: E402
    if not E.available():
        return False
    src = files_by_module.get(ch.module)
    if not src:
        return False
    from dispatch import branch_bodies_at  # noqa: E402
    bodies = branch_bodies_at(src, ch.lineno)
    if len(bodies) < 2:
        return False
    coh = E.mean_pairwise_cosine(E.embed(bodies))
    return coh is not None and coh >= thr


def ocp_snapshot(files: dict[str, str], touched: set[str]) -> dict:
    """Dispatch chains for one tree, keyed by location, with scope + smell flag."""
    files_by_module = {m: src for path, src in files.items()
                       if (m := path_to_module(path)) is not None}
    chains: dict[tuple[str, str, str], dict] = {}
    suppress_closed = getattr(C, "OCP_CLOSED_SET_SUPPRESS", False)
    for ch in find_dispatch_chains(files):
        # Deterministic closed-set suppressor (config.OCP_CLOSED_SET_SUPPRESS):
        # a type-dispatch over builtins/one fixed grammar is exhaustive, not a smell.
        smell = is_dispatch_smell(ch, C.OCP_THRESHOLDS, suppress_closed=suppress_closed)
        # Optional embedding branch-cohesion suppressor (off by default -- failed gate).
        if smell and ch.module in touched and _branch_cohesion_suppresses(
                files_by_module, ch):
            smell = False
        # Keep the widest chain per location key (defensive against duplicates).
        prev = chains.get(ch.key)
        if prev is not None and prev["n_branches"] >= ch.n_branches:
            continue
        chains[ch.key] = {
            "module": ch.module, "qualname": ch.qualname, "subject": ch.subject,
            "kind": ch.kind, "n_branches": ch.n_branches, "lineno": ch.lineno,
            "smell": smell, "in_scope": ch.module in touched,
        }
    return {"chains": chains, "index": def_index(files)}


def pr_delta_reasons(before: dict, after: dict) -> list[str]:
    """Delta reasons over in-scope AFTER chains vs the full BEFORE table
    (a pre-existing smell is never re-debited to this PR)."""
    reasons: set[str] = set()
    for key, row in after["chains"].items():
        if not row["in_scope"]:
            continue
        prev = before["chains"].get(key)
        if row["smell"] and (prev is None or not prev["smell"]):
            reasons.add("new_dispatch_smell")
        if prev is not None and row["n_branches"] > prev["n_branches"]:
            reasons.add("dispatch_widened")
    return sorted(reasons)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    gold = pd.read_parquet(C.GOLD_SET)
    if args.limit:
        gold = gold.head(args.limit)

    permanent = {"ok", "no_touched_modules", "repo_too_large"}  # AUDIT FIX: no_merge_sha now retryable
    done_ids: set = set()
    rows: list[dict] = []
    if C.OCP_RESULTS.exists():
        prev = pd.read_parquet(C.OCP_RESULTS)
        pr_rows = prev[prev.row_type == "pr"]
        done_ids = set(pr_rows[pr_rows.status.isin(permanent)].pr_id)
        rows = prev[prev.pr_id.isin(done_ids)].to_dict("records")
        n_retry = len(pr_rows) - len(done_ids)
        print(f"[ocp] resuming: {len(done_ids)} PRs done, {n_retry} failures to retry")

    n = len(gold)
    for i, pr in enumerate(gold.itertuples(index=False), 1):
        if pr.pr_id in done_ids:
            continue
        base = {"pr_id": pr.pr_id, "agent": pr.agent, "full_name": pr.full_name,
                "pr_number": pr.pr_number, "merge_sha": pr.merge_sha or ""}

        def emit(status: str, **extra) -> None:
            rows.append({**base, "row_type": "pr", "status": status, **extra})

        try:
            touched = [m for m in (path_to_module(p) for p in touched_py_files(pr)) if m]
            if not touched:
                emit("no_touched_modules")
                continue

            d = gitsnap.ensure_repo(pr.full_name)
            # AUDIT FIX 2026-07-28: use the shared gitsnap resolver. It (a) reads
            # the merge parent graft-safely, (b) falls back to reconstructing the
            # pair from the PR's own commit SHAs when the timeline has no merge
            # SHA, and (c) caches trees per (repo,sha). Before this, ~57 PRs per
            # principle were dropped as `no_merge_sha`, and the loss was heavily
            # agent-biased (Copilot 22.7% of its PRs vs Codex 1.3%).
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
            before = ocp_snapshot(before_files, tset)
            after = ocp_snapshot(after_files, tset)
            reasons = pr_delta_reasons(before, after)
            profile = def_delta(before["index"], after["index"], tset)

            scoped = {k: v for k, v in after["chains"].items() if v["in_scope"]}
            emit("ok",
                 n_touched_modules=len(tset),
                 n_chains_in_scope=len(scoped),
                 n_smells_in_scope=sum(1 for v in scoped.values() if v["smell"]),
                 n_defs_added=profile.n_added,
                 n_defs_modified=profile.n_modified,
                 n_defs_removed=profile.n_removed,
                 n_classes_added=profile.n_classes_added,
                 ocp_risk_reasons=reasons,
                 ocp_risk=bool(len(reasons) >= C.OCP_RISK_MIN_HITS))

            # Emit EVERY in-scope chain (>=2 branches), not just smells, so the
            # branch-count distribution is observable for threshold calibration
            # BEFORE labeling -- circular otherwise (the ISP percentile method).
            for key, v in scoped.items():
                rows.append({**base, "row_type": "dispatch",
                             **{k: v[k] for k in (
                                 "module", "qualname", "subject", "kind",
                                 "n_branches", "lineno")},
                             "smell": v["smell"],
                             "ocp_risk": v["smell"]})
        except Exception as e:  # noqa: BLE001 -- one bad repo must not abort the batch
            print(f"  !! error on {pr.full_name}#{pr.pr_number}: {e}", flush=True)
            emit("error")

        if i % 25 == 0:
            pd.DataFrame(rows).to_parquet(C.OCP_RESULTS, index=False)

    results = pd.DataFrame(rows)
    results.to_parquet(C.OCP_RESULTS, index=False)
    print(f"\n[ocp] results written to {C.OCP_RESULTS}")
    pr_rows = results[results.row_type == "pr"]
    print(pr_rows["status"].value_counts().to_string())
    ok = pr_rows[pr_rows.status == "ok"]
    if len(ok):
        print("\nOCP-risk rate per agent (PR-level deltas):")
        print(ok.groupby("agent").agg(
            prs=("pr_id", "size"),
            ocp_risk_rate=("ocp_risk", "mean"),
            smells_in_scope=("n_smells_in_scope", "sum"),
            defs_added=("n_defs_added", "sum"),
            defs_modified=("n_defs_modified", "sum"),
        ).round(3).to_string())


if __name__ == "__main__":
    main()
