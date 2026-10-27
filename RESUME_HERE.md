# RESUME HERE

## State (26 September 2026): both studies finished, thesis and deck rebuilt

Nothing is running and no experiment is in progress.

```
Java study ............ finished   4,245 PRs, 1,495 judged entities
Python replication .... finished   4,946 PRs, 980 judged entities
Thesis ................ 97 pages, both languages, thesis/latex/main.pdf
Defence deck .......... 50 slides, both languages, thesis/presentation/main.pdf
Verifier .............. 61 checks, 0 FAIL
Branch ................ main only (side branch merged and deleted 2026-10-27)
```

Check it from the data, not from this file:

```
python vibebench/07_verify/verify_thesis_numbers.py     # expect 61 checks, 0 FAIL
cd thesis/latex && latexmk -xelatex main.tex            # expect 0 undefined references
cd thesis/presentation && latexmk -xelatex main.tex
```

## What the 26 September pass changed

Recorded in `thesis/JURY_REVIEW.md`, sections D to F.

* **Weighted recall was weighted by the wrong population.** The weights used raw parquet rows
  instead of the frame the sampler draws from. Every Java recall moved: SRP 0.875 to 0.828,
  OCP 0.733 to 0.742, LSP 0.352 to 0.386, ISP 0.552 to 0.464. Python was already correct.
* **`labels/score_java_labels.py` written.** Java had no scoring script, so its validation
  figures lived only in prose. It is now the single source, logged to
  `logs_archive/java_validation.txt`.
* **Open/Closed compared with DesigniteJava at last:** kappa 0.122 over 63,326 types in 138
  repositories. A negative result, reported in Section 4.7.2 and in the limitations.
* The balanced-outcome corpus and the sampler/report deduplication difference are now stated
  in the text.

## Still open

1. **Cover fields.** Jury president, defence date and topic code are blank fill-in lines
   (`\jurypresident`, `\defencedate`, `\topiccode` in `thesis/latex/main.tex`). Fill them in when
   the department sets them; never put invented values there.
2. **Merge.** Done on 2026-10-27: everything is on `main`, the only branch.
3. **Author's own read** of the thesis and deck before submission.

## What the 15 September review changed

Recorded in full in `thesis/JURY_REVIEW.md`. In short:

* Every citation checked against its arXiv or publisher page. Wrong figures corrected (RealBench,
  SlopCodeBench, SWE-EVO, METR, Ben Sghaier, Pehlivan); placeholder author fields replaced; seven
  sources added for claims that had none.
* **The Java "present" counts were wrong.** 11,822 / 2,577 / 1,090 / 681 were produced by no script
  and no current definition reproduces them. Recomputed with the samplers' own population
  definition: SRP 7,501, OCP 2,343, ISP 816, refused bequest 183 (Python 176). The verifier now
  recomputes them, and the old values are on its stale list.
* Bonferroni reporting added: the Java OCP effect alone does not pass it (p = 0.010); the Python one
  does (p = 0.0001); the Python import-cycle effect (p = 0.0096) is now "not confirmed".
* AIDev size (957,209 repositories, not 116,000), environment versions, the agent-exclusion
  reasons and the DesigniteJava OCP claim corrected from the data and the jar.
* All chapters rewritten in plain academic style ("rather than" 137 to 6 uses).
* All nine figures redesigned and checked for overlaps; a new deck built.
* `FINDINGS.md` summarises the results and conclusions.

## Decisions that stand

* **No human labelling.** The supervisor ruled it out; LLM judging of SOLID is cited as validated
  (Pehlivan et al. 2025).
* **Title:** Evaluating Coding Agent Quality: Application to Software Design Principles.
* **Never mention TypeScript or the V1 Python study** in the thesis or deck.

The history of how the Python replication was run, with its traps, is in
`archive/RESUME_HERE_python_v2_log.md`.
