# RESUME HERE — VibeBench V2 Java, state as of 2026-09-05 (PAUSED)

**Read this first if context was lost.** Verified against the files on disk.

## THE HEADLINE: validation round 1 is VOID. Round 2 is in progress.

On 2026-09-05 the user challenged how fast the judging had gone. The audit that
followed found the challenge was correct, and round 1 was voided.

### What was actually wrong (measured, not guessed)

| defect | evidence |
|---|---|
| **294 of 400 ISP labels had no source file.** Extraction was killed at row 105; the directory held 106 files and looked complete. | `audit_label_batches.py` check A |
| Those labels are **99.0% reproducible** from `n_members` + `n_implementers` alone, i.e. a threshold detector graded by re-applying a threshold | decision tree, 5-fold CV |
| **SRP and OCP rows 200-399 judged at 3-4.5 s/row**, after their first halves ran at ~15 s/row | git commit timestamps |
| **7 SRP + 2 ISP batches were header-only** — the entity was not found in the tree, so the file rendered with no source at all | audit check C |
| **`notes` was empty for all 1,995 rows.** No audit trail, which is why the user had to catch this instead of the pipeline | — |
| **LSP's reported recall was wrong.** P=0.823 came from the narrow flag, R=0.918 from the broad one. Correct pair is P=0.823 / **R=0.836**, on **62** flags not 200 | recomputed |

### What was NOT wrong, and is not being redone

* The corpus: **4,245 analysable PRs over 1,010 repositories**, 3 agents x 3 outcomes.
* The detectors, the analysis parquets, `report_java.py`, `verify_v2_java.py`.
* **OCP round-1 labels tested as genuine**: only 71% recoverable from the
  detector's own numbers against a 72% baseline, so the judgment used
  information that exists nowhere but the source. LSP and DIP sustained
  29-35 s/row throughout with no discontinuity.

Round 1 is archived, not deleted, in `vibebench/labels/VOID_ROUND1_2026-09-05/`.

## Round 2 design (user's call)

**200 rows per principle (100 flagged + 100 unflagged), seed 777**, then look at
results, then optionally 200 more as a **holdout** — strictly better than 400 in
one pass, because two rounds can be checked against each other.

Verified balanced: every principle 100/100, 0 duplicate rows, 0 pre-filled
labels, 3 agents, 60-112 repos each.

**Overlap with round 1 (anchoring risk), measured before judging:**

| principle | overlap | why |
|---|---|---|
| SRP | 2/200 | population 62,145 |
| OCP | 7/200 | population 15,755 |
| ISP | 25/200 | flagged population 816 |
| LSP | 37/200 | flagged population 574 |
| **DIP** | **103/200** | **flagged population is 195 and round 1 judged all 195** |

DIP's flagged stratum is exhausted; no seed can draw 100 fresh ones. So DIP
round 2 is a **test-retest** measurement. **Commitment: the old labels are not
consulted until all 1,000 rows are judged and locked**, then agreement is
computed and reported whatever it says.

## Where the work stopped (PAUSED 2026-09-05, nothing running)

```
A  SAMPLE        DONE     200 x 5, seed 777, verified balanced
B  EXTRACT       dip 200 DONE | lsp 200 DONE | ocp 200 DONE | isp 34/200 | srp 0/200
C  AUDIT GATE    PASSED for dip, lsp, ocp (0 FAIL each)
D  JUDGE         dip 200/200 DONE | lsp 59/200 | ocp 0 | isp 0 | srp 0
E  SCORE         DIP scored, see below
F  REPORT+VERIFY pending
```

**259 of 1,000 rows judged.** Every judged row carries a written reason in
`notes`; the recorder refuses a label without one.

### DIP is finished and scored

```
n=200  TP=89  FP=11  FN=0   precision 0.890  recall 1.000
per agent : Claude_Code 0.778 | Copilot 0.943 | OpenAI_Codex 1.000  (all R=1.000)
per outcome: merged 0.907 | open 0.769 | rejected 0.909
```

Precision is comparable across agents and outcomes, so the DIP agent ranking and
the merged-vs-rejected contrast are both licensed rather than detector artefacts.
Round 1 reported 0.862 on material that was measuring the wrong thing.

### Known, accepted gap

`java_ocp_batches/180_UNAVAILABLE.txt` -- javifalces/HFTFramework#10, the tree
could not be fetched. OCP will be judged on 199 of 200 rows. This is a genuine
fetch failure, NOT the file-size cap.

## Resume with exactly this

```bash
# finish extraction (isp then srp); --only-missing is already wired in
bash vibebench/extract_round2.sh <scratchdir>

# gate, per principle or all
python vibebench/07_verify/audit_label_batches.py --principle isp

# then continue judging LSP from row 59, then OCP, ISP, SRP.
# Labels are ONLY written through record_labels.py:
#   python -c "import sys; sys.path.insert(0,'vibebench/labels'); import record_labels as R;
#              R.record('lsp', {59:(False,'reason...')})"
# Progress: python vibebench/labels/record_labels.py --status
```

Compact batch viewers used while judging live in the scratchpad
(`lspview.py`); the DIP one was an inline grep over
`NEWLY CYCLIC PAIRS` / `NO newly` plus the `NEW IMPORT EDGES` block.

## Judging rules being applied consistently (keep these)

**DIP** -- a ONE-WAY concrete import is not a violation; a CLOSED CYCLE or an
abstraction importing a concrete IS. Pre-existing cycles never count.

**LSP** -- not a violation when: the override calls `super` first and adds
behaviour; the base already DECLARES the exception the override throws (java
-dataloader `ValueCachingNotSupported`); both methods are PRIVATE, so there is
no polymorphic override at all (deeplearning4j); both are `@Nullable`, so
returning null is inside the contract (maven); the override reports a STRONGER
guarantee (questdb `isThreadSafe` false -> true); it is a protected template
hook only the base calls (micronaut `buildAnnotation`).
IS a violation when: the base does real work and the override is a bare
`throw new UnsupportedOperationException` (refused bequest); the override
narrows what inputs are accepted (Cronvoy rejects non-DIRECT proxies); the
override returns null where the base guarantees a value (StarRocks REST catalog,
nd4j `compress`).

## Guards added 2026-09-05 (these are what make round 2 trustworthy)

* **`vibebench/07_verify/audit_label_batches.py`** — 6 mechanical checks per
  principle, exit 1 on any FAIL. Run before judging and after.
* **`vibebench/labels/record_labels.py`** — the ONLY sanctioned way to write a
  label. Refuses a row with no source on disk, refuses a label with no reason
  (>=12 chars), writes atomically per call. All three refusals tested live.
  Round 1's labels went in by a throwaway pandas assignment; that path is gone.
* **`extract_java_label_batches.py`** — writes `_manifest.json` LAST and only on
  a clean exit, so a killed run cannot look finished; names a source-less batch
  `UNJUDGEABLE_*` instead of writing a silent header-only file; gained
  `--only-missing`.
* **`make_java_labeling_sample.py`** — gained `--seed` and `--suffix`.

## How to judge each principle

**SRP** — "does this class have MORE THAN ONE reason to change?" A DTO with 40
getters is one reason. A class that parses AND caches AND validates is several.

**OCP** — "is the variant set OPEN or CLOSED by nature?" Nearly all false
positives are closed sets fixed by an external specification: JSON delimiters,
Java primitives, JDBC `Types.*`, Avro schema kinds, SVG transforms, HTTP
methods, SQL keyword tables. Those are NOT violations.

**LSP** — only `refused_bequest` counts (`config.LSP_JAVA_STRONG_SIGNALS`). The
shape: base does real work, override is nothing but `throw new
UnsupportedOperationException`.

**ISP** — "are clients FORCED to depend on members they do not use?" Fluent
builders and query DSLs are excluded by design: calling 5 of `TaskQuery`'s 141
chained methods is using the DSL as intended, not suffering a fat interface.

**DIP** — PR-scoped. `new_cycle` only (`DIP_FLAG_INCLUDES_MEAN_D=False`). Did
this PR introduce a genuine import cycle? Pre-existing cycles are labelled as
such in the batch and do not count.

## Decisions already applied

**LSP narrowed to `refused_bequest`** in `config.LSP_JAVA_STRONG_SIGNALS`,
recomputed in `report_java.py::apply_lsp_signal_filter`, no re-run needed.
Round-1 evidence: refused_bequest 62 flags/51 true; precondition_strengthened
62/5; postcondition_weakened 76/**0**. Effect: 54 -> 16 flagged PRs, 1.3% -> 0.4%.
The narrowing stands and is independent of the voided labels, but it should be
CONFIRMED against round 2 before it goes in the thesis.

## Corpus results (unaffected by the validation defect)

| principle | rate | Claude_Code | Copilot | OpenAI_Codex |
|---|---|---|---|---|
| SRP (authored classes) | 12.2% | 9.5% | 5.1% | 6.8% |
| OCP | 6.7% | 10.6% | 6.4% | 2.1% |
| ISP | 6.5% | 8.7% | 6.7% | 2.9% |
| DIP | 4.6% | 6.5% | 4.1% | 3.2% |
| LSP | 0.4% | 0.7% | 0.3% | 0.0% |

Tier-matched merged vs rejected (tiers 2+3+4 only; tier 1 is merged-only and
would confound): SRP 6.1/9.7, OCP 8.3/8.0, ISP 7.5/6.6, DIP 4.6/6.6, LSP 1.7/1.5.

## Still outstanding after round 2

* CK / PMD / DesigniteJava cross-check is **V1 only** (27 repos, 1 agent, merged
  only), quarantined in `vibebench/04_report/V1_ARCHIVE/`. Needs a V2 re-run.
* Optional holdout round (the "other 200").
* Python V2 — user has said optional.

## House rules

* Never write an em dash; use a comma.
* Report all FIVE principles in every table.
* Lead with macro-per-repo, print counts beside rates.
* "Rejected" is reported as rejected; every caveat goes in one brief limitations
  section after the conclusion.
* Verify against code and data before stating anything.
* Do not stop for permission on routine steps.

## Traps this project has already paid for

* `ps -W` reports ZERO python processes while several run. Use PowerShell
  `Get-Process`, and check `CommandLine` via `Get-CimInstance Win32_Process`.
* **Stopping a background bash script does NOT stop its child python, and the
  script advances to the next principle.** Kill by command-line match.
* **`cd` in a Bash tool call PERSISTS** into later calls and silently breaks
  every relative path. It looked exactly like data loss on 2026-09-05.
* Bash heredocs mangle backslash escapes in patch scripts. Write the patch to a
  file with the Write tool and run it.
* RAM is the ceiling, not CPU. Five to six workers; extraction is sequential.
* A partial result that looks complete is THE recurring bug in this codebase.
  Empty diff, empty file list, truncated API page, partial batch directory, a
  status counter reporting 200/200 notes on a file with none. Every one cost
  real data.
