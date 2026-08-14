"""
Step 3-LSP-TS - Liskov Substitution evaluator over the TypeScript corpus.

Same before/after delta shape as run_ocp_ts.py (shared `gitsnap` helpers, tree
cache, thread pool, resumable), but the unit is an **override pair**: a
subclass method and the nearest ancestor method it overrides, both resolved
inside the PR's own tree.

Rows (`row_type`):
  pr        -- one per gold-set PR: status, delta reasons, lsp_risk
  override  -- EVERY in-scope override pair at head (violation True/False),
               so the head-state inventory is available for non-circular
               labeling exactly like the Python LSP parquet

Delta reasons (bucketed, mirroring run_lsp.py so languages compare directly):
  new_refused_bequest | new_incompatible_signature | new_type_variance

The deterministic FP suppressor (config.LSP_TS_SUPPRESS_FPS, default True) is
applied inside the sidecar; it is the TS twin of the Python suppressor that
lifted LSP-Python holdout precision 0.42 -> 1.00.

Run:  python vibebench/03_principles/lsp_ts/run_lsp_ts.py [--limit N] [--workers N]
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
PERMANENT = {"ok", "no_touched_modules", "repo_too_large"}

SIGNATURE_SIGNALS = {"param_required_added", "param_removed"}
VARIANCE_SIGNALS = {"param_type_narrowed", "return_type_widened"}


def snapshot(files: dict[str, str], touched_modules: set[str], suppress: bool) -> dict:
    """{(module, qualname, method): pair} for override pairs in touched modules."""
    facts = ts_bridge.analyze_tree(files, script="lsp", suppress_fps=suppress)
    if not facts.get("ok"):
        return {"__ERROR__": facts.get("error", "sidecar failed")}
    out = {}
    for p in facts["pairs"]:
        if p["module"] not in touched_modules:
            continue
        # The sidecar reports every signal it can see; WHICH of them count as a
        # violation is policy, and policy lives in Python (config).
        p = dict(p)
        p["violation"] = bool(set(p["signals"]) & set(C.LSP_TS_STRONG_SIGNALS))
        out[(p["module"], p["qualname"], p["method"])] = p
    return out


def reasons_for(signals: list[str]) -> list[str]:
    s = set(signals) & set(C.LSP_TS_STRONG_SIGNALS)
    out = []
    if "refused_bequest" in s:
        out.append("new_refused_bequest")
    if s & SIGNATURE_SIGNALS:
        out.append("new_incompatible_signature")
    if s & VARIANCE_SIGNALS:
        out.append("new_type_variance")
    return out


def process_pr(pr, suppress: bool) -> list[dict]:
    meta = {"pr_id": pr.pr_id, "agent": pr.agent, "full_name": pr.full_name,
            "pr_number": pr.pr_number, "merge_sha": pr.merge_sha}

    def pr_row(status, **kw):
        return [{**meta, "row_type": "pr", "status": status,
                 "n_touched_modules": kw.get("n_touched", 0),
                 "n_overrides_in_scope": kw.get("n_pairs", 0),
                 "n_violations_in_scope": kw.get("n_viol", 0),
                 "lsp_risk_reasons": kw.get("reasons", ""),
                 "lsp_risk": kw.get("risk", None)}]

    touched = gitsnap.touched_files(pr, EXTS, EXCL, extract_dir=C.EXTRACT_DIR_TS)
    tmods = {p.rsplit(".", 1)[0] for p in touched}
    if not tmods:
        return pr_row("no_touched_modules")

    d = gitsnap.ensure_repo(pr.full_name)
    with gitsnap.repo_lock(pr.full_name):
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

    before = snapshot(before_files, tmods, suppress)
    after = snapshot(after_files, tmods, suppress)
    if "__ERROR__" in before or "__ERROR__" in after:
        return pr_row("sidecar_failed", n_touched=len(tmods))

    reasons: list[str] = []
    for key, p in after.items():
        if not p["violation"]:
            continue
        prev = before.get(key)
        # New pair, or an existing pair that gained a signal it didn't have.
        if prev is None or not prev["violation"]:
            reasons.extend(reasons_for(p["signals"]))
        else:
            gained = set(p["signals"]) - set(prev["signals"])
            reasons.extend(reasons_for(sorted(gained)))
    reasons = sorted(set(reasons))

    n_viol = sum(1 for p in after.values() if p["violation"])
    rows = pr_row("ok", n_touched=len(tmods), n_pairs=len(after), n_viol=n_viol,
                  reasons="|".join(reasons), risk=len(reasons) >= 1)
    for (mod, qual, meth), p in after.items():
        rows.append({**meta, "row_type": "override", "status": "ok",
                     "module": mod, "qualname": qual, "base_id": p["base_id"],
                     "method": meth, "lineno": p["lineno"],
                     "file_path": p["file_path"],
                     "signals": ";".join(p["signals"]),
                     "violation": p["violation"]})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=C.FETCH_WORKERS)
    args = ap.parse_args()
    suppress = getattr(C, "LSP_TS_SUPPRESS_FPS", True)

    gold = pd.read_parquet(C.GOLD_SET_TS)
    if args.limit:
        gold = gold.head(args.limit)

    done, old_rows = set(), []
    if C.LSP_RESULTS_TS.exists() and not args.limit:
        prev = pd.read_parquet(C.LSP_RESULTS_TS)
        keep = set(prev[(prev.row_type == "pr")
                        & (prev.status.isin(PERMANENT))]["pr_id"])
        if keep:
            old_rows = prev[prev.pr_id.isin(keep)].to_dict("records")
            done = keep
            print(f"[lsp-ts] resuming: {len(done)} PRs already final", flush=True)

    todo = gold[~gold.pr_id.isin(done)]
    groups = list(todo.groupby("full_name", sort=False))
    print(f"[lsp-ts] {len(todo)} PRs across {len(groups)} repos, "
          f"{args.workers} workers, suppress_fps={suppress}", flush=True)

    rows, lock, n_done = list(old_rows), threading.Lock(), 0

    def do_repo(name, grp):
        out = []
        for pr in grp.itertuples(index=False):
            try:
                out.extend(process_pr(pr, suppress))
            except Exception as e:  # noqa: BLE001
                print(f"  !! {name}#{pr.pr_number}: {e}", flush=True)
                out.append({"pr_id": pr.pr_id, "agent": pr.agent,
                            "full_name": pr.full_name, "pr_number": pr.pr_number,
                            "merge_sha": pr.merge_sha, "row_type": "pr",
                            "status": "error", "lsp_risk": None})
        return out

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = {ex.submit(do_repo, n, g): (n, len(g)) for n, g in groups}
        for fut in as_completed(futs):
            name, n_grp = futs[fut]
            rows_got = fut.result()
            with lock:
                rows.extend(rows_got)
                n_done += n_grp
                if n_done % 25 < n_grp:
                    pd.DataFrame(rows).to_parquet(C.LSP_RESULTS_TS, index=False)
                    print(f"[lsp-ts] progress {n_done}/{len(todo)}", flush=True)

    df = pd.DataFrame(rows)
    df.to_parquet(C.LSP_RESULTS_TS, index=False)
    prs = df[df.row_type == "pr"]
    ov = df[df.row_type == "override"]
    print(f"\n[lsp-ts] wrote {C.LSP_RESULTS_TS}")
    print(f"[lsp-ts] PRs: {len(prs)}  in-scope override pairs: {len(ov)}")
    print(prs["status"].value_counts().to_string())
    if len(ov):
        print(f"\n[lsp-ts] violating pairs: {int(ov.violation.sum())} of {len(ov)}")
        sig = {}
        for s in ov[ov.violation]["signals"].dropna():
            for tok in str(s).split(";"):
                if tok:
                    sig[tok] = sig.get(tok, 0) + 1
        print("signals:", sig)
        print("\nviolating pairs by agent:")
        print(ov[ov.violation].groupby("agent").size().to_string())
    ok = prs[prs.status == "ok"]
    if len(ok):
        print("\n[lsp-ts] PR-level LSP risk rate by agent:")
        print(ok.groupby("agent")["lsp_risk"].agg(["size", "mean"]).round(4).to_string())


if __name__ == "__main__":
    main()
