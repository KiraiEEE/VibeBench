"""
Step 3-OCP-TS - Open/Closed evaluator over the TypeScript corpus.

The first TS *delta* harness: for each gold-set PR it reconstructs the repo at
the merge commit's first parent (state just before) and at the merge commit
(just after), extracts every type/tag dispatch chain from both via the ts-morph
sidecar, and reports what the PR CHANGED -- a new wide switch, or an existing
one widened. Only chains in modules the PR touched are in scope, so pre-existing
structure the agent merely edited around is never charged to it.

Mirrors run_ocp.py (Python) exactly in shape and vocabulary so the two languages
are directly comparable, but is built on the shared `gitsnap` helpers with the
per-(repo,sha) tree cache, and parallelises across repos.

Rows (`row_type`):
  pr        -- one per gold-set PR: status, delta reasons, ocp_risk
  dispatch  -- EVERY in-scope chain at head (smell True/False), so the
               branch-count threshold can be calibrated non-circularly

Two-phase thresholds (the OCP-Python lesson):
  config.OCP_TS_THRESHOLDS is None -> CALIBRATION MODE: chains are emitted and
  the branch-count distribution is printed; nothing is flagged. Set the
  thresholds from those percentiles, then re-run to get flags.

Run:  python vibebench/03_principles/ocp_ts/run_ocp_ts.py
      python vibebench/03_principles/ocp_ts/run_ocp_ts.py --limit 10 --workers 2
"""
from __future__ import annotations

import argparse
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))   # 03_principles (gitsnap, ts_bridge)
sys.path.insert(0, str(_HERE.parents[2]))   # vibebench (config)
import config as C  # noqa: E402
import gitsnap  # noqa: E402
import ts_bridge  # noqa: E402

EXTS = C.TS_SOURCE_EXTENSIONS
EXCL = C.TS_EXCLUDED_PATH_PARTS

# Permanent = never retried on re-run; transient failures are retried.
# NOTE: `no_merge_sha` is deliberately NOT permanent since 2026-07-28 -- with
# commit SHAs now attached for every agent, gitsnap.delta_pair can reconstruct
# the pair from the PR's own commits, so those PRs are worth retrying.
PERMANENT = {"ok", "no_touched_modules", "repo_too_large"}


def is_smell(chain: dict, thresholds: dict | None, suppress_closed: bool) -> bool:
    """Wide enough to be an OCP 'switch statement' smell, unless the type set is
    closed (typeof results / built-in constructors are exhaustive by
    construction -- see analyze_ocp.js)."""
    if thresholds is None:
        return False
    if suppress_closed and chain.get("closed_set"):
        return False
    key = ("type_min_branches" if chain["kind"] in ("instanceof", "typeof")
           else "tag_min_branches")
    return chain["n_branches"] >= thresholds[key]


def snapshot(files: dict[str, str], touched_modules: set[str],
             thresholds, suppress_closed) -> dict:
    """{(module, qualname, subject): chain} for chains in touched modules."""
    facts = ts_bridge.analyze_tree(files, script="ocp")
    if not facts.get("ok"):
        return {"__ERROR__": facts.get("error", "sidecar failed")}
    out: dict[tuple, dict] = {}
    for ch in facts["chains"]:
        if ch["module"] not in touched_modules:
            continue
        key = (ch["module"], ch["qualname"], ch["subject"])
        prev = out.get(key)
        if prev is not None and prev["n_branches"] >= ch["n_branches"]:
            continue  # keep the widest chain per location (defensive)
        ch = dict(ch)
        ch["smell"] = is_smell(ch, thresholds, suppress_closed)
        out[key] = ch
    return out


def process_pr(pr, thresholds, suppress_closed) -> list[dict]:
    meta = {"pr_id": pr.pr_id, "agent": pr.agent, "full_name": pr.full_name,
            "pr_number": pr.pr_number, "merge_sha": pr.merge_sha}

    def pr_row(status, **kw):
        return [{**meta, "row_type": "pr", "status": status,
                 "n_touched_modules": kw.get("n_touched", 0),
                 "n_chains_in_scope": kw.get("n_chains", 0),
                 "n_smells_in_scope": kw.get("n_smells", 0),
                 "ocp_risk_reasons": kw.get("reasons", ""),
                 "ocp_risk": kw.get("risk", None)}]

    touched = gitsnap.touched_files(pr, EXTS, EXCL, extract_dir=C.EXTRACT_DIR_TS)
    tmods = {p.rsplit(".", 1)[0] for p in touched}
    if not tmods:
        return pr_row("no_touched_modules")

    d = gitsnap.ensure_repo(pr.full_name)
    with gitsnap.repo_lock(pr.full_name):
        # delta_pair falls back to the PR's own commit set when the timeline has
        # no merge SHA -- that loss was concentrated in Copilot (see gitsnap).
        pair = gitsnap.delta_pair(d, pr)
        if pair is None:
            has_sha = isinstance(pr.merge_sha, str) and bool(pr.merge_sha)
            return pr_row("fetch_failed" if has_sha else "no_merge_sha",
                          n_touched=len(tmods))
        before_sha, after_sha = pair
        before_files = gitsnap.src_tree(d, before_sha, EXTS, EXCL, pr.full_name)
        after_files = gitsnap.src_tree(d, after_sha, EXTS, EXCL, pr.full_name)

    if before_files is None or after_files is None:
        return pr_row("archive_failed", n_touched=len(tmods))
    if gitsnap.TOO_LARGE in before_files or gitsnap.TOO_LARGE in after_files:
        return pr_row("repo_too_large", n_touched=len(tmods))

    before = snapshot(before_files, tmods, thresholds, suppress_closed)
    after = snapshot(after_files, tmods, thresholds, suppress_closed)
    if "__ERROR__" in before or "__ERROR__" in after:
        return pr_row("sidecar_failed", n_touched=len(tmods))

    reasons = []
    for key, ch in after.items():
        if not ch["smell"]:
            continue
        if key not in before:
            reasons.append("new_dispatch_smell")
        elif ch["n_branches"] > before[key]["n_branches"]:
            reasons.append("dispatch_widened")
    reasons = sorted(set(reasons))

    n_smells = sum(1 for c in after.values() if c["smell"])
    rows = pr_row(
        "ok", n_touched=len(tmods), n_chains=len(after), n_smells=n_smells,
        reasons="|".join(reasons),
        risk=(len(reasons) >= 1) if thresholds is not None else None,
    )
    for (mod, qual, subj), ch in after.items():
        rows.append({**meta, "row_type": "dispatch", "status": "ok",
                     "module": mod, "qualname": qual, "subject": subj,
                     "kind": ch["kind"], "n_branches": ch["n_branches"],
                     "lineno": ch["lineno"], "file_path": ch["file_path"],
                     "closed_set": ch["closed_set"],
                     "type_names": "|".join(map(str, ch.get("type_names") or [])),
                     "smell": ch["smell"]})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=C.FETCH_WORKERS)
    args = ap.parse_args()

    thresholds = C.OCP_TS_THRESHOLDS
    suppress_closed = getattr(C, "OCP_TS_CLOSED_SET_SUPPRESS", True)

    gold = pd.read_parquet(C.GOLD_SET_TS)
    if args.limit:
        gold = gold.head(args.limit)

    # Resume: keep permanent statuses from a previous run, retry the rest.
    done: set = set()
    old_rows: list[dict] = []
    if C.OCP_RESULTS_TS.exists() and not args.limit:
        prev = pd.read_parquet(C.OCP_RESULTS_TS)
        keep_ids = set(prev[(prev.row_type == "pr")
                            & (prev.status.isin(PERMANENT))]["pr_id"])
        if keep_ids:
            old_rows = prev[prev.pr_id.isin(keep_ids)].to_dict("records")
            done = keep_ids
            print(f"[ocp-ts] resuming: {len(done)} PRs already final", flush=True)

    todo = gold[~gold.pr_id.isin(done)]
    groups = list(todo.groupby("full_name", sort=False))
    print(f"[ocp-ts] {len(todo)} PRs across {len(groups)} repos, "
          f"{args.workers} workers, thresholds="
          f"{'CALIBRATION (none)' if thresholds is None else thresholds}", flush=True)

    rows: list[dict] = list(old_rows)
    lock = threading.Lock()
    n_done = 0

    def do_repo(name, grp):
        out = []
        for pr in grp.itertuples(index=False):
            try:
                out.extend(process_pr(pr, thresholds, suppress_closed))
            except Exception as e:  # noqa: BLE001 -- one PR must not abort the batch
                print(f"  !! {name}#{pr.pr_number}: {e}", flush=True)
                out.append({"pr_id": pr.pr_id, "agent": pr.agent,
                            "full_name": pr.full_name, "pr_number": pr.pr_number,
                            "merge_sha": pr.merge_sha, "row_type": "pr",
                            "status": "error", "ocp_risk": None})
        return out

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = {ex.submit(do_repo, n, g): (n, len(g)) for n, g in groups}
        for fut in as_completed(futs):
            name, n_grp = futs[fut]
            got = fut.result()
            with lock:
                rows.extend(got)
                n_done += n_grp
                if n_done % 25 < n_grp:
                    pd.DataFrame(rows).to_parquet(C.OCP_RESULTS_TS, index=False)
                    print(f"[ocp-ts] progress {n_done}/{len(todo)}", flush=True)

    df = pd.DataFrame(rows)
    df.to_parquet(C.OCP_RESULTS_TS, index=False)
    prs = df[df.row_type == "pr"]
    disp = df[df.row_type == "dispatch"]
    print(f"\n[ocp-ts] wrote {C.OCP_RESULTS_TS}")
    print(f"[ocp-ts] PRs: {len(prs)}  in-scope chains: {len(disp)}")
    print(prs["status"].value_counts().to_string())

    if len(disp):
        if thresholds is None:
            print("\n[ocp-ts] CALIBRATION -- branch-count distribution "
                  "(choose thresholds BEFORE labeling):")
            typek = disp[disp.kind.isin(["instanceof", "typeof"])]["n_branches"]
            tagk = disp[disp.kind.isin(["switch_tag", "value_switch"])]["n_branches"]
            for nm, s in (("type-based (instanceof/typeof)", typek),
                          ("tag-based (switch_tag/value_switch)", tagk)):
                if len(s):
                    q = " ".join(f"p{int(p*100)}={s.quantile(p):.1f}"
                                 for p in (0.5, 0.75, 0.9, 0.95))
                    print(f"  {nm}: n={len(s)} {q} max={s.max()}")
            print("\n  by kind:")
            print(disp.groupby("kind")["n_branches"]
                  .agg(["size", "median", "max"]).to_string())
            print(f"\n  closed_set chains (suppressible): "
                  f"{int(disp.closed_set.sum())} of {len(disp)}")
        else:
            print(f"\n[ocp-ts] smells: {int(disp.smell.sum())} of {len(disp)} chains")
            ok = prs[prs.status == "ok"]
            if len(ok):
                print("\n[ocp-ts] PR-level OCP risk rate by agent:")
                print(ok.groupby("agent")["ocp_risk"].agg(["size", "mean"])
                      .round(4).to_string())


if __name__ == "__main__":
    main()
