#!/usr/bin/env bash
# Extract the remaining ISP and DIP judging batches, restarting on any exit.
# Written to a file because an inline loop died with rc=127 from shell quoting
# and silently stopped restarting after a connection drop.
set -u
S="${1:?scratchdir}"
for pass in $(seq 1 25); do
  done_all=1
  for p in isp dip; do
    want=400; [ "$p" = "dip" ] && want=395
    have=$(ls "vibebench/labels/java_${p}_batches" 2>/dev/null | wc -l)
    if [ "$have" -lt "$want" ]; then
      done_all=0
      echo "[extract] pass $pass: $p at $have/$want" >> "$S/extract_final.log"
      python vibebench/labels/extract_java_label_batches.py --principle "$p" \
        --file "java_${p}_labeling_sample.csv" >> "$S/extract_final.log" 2>&1
    fi
  done
  if [ "$done_all" -eq 1 ]; then
    echo "EXTRACTION_COMPLETE" >> "$S/extract_final.log"; break
  fi
  sleep 5
done
