#!/usr/bin/env bash
# Python V2, phases 2 through 5, unattended.
#
#   phase 2  clone + extract, driven to a MEASURED coverage floor
#   ....     attrition table, measured per PR before anything is analysed
#   phase 3  calibration pass, flags left null, distributions printed
#   phase 4  thresholds derived from THIS corpus at ~p90, applied to config.py
#   ....     the parquets are DELETED, so no flag can predate its threshold
#   phase 3' the real flagged pass
#   phase 5  the report, all five principles
#
# IT STOPS THERE, DELIBERATELY. Phase 6/7 is labelling, the only token-expensive
# phase, and the author's standing instruction is to run it in a fresh session.
# It also needs a human decision about sample sizes.
#
# WAITING FOR A PROCESS TO EXIT IS NOT THE SAME AS WAITING FOR IT TO FINISH.
# An earlier version of this file waited on the process and nothing else. A crash
# or an OOM at 2,000 of 7,050 also exits, and the chain would then have
# calibrated thresholds on a third of the corpus with nothing saying so. That is
# this project's recurring bug. Completion is decided by COVERAGE MEASURED FROM
# THE MANIFEST, and a short pass is simply re-run: phase 2 is resumable and
# idempotent, so a re-run costs a fast-resume scan when there is nothing to do.
#
# EVERY STEP IS GATED ON THE ONE BEFORE IT. A failure stops the chain rather
# than letting a later step run on absent or partial input and produce a result
# that looks complete.
set -u
cd /d/VibeBench || exit 1
LOG=logs_archive/py2_chain.log
exec >>"$LOG" 2>&1
echo "=== chain started $(date -Is) ==="

MAX_PASSES=8
COVERAGE_FLOOR=0.995
FETCH_WORKERS=12          # phase 2 is network bound and uses ~0.6 GB, so it
                          # scales past the RAM-limited analysis worker count.
                          # Verified no GitHub throttling at this level.

die() { echo "!! CHAIN STOPPED: $*  $(date -Is)"; exit 1; }

phase2_running() {
  powershell.exe -NoProfile -Command \
    "if (Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | \
      Where-Object { \$_.CommandLine -like '*clone_and_extract*--lang py*' }) \
      { exit 0 } else { exit 1 }" >/dev/null 2>&1
}

# Fraction of gold-set PRs with ANY manifest row. Not the fraction that
# succeeded: a PR whose files all failed still has rows and counts as reached.
# Reached means phase 2 formed an opinion, which is what phase 3 needs.
coverage() {
  python - <<'PY'
import sys
sys.path.insert(0, 'vibebench')
import config as C, pandas as pd
try:
    g = pd.read_parquet(C.GOLD_SET); m = pd.read_parquet(C.CLONE_MANIFEST)
    print("%.6f" % (m.pr_id.nunique() / max(len(g), 1)))
except Exception:
    print("0.0")
PY
}

# ---- 1. phase 2 to genuine completion ---------------------------------------
while phase2_running; do sleep 120; done
echo "phase 2 process not running $(date -Is)"

for pass in $(seq 1 "$MAX_PASSES"); do
  cov=$(coverage)
  echo "--- coverage after pass $((pass-1)): $cov (floor $COVERAGE_FLOOR) $(date -Is)"
  if python -c "import sys; sys.exit(0 if float('$cov') >= $COVERAGE_FLOOR else 1)"; then
    echo "phase 2 COMPLETE at coverage $cov"; break
  fi
  [ "$pass" -eq "$MAX_PASSES" ] && {
    python vibebench/07_verify/py2_attrition.py
    die "coverage $cov still below floor after $MAX_PASSES passes. Calibrating on
a short corpus would anchor every threshold on a biased sample."
  }
  echo "--- phase 2 pass $pass, $FETCH_WORKERS workers (resumable, retries transients)"
  python vibebench/02_clone/clone_and_extract.py --lang py --workers "$FETCH_WORKERS"
done

# ---- 2. attrition, measured before anything is analysed ---------------------
echo "=== phase 2 attrition $(date -Is) ==="
python vibebench/07_verify/py2_attrition.py || die "attrition script failed"

# ---- 3. phase 3, CALIBRATION pass -------------------------------------------
# Flags left NULL, distributions printed. No flag from this pass may ever be
# published; the parquets are deleted below.
#
# WORKER COUNT FROM FREE RAM. RAM is this machine's ceiling, not CPU: phase 3
# holds two parsed trees plus five detector indexes per worker. Measured once at
# 0.4 GB available with a game open, which six workers would have thrashed.
# DECIDED ON COMMIT HEADROOM, NOT "AVAILABLE MBYTES" (corrected 2026-09-14).
#
# The first version read Available MBytes and would have picked 2 workers for
# the flagged re-run. That counter is misleading on Windows: it excludes
# reclaimable cache, so it swings by gigabytes without anything being short of
# memory. Measured side by side on this machine, idle: Available MBytes 5,123
# against COMMIT HEADROOM 13,019 MB. During the calibration pass it read as low
# as 1,401 while commit charge sat at 57% and nothing thrashed.
#
# Phase 3 is ONE process running THREADS, so workers share memory: the whole
# analysis process measured 687 to 732 MB at 4 workers and did not grow. RAM is
# not what limits it here. Commit headroom (limit minus committed) is what the
# process can genuinely still allocate, so it is what decides. Capped at 6, the
# CLAUDE.md ceiling for this machine.
pick_workers() {
  local mb
  mb=$(powershell.exe -NoProfile -Command \
    "[int](((Get-Counter '\Memory\Commit Limit').CounterSamples[0].CookedValue - (Get-Counter '\Memory\Committed Bytes').CounterSamples[0].CookedValue)/1MB)" \
    2>/dev/null | tr -d '\r')
  mb=${mb:-3000}
  if   [ "$mb" -ge 8000 ]; then echo 6
  elif [ "$mb" -ge 5000 ]; then echo 4
  elif [ "$mb" -ge 3000 ]; then echo 3
  else                          echo 2; fi
}
W=$(pick_workers)
echo "=== phase 3 CALIBRATION, $W workers $(date -Is) ==="
python vibebench/03_principles/py_lang/run_python.py --calibrate --workers "$W" \
  || die "calibration pass failed"

# ---- 4. thresholds from THIS corpus, then DELETE and re-run -----------------
# Anchored at ~p90 of production, entity-deduplicated classes, derived BEFORE any
# label exists so the bar is not fitted to the answer it is validated against.
echo "=== phase 4 thresholds $(date -Is) ==="
python vibebench/03_principles/py_lang/propose_py_thresholds.py --apply \
  || die "threshold proposal failed; config.py is unchanged"

# THE DELETION IS NOT HOUSEKEEPING, IT IS THE METHOD. run_python.py resumes by
# keeping rows with a permanent status, and calibration rows carry status "ok".
# Without this, the flagged run would resume the null-flag rows untouched and
# emit one parquet holding both, with nothing saying so. The runner also detects
# that case and evicts, so this is belt and braces on the step that matters most.
echo "deleting calibration parquets so no flag predates its threshold"
rm -fv vibebench/corpus/srp_results.parquet \
       vibebench/corpus/ocp_results.parquet \
       vibebench/corpus/lsp_results.parquet \
       vibebench/corpus/isp_results.parquet \
       vibebench/corpus/dip_results.parquet \
       vibebench/corpus/py_pr_results.parquet

W=$(pick_workers)
echo "=== phase 3 FLAGGED pass, $W workers $(date -Is) ==="
python vibebench/03_principles/py_lang/run_python.py --workers "$W" \
  || die "flagged pass failed"

# ---- 5. report ---------------------------------------------------------------
echo "=== phase 5 report $(date -Is) ==="
python vibebench/04_report/report_python.py || die "report failed"

echo "=== phase 5 joint model $(date -Is) ==="
python vibebench/06_model/fit_mixed_model.py || echo "(joint model needs a
Python adapter, see its --help; not fatal, the report above stands)"

echo "=== CHAIN FINISHED $(date -Is) ==="
echo "STOPPING BEFORE PHASE 6/7 (labelling) ON PURPOSE: it is the only
token-expensive phase and belongs in a fresh session. Sampler guards are already
in place, and Python V2 must pass --lang py2 everywhere."
