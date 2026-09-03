"""
Repair the touched-file list of every PR whose recorded count is impossible.

WHAT "IMPOSSIBLE" MEANS. `n_touched_files` counts only `.java` files that are
neither test nor machine-generated; GitHub's `changed_files` counts every path
the PR changed, in any language, deletions included. Ours is a strict subset of
theirs, so `ours > changed_files` cannot happen if the before/after pair we
diffed actually describes that PR. `07_verify/validate_file_counts.py` finds
them; this script replaces their file lists with GitHub's own.

WHY IT MATTERS RATHER THAN BEING A CAVEAT (measured 2026-09-02, first 413 PRs):
16.0% of analysed PRs were impossible, and the rate is AGENT-BIASED --
Copilot 23.0%, Claude_Code 10.5%, OpenAI_Codex 2.6%. Violations are counted
over touched files, so an inflated list inflates that agent's rate. A ~9x
difference in inflation between two agents is not a limitation of the method,
it is wrong data underneath the study's main comparison.

WHY THE API IS THE AUTHORITY HERE. Our local derivation has to INFER the PR's
starting point, and that inference is what fails: tier 2 diffs each commit
against its first parent and sweeps in unrelated work when a parent lies
outside the PR; tier 3's merge-base returns the head itself when the head is
already an ancestor of the base tip. GitHub does not infer -- it records what
the PR changed. Verified against the local method on 25 PRs: 18 agreed exactly,
and every disagreement was ours over-counting.

TRUNCATION IS HANDLED. The /files endpoint caps how many entries it serves, so
a list shorter than `changed_files` is not trusted; such PRs are left alone and
reported, never rewritten from a partial answer.

Run:  python vibebench/02_clone/repair_file_lists.py [--apply] [--workers N]
      (dry run by default -- it prints what it WOULD change and writes nothing)
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
import safeio               # noqa: E402

AUDIT = C.CORPUS_DIR / "file_count_audit.parquet"
_lock = threading.Lock()
_stats = {"repaired": 0, "unchanged": 0, "truncated": 0, "failed": 0}


def api_files(full_name: str, pr_number: int):
    """([(status, path)], changed_files) or (None, None)."""
    files = None
    for attempt in (1, 2):
        try:
            r = subprocess.run(
                ["gh", "api", f"repos/{full_name}/pulls/{pr_number}/files",
                 "--paginate", "-q", '.[] | .status + "\\t" + .filename'],
                capture_output=True, text=True, timeout=180, **gitsnap.NO_WINDOW)
        except FileNotFoundError:
            sys.exit("gh is not on PATH")
        except (subprocess.TimeoutExpired, OSError):
            r = None
        if r is not None and r.returncode == 0:
            files = []
            for ln in r.stdout.splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                st, _, name = ln.partition("\t")
                files.append((st, name))
            break
        if attempt == 1 and not gitsnap.network_is_up():
            gitsnap.wait_for_network()
            continue
        break
    if files is None:
        return None, None
    try:
        r2 = subprocess.run(
            ["gh", "api", f"repos/{full_name}/pulls/{pr_number}",
             "-q", ".changed_files"],
            capture_output=True, text=True, timeout=90, **gitsnap.NO_WINDOW)
        n = int(r2.stdout.strip()) if r2.returncode == 0 and r2.stdout.strip() else None
    except Exception:                                  # noqa: BLE001
        n = None
    return files, n


def repair(row, apply: bool) -> tuple[str, int, int]:
    files, n_changed = api_files(row.full_name, int(row.pr_number))
    if files is None:
        return "failed", 0, 0
    if n_changed is not None and len(files) < n_changed:
        # Partial answer: refuse to rewrite a list from data we know is short.
        return "truncated", 0, 0
    keep = [name for st, name in files
            if st != "removed"
            and gitsnap.is_source_file(name, C.JAVA_SOURCE_EXTENSIONS,
                                       C.JAVA_EXCLUDED_PATH_PARTS)]
    cache = Path(C.EXTRACT_DIR_JAVA) / str(row.pr_id) / "_touched.json"
    old = []
    if cache.exists():
        try:
            old = json.loads(cache.read_text())
        except Exception:                              # noqa: BLE001
            old = []
    if sorted(old) == sorted(keep):
        return "unchanged", len(old), len(keep)
    if apply:
        cache.parent.mkdir(parents=True, exist_ok=True)
        safeio.write_json(cache, keep)
    return "repaired", len(old), len(keep)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="actually rewrite the lists (default: dry run)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    if not AUDIT.exists():
        sys.exit(f"no audit at {AUDIT} -- run 07_verify/validate_file_counts.py first")
    aud = pd.read_parquet(AUDIT)
    bad = aud[aud.impossible.fillna(False)]
    if a.limit:
        bad = bad.head(a.limit)
    print(f"[repair] {len(aud):,} audited, {len(bad):,} impossible "
          f"({100*len(bad)/max(len(aud),1):.1f}%)")
    print(f"[repair] mode: {'APPLY (writing)' if a.apply else 'DRY RUN (writing nothing)'}",
          flush=True)
    if not len(bad):
        return

    t0, deltas = time.time(), []
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(repair, r, a.apply): r for r in bad.itertuples()}
        for i, fut in enumerate(as_completed(futs), 1):
            r = futs[fut]
            try:
                res, n_old, n_new = fut.result()
            except Exception:                          # noqa: BLE001
                res, n_old, n_new = "failed", 0, 0
            with _lock:
                _stats[res] = _stats.get(res, 0) + 1
                if res == "repaired":
                    deltas.append((r.full_name, r.pr_number, n_old, n_new))
            if i % 50 == 0 or i == len(bad):
                el = time.time() - t0
                print(f"[repair] {i}/{len(bad)}  repaired={_stats['repaired']} "
                      f"unchanged={_stats['unchanged']} truncated={_stats['truncated']} "
                      f"failed={_stats['failed']}  eta "
                      f"{(len(bad)-i)/max(i/el,1e-9)/60:.0f} min", flush=True)

    print(f"\n[repair] {_stats}")
    if deltas:
        tot_old = sum(d[2] for d in deltas)
        tot_new = sum(d[3] for d in deltas)
        print(f"[repair] touched-file count across repaired PRs: "
              f"{tot_old:,} -> {tot_new:,} ({tot_new-tot_old:+,})")
        print("\n  biggest corrections:")
        for fn, num, o, n in sorted(deltas, key=lambda x: x[2]-x[3], reverse=True)[:10]:
            print(f"    {fn}#{num}: {o} -> {n}")
    if not a.apply:
        print("\n[repair] DRY RUN -- nothing was written. Re-run with --apply.")
    else:
        print("\n[repair] next: re-run run_java.py so the repaired PRs are re-measured")


if __name__ == "__main__":
    main()
