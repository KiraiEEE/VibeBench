#!/usr/bin/env bash
# Re-derive the cleared file lists, re-analyse what changed, then run phases 6-9.
set -u
S="${1:?scratchdir}"
echo "=== STEP 1: re-derive cleared file lists (batched GraphQL) ==="
python vibebench/02_clone/fix_file_lists.py --apply --workers 4 2>&1 | tail -6
echo "=== STEP 2: re-analyse ==="
python vibebench/03_principles/java_lang/run_java.py --workers 6 2>&1 | tail -4
echo "=== STEP 3: phases 6-9 ==="
bash vibebench/run_after_analysis.sh "$S"
