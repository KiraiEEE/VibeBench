"""
Repair inflated file lists AND recover lost ones, in one batched pass.

TWO POPULATIONS, ONE FIX. Both come from the same root cause -- a before/after
pair that does not describe the PR -- and both are cured by asking GitHub what
the PR actually changed instead of inferring it locally:

  REPAIR   374 analysed PRs whose recorded file count EXCEEDS GitHub's own
           `changedFiles`. Ours counts only non-test, non-generated `.java`, so
           it is a strict subset of GitHub's total and cannot legitimately be
           larger. Agent-biased: Copilot 18.2%, Claude_Code 10.2%,
           OpenAI_Codex 10.2%, which distorts the study's main comparison.

  RECOVER  2,444 PRs dropped as `no_touched_files` because tier 3's merge-base
           returned the PR head itself, making every diff empty.

WHY BATCHED (measured 2026-09-02). Over REST this is two round trips per PR and
the link on this machine drops for minutes at a time, so the binding constraint
is REQUEST COUNT: the REST audit stalled repeatedly and managed 1,253 PRs/hour.
The same work over batched GraphQL ran at 41,000/hour. ~2,800 PRs become ~60
requests instead of ~5,600.

SAFETY RULES, each one a bug this project already paid for:
  * a TRUNCATED list is never written -- a partial answer that looks complete is
    the failure mode being repaired, not a cheaper version of the answer;
  * a PR GitHub cannot find is recorded as unresolved, never as "changed
    nothing";
  * DELETED paths are excluded, since a removed file has no content in the
    after-tree and cannot carry a violation;
  * writes are atomic, so a power cut cannot leave half a list;
  * dry run by default.

Run:  python vibebench/02_clone/fix_file_lists.py            # dry run
      python vibebench/02_clone/fix_file_lists.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "03_principles"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C          # noqa: E402
import gitsnap              # noqa: E402
import safeio               # noqa: E402
import gh_files             # noqa: E402

AUDIT = C.CORPUS_DIR / "file_count_audit.parquet"
BATCH = 50
_lock = threading.Lock()


def _mark_verified(d: Path, res) -> None:
    """Record that GitHub itself answered for this PR.

    Written beside `_touched.json`. Its presence is what lets the verifier
    accept an empty file list as a FINDING ("no Java in this PR") rather than a
    FAILURE ("we could not work out what this PR changed").
    """
    try:
        safeio.write_json(d / "_verified.json",
                          {"source": "github_api",
                           "changed_files": res.changed_files,
                           "complete": bool(res.complete)})
    except Exception:                                  # noqa: BLE001
        pass


def _targets(pr: pd.DataFrame) -> pd.DataFrame:
    """The PRs to fix, tagged by why."""
    rows = []
    lost = pr[pr.status == "no_touched_files"]
    base = Path(C.EXTRACT_DIR_JAVA)
    for r in lost.itertuples():
        if not (base / str(r.pr_id) / "_touched.json").exists():
            rows.append((r.pr_id, r.full_name, r.pr_number, "recover"))
    if AUDIT.exists():
        aud = pd.read_parquet(AUDIT)
        for r in aud[aud.impossible.fillna(False)].itertuples():
            rows.append((r.pr_id, r.full_name, r.pr_number, "repair"))
    return pd.DataFrame(rows, columns=["pr_id", "full_name", "pr_number", "why"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    pr = pd.read_parquet(C.CORPUS_DIR / "java_pr_results.parquet")
    tg = _targets(pr).drop_duplicates("pr_id")
    if a.limit:
        tg = tg.head(a.limit)
    n_rec = int((tg.why == "recover").sum())
    n_rep = int((tg.why == "repair").sum())
    print(f"[fix] {len(tg):,} PRs to fix: {n_rec:,} recover + {n_rep:,} repair")
    print(f"[fix] mode: {'APPLY' if a.apply else 'DRY RUN (writes nothing)'}", flush=True)
    if not len(tg):
        return

    keymap = {(r.full_name, int(r.pr_number)): r for r in tg.itertuples()}
    keys = list(keymap)
    batches = [keys[i:i + BATCH] for i in range(0, len(keys), BATCH)]
    print(f"[fix] {len(batches)} GraphQL requests instead of {2*len(keys):,} REST calls",
          flush=True)

    st = {"written": 0, "same": 0, "truncated": 0, "notfound": 0, "failed": 0}
    deltas, t0, seen = [], time.time(), 0

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(gh_files.fetch_batch, b): b for b in batches}
        for fut in as_completed(futs):
            b = futs[fut]
            try:
                got = fut.result()
            except Exception:                          # noqa: BLE001
                got = {}
            with _lock:
                for key in b:
                    row = keymap[key]
                    res = got.get(key)
                    if res is None:
                        st["failed"] += 1
                        continue
                    if not res.found:
                        st["notfound"] += 1
                        continue
                    if not res.complete:
                        st["truncated"] += 1
                        continue
                    keep = res.source_paths(C.JAVA_SOURCE_EXTENSIONS,
                                            C.JAVA_EXCLUDED_PATH_PARTS)
                    cache = Path(C.EXTRACT_DIR_JAVA) / str(row.pr_id) / "_touched.json"
                    old = []
                    if cache.exists():
                        try:
                            old = json.loads(cache.read_text())
                        except Exception:              # noqa: BLE001
                            old = []
                    if sorted(old) == sorted(keep):
                        # STILL RECORD THE VERIFICATION even when the list did
                        # not change. An EMPTY list means two very different
                        # things -- "GitHub confirms this PR touches no Java"
                        # and "our diff failed and produced nothing" -- and the
                        # second is the bug that lost 2,948 PRs. Without a
                        # marker the two are indistinguishable on disk, and the
                        # verifier has to treat every empty list as suspect.
                        if a.apply:
                            cache.parent.mkdir(parents=True, exist_ok=True)
                            _mark_verified(cache.parent, res)
                        st["same"] += 1
                        continue
                    if a.apply:
                        cache.parent.mkdir(parents=True, exist_ok=True)
                        safeio.write_json(cache, keep)
                        _mark_verified(cache.parent, res)
                    st["written"] += 1
                    deltas.append((row.why, row.full_name, row.pr_number,
                                   len(old), len(keep)))
                seen += len(b)
            el = time.time() - t0
            print(f"[fix] {seen}/{len(keys)}  written={st['written']} same={st['same']} "
                  f"trunc={st['truncated']} notfound={st['notfound']} "
                  f"failed={st['failed']}  {seen/max(el,1e-9)*3600:,.0f}/h  "
                  f"eta {(len(keys)-seen)/max(seen/max(el,1e-9),1e-9)/60:.0f} min",
                  flush=True)

    print(f"\n[fix] {st}")
    rep = [d for d in deltas if d[0] == "repair"]
    rec = [d for d in deltas if d[0] == "recover"]
    if rep:
        o, n = sum(d[3] for d in rep), sum(d[4] for d in rep)
        print(f"[fix] REPAIR: {len(rep):,} PRs, touched files {o:,} -> {n:,} ({n-o:+,})")
        print("   biggest corrections:")
        for _, fn, num, x, y in sorted(rep, key=lambda d: d[3]-d[4], reverse=True)[:8]:
            print(f"     {fn}#{num}: {x} -> {y}")
    if rec:
        withjava = sum(1 for d in rec if d[4] > 0)
        print(f"[fix] RECOVER: {len(rec):,} lists written, {withjava:,} contain Java "
              f"({100*withjava/max(len(rec),1):.0f}%)")
    if not a.apply:
        print("\n[fix] DRY RUN -- nothing written. Re-run with --apply.")
    else:
        print("\n[fix] next: re-run run_java.py to re-measure the changed PRs")


if __name__ == "__main__":
    main()
