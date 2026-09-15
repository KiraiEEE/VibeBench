#!/usr/bin/env bash
# Re-run shards whose git archives failed under concurrent load, ONE AT A TIME.
#
# ROOT CAUSES, diagnosed after the first version of this script: Git LFS
# downloads inside `git archive` (onnxscript, splunk), giant archives timing out
# under load (ray, home-assistant), and one 4.86 GB data tree (nbn-upgrade-map)
# that must not be loaded at all. See py_lang/rerun_shard_safely.py.
#
# WHY. The six-way flagged pass produced 18 archive_failed PRs against 12 in the
# single-process calibration, while repo_too_large fell from 14 to 9, and a
# deterministic size check does not move. The same repositories produced BOTH
# statuses (home-assistant/core: 8 too-large, 1 archive-failed), so under load a
# giant `git archive` could fail before the size check ran. Retried on an idle
# machine, ray-project/ray archived in 6 s (recoverable) while
# originalankur/maptoposter failed again in 1 s (a genuine failure). The status
# is retryable by design, so the affected shards are re-run with no contention
# and the merge, report and model regenerated.
#
# Resume-safe: the stage is set to calibrated_applied first, so if this is
# paused, `bash vibebench/run_py2_finish.sh` completes the missing shards and
# re-merges.
set -u
cd /d/VibeBench || exit 1
LOG=logs_archive/py2_rerun.log
exec >>"$LOG" 2>&1
CORPUS=vibebench/corpus
SHARDS="${*:-0 2 3 4 5}"
echo "=== rerun of contended shards [$SHARDS] started $(date -Is) ==="

# The canonical merge is about to change: remove it now so nothing can read a
# result that is being superseded.
rm -fv $CORPUS/srp_results.parquet $CORPUS/ocp_results.parquet \
       $CORPUS/lsp_results.parquet $CORPUS/isp_results.parquet \
       $CORPUS/dip_results.parquet $CORPUS/py_pr_results.parquet \
       $CORPUS/py_pr_results.DONE.json
printf 'calibrated_applied\n' > $CORPUS/py2_stage.txt.tmp && mv -f $CORPUS/py2_stage.txt.tmp $CORPUS/py2_stage.txt
for i in $SHARDS; do rm -fv $CORPUS/py_pr_results.shard${i}of6.DONE.json; done

for i in $SHARDS; do
  echo "--- shard $i/6, 2 threads, alone $(date -Is)"
  # Through the safe wrapper: LFS downloads skipped, git timeout raised, and
  # trees over 1.5 GB refused before they can exhaust memory. Diagnosis and
  # justification in py_lang/rerun_shard_safely.py.
  python vibebench/03_principles/py_lang/rerun_shard_safely.py --shard "$i/6" --workers 2 \
    >> "logs_archive/py2_shard${i}of6.log" 2>&1
  rc=$?
  [ -f "$CORPUS/py_pr_results.shard${i}of6.DONE.json" ] \
    || { echo "!! shard $i rc=$rc and NO completion marker; stopping"; exit 1; }
  python -c "import json;d=json.load(open('$CORPUS/py_pr_results.shard${i}of6.DONE.json'));print('shard $i status', d['status'])"
done

echo "=== all reruns done, handing to the finish chain for merge/report/model $(date -Is) ==="
bash vibebench/run_py2_finish.sh
echo "=== RERUN CHAIN DONE, stage $(cat $CORPUS/py2_stage.txt) $(date -Is) ==="
