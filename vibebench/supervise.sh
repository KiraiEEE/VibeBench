#!/usr/bin/env bash
# Generic phase supervisor: wait for the link, run, restart on any exit.
#
# WHY A FILE AND NOT AN INLINE LOOP. The first version of this was written
# inline in a shell call and died with rc=127 from a process substitution this
# shell does not support. Nothing restarted the phase when the connection came
# back, and hours passed with zero progress and no error anywhere. Shell quoting
# is not worth re-deriving under pressure, so it lives here.
#
# Every phase it drives is resumable and checkpointed, so a restart continues
# rather than repeats. It stops when the command exits 0 twice in a row with no
# progress between them, which is the signal that there is genuinely no work
# left rather than a transient failure.
#
# Usage: supervise.sh <logfile> <max_passes> <command...>
set -u

LOG="${1:?usage: supervise.sh <logfile> <max_passes> <command...>}"; shift
MAX="${1:?max passes}"; shift

note() { echo "[supervise] $*" >> "$LOG"; }

link_up() {
  python -c "
import socket,sys
for h in ('api.github.com','github.com'):
    try:
        socket.create_connection((h,443),timeout=6).close(); sys.exit(0)
    except OSError: pass
sys.exit(1)
" 2>/dev/null
}

wait_for_link() {
  local waited=0
  while ! link_up; do
    [ "$waited" -eq 0 ] && note "link DOWN, waiting"
    sleep 20; waited=$((waited+20))
    if [ "$waited" -ge 7200 ]; then note "link down 2h, giving up"; return 1; fi
  done
  [ "$waited" -gt 0 ] && note "link BACK after ${waited}s"
  return 0
}

clean_passes=0
for pass in $(seq 1 "$MAX"); do
  wait_for_link || break
  note "pass $pass starting"
  "$@" >> "$LOG" 2>&1
  rc=$?
  note "pass $pass exited rc=$rc"
  if [ "$rc" -eq 0 ]; then
    clean_passes=$((clean_passes+1))
    if [ "$clean_passes" -ge 2 ]; then
      note "COMPLETE (two clean passes)"
      echo "SUPERVISE_COMPLETE" >> "$LOG"
      break
    fi
  else
    clean_passes=0
  fi
  sleep 5
done
note "supervisor finished"
