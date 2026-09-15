#!/usr/bin/env bash
# Python V2, from a finished CALIBRATION pass to the report. Replaces the tail of
# run_py2_chain.sh, whose flagged pass ran as one GIL-bound process.
#
#   start               calibration verified complete, thresholds applied,
#                       canonical parquets DELETED
#   calibrated_applied  N repository-disjoint shards run the flagged pass
#   shards_done         shards merged, with full verification
#   merged              report written
#   reported            joint model written
#   finished            stop, before labelling
#
# WHY SHARDS. The calibration pass measured 1.04 of 12 cores busy, 82% idle:
# threads cannot parallelise pure-Python parsing. Separate processes can, and
# repository-disjoint ones never share a git directory, a cache key or an output
# file. See the sharding note in py_lang/run_python.py.
#
# WHY A STAGE FILE. The author pauses runs often. Re-running from the top after
# the calibration parquets were deleted would find no calibration and stop, or
# worse, delete a half-finished flagged pass. Each gate records its stage only
# AFTER it succeeds, and a restart resumes at the first unfinished stage.
#
# THE SHARD COUNT IS FIXED ON FIRST LAUNCH. A resume that recomputed it from
# different free memory would assign repositories differently, silently ignore
# the finished shards and redo them.
#
# PAUSE: stop THIS script first, then every run_python.py, then git. See
# RESUME_HERE.md.
set -u
cd /d/VibeBench || exit 1
LOG=logs_archive/py2_finish.log
exec >>"$LOG" 2>&1
CORPUS=vibebench/corpus
STAGE_FILE=$CORPUS/py2_stage.txt
NSHARD_FILE=$CORPUS/py2_nshards.txt
THREADS=3          # per shard: git archive runs outside the GIL, so a few
                   # threads still help; more just contend for the lock

echo "=== finish chain started $(date -Is) ==="
stage()     { cat "$STAGE_FILE" 2>/dev/null || echo start; }
set_stage() { printf '%s\n' "$1" > "$STAGE_FILE.tmp" && mv -f "$STAGE_FILE.tmp" "$STAGE_FILE"
              echo "--- stage -> $1  $(date -Is)"; }
die()       { echo "!! FINISH CHAIN STOPPED at stage $(stage): $*  $(date -Is)"; exit 1; }

py_running() {   # any python whose command line matches $1
  powershell.exe -NoProfile -Command \
    "if (Get-CimInstance Win32_Process | Where-Object { \$_.Name -match 'python' -and \$_.CommandLine -match '$1' }) { exit 0 } else { exit 1 }" \
    >/dev/null 2>&1
}
git_running() {
  powershell.exe -NoProfile -Command \
    "if (Get-CimInstance Win32_Process -Filter \"Name like '%git%'\") { exit 0 } else { exit 1 }" \
    >/dev/null 2>&1
}
headroom_mb() {
  powershell.exe -NoProfile -Command \
    "[int](((Get-Counter '\Memory\Commit Limit').CounterSamples[0].CookedValue - (Get-Counter '\Memory\Committed Bytes').CounterSamples[0].CookedValue)/1MB)" \
    2>/dev/null | tr -d '\r'
}

# Nothing may start while any analysis or extraction process is alive: the
# calibration pass finishing, or shards from before a pause.
while py_running 'run_python|clone_and_extract'; do sleep 60; done
echo "no analysis process running; stage is '$(stage)'"

# ---------------------------------------------------------------- start
if [ "$(stage)" = start ]; then
  verify_cal() {
    python - <<'PY'
import sys
sys.path.insert(0, 'vibebench')
import config as C, pandas as pd
p = C.CORPUS_DIR / 'py_pr_results.parquet'
if not p.exists():
    print("NO_FILE"); sys.exit(2)
d = pd.read_parquet(p)
gold = set(pd.read_parquet(C.GOLD_SET, columns=['pr_id']).pr_id)
ok = d[d.status == 'ok']
complete = set(d.pr_id) == gold and len(d) == len(gold)
calib = len(ok) > 0 and ok['ocp_risk'].isna().all() and ok['srp_risk'].isna().all()
print(f"rows={len(d)} unique={d.pr_id.nunique()} gold={len(gold)} ok={len(ok)} "
      f"complete={complete} calibration={calib}")
sys.exit(0 if (complete and calib) else 1)
PY
  }
  # rc captured directly: inside `if ! cmd; then`, $? is the status of the
  # NEGATION (always 0), so the no-file case could never be told apart.
  verify_cal; rc=$?
  if [ "$rc" -ne 0 ]; then
    [ "$rc" -eq 2 ] && die "no calibration parquet exists; run the calibration pass"
    echo "calibration incomplete; resuming it once (unsharded, resumable)"
    python vibebench/03_principles/py_lang/run_python.py --calibrate --workers 6 \
      || die "calibration resume failed"
    verify_cal || die "calibration still incomplete after a resume"
  fi

  echo "=== phase 4 thresholds $(date -Is) ==="
  python vibebench/03_principles/py_lang/propose_py_thresholds.py --apply \
    || die "threshold proposal refused or failed; config.py unchanged"

  # Deletion is the method: no flag may predate the threshold that produced it.
  echo "deleting calibration parquets and any stale shard files"
  rm -fv $CORPUS/srp_results.parquet $CORPUS/ocp_results.parquet \
         $CORPUS/lsp_results.parquet $CORPUS/isp_results.parquet \
         $CORPUS/dip_results.parquet $CORPUS/py_pr_results.parquet \
         $CORPUS/py_pr_results.DONE.json
  rm -fv $CORPUS/*_results.shard*of*.parquet $CORPUS/py_pr_results.shard*of*.parquet \
         $CORPUS/py_pr_results.shard*of*.DONE.json
  rm -f "$NSHARD_FILE"
  set_stage calibrated_applied
fi

# ---------------------------------------------------------------- flagged shards
if [ "$(stage)" = calibrated_applied ]; then
  if [ ! -s "$NSHARD_FILE" ]; then
    H=$(headroom_mb); H=${H:-6000}
    N=$(( H / 2200 )); [ "$N" -lt 2 ] && N=2; [ "$N" -gt 6 ] && N=6
    echo "$N" > "$NSHARD_FILE"
    echo "commit headroom ${H} MB -> $N shards x $THREADS threads (fixed for this run)"
  fi
  N=$(cat "$NSHARD_FILE")

  # Swept ONCE, here, where it is safe: no git process can be alive, so every
  # lock left is a dead one from a hard stop. Shards never sweep.
  git_running && die "a git process is alive; refusing to sweep locks under it"
  python -c "import sys; sys.path.insert(0,'vibebench'); sys.path.insert(0,'vibebench/03_principles'); import gitsnap; print('swept stale locks:', gitsnap.sweep_stale_locks())"

  for attempt in 1 2 3; do
    pids=(); todo=()
    for i in $(seq 0 $((N-1))); do
      [ -f "$CORPUS/py_pr_results.shard${i}of${N}.DONE.json" ] && continue
      todo+=("$i")
      python vibebench/03_principles/py_lang/run_python.py --shard "$i/$N" \
        --workers "$THREADS" >> "logs_archive/py2_shard${i}of${N}.log" 2>&1 &
      pids+=("$!")
    done
    [ "${#todo[@]}" -eq 0 ] && break
    echo "=== attempt $attempt: shards ${todo[*]} of $N running, pids ${pids[*]} $(date -Is) ==="
    for k in "${!pids[@]}"; do
      wait "${pids[$k]}"; rc=$?
      echo "shard ${todo[$k]}/$N exited rc=$rc $(date -Is)"
    done
  done
  for i in $(seq 0 $((N-1))); do
    [ -f "$CORPUS/py_pr_results.shard${i}of${N}.DONE.json" ] \
      || die "shard $i/$N has no completion marker after 3 attempts; see logs_archive/py2_shard${i}of${N}.log"
  done
  set_stage shards_done
fi

# ---------------------------------------------------------------- merge
if [ "$(stage)" = shards_done ]; then
  N=$(cat "$NSHARD_FILE")
  # A canonical PR frame WITHOUT the canonical marker can only be a merge that
  # died mid-write (the calibration canonicals were deleted before sharding), so
  # it is removed and the merge redone rather than refused forever.
  # And a canonical marker WITH the frame means the merge finished but the stage
  # was not recorded (a pause in between); merging again would only be refused.
  if [ -f "$CORPUS/py_pr_results.DONE.json" ]; then
    echo "merge already complete (canonical marker present), not redoing it"
  else
    rm -f $CORPUS/srp_results.parquet $CORPUS/ocp_results.parquet \
          $CORPUS/lsp_results.parquet $CORPUS/isp_results.parquet \
          $CORPUS/dip_results.parquet $CORPUS/py_pr_results.parquet
    echo "=== merge $N shards $(date -Is) ==="
    python vibebench/03_principles/py_lang/merge_py_shards.py --n "$N" || die "merge refused"
  fi
  set_stage merged
fi

# ---------------------------------------------------------------- report
if [ "$(stage)" = merged ]; then
  echo "=== phase 5 report $(date -Is) ==="
  python vibebench/04_report/report_python.py > logs_archive/py2_report.txt 2>&1 \
    || { tail -20 logs_archive/py2_report.txt; die "report failed"; }
  echo "report written to logs_archive/py2_report.txt"
  set_stage reported
fi

# ---------------------------------------------------------------- joint model
if [ "$(stage)" = reported ]; then
  echo "=== phase 5 joint model (Python) $(date -Is) ==="
  if python vibebench/06_model/fit_mixed_model.py --lang py > logs_archive/py2_model.txt 2>&1; then
    echo "joint model written to logs_archive/py2_model.txt"
  else
    echo "!! joint model FAILED, see logs_archive/py2_model.txt (the report stands)"
  fi
  set_stage finished
fi

echo "=== FINISH CHAIN DONE, stage $(stage) $(date -Is) ==="
echo "Stopping before phase 6/7 (labelling) on purpose: it is the only
token-expensive phase and belongs in a fresh session. Use --lang py2 everywhere."
