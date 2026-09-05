# RESUME HERE — VibeBench V2 Java, state as of 2026-09-04

**Read this first if context was lost.** Everything below is verified against
the files on disk, not remembered.

## Where the work is

The corpus is finished and verified. What remains is **judging label rows** and
then regenerating the report.

| principle | judged | precision | recall | status |
|---|---|---|---|---|
| SRP | 400/400 | 0.270 | 1.000 | DONE |
| OCP | 400/400 | 0.480 | 0.873 | DONE |
| LSP | 400/400 | 0.823 (after narrowing) | 0.918 | DONE |
| ISP | 98/400 | 0.277 | 1.000 | IN PROGRESS |
| DIP | 0/395 | | | NOT STARTED |

Labels live in `vibebench/labels/java_<p>_labeling_sample.csv`, column
`llm_<p>_risk`. They are written as judging proceeds, so progress is never in
memory only. Judging material is `vibebench/labels/java_<p>_batches/NNN_*.txt`,
where NNN is the row index in the CSV.

## The exact next steps

1. `bash vibebench/finish_extraction.sh <scratchdir>` — extracts the remaining
   ISP and DIP batches, restarts itself on any exit. Check progress with
   `ls vibebench/labels/java_isp_batches | wc -l` (want 400) and
   `java_dip_batches` (want 395).
2. Judge ISP rows 98-399, then DIP rows 0-394. Record with a small pandas
   script that sets `llm_isp_risk` / `llm_dip_risk` by row index, then
   `python vibebench/labels/score_labels.py --file java_<p>_labeling_sample.csv
   --pred-col <flagged|dip_risk> --label-col llm_<p>_risk`.
3. `python vibebench/04_report/report_java.py` and
   `python vibebench/07_verify/verify_v2_java.py` (must be 0 FAIL).
4. Optional, previously discussed: re-run the CK/PMD/Designite cross-check,
   which is still V1-only (27 repos, 1 agent) and quarantined in
   `vibebench/04_report/V1_ARCHIVE/`.

## How to judge each principle

**SRP** — "does this class have MORE THAN ONE reason to change?" Batch shows the
class with loc/methods/wmc/lcom/c3. A DTO with 40 getters is one reason. A class
that parses AND caches AND validates is several.

**OCP** — "is the variant set OPEN or CLOSED by nature?" The detector cannot
tell a fixed grammar from an open type set, which is where nearly all its false
positives come from: JSON delimiters, Java primitive classes, JDBC `Types.*`,
Avro schema kinds, SVG transforms, HTTP methods and a 492-branch SQL keyword
table are all closed by an external specification and are NOT violations.

**LSP** — "can the subclass be substituted for the base?" Only `refused_bequest`
counts now (see below). The shape is: base does real work, override is nothing
but `throw new UnsupportedOperationException`.

**ISP** — "are clients forced to depend on members they do not use?" Rule
applied to the first 98: a contract is a violation if it is WIDE (>=30 members)
and not a fluent builder/query DSL, OR fat (>=18 members) with many implementers
(>=5) each forced to implement the whole surface. Fluent DSLs are excluded by
design: a client calling 5 of `TaskQuery`'s 141 chained methods is using the DSL
as intended, not suffering a fat interface.

**DIP** — PR-scoped, not entity-scoped. The flag is `new_cycle` only
(`DIP_FLAG_INCLUDES_MEAN_D=False`), so the question is whether the PR introduced
a genuine import cycle between packages.

## Decisions already made and applied

**LSP narrowed to `refused_bequest` alone** (config.LSP_JAVA_STRONG_SIGNALS),
approved by the user after validation on 400 pairs:

    refused_bequest             62 flags, 51 true, P=0.823
    precondition_strengthened   62 flags,  5 true, P=0.081
    postcondition_weakened      76 flags,  0 true, P=0.000

The flag is recomputed in `report_java.py::apply_lsp_signal_filter` from stored
`signals` / `lsp_risk_reasons`, so no re-run is needed. Effect: 54 -> 16 flagged
PRs, 1.3% -> 0.4%.

## Current results (report_v4, after the LSP change)

Corpus: **4,245 analysable PRs over 1,010 repositories**, 3 agents x 3 outcomes.

| principle | rate | Claude_Code | Copilot | OpenAI_Codex |
|---|---|---|---|---|
| SRP (authored classes) | 12.2% | 9.5% | 5.1% | 6.8% |
| OCP | 6.7% | 10.6% | 6.4% | 2.1% |
| ISP | 6.5% | 8.7% | 6.7% | 2.9% |
| DIP | 4.6% | 6.5% | 4.1% | 3.2% |
| LSP | 0.4% | 0.7% | 0.3% | 0.0% |

Merged vs rejected, tier-matched (tiers 2+3+4 only — tier 1 is merged-only and
would confound the comparison):

| principle | merged | rejected |
|---|---|---|
| SRP | 6.1% | 9.7% |
| OCP | 8.3% | 8.0% |
| ISP | 7.5% | 6.6% |
| DIP | 4.6% | 6.6% |
| LSP | 1.7% | 1.5% |

## House rules the user has set

* Never write an em dash; use a comma.
* Report all FIVE principles in every table. Leaving SRP out has been a repeated
  complaint.
* Lead with macro-per-repo, print counts beside rates.
* "Rejected" is reported as rejected, with no qualifying language in the body.
  Every caveat goes in one brief limitations section after the conclusion.
* Verify against code and data before stating anything; the docs have been wrong.
* Do not stop for permission on routine steps.

## Traps this project has already paid for

* `ps -W` reports ZERO python processes while several are running. Use
  PowerShell `Get-Process`.
* Judge a bottleneck by whether something is WAITING (processor queue, disk
  queue), not by a raw utilisation counter. RAM is the ceiling on this machine,
  not CPU: 7 workers drove available memory to 0.4 GB while CPU idled at 18%.
  Five to six workers is right.
* Any inline shell loop with process substitution dies with rc=127 here. Put
  supervisors in files.
* A partial result that looks complete is the recurring bug in this codebase:
  an empty diff, an empty file list, a truncated API page, a stale conclusion.
  Every one of them cost real data.
