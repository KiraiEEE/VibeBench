# Python V2 validation notes

Findings from judging, recorded as each principle finishes. Numbers here come
from `score_py2_labels.py`; re-run it rather than trusting this file.

## LSP (200 rows judged, 2026-09-14)

```
precision        51/100 = 0.510   [95% CI 0.413, 0.606]    Java 0.420
weighted recall  0.545            [bootstrap 0.315, 1.000] Java 0.352
drift check      flagged halves 0.510 / 0.510; unflagged 0/51 vs 2/49   OK
```

**Precision is concentrated in two pull requests, and that, not the judging,
produced the evenness warning.** The 100 flagged rows come from 36 PRs, and two
hold 50 of them: huggingface/transformers#44781 (30 rows, 14 true) and
mims-harvard/ToolUniverse#154 (20 rows, 0 true). Without those two, precision is
37/50 = 0.740 and the outcome spread (merged 0.76, rejected 0.65) falls inside
the 15-point bar; PR-weighted precision is 0.744. The raw per-outcome spread
(merged 0.759 against rejected 0.297) is the ToolUniverse PR alone, which is
Claude_Code and rejected. Judging was outcome-blind (batches carry no outcome),
so a re-judge would reproduce the same labels; the correct response is to report
the clustering.

**One precedent carries 20 rows.** Every ToolUniverse row is the same pattern:
`BaseTool.run(arguments=None, stream_callback=None, use_cache=False,
validate=True)` whose docstring states that tools not accepting the extra
parameters "will still work", with the framework adapting. The detector
correctly sees `param_removed`; the base contract explicitly permits it
(precedent L6). This is the largest single false-positive class: a
DOCUMENTED flexible signature the detector cannot read.

**The dominant true-positive classes**, all live only because Python has no
compiler: a required parameter added against a base signature (L2, e.g. the
transformers image-processor `_preprocess` family), a keyword catch-all removed
where the base forwards it (L3), optional parameters dropped with no catch-all
(L12), a return contract broken (L22), and refused bequests of real behaviour
(L7, L11).

Precedents L1 to L28 are in `py2_precedents.md`.

***

## OCP (2026-09-14, 200/200 judged, audit 0 FAIL)

```
precision        39/100 = 0.390   [0.300, 0.488]
weighted recall  0.364            [0.257, 0.523]   (raw 0.750, not quotable)
population       2,580 flagged / 13,516 unflagged chains
per agent        Claude_Code 0.444 (45), Copilot 0.368 (19), OpenAI_Codex 0.333 (36)
per outcome      merged 0.239 (46), open 0.562 (16), rejected 0.500 (38)
clustering       100 rows from 90 PRs; top 2 hold 4 rows; PR-weighted 0.378
drift            flagged 0.469 vs 0.314 (ratio 1.50, at the limit), unflagged 1.21
```

Java for reference: precision 0.330, weighted recall 0.733.

**No clustering, so the outcome spread is NOT a sampling artefact.** Unlike
LSP, the flagged rows come from 90 distinct PRs and removing the two largest
changes nothing (0.375). Merged PRs' flagged chains are confirmed at 0.239
against 0.50 to 0.56 for the others. Judging was outcome-blind. The OCP outcome
contrast therefore has to be reported with this precision gap beside it: a
flag rate compared across outcomes mixes different proportions of true
positives.

**The false-positive classes** (precedents O1 to O11): values fixed by an
external specification (vendor SSE event types, protocol methods, grammar node
kinds), input normalisation over argument shapes, Enum dispatch, and special
cases over a generic default. The detector counts branches; it cannot know
whether the value set belongs to the project.

**Weighted recall is low (0.364) because the unflagged stratum is large.**
13 of 100 unflagged chains were open families below the branch threshold
(mostly two- to four-arm factories and project-owned isinstance ladders).
Scaled to 13,516 unflagged chains that outweighs the flagged true positives.

**Two extraction defects found and fixed during this round, neither touching a
label:** (1) two files mapping to one module name made row 114 render another
class's function at the right line of the wrong file; the renderer now picks
the candidate whose enclosing function carries the chain's name, and a scan of
all 200 batches found no other case; (2) the DIP renderer rebuilt a set inside
a list comprehension, quadratic on the transformers import graph (30 minutes
hung); fixed and resumed with --only-missing. The audit's leak check also
matched a repository variable named `hits =` in ISP row 32; it now ignores
line-numbered source lines.

***

## SRP (2026-09-14, 200/200 judged, audit 0 FAIL)

```
precision        11/100 = 0.110   [0.063, 0.186]
weighted recall  1.000            [1.000, 1.000]   (0 of 100 unflagged were true)
population       8,208 flagged / 63,474 unflagged classes
per agent        Claude_Code 0.130 (46), Copilot 0.077 (26), OpenAI_Codex 0.107 (28)
per outcome      merged 0.120 (50), open 0.105 (19), rejected 0.097 (31)
clustering       100 rows from 92 PRs; PR-weighted 0.114
drift            flagged 0.082 vs 0.137 (ratio 1.68, OK by the absolute-gap rule), unflagged 0/0
```

Java for reference: precision 0.350, weighted recall 0.875.

**Python SRP precision is a third of Java's.** The flag fires on two or more
size or complexity hits (class loc, wmc, methods, lcom, function ccn, function
params). In Python the classes that reach those thresholds are dominated by
framework-imposed shapes (transformers heads, Home Assistant entities, Lightning
callbacks), clients for one service or device, and large configuration classes.
Size and complexity measure the same thing in both languages; responsibility
does not follow size as closely in Python.

Even across outcome and agent (within 5 points), so the SRP agent and outcome
contrasts are not distorted by uneven precision; they are simply built on a
flag that is right about one time in nine.

**Diagnostic only, fitted on this sample, NOT a refinement:** strict flag 10/75
(0.133), five hits 5/15 (0.333), class_loc >= 800 4/14. Java's lesson applies:
a numeric cut fitted on 100 rows did not survive its holdout. None of these is
adopted.

**Recall 1.000 has a degenerate interval** because no unflagged class was
judged a violation; the bootstrap cannot move off 1. Report it with the count
(0 of 100) rather than as an interval.

**Data hygiene, disclosed:** 489 of 124,143 SRP class rows (41 of 11,529
flagged, 0.36%) lie under committed virtual environments (`site-packages`,
`.venv`), 469 of them from one PR (philzook58/knuckledragger#20). The production
filter does not exclude vendored environments. Effect on PR-level rates is one
PR; not re-run.

**Batch cap:** classes over 400 rendered lines are truncated by the extractor
(rows 14, 17, 28, 29, 50, 99, 113, 118, 123, 193 among others). Verdicts on
those rows rest on the visible methods; three of the eleven true positives
(28, 99, 193) are such rows, where the visible part already showed the second
domain.

***

## ISP (2026-09-14, 180/180 judged, flagged stratum is a CENSUS, audit 0 FAIL)

```
precision        34/80 = 0.425    EXACT (all 80 flagged contracts judged)
weighted recall  1.000            [1.000, 1.000]   (0 of 100 unflagged were true)
population       80 flagged / 3,011 unflagged contracts
per agent        Claude_Code 0.479 (48), Copilot 0.353 (17), OpenAI_Codex 0.333 (15)
per outcome      merged 0.375 (32), open 0.722 (18), rejected 0.300 (30)
clustering       80 rows from 57 PRs; pylabrobot#976 holds 8 (8 true); PR-weighted 0.368
without top 2    24/69 = 0.348; merged 0.34, open 0.50, rejected 0.30
drift            flagged 0.513 vs 0.341 (ratio 1.50, DRIFT? by the absolute-gap rule)
```

Java for reference: precision 0.335 (sample), weighted recall 0.552.

**Precision is exact.** With only 80 flagged contracts in the whole corpus the
flagged stratum is judged entirely, as the DIP stratum was in Java.

**The open-outcome precision of 0.722 is one PR.** pylabrobot#976 contributes 8
flagged device-capability contracts, all true; without the two largest PRs the
outcomes are 0.34 / 0.50 / 0.30, within the range sampling noise allows for
n = 10 open rows.

**The drift flag was inspected, not waved through.** Every flagged row's note
was re-read against precedents I1 to I9. No rule is applied differently in the
second half: the second-half negatives are test doubles, mixin self-types,
pending implementations and single stubs, each with a first-half counterpart
judged the same way (rows 9, 32, 45, 11). The first half holds three contracts
that appear twice from forked or sibling repositories (rows 30/44, 48/69,
76/87), all true, which concentrates positives there. No label was changed.
The flag is reported as it stands.

**Where the false positives come from.** The detector counts implementers with
stub bodies. It cannot tell a test double (I1), a null object (I2) or unfinished
work (I3) from a production class forced into members it cannot support. Those
three classes account for most of the 46 false positives. A production-path
filter on IMPLEMENTERS (not only on the contract) would remove the I1 class
mechanically; recorded as a candidate refinement, not applied.

***

## DIP (2026-09-14, 200/200 judged, audit 0 FAIL)

```
precision        100/100 = 1.000  [0.963, 1.000]   (100 of 173 flagged PRs)
weighted recall  1.000            [1.000, 1.000]   (0 of 100 unflagged were true)
population       173 flagged / 4,773 unflagged PRs
per agent        Claude_Code 1.000 (53), Copilot 1.000 (16), OpenAI_Codex 1.000 (31)
clustering       100 rows from 100 PRs (DIP is PR-level)
drift            1.000 vs 1.000
```

Java for reference: precision 0.892 exact (census of 195), recall 1.000.

**What this number does and does not show.** The Java census found exactly one
systematic false-positive class: a module listed as touched whose text was
identical between the trees. The Python V2 detector charges a cycle only to
modules whose text differs, so that class cannot occur, and the judging rule is
the same mechanical rule. Agreement at 1.000 therefore confirms that every
flagged PR really closes a new import cycle through code it changed, and that no
unflagged PR does. It does NOT show that each cycle is a design problem: the
dominant shape (D2) is a leaf module importing its own package root, which
Python tolerates and many projects accept. The thesis must say "introduced an
import cycle", not "violated DIP", for this principle, as the Java chapter does.

**No census round.** With 100 of 100 true and a mechanical rule that shares the
reconstruction with the detector, judging the remaining 73 flagged PRs would
re-read the same computation. The interval [0.963, 1.000] is reported instead.

***

## Summary of the Python V2 validation (all five principles)

```
principle  precision                 weighted recall          Java precision / recall
SRP        0.110  [0.063, 0.186]     1.000 (0/100 FN)         0.350 / 0.875
OCP        0.390  [0.300, 0.488]     0.364 [0.257, 0.523]     0.330 / 0.733
LSP        0.510  [0.413, 0.606]     0.545 [0.315, 1.000]     0.420 / 0.352
ISP        0.425  exact (census 80)  1.000 (0/100 FN)         0.335 / 0.552
DIP        1.000  [0.963, 1.000]     1.000 (0/100 FN)         0.892 exact / 1.000
```

Unevenness that must travel with any contrast: LSP outcome spread is two PRs
(transformers#44781, ToolUniverse#154); ISP open-outcome precision is one PR
(pylabrobot#976); OCP merged precision (0.239) is genuinely lower than open and
rejected (no clustering). SRP and DIP are even.
