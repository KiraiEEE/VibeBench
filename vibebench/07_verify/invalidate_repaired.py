"""
Invalidate the analysis rows of PRs whose touched-file list was repaired.

WHY THIS IS NEEDED. `run_java.py` treats `ok` as a PERMANENT status, so a PR
that was already analysed is never revisited. That is correct while the inputs
are stable -- but the repair pass CHANGED the inputs: 257 PRs had their file
lists corrected, dropping 11,707 recorded touched files to 1,586. Their stored
results describe files those PRs never touched. Left alone, `run_java.py` would
skip every one of them and the corrected lists would have no effect.

The recovered PRs need no help here: `no_touched_files` is deliberately NOT
permanent, so they are retried automatically.

WHAT IT DOES. Drops the affected PRs from the PR frame and from all four entity
parquets, after writing a timestamped backup of each. Dropping rather than
flagging is deliberate: a stale entity row that is merely marked would still be
counted by any report that forgot to filter it, and this project has already
been bitten by exactly that shape of bug.

Run:  python vibebench/07_verify/invalidate_repaired.py [--apply]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

AUDIT = C.CORPUS_DIR / "file_count_audit.parquet"
PR = C.CORPUS_DIR / "java_pr_results.parquet"
ENTITIES = {
    "srp": C.SRP_RESULTS_JAVA,
    "ocp": C.OCP_RESULTS_JAVA,
    "isp": C.ISP_RESULTS_JAVA,
    "lsp": C.LSP_RESULTS_JAVA,
    "dip": C.DIP_RESULTS_JAVA,
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    if not AUDIT.exists():
        sys.exit(f"no audit at {AUDIT}")
    aud = pd.read_parquet(AUDIT)
    bad = set(aud[aud.impossible.fillna(False)].pr_id)
    print(f"[invalidate] {len(bad):,} PRs flagged impossible by the audit")
    if not bad:
        return

    stamp = time.strftime("%Y%m%d-%H%M%S")
    for name, path in [("pr", PR)] + list(ENTITIES.items()):
        if not path.exists():
            print(f"  {name:<4} missing, skipped")
            continue
        d = pd.read_parquet(path)
        if "pr_id" not in d.columns:
            print(f"  {name:<4} no pr_id column, skipped")
            continue
        hit = d.pr_id.isin(bad)
        n = int(hit.sum())
        print(f"  {name:<4} {len(d):>9,} rows -> drop {n:>7,} "
              f"({n/max(len(d),1):.1%})")
        if a.apply and n:
            bak = path.with_suffix(f".prerepair-{stamp}.parquet")
            d.to_parquet(bak, index=False)
            d[~hit].to_parquet(path, index=False)

    if a.apply:
        print(f"\n[invalidate] applied. Backups written with suffix "
              f".prerepair-{stamp}.parquet")
        print("[invalidate] next: python vibebench/03_principles/java_lang/run_java.py")
    else:
        print("\n[invalidate] DRY RUN -- nothing changed. Re-run with --apply.")


if __name__ == "__main__":
    main()
