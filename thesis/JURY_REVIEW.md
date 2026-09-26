# Jury-style review of the thesis (15 September 2026)

Written as an examiner would: what is wrong, what is weak, what a jury will ask.
Each item records what was done about it. Checked against the arXiv/publisher
pages, the corpus parquets and the model output, not against memory.

## A. Factual errors (must fix)

| # | Where | Problem | Evidence | Fix |
|---|---|---|---|---|
| A1 | Ch2 | RealBench quoted as best Architecture@k "2.25/4" and pass@1 ">40% under 500 lines, <15% over 2,000" | Full text: best 2.29; 25.48% (<=500 LOC) vs 5.81% (>=2,000 LOC) | Numbers corrected |
| A2 | Ch2 | SlopCodeBench: "20 problems, 93 checkpoints, 11 models, 2.2x, 17.2%" | Current version: 36 problems, 196 checkpoints, 15 agents, 2.3x, best 14.8%; erosion rises in 77% of trajectories | Numbers corrected |
| A3 | Ch1, Ch2 | METR: agent rejection "roughly twice" the human reference | Note gives: maintainers merged 68% of golden patches; ~24-point gap. "Twice" is not stated | Replaced by the stated figures |
| A4 | Ch2 | SWE-EVO: "a leading agent that resolves 73% of Verified resolves 25% of SWE-EVO" | Two different models: GPT-5.2 72.80% on Verified, GPT-5.4 + OpenHands 25% on SWE-EVO | Reworded |
| A5 | Ch1, Ch2 | Ben Sghaier: "35 sequential releases of five harnesses" | Five harnesses studied; the 35 releases are of Qwen Code CLI alone, on 50 SWE-bench Verified tasks | Reworded |
| A6 | Ch1 | Cynthia et al.: "merge rates differ" | Paper reports merge *practices* (CI before merge), not merge rates | Reworded |
| A7 | Ch2, Ch5 | Pehlivan: "SRP and ISP detected reliably" | Full text: SRP easiest, then OCP/ISP, LSP, DIP hardest | Reworded to SRP easiest, DIP hardest |
| A8 | Ch3 | AIDev "more than 116,000 repositories" | `all_repository.parquet`: 957,209 rows; `all_pull_request.parquet`: 7,685,281 | Corrected from the data |
| A9 | Ch4 | "No published tool implements an open/closed check"; "OCP has no counterpart in the three tools" | DesigniteJava 1.1.2 jar contains *Missing Hierarchy* and *Unexploited Encapsulation*, both about type-based conditionals. Our cross-check recorded them but never compared them | Claim withdrawn; stated as an uncompared counterpart, added to future work |
| A10 | Ch4 | Resolution "93.3% to 17.3%" | Facts.java records "17% vs 93% on Chronicle-Wire"; no decimal source | Rounded to the recorded values |
| A11 | Ch4 | Fact cache "cold pass 125 minutes, warm 3.6" | No log, commit or code comment records these | Removed |
| A12 | Ch4 | Environment: pyarrow 25.0.0, VS Code 1.133, "Windows 11 Home" | Measured: pyarrow 25.0.1, VS Code 1.137.0, Windows 11 Home Single Language, Lenovo model 82K2 | Corrected |
| A13 | Bib | LCOM4 attributed to Henderson-Sellers | LCOM4 is Hitz and Montazeri (1995); Henderson-Sellers is LCOM* | Citation added and note removed |
| A14 | Bib | Author fields "SprocketLab", "Fsoft AI Center", "authors not listed", "Li, Jia and Others" | Real author lists on arXiv | Replaced |
| A15 | Ch1, Ch2 | Uncited claims: ReAct; HumanEval (Chen et al. 2021 cited inline with no entry); smell/fault-proneness literature; "Martin's minimum switch" | Sources found for the first three; nothing supports the fourth | Citations added; the "minimum switch" phrase removed |

## B. Methodological points a jury will raise

1. **Multiple comparisons.** Agent coefficients are tested per principle with no correction.
   Bonferroni over the agent coefficients: Java 6 tests (alpha 0.0083), Python 10 (alpha 0.005).
   Java Claude Code OCP p = 0.0100 does *not* survive alone; Python OCP p = 0.0001 does;
   Python DIP p = 0.0096 does not. **Consequence:** the OCP effect is claimed on the
   replication, and the Python DIP effect is downgraded to unconfirmed. Added to results and
   limitations.
2. **Judge self-consistency exists only for Java** (174 rows, 89.7%). No Python re-judging.
   Stated as a limitation.
3. **Python SRP model result** (era OR 1.05/month) rests on a flag with precision 0.110.
   It is now described as a trend in large-class flags, not in responsibility violations.
4. **Rejected is not refused.** Already handled by stratum; kept.
5. **Change size as a mediator.** Size is adjusted for, but agents may choose size as part of
   how they work; adjusting removes that part of any agent difference. Stated.
6. **One LLM judge, same family as one agent.** Tested per agent; kept, wording tightened.
7. **Construct validity of "SOLID adherence".** Handled in limitations; kept.

## C. Presentation

1. **Machine-sounding prose.** Counted before the rewrite: "rather than" 137 times,
   453 `\textbf` in chapter text, repeated stock phrases ("measured rather than assumed",
   "is itself a finding", "the honest statement", "precisely", "deliberately",
   "by construction"), negative parallelisms ("not X; it is Y"), aphorism-style closing
   sentences, self-description of rigour. Rewritten in plain academic style; bold kept to
   table headers and defined terms.
2. **Overclaiming.** "A methodological result independent of SOLID", "the gap is the
   contribution", "carries the chapter". Toned down to what the data shows.
3. **Figures.** Several were generic box-and-arrow diagrams and one printed log values as
   counts. Redesigned; every figure rendered and checked for collisions.
4. **Defence deck.** Deleted by the author; rebuilt for both languages.

---

# Second pass (26 September 2026)

Three defects found by re-deriving the figures from the scripts instead of reading
the text, and two gaps closed.

## D. Defects found and corrected

| # | Where | Problem | Evidence | Fix |
|---|---|---|---|---|
| D1 | Ch4, Ch5, figures, deck | **Stratum-weighted recall was weighted by the wrong population.** The weights used raw parquet rows, but the samplers draw from a deduplicated, filtered frame. For Java LSP the raw set is 1,546 flagged / 79,689 unflagged; the sampled population is 574 / 25,594. | `labels/make_java_labeling_sample.py:SPEC` defines the frame the sample is drawn from; the weight must be that frame | Every Java recall recomputed: SRP 0.875 to 0.828, OCP 0.733 to 0.742, LSP 0.352 to 0.386, ISP 0.552 to 0.464. Python figures were already drawn from the sampler frame and did not move. |
| D2 | Validation figures | **No script produced the Java validation numbers.** Python had `score_py2_labels.py`; Java did not, so its figures were only in prose. | Same class of defect as the 2026-09-15 "681" headline | `labels/score_java_labels.py` written as the Java twin: population from `SPEC`, Wilson precision intervals, seeded bootstrap for weighted recall, per-agent precision. Log kept at `logs_archive/java_validation.txt`. |
| D3 | Ch4 | **The corpus is balanced on outcome, and the text did not say so.** | `all_pull_request.parquet`: the natural mix is 80.9% merged, 9.5% rejected, 9.6% open | Disclosed where outcome results are reported; outcome rates are conditional, not population rates. |

## E. Gaps closed

1. **A9 from the first pass is now resolved by measurement, not by withdrawal.** The
   Open/Closed detector was compared with DesigniteJava's *Missing Hierarchy* and
   *Unexploited Encapsulation* over the 63,326 production types of the 138 cross-check
   repositories where both tools produced output: **kappa 0.122**, ours 886 flags (1.40%)
   against DesigniteJava's 299 (0.47%), 76 shared. Reported in Section 4.7.2 as a negative
   result, carried into the limitations and into future work. Script:
   `05_crosscheck/compare_ocp_designite.py`; output `04_report/ocp_designite_comparison.json`.
2. **Deduplication differs between the sampler and the report.** The sampler keeps the first
   row per class (7,501), the report keeps the largest (7,608); any-row gives 7,624. The
   123 classes involved are 1.6% of the total. Stated in Section 4.6.

## F. Verifier

`07_verify/verify_thesis_numbers.py` now runs **61 checks, 0 FAIL**, including the corrected
recall population, the present counts, the OCP/DesigniteJava comparison, and a stale-text
list that fails the build if a withdrawn figure reappears.
