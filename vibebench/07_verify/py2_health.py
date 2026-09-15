"""
Is the Python V2 pipeline actually alive and making progress?

WHY THIS FILE EXISTS. The `[clone] progress: N/7050` counter is NOT a health
signal and reading it as one wasted ten minutes of a session. It increments only
when a whole REPO GROUP finishes, so eight workers part-way through eight large
repositories leave it frozen for many minutes while the run is downloading at
full speed. Manifest coverage behaves the same way, because a repo's rows are
only committed when its group completes.

THE SIGNAL THAT ACTUALLY WORKS IS BYTES LANDING ON DISK. A run that is fetching
grows the clone store; one that is wedged does not, whatever the counter says.
So this script samples disk over a few seconds and reports that first, and
treats every counter as context rather than evidence.

It also checks the things that fail silently rather than loudly:
  * a `*.tmp` parquet, which means a checkpoint was interrupted mid-write
  * a manifest that no longer parses, which is the one unrecoverable failure
  * orphaned git children, which outlive a killed parent and keep writing into
    repositories the next run also writes to
  * free disk against safeio's floor, because a full disk makes every write fail
    while the run keeps going and reports success

Run:  python vibebench/07_verify/py2_health.py            # 8-second sample
      python vibebench/07_verify/py2_health.py --sample 30
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

OK, WARN, BAD = "ok  ", "WARN", "FAIL"
_verdicts: list[str] = []


def say(level: str, msg: str) -> None:
    _verdicts.append(level)
    print(f"  [{level}] {msg}")


def ps_processes() -> list[dict]:
    """Pipeline processes. PowerShell only: `ps -W` reports ZERO python
    processes on this machine while several run, and `pgrep` does not exist."""
    script = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -match '^git' -or ($_.Name -match 'python' -and "
        "$_.CommandLine -match 'clone_and_extract|run_python') -or "
        "($_.Name -eq 'bash.exe' -and $_.CommandLine -match 'run_py2_chain') } | "
        "ForEach-Object { '{0}|{1}|{2}' -f $_.ProcessId, $_.Name, "
        "($_.CommandLine -replace '\\|','/') }"
    )
    try:
        out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script],
                             capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in out.splitlines():
        parts = line.strip().split("|", 2)
        if len(parts) == 3 and parts[0].isdigit():
            rows.append({"pid": int(parts[0]), "name": parts[1], "cmd": parts[2]})
    return rows


def dir_bytes(p: Path) -> int:
    try:
        return shutil.disk_usage(p).used
    except OSError:
        return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=8,
                    help="seconds to sample disk growth over")
    a = ap.parse_args()

    print("=" * 68)
    print("Python V2 pipeline health")
    print("=" * 68)

    # ---- 1. what is running ------------------------------------------------
    procs = ps_processes()
    phase2 = [p for p in procs if "clone_and_extract" in p["cmd"]]
    phase3 = [p for p in procs if "run_python" in p["cmd"]]
    chain = [p for p in procs if p["name"] == "bash.exe"]
    gits = [p for p in procs if p["name"].startswith("git")]

    print("\n-- processes --")
    if len(phase2) > 1:
        say(BAD, f"{len(phase2)} phase 2 processes. TWO WRITERS ON ONE MANIFEST "
                 f"is corruption; kill all but one.")
    elif phase2:
        say(OK, f"phase 2 running (pid {phase2[0]['pid']})")
    if len(phase3) > 1:
        say(BAD, f"{len(phase3)} phase 3 processes, same problem")
    elif phase3:
        say(OK, f"phase 3 running (pid {phase3[0]['pid']})")
    if not phase2 and not phase3:
        say(WARN, "no analysis or extraction process running")
    say(OK if chain else WARN,
        f"chain {'running' if chain else 'NOT running'}"
        + (f" (pid {chain[0]['pid']})" if chain else ", nothing will auto-advance"))
    if gits and not phase2 and not phase3:
        say(BAD, f"{len(gits)} ORPHANED git processes with no parent run. "
                 f"They keep writing into repos the next run also writes to.")
    elif gits:
        say(OK, f"{len(gits)} git children (normal while fetching)")

    # ---- 2. THE signal: bytes landing --------------------------------------
    print(f"\n-- progress, measured as bytes landing over {a.sample}s --")
    print("   (the [clone] progress counter is NOT a health signal: it moves only")
    print("    when a whole repo GROUP finishes, so it freezes during big fetches)")
    b1 = dir_bytes(C.CLONES_DIR)
    time.sleep(a.sample)
    b2 = dir_bytes(C.CLONES_DIR)
    mb = (b2 - b1) / 2 ** 20
    rate = mb / a.sample
    if not (phase2 or phase3):
        say(WARN, f"{mb:,.0f} MB in {a.sample}s, but nothing is running")
    elif mb > 1:
        say(OK, f"{mb:,.0f} MB landed ({rate:.1f} MB/s). The run is working.")
    elif phase3:
        say(OK, "no disk growth, which is CORRECT for phase 3 (it reads cached "
                "trees and is CPU bound, not network bound)")
    else:
        say(WARN, f"only {mb:.1f} MB in {a.sample}s. Could be a slow repo or a "
                  f"network wait. Re-run with --sample 60 before concluding.")

    # ---- 3. integrity ------------------------------------------------------
    print("\n-- integrity --")
    tmps = list(C.CORPUS_DIR.glob("*.tmp"))
    if tmps:
        say(WARN, f"{len(tmps)} .tmp parquet(s): a checkpoint was interrupted "
                  f"mid-write. The previous checkpoint is still intact "
                  f"(os.replace is atomic), so this is stale, not lost data.")
    else:
        say(OK, "no interrupted checkpoint writes")

    import pandas as pd
    try:
        gold = pd.read_parquet(C.GOLD_SET)
        man = pd.read_parquet(C.CLONE_MANIFEST)
        cov = man.pr_id.nunique() / max(len(gold), 1)
        say(OK, f"manifest parses: {len(man):,} rows, {man.pr_id.nunique():,} PRs, "
                f"coverage {cov:.1%}")
        age = time.time() - C.CLONE_MANIFEST.stat().st_mtime
        if phase2 and age > 1800:
            say(WARN, f"manifest last written {age/60:.0f} min ago. Checkpoints "
                      f"fire every 25 PRs, so a long gap means very large repos.")
        else:
            say(OK, f"manifest written {age/60:.0f} min ago")
    except Exception as e:                                  # noqa: BLE001
        say(BAD, f"MANIFEST DOES NOT PARSE: {e}. This is the one unrecoverable "
                 f"failure; restore from git before doing anything else.")

    # ---- 4. resources ------------------------------------------------------
    print("\n-- resources --")
    free_gb = shutil.disk_usage(C.CORPUS_DIR).free / 2 ** 30
    floor = getattr(__import__("safeio"), "MIN_FREE_GB", 15.0)
    if free_gb < floor:
        say(BAD, f"{free_gb:.0f} GB free, BELOW the {floor:.0f} GB floor. "
                 f"safeio stops the pipeline here deliberately.")
    elif free_gb < floor * 3:
        say(WARN, f"{free_gb:.0f} GB free, approaching the {floor:.0f} GB floor")
    else:
        say(OK, f"{free_gb:,.0f} GB free (floor {floor:.0f} GB)")

    # MEMORY PRESSURE IS COMMIT CHARGE, NOT "AVAILABLE MBYTES".
    #
    # An earlier version of this check failed the run whenever Available MBytes
    # fell below 2,000 with phase 3 up. That fired while the machine was
    # perfectly healthy: Windows counts cached pages separately from "available",
    # so the number swings by gigabytes without anything being short of memory.
    # Observed directly at 1,816 MB available with commit charge at 52% and the
    # analysis process sitting at 687 MB, which is no pressure at all.
    #
    # Commit charge is the honest signal: it is the fraction of the commit limit
    # actually promised to processes, so it cannot be inflated by reclaimable
    # cache. Available MBytes is still printed, because it is what decides the
    # worker count, but it no longer decides health. A check that cries wolf is
    # worse than no check, because the next real warning gets ignored.
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "[int](Get-Counter '\\Memory\\Available MBytes')"
             ".CounterSamples[0].CookedValue; "
             "[int](Get-Counter '\\Memory\\% Committed Bytes In Use')"
             ".CounterSamples[0].CookedValue"],
            capture_output=True, text=True, timeout=60).stdout.split()
        avail, commit = int(out[0]), int(out[1])
        w = 6 if avail >= 8000 else 4 if avail >= 5000 else 3 if avail >= 3000 else 2
        if commit >= 95:
            say(BAD, f"commit charge {commit}% with {avail:,} MB available. This "
                     f"IS real pressure; an OOM kill during phase 3 leaves a "
                     f"parquet that looks finished.")
        elif commit >= 85:
            say(WARN, f"commit charge {commit}%, {avail:,} MB available. "
                      f"Approaching real pressure.")
        else:
            say(OK, f"commit charge {commit}% (healthy), {avail:,} MB available "
                    f"-> phase 3 would use {w} workers")
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        say(WARN, "could not read memory counters")

    # ---- verdict -----------------------------------------------------------
    print("\n" + "=" * 68)
    if BAD in _verdicts:
        print("VERDICT: SOMETHING IS WRONG, see the FAIL lines above")
        sys.exit(1)
    if WARN in _verdicts:
        print("VERDICT: working, with warnings noted above")
    else:
        print("VERDICT: everything healthy")
    print("=" * 68)


if __name__ == "__main__":
    main()
