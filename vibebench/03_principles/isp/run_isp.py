"""
Step 3-ISP - Interface Segregation evaluator: before/after PR delta.

For each gold-set PR, compare the repo's interface/implementer landscape at
the merge commit's first parent vs the merge commit itself. Like DIP, only
the DELTA is debited to the agent -- never pre-existing architecture.

Because interface *definitions* are sparse in AI-touched files (~4% of files,
~12% of PRs; measured 2026-07-20), the evaluator also scores implementer
classes in AI-touched modules against interfaces defined ANYWHERE in the
repo -- that is what keeps ISP measurable on typical PRs and what forces the
repo-tree (DIP-style) harness rather than SRP's per-file extraction.

Output: corpus/isp_results.parquet with three row types --
  pr          -- one per gold-set PR: status, delta reasons, isp_risk flag
  interface   -- head-state interface rows in scope (for labeling + inventory)
  implementer -- head-state (implementer, interface) pairs in scope

Scope (attribution, ripple-aware on BOTH row types): an interface is in scope if it is defined in an
AI-touched module OR has an implementer in one; an implementer pair is in
scope if the implementer's module is AI-touched. Entity rows come from the
AFTER snapshot only. PR-level delta reasons (thresholds in config.py):
  new_flagged_iface  -- PR introduces (or newly flags) a rubric-flagged interface
  widened_fat_iface  -- PR grows an existing interface's abstract contract and
                        it is fat at head
  new_forced_stub    -- a touched implementer newly stubs out >25% of an
                        interface's contract (the textbook ISP violation)
kind="implicit" interfaces are inventoried but never flagged in v1 (template-
method hooks are a known false-positive source; see config.py).

Resumable exactly like run_dip.py: permanent statuses kept, transient
failures retried, parquet checkpointed every 25 PRs.

Run:  python vibebench/03_principles/isp/run_isp.py            # full gold set
      python vibebench/03_principles/isp/run_isp.py --limit 5  # smoke test
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path
from statistics import fmean

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "isp"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import config as C  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 03_principles
import gitsnap  # noqa: E402
from interfaces import (  # noqa: E402
    build_class_index, client_usage, find_interfaces, forced_stub,
    implementer_stats, interface_hits, path_to_module,
)

# --- Git snapshot helpers: duplicated from run_dip.py on purpose.
# DIP's numbers are published-state; the project rule is to never mix
# refactoring risk with new-feature correctness.
# TODO: factor into 03_principles/gitsnap.py once ISP is label-validated.
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
        # later command in this repo (see PROJECT.md section 6). Clone dirs are
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

    Differs from run_dip.py: the parent is read from the commit header
    (`git cat-file -p`) instead of `rev-parse ^1`. When several PRs of the
    same repo are fetched shallowly, a later fetch can re-graft the shallow
    boundary AT this merge commit, after which rev-parse ^1 fails with
    "unknown revision" even though the commit object is present (the trap
    documented in PROJECT.md section 2; it cost ISP 24 PRs that DIP -- which
    fetched fresh -- had analyzed fine). cat-file always sees the real
    parent SHAs; a missing parent object is then fetched directly.
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


def isp_snapshot(files: dict[str, str], touched: set[str]) -> dict:
    """Full interface/implementer tables for one tree, plus the scoped subset.

    Returns {"ifaces": {iface_id: row}, "impls": {(impl_id, iface_id): row}};
    each iface row carries hits/flag over ALL its internal implementers (the
    contract is repo-wide), while `in_scope` marks what is attributable to
    this PR and becomes an entity row / delta reason.
    """
    idx = build_class_index(files)
    ifaces = find_interfaces(idx, C.ISP_IMPLICIT_MIN_STUBS)
    stats = implementer_stats(idx, ifaces)
    stats_by_iface: dict[str, list] = {}
    for s in stats:
        stats_by_iface.setdefault(s.iface_id, []).append(s)

    iface_rows: dict[str, dict] = {}
    for iface_id, iface in ifaces.items():
        istats = stats_by_iface.get(iface_id, [])
        hits = interface_hits(iface, istats, C.ISP_THRESHOLDS)
        usage = client_usage(idx, iface_id, iface.abstract)
        in_scope = (iface.module in touched
                    or any(s.impl_id.split(":")[0] in touched for s in istats))
        iface_rows[iface_id] = {
            "module": iface.module, "class_name": iface.name,
            "kind": iface.kind, "lineno": iface.lineno,
            "n_abstract_methods": len(iface.abstract),
            "n_implementers": len(istats),
            "mean_coverage": fmean([s.coverage for s in istats]) if istats else None,
            "n_forced_stub_impls": sum(
                1 for s in istats if forced_stub(s, C.ISP_THRESHOLDS)),
            "n_clients": len(usage),
            "mean_client_usage": fmean(usage) if usage else None,
            "hits": hits,
            # v1 policy: implicit interfaces are inventory, never flagged.
            "flagged": bool(len(hits) >= C.ISP_RISK_MIN_HITS
                            and iface.kind != "implicit"),
            "in_scope": in_scope,
        }

    impl_rows: dict[tuple[str, str], dict] = {}
    for s in stats:
        impl_rows[(s.impl_id, s.iface_id)] = {
            "module": s.impl_id.split(":")[0],
            "class_name": s.impl_id.split(":")[1],
            "iface_id": s.iface_id,
            "iface_kind": ifaces[s.iface_id].kind,
            "n_iface_methods": s.n_iface_methods,
            "n_implemented": s.n_implemented,
            "n_stubbed": s.n_stubbed,
            "coverage": s.coverage,
            "stub_ratio": s.stub_ratio,
            "forced_stub": forced_stub(s, C.ISP_THRESHOLDS),
            # ATTRIBUTION (ripple-aware), matching the interface row above: an
            # (implementer, interface) pair is charged when the PR touched
            # EITHER side. Scoping on the implementer alone missed the case
            # where an agent WIDENS a touched interface and thereby forces new
            # stubs into implementers it never opened -- which is precisely the
            # `new_forced_stub` reason this row feeds.
            "in_scope": (s.impl_id.split(":")[0] in touched
                         or ifaces[s.iface_id].module in touched),
        }

    return {"ifaces": iface_rows, "impls": impl_rows,
            "n_classes": len(idx.classes), "n_parse_errors": idx.n_parse_errors}


def pr_delta_reasons(before: dict, after: dict) -> list[str]:
    """Delta reasons over in-scope AFTER entities vs the full BEFORE tables
    (existence/flag checks use the unscoped before-state so a pre-existing
    flagged interface is never re-debited to this PR)."""
    reasons: set[str] = set()
    for iface_id, row in after["ifaces"].items():
        if not row["in_scope"]:
            continue
        prev = before["ifaces"].get(iface_id)
        if row["flagged"] and (prev is None or not prev["flagged"]):
            reasons.add("new_flagged_iface")
        if (prev is not None and "fat_iface" in row["hits"]
                and row["n_abstract_methods"] > prev["n_abstract_methods"]):
            reasons.add("widened_fat_iface")
    for key, row in after["impls"].items():
        if not row["in_scope"] or row["iface_kind"] == "implicit":
            continue
        prev = before["impls"].get(key)
        if row["forced_stub"] and (prev is None or not prev["forced_stub"]):
            reasons.add("new_forced_stub")
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
    if C.ISP_RESULTS.exists():
        prev = pd.read_parquet(C.ISP_RESULTS)
        pr_rows = prev[prev.row_type == "pr"]
        done_ids = set(pr_rows[pr_rows.status.isin(permanent)].pr_id)
        # Keep ALL row types of finished PRs; retried PRs re-emit everything.
        rows = prev[prev.pr_id.isin(done_ids)].to_dict("records")
        n_retry = len(pr_rows) - len(done_ids)
        print(f"[isp] resuming: {len(done_ids)} PRs done, {n_retry} failures to retry")

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
            before = isp_snapshot(before_files, tset)
            after = isp_snapshot(after_files, tset)
            reasons = pr_delta_reasons(before, after)

            scoped_ifaces = {k: v for k, v in after["ifaces"].items()
                             if v["in_scope"]}
            scoped_impls = {k: v for k, v in after["impls"].items()
                            if v["in_scope"]}
            emit("ok",
                 n_touched_modules=len(tset),
                 n_classes=after["n_classes"],
                 n_parse_errors=after["n_parse_errors"],
                 n_ifaces_repo=len(after["ifaces"]),
                 n_ifaces_in_scope=len(scoped_ifaces),
                 n_impl_pairs_in_scope=len(scoped_impls),
                 n_flagged_ifaces_in_scope=sum(
                     1 for v in scoped_ifaces.values() if v["flagged"]),
                 n_forced_stub_in_scope=sum(
                     1 for v in scoped_impls.values()
                     if v["forced_stub"] and v["iface_kind"] != "implicit"),
                 isp_risk_reasons=reasons,
                 isp_risk=bool(reasons))

            for iface_id, v in scoped_ifaces.items():
                rows.append({**base, "row_type": "interface",
                             "iface_id": iface_id,
                             **{k: v[k] for k in (
                                 "module", "class_name", "kind", "lineno",
                                 "n_abstract_methods", "n_implementers",
                                 "mean_coverage", "n_forced_stub_impls",
                                 "n_clients", "mean_client_usage", "hits")},
                             "isp_risk": v["flagged"]})
            for (impl_id, iface_id), v in scoped_impls.items():
                rows.append({**base, "row_type": "implementer",
                             "impl_id": impl_id,
                             **{k: v[k] for k in (
                                 "module", "class_name", "iface_id", "iface_kind",
                                 "n_iface_methods", "n_implemented", "n_stubbed",
                                 "coverage", "stub_ratio", "forced_stub")},
                             "isp_risk": bool(v["forced_stub"]
                                              and v["iface_kind"] != "implicit")})
        except Exception as e:  # noqa: BLE001 -- one bad repo must not abort the batch
            print(f"  !! error on {pr.full_name}#{pr.pr_number}: {e}", flush=True)
            emit("error")

        if i % 25 == 0:
            pd.DataFrame(rows).to_parquet(C.ISP_RESULTS, index=False)

    results = pd.DataFrame(rows)
    results.to_parquet(C.ISP_RESULTS, index=False)
    print(f"\n[isp] results written to {C.ISP_RESULTS}")
    pr_rows = results[results.row_type == "pr"]
    print(pr_rows["status"].value_counts().to_string())
    ok = pr_rows[pr_rows.status == "ok"]
    if len(ok):
        print("\nISP-risk rate per agent (PR-level deltas):")
        print(ok.groupby("agent").agg(
            prs=("pr_id", "size"),
            isp_risk_rate=("isp_risk", "mean"),
            ifaces_in_scope=("n_ifaces_in_scope", "sum"),
            impl_pairs=("n_impl_pairs_in_scope", "sum"),
            forced_stubs=("n_forced_stub_in_scope", "sum"),
        ).round(3).to_string())


if __name__ == "__main__":
    main()
