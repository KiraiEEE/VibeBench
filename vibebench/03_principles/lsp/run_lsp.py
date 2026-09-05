"""
Step 3-LSP - Liskov Substitution evaluator: before/after PR delta.

For each gold-set PR, compare the repo's override landscape at the merge
commit's first parent vs the merge commit itself. Like DIP/ISP/OCP, only the
DELTA is debited to the agent -- pre-existing incompatible overrides are never
counted.

LSP's canonical static signal is a type checker's override-compatibility rule
(mypy `[override]` / Pyright reportIncompatibleMethodOverride): an override that
strengthens a precondition or weakens a postcondition breaks substitutability.
`overrides.py` replicates that rule deterministically on the AST (no importable
environment), adds the semantic type-variance dimension (contravariant params /
covariant returns over the internal class hierarchy) and the behavioral
"refused bequest" smell. See overrides.py for the signal definitions.

Output: corpus/lsp_results.parquet with two row types --
  pr        -- one per gold-set PR: status, delta reasons, lsp_risk flag, plus
               raw counts of overrides / violations in scope.
  override  -- head-state override pairs in AI-touched modules (every in-scope
               override, violating or not, for non-circular calibration +
               labeling).

Scope (attribution): an override pair is in scope iff the SUBCLASS's OR the BASE's module is
AI-touched. Entity rows come from the AFTER snapshot only. PR-level delta reasons
(bucketed by which signal family newly appeared on an in-scope override):
  new_refused_bequest      -- a new override that refuses an inherited contract.
  new_incompatible_signature -- a new override with param_required_added /
                              param_removed (the mypy [override] break).
  new_type_variance        -- a new override with param_type_narrowed /
                              return_type_widened.

Resumable exactly like run_ocp.py: permanent statuses kept, transient failures
retried, parquet checkpointed every 25 PRs.

Run:  python vibebench/03_principles/lsp/run_lsp.py            # full gold set
      python vibebench/03_principles/lsp/run_lsp.py --limit 5  # smoke test
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lsp"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import config as C  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 03_principles
import gitsnap  # noqa: E402
from overrides import (  # noqa: E402
    build_index, find_overrides, is_lsp_violation,
)
from graph import path_to_module  # noqa: E402

# --- Git snapshot helpers: duplicated from run_ocp.py on purpose (same reason:
# each delta harness stays self-contained). Uses the graft-safe cat-file parent
# read (PROJECT.md section 2 trap).
MAX_PY_FILES = 10_000

# Which signals roll up into which PR-level delta reason.
_SIG_REASON = {
    "refused_bequest": "new_refused_bequest",
    "param_required_added": "new_incompatible_signature",
    "param_removed": "new_incompatible_signature",
    "param_type_narrowed": "new_type_variance",
    "return_type_widened": "new_type_variance",
}



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
    Parent read from the commit header (never `rev-parse ^1`: a later shallow
    fetch can re-graft the boundary and break rev-parse -- the trap that cost
    ISP 24 PRs, PROJECT.md section 2)."""
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
        return None
    parent = parents[0]
    if not have_commit(d, parent):
        r = run_git(["fetch", "--depth", "1", "-q", "origin", parent], cwd=d)
        if r is None or r.returncode != 0:
            return None
    return parent if have_commit(d, parent) else None


def py_tree(d: Path, sha: str) -> dict[str, str] | None:
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
    if getattr(pr, "files_source", "dataset") == "git_diff":
        cache = C.EXTRACT_DIR / str(pr.pr_id) / "_touched.json"
        return json.loads(cache.read_text()) if cache.exists() else []
    return [f for f in pr.touched_files if f.lower().endswith(".py")]
# --- end of duplicated harness helpers.


def lsp_snapshot(files: dict[str, str], touched: set[str]) -> dict:
    """Override pairs for one tree, keyed by (module, subclass, method), with
    scope + signals + violation flag."""
    index = build_index(files)
    overrides: dict[tuple[str, str, str], dict] = {}
    for ov in find_overrides(index, suppress_fps=getattr(C, "LSP_SUPPRESS_FPS", False)):
        overrides[ov.key] = {
            "module": ov.module, "subclass": ov.subclass,
            "base_id": ov.base_id, "method": ov.method, "lineno": ov.lineno,
            "signals": list(ov.signals),
            "violation": is_lsp_violation(ov, C.LSP_THRESHOLDS),
            # ATTRIBUTION (ripple-aware). A pair is charged to the PR when the
            # PR touched EITHER side of the relation. Scoping on the subclass
            # alone missed the reverse direction: an agent editing a BASE class
            # can break substitutability for subclasses in files it never
            # opened, and those subclasses are untouched by construction.
            # Measured exposure before the fix: 4% of PRs, ~0.5x the attributed
            # scope where it occurred.
            "in_scope": (ov.module in touched
                         or ov.base_id.split(":")[0] in touched),
        }
    return {"overrides": overrides}


def pr_delta_reasons(before: dict, after: dict) -> list[str]:
    """Delta reasons over in-scope AFTER overrides vs the full BEFORE table
    (a pre-existing violation is never re-debited to this PR)."""
    reasons: set[str] = set()
    for key, row in after["overrides"].items():
        if not row["in_scope"] or not row["violation"]:
            continue
        prev = before["overrides"].get(key)
        prev_signals = set(prev["signals"]) if prev is not None else set()
        # Signals that are NEW on this override (absent before) count as caused.
        new_signals = set(row["signals"]) - prev_signals
        for sig in new_signals:
            reason = _SIG_REASON.get(sig)
            if reason:
                reasons.add(reason)
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
    if C.LSP_RESULTS.exists():
        prev = pd.read_parquet(C.LSP_RESULTS)
        pr_rows = prev[prev.row_type == "pr"]
        done_ids = set(pr_rows[pr_rows.status.isin(permanent)].pr_id)
        rows = prev[prev.pr_id.isin(done_ids)].to_dict("records")
        n_retry = len(pr_rows) - len(done_ids)
        print(f"[lsp] resuming: {len(done_ids)} PRs done, {n_retry} failures to retry")

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
            rows.append({**base, "row_type": "pr", "status": status, **extra})

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
            before = lsp_snapshot(before_files, tset)
            after = lsp_snapshot(after_files, tset)
            reasons = pr_delta_reasons(before, after)

            scoped = {k: v for k, v in after["overrides"].items() if v["in_scope"]}
            emit("ok",
                 n_touched_modules=len(tset),
                 n_overrides_in_scope=len(scoped),
                 n_violations_in_scope=sum(1 for v in scoped.values()
                                           if v["violation"]),
                 lsp_risk_reasons=reasons,
                 lsp_risk=bool(len(reasons) >= C.LSP_RISK_MIN_HITS))

            # Emit EVERY in-scope override pair (violating or not) so signal
            # frequency + precision are measured on the full population.
            for key, v in scoped.items():
                rows.append({**base, "row_type": "override",
                             "module": v["module"], "qualname": v["subclass"],
                             "base_id": v["base_id"], "method": v["method"],
                             "lineno": v["lineno"],
                             "signals": ";".join(v["signals"]),
                             "violation": v["violation"],
                             "lsp_risk": v["violation"]})
        except Exception as e:  # noqa: BLE001 -- one bad repo must not abort the batch
            print(f"  !! error on {pr.full_name}#{pr.pr_number}: {e}", flush=True)
            emit("error")

        if i % 25 == 0:
            _atomic_parquet(pd.DataFrame(rows), C.LSP_RESULTS)

    results = pd.DataFrame(rows)
    _atomic_parquet(results, C.LSP_RESULTS)
    print(f"\n[lsp] results written to {C.LSP_RESULTS}")
    pr_rows = results[results.row_type == "pr"]
    print(pr_rows["status"].value_counts().to_string())
    ok = pr_rows[pr_rows.status == "ok"]
    if len(ok):
        print("\nLSP-risk rate per agent (PR-level deltas):")
        print(ok.groupby("agent").agg(
            prs=("pr_id", "size"),
            lsp_risk_rate=("lsp_risk", "mean"),
            overrides_in_scope=("n_overrides_in_scope", "sum"),
            violations_in_scope=("n_violations_in_scope", "sum"),
        ).round(3).to_string())


if __name__ == "__main__":
    main()
