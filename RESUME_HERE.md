# RESUME HERE — VibeBench, state as of 2026-09-06 evening

**Read `CLAUDE.md` first for the finished results.** This file is the live worklog.

## Everything through the thesis is DONE and PUSHED

Java experiment, 1,000-row validation, cross-check, thesis (85 pages), all committed
and on **https://github.com/KiraiEEE/VibeBench** (private). `CLAUDE.md` is now
tracked too. Working tree was clean at the last push.

## IN PROGRESS: the holdout round (ISP and LSP only)

**Why only two.** The five shipped detectors had thresholds set from corpus
percentiles *before* any label existed, so their P/R was already out-of-sample.
Two refinements were not: LSP restricted to `refused_bequest`, and ISP restricted
to `members>=30`, were both derived by reading false positives in the sample they
are scored on. Those are the only fitted quantities in the study.

**Samples drawn, seed 424242, both 100 flagged + 100 unflagged:**

```
LSP  from 310 fresh flagged rows   (563 previously judged rows burned)
ISP  from 538 fresh flagged rows   (575 previously judged rows burned)
```

**Bug fixed while drawing them:** `--holdout` used to exclude rows only from the
current sample file, so round 1's 400 judged rows per principle (which live in
`labels/VOID_ROUND1_2026-09-05/`) were eligible for redraw. That would have
reported an in-sample estimate as out-of-sample. The sampler now burns every
judged round it can find and prints which files it burned from.

### Progress

```
LSP  extraction 200/200 DONE   audit gate 0 FAIL   judged 8/200
ISP  extraction in progress    (was ~17/200 at 19:54)
```

### Commands

```bash
# finish extraction + gate (resumable, --only-missing is wired in)
bash vibebench/extract_holdout.sh <scratchdir>

# gate alone
python vibebench/07_verify/audit_label_batches.py --principle isp --round _holdout

# record labels: the tools are ROUND-AWARE now, call use_round first
cd vibebench/labels && python -c "
import record_labels as R
R.use_round('_holdout')
R.record('lsp', {8:(False,'reason at least 12 chars')})
"

# score when done
python vibebench/labels/score_labels.py --file java_lsp_labeling_sample_holdout.csv \
    --pred-col violation --label-col llm_lsp_risk
```

`record_labels.py`, `audit_label_batches.py` and the batch extractor all take a
round suffix, so the holdout gets identical guards and its batch directory
(`java_X_batches_holdout`) cannot overwrite round 2's material.

### Judging rules (keep these consistent with round 2)

**LSP not a violation when:** the override calls `super` first; the base already
DECLARES the exception the override throws; both methods are `private` (no
polymorphic override at all); both are `@Nullable`; the override reports a
STRONGER guarantee; it is a protected template hook only the base calls; the base
itself can return null.
**IS a violation when:** the base does real work and the override is a bare
`throw new UnsupportedOperationException`; the override narrows accepted inputs;
the override returns null where the base guarantees a value.

**ISP:** width plus whether implementers carve it up. Violation when the contract
is wide (>=30 members) and implementers take wildly different slices, or when an
adapter exists purely to stub most of it. Not a violation when every implementer
implements the whole surface, when defaults let implementers override only what
they need, or when it is a fluent builder or query DSL.

## Disk: decision recorded 2026-09-06

Project is **352 GB**; 88 GB free on the disk.

```
vibebench/corpus/clones/            338 GB   1,988 git repos
vibebench/corpus/java_facts_cache/  7.7 GB   8,713 parsed fact records
vibebench/corpus/tree_cache/        4.3 GB   6,454 cached source trees (76% of what analysis used)
vibebench/corpus/ai_files_java/     2.1 GB
datasets/                          12.7 GB
```

**DECISION: keep `clones/` for now.** Deleting it frees 338 GB and breaks nothing
that is published, every thesis number regenerates from the parquets. But it is
the only local snapshot of the exact commits, and upstream decay is irreversible:
18 pull requests were already unrecoverable during this study. Revisit only if
space actually blocks work. If deleted, KEEP `tree_cache/` and `java_facts_cache/`.

## What is left after the holdout

1. Fold the holdout numbers into Ch4 `sec:instrument-validation` and Ch5
   limitations, which currently say the refinements are in-sample.
2. Optional: Python V2. Different corpus, so the Java clones do not help it much.
3. Optional polish: 21 remaining em dashes; three figures never inspected for
   design (`fig_martin_plane`, `fig_delta_harness`, `fig_solid_wheel`).

## House rules

* Never write an em dash; use a comma. Applies to the thesis too.
* Report all FIVE principles in every table.
* Lead with macro-per-repo, print counts beside rates.
* Every caveat in ONE limitations section after the conclusion.
* No citation without reading its arXiv page first.
* Do not stop for permission on routine steps.

## Traps (each cost real time)

* `ps -W` reports zero python while several run. Use PowerShell `Get-Process`.
* Killing a background bash script does not kill its child python.
* `cd` in a Bash call PERSISTS and breaks relative paths. Twice looked like data loss.
* **Bash heredocs mangle backslashes.** Write patch scripts to a file, then run them.
* Never capitalise "first word on a line" in LaTeX; source wraps mid-sentence.
* A partial result that looks complete is THE recurring bug here.
