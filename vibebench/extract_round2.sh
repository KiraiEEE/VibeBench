#!/usr/bin/env bash
# Round 2 label-batch extraction, all five principles, sequential.
#
# SEQUENTIAL ON PURPOSE. Each principle materialises a Java source tree and
# runs the JavaParser sidecar over it; two at once drove available memory to
# 0.4 GB on this machine while CPU idled. One at a time is the throughput
# optimum here, not a compromise.
#
# The auditor is the gate afterwards: extraction now writes _manifest.json LAST
# and only on a clean exit, so a killed run leaves no manifest and cannot pass
# for a finished one. That is the defect that voided round 1.
#
# Usage: extract_round2.sh <scratchdir>
set -u
S="${1:?usage: extract_round2.sh <scratchdir>}"

for p in dip lsp ocp isp srp; do
  echo ""
  echo "=============================================="
  echo "EXTRACT $p  ($(date '+%H:%M:%S'))"
  echo "=============================================="
  python vibebench/labels/extract_java_label_batches.py \
      --principle "$p" --file "java_${p}_labeling_sample.csv" \
      > "$S/ex_${p}_r2.log" 2>&1
  rc=$?
  n=$(ls "vibebench/labels/java_${p}_batches" 2>/dev/null | wc -l)
  echo "  $p: exit=$rc, $n files"
  tail -3 "$S/ex_${p}_r2.log"
done

echo ""
echo "=============================================="
echo "AUDIT (gate)"
echo "=============================================="
python vibebench/07_verify/audit_label_batches.py
echo "AUDIT_EXIT=$?"
echo "EXTRACT_ROUND2_COMPLETE"
