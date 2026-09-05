#!/usr/bin/env bash
# Phases 6-9, chained: report -> verify -> draw samples -> extract batches.
#
# WHY CHAINED. Each of these takes minutes and the next one only makes sense if
# the previous passed, so running them by hand means four round trips of
# waiting. Chaining lets phase 5 finish unattended overnight and leaves
# everything staged for judging.
#
# THE VERIFIER IS A GATE, NOT A STEP. If verify_v2_java.py reports any FAIL the
# chain STOPS before drawing samples. Drawing a sample from a corpus that failed
# verification is how you spend twelve hours judging rows that describe the
# wrong population -- which is exactly the defect found on 2026-09-01, where the
# sampler had no production filter and 18% of the SRP sample was protoc output.
#
# Usage: run_after_analysis.sh <scratchdir>
set -u
S="${1:?usage: run_after_analysis.sh <scratchdir>}"

say() { echo ""; echo "=============================================="; echo "$*"; echo "=============================================="; }

say "PHASE 6: report"
python vibebench/04_report/report_java.py > "$S/report_v3.log" 2>&1
rc=$?
echo "report exit=$rc"
if [ $rc -ne 0 ]; then echo "CHAIN STOPPED: report failed"; tail -20 "$S/report_v3.log"; exit 1; fi
cp -f "$S/report_v3.log" vibebench/04_report/V2_java_report.txt
grep -E "analysable PRs|FLAGGED|production-only|DISTINCT production" "$S/report_v3.log" | head -20

say "PHASE 7: verifier (GATE -- must be 0 FAIL)"
python vibebench/07_verify/verify_v2_java.py > "$S/verify_v3.log" 2>&1
vrc=$?
tail -25 "$S/verify_v3.log"
if [ $vrc -ne 0 ]; then
  echo ""
  echo "CHAIN STOPPED: verifier reported FAIL. Samples NOT drawn."
  echo "Judging a corpus that failed verification wastes the most expensive phase."
  exit 1
fi

say "PHASE 8: draw label samples (200 per stratum = 400 rows each)"
for p in srp ocp lsp isp dip; do
  python vibebench/labels/make_java_labeling_sample.py --principle "$p" --n-per-stratum 200 2>&1 | grep -E "wrote|filter"
done

say "PHASE 9: extract judging batches"
for p in srp ocp lsp isp dip; do
  python vibebench/labels/extract_java_label_batches.py --principle "$p" \
      --file "java_${p}_labeling_sample.csv" > "$S/ex_${p}_v3.log" 2>&1
  n=$(ls "vibebench/labels/java_${p}_batches" 2>/dev/null | wc -l)
  echo "  $p: $n batches"
done

say "CHAIN COMPLETE -- ready for judging"
echo "AFTER_ANALYSIS_COMPLETE"
