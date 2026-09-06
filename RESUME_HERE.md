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
LSP  extraction 200/200 DONE   audit gate 0 FAIL   judged 200/200 COMPLETE
ISP  extraction 200/200 DONE   audit gate 0 FAIL   judged 200/200 COMPLETE
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

**ISP decision rule actually applied in the holdout** (written down because it
must NOT be the width threshold under test, or the validation is circular). A
contract is a violation when any of these holds, and otherwise is not:

* **(A)** the contract has >= 10 members AND at least one implementer is forced
  to stub one of them;
* **(B)** the contract is very wide (>= 35 members) and its members fall into
  clearly separable groups serving different clients, even with no stub present;
* **(C)** at least two implementers each stub half or more of what they declare,
  which is systematic carving rather than one class that cannot cope.

Stubs inside a TEST DOUBLE do not count as evidence, since a mock stubs what the
test does not exercise rather than what the contract forces on production code.
An implementer that stubs EVERYTHING it declares is a substitutability problem,
not a fat interface, so that alone is not counted. Tiny contracts are not fat by
construction. The original round 2 wording follows.

**ISP (round 2 wording):** width plus whether implementers carve it up. Violation when the contract
is wide (>=30 members) and implementers take wildly different slices, or when an
adapter exists purely to stub most of it. Not a violation when every implementer
implements the whole surface, when defaults let implementers override only what
they need, or when it is a fluent builder or query DSL.

## HOLDOUT ROUND: COMPLETE, FOLDED INTO THE THESIS, VERIFIED (2026-09-06)

400 further entities judged, 200 LSP + 200 ISP, drawn with seed 424242 and
excluding every row judged in any earlier round. Both gates passed 0 FAIL.

### The two refinements behaved in OPPOSITE ways, and that is the result

```
                                   fitted sample     HOLDOUT      pooled
LSP restricted to refused_bequest   1.000 (31/31)  1.000 (33/33)  1.000 (64/64)
ISP restricted to >=30 members      0.862 (25/29)  0.704 (19/27)  0.786 (44/56)
```

The Liskov restriction held exactly, no false positive in 64 flags over two
independent samples. The width condition lost 16 points and its fitted 0.862 is
OUTSIDE the holdout interval [0.515, 0.841]. Refused bequest is categorical, so
there is no quantity to overfit; 30 members is a numeric threshold read off the
false positives it was then scored against. Broad flags reproduced closely
(LSP 0.400 -> 0.440, ISP 0.320 -> 0.350), so the shipped numbers are stable.

### The holdout also found a real error in the thesis. NOW FIXED.

Ch4 reported recall as raw TP/(TP+FN) on a sample that is half flagged, and
concluded every flag rate is "an upper bound". The corpus is nowhere near half
flagged: for LSP it is 1,546 flagged against 79,689 unflagged, so a sampled
unflagged row stands for 797 entities and a flagged one for 15. Weighting each
stratum back to its population size:

```
        raw R   weighted R      95% CI          judged
SRP     0.972     0.875     [0.562, 0.975]        200
OCP     0.943     0.733     [0.440, 0.909]        200
DIP     1.000     1.000     [0.410, 1.000]        200
ISP     0.914     0.552     [0.400, 0.700]        400
LSP     0.966     0.352     [0.159, 0.614]        400
```

The upper-bound claim survives for SRP, OCP and DIP and does NOT for LSP and
ISP. Flag rates, agent comparison and merged-vs-rejected are unaffected: one
detector applied uniformly, so the ratios stand.

The three LSP violations the detector missed say what its blind spot is made of,
and all three are silent rather than declared refusals:
* Vaadin `StateTree.RootNode.setParent` throws `IllegalStateException` for every
  non-null parent where the base accepts any.
* CommCare `BlankLoginActivityUIController.setPermissionDeniedState` is an
  EMPTY body where the base disables the login button and shows the error.
* nd4j `Linspace.setY` assigns `this.y = null`, a setter that discards its input.

### What was changed in the thesis

* ch3: the in-sample paragraph now describes the holdout design.
* ch4 `sec:validation`: "no holdout was drawn" replaced by how it was drawn.
* ch4 `tab:validation`: pooled over every round, weighted recall with CIs, plus
  a new paragraph on why weighting is required and what it costs.
* ch4: new `tab:holdout` and the categorical-vs-numeric explanation.
* ch4 summary table cells, ch5 RQ2, and all three abstracts (EN, FR, AR).
* `07_verify/verify_thesis_numbers.py` now pools every judged round, weights
  recall against the parquet populations, and checks the holdout figures.
  Its stale-number check respects digit boundaries, so a real 1,546 is no
  longer reported as the superseded 546.

**Verifier: 21 checks, 0 FAIL. Thesis builds clean, 86 pages.**

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
