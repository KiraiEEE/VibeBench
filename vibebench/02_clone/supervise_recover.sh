#!/usr/bin/env bash
# Supervisor for the phase-2 recovery pass.
#
# WHY THIS EXISTS. The recovery run is long and the machine's connection drops
# for a few minutes most mornings. Three separate things then went wrong at
# once on 2026-09-02:
#   1. the pipeline correctly PAUSED on the outage (it refuses to burn PRs as
#      failures while the network is down) -- that part worked;
#   2. it eventually exited, and nothing restarted it when the link came back;
#   3. the ad-hoc restart loop written inline used a process substitution that
#      exits 127 under this shell, so the loop itself died silently.
# Net effect: hours of wall clock with zero progress and no error anywhere.
#
# This script fixes all three: it waits for the link, restarts the pass on ANY
# exit, and stops only when there is genuinely no work left. It is written to a
# file rather than inlined so shell quoting cannot break it again.
set -u

LOG="${1:?usage: supervise_recover.sh <logfile>}"
WORKERS="${2:-3}"
MAX_PASSES="${3:-40}"

note() { echo "[supervise] $*" >> "$LOG"; }

wait_for_net() {
  local waited=0
  while ! python -c "
import socket,sys
try:
    socket.create_connection(('github.com',443),timeout=6).close()
except OSError:
    sys.exit(1)
" 2>/dev/null; do
    if [ "$waited" -eq 0 ]; then note "link DOWN, waiting"; fi
    sleep 30
    waited=$((waited + 30))
    if [ "$waited" -ge 7200 ]; then note "link still down after 2h, giving up"; return 1; fi
  done
  if [ "$waited" -gt 0 ]; then note "link BACK after ${waited}s, resuming"; fi
  return 0
}

for pass in $(seq 1 "$MAX_PASSES"); do
  wait_for_net || break

  before=$(python - <<'PY' 2>/dev/null
import sys, json
sys.path.insert(0, 'vibebench')
import config as C, pandas as pd
from pathlib import Path
base = Path(C.EXTRACT_DIR_JAVA)
pr = pd.read_parquet(C.CORPUS_DIR / 'java_pr_results.parquet')
lost = set(pr[pr.status == 'no_touched_files'].pr_id.astype(str))
print(sum(1 for p in lost if (base / p / '_touched.json').exists()))
PY
)
  before=${before:-0}
  note "pass $pass starting ($before of 2948 lost PRs already reprocessed)"

  python vibebench/02_clone/clone_and_extract.py --lang java --workers "$WORKERS" >> "$LOG" 2>&1
  rc=$?

  after=$(python - <<'PY' 2>/dev/null
import sys, json
sys.path.insert(0, 'vibebench')
import config as C, pandas as pd
from pathlib import Path
base = Path(C.EXTRACT_DIR_JAVA)
pr = pd.read_parquet(C.CORPUS_DIR / 'java_pr_results.parquet')
lost = set(pr[pr.status == 'no_touched_files'].pr_id.astype(str))
print(sum(1 for p in lost if (base / p / '_touched.json').exists()))
PY
)
  after=${after:-0}
  note "pass $pass exited rc=$rc, reprocessed $before -> $after"

  # Done when the pass exited cleanly AND every lost PR has been revisited.
  if [ "$rc" -eq 0 ] && [ "$after" -ge 2948 ]; then
    note "COMPLETE: all 2948 lost PRs reprocessed"
    echo "PHASE2_RECOVERY_COMPLETE" >> "$LOG"
    break
  fi

  # A clean exit that moved nothing means there is no work left that this
  # pass can do; retrying identically would spin forever.
  if [ "$rc" -eq 0 ] && [ "$after" -le "$before" ]; then
    note "clean exit with no progress ($after unchanged) -- stopping"
    echo "PHASE2_RECOVERY_NO_PROGRESS" >> "$LOG"
    break
  fi

  sleep 10
done

note "supervisor finished"
