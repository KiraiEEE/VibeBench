#!/usr/bin/env bash
# Holdout batch extraction for the two principles whose refinements were fitted
# to the first round's labels. The other three had their thresholds set from
# corpus percentiles before any labelling, so there is nothing fitted to re-test.
set -u
S="${1:?usage: extract_holdout.sh <scratchdir>}"
for p in lsp isp; do
  echo ""; echo "=== EXTRACT $p holdout ($(date '+%H:%M:%S')) ==="
  python vibebench/labels/extract_java_label_batches.py \
      --principle "$p" --file "java_${p}_labeling_sample_holdout.csv" --only-missing \
      > "$S/ex_${p}_holdout.log" 2>&1
  echo "  $p: exit=$?, $(ls vibebench/labels/java_${p}_batches_holdout 2>/dev/null | wc -l) files"
done
echo ""; echo "=== AUDIT GATE ==="
python vibebench/07_verify/audit_label_batches.py --principle lsp --round _holdout
python vibebench/07_verify/audit_label_batches.py --principle isp --round _holdout
echo "HOLDOUT_EXTRACTION_COMPLETE"
