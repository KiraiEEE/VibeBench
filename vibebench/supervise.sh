#!/usr/bin/env bash
# Generic phase supervisor: run a pipeline stage until it actually finishes.
#
#   bash vibebench/supervise.sh <logfile> <max_passes> <python args...>
#
# e.g.
#   bash vibebench/supervise.sh $LOG 40 vibebench/02_clone/clone_and_extract.py --lang java --workers 12
#   bash vibebench/supervise.sh $LOG 20 vibebench/03_principles/java_lang/run_java.py --workers 6
#
# WHY THIS EXISTS. Every stage of this pipeline is checkpointed and resumable,
# so the only thing standing between "interrupted" and "finished" is somebody
# noticing and starting it again. On 2026-09-02 nobody did: this machine's link
# drops for a few minutes most mornings, the stage paused (correctly), then
# exited, and hours passed with zero progress and no error anywhere. A second
# attempt at an inline restart loop died with rc=127 from a shell-quoting bug,
# which is exactly why this lives in a file with a self-check instead.
#
# TWO LAYERS, deliberately. gitsnap.run_git already waits out an outage IN
# PROCESS (NET_WAIT_S, 8h) so no PR is ever recorded as failed because the wifi
# blinked. This is the OUT OF PROCESS layer: if the interpreter exits for any
# reason at all -- outage, OOM, an external kill -- the stage is started again.
# Neither layer can cover the other's failure mode.
#
# STOPPING CONDITIONS, so it can never spin forever:
#   * the stage exits 0 and made no progress  -> there is no work left
#   * max_passes reached
#   * the link stays down for 2h
set -u

LOG="${1:?usage: supervise.sh <logfile> <max_passes> <python args...>}"
MAX_PASSES="${2:?}"
shift 2
[ "$#" -gt 0 ] || { echo "supervise.sh: no command given" >&2; exit 2; }

note() { echo "[supervise] $*" >> "$LOG"; }

link_up() {
  python -c "
import socket,sys
try: socket.create_connection(('github.com',443),timeout=6).close()
except OSError: sys.exit(1)
" 2>/dev/null
}

wait_for_link() {
  local waited=0
  while ! link_up; do
    [ "$waited" -eq 0 ] && note "link DOWN, waiting"
    sleep 20
    waited=$((waited + 20))
    if [ "$waited" -ge 7200 ]; then note "link down 2h, giving up"; return 1; fi
  done
  [ "$waited" -gt 0 ] && note "link BACK after ${waited}s, resuming"
  return 0
}

# Progress marker: total bytes of the corpus parquets. Every stage writes at
# least one of them, and they only grow, so this is a stage-agnostic way to ask
# "did that pass actually do anything" without knowing what the stage does.
progress_marker() {
  python -c "
import sys,glob,os
sys.path.insert(0,'vibebench')
import config as C
print(sum(os.path.getsize(f) for f in glob.glob(str(C.CORPUS_DIR/'*.parquet'))))
" 2>/dev/null || echo 0
}

note "supervising: python $* (max ${MAX_PASSES} passes)"

for pass in $(seq 1 "$MAX_PASSES"); do
  wait_for_link || break
  before=$(progress_marker); before=${before:-0}
  note "pass ${pass} starting"

  python "$@" >> "$LOG" 2>&1
  rc=$?

  after=$(progress_marker); after=${after:-0}
  note "pass ${pass} exited rc=${rc} (corpus ${before} -> ${after} bytes)"

  if [ "$rc" -eq 0 ] && [ "$after" -le "$before" ]; then
    note "clean exit, nothing changed -- stage is COMPLETE"
    echo "SUPERVISE_COMPLETE rc=0" >> "$LOG"
    exit 0
  fi
  sleep 5
done

note "supervisor stopped after ${MAX_PASSES} passes or a long outage"
echo "SUPERVISE_EXHAUSTED" >> "$LOG"
