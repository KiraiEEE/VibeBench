#!/usr/bin/env bash
# Extract every Python V2 label batch, then run the audit gate. Sequential on
# purpose: the extractors resolve git trees, and two processes on one repository
# is the race the sharded pass was built to avoid.
set -u
cd /d/VibeBench || exit 1
exec >> logs_archive/py2_extraction.log 2>&1
echo "=== extraction started $(date -Is) ==="
for p in srp ocp lsp isp dip; do
  echo "--- $p $(date -Is)"
  python vibebench/labels/extract_py2_label_batches.py --principle "$p" || { echo "!! $p extraction failed"; exit 1; }
done
echo "=== audit gate $(date -Is) ==="
python vibebench/07_verify/audit_label_batches.py --lang py2
echo "=== EXTRACTION DONE rc=$? $(date -Is) ==="
