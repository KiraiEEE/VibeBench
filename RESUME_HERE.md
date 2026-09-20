# RESUME HERE

## State (15 September 2026): both studies finished, thesis and deck rebuilt

Nothing is running and no experiment is in progress.

```
Java study ............ finished   4,245 PRs, 1,495 judged entities
Python replication .... finished   4,946 PRs, 980 judged entities
Thesis ................ 95 pages, both languages, thesis/latex/main.pdf
Defence deck .......... 50 slides, both languages, thesis/presentation/main.pdf
Verifier .............. 53 checks, 0 FAIL
Branch ................ python-v2-replication (pushed); main is older
```

Check it from the data, not from this file:

```
python vibebench/07_verify/verify_thesis_numbers.py     # expect 53 checks, 0 FAIL
cd thesis/latex && latexmk -xelatex main.tex            # expect 0 undefined references
cd thesis/presentation && latexmk -xelatex main.tex
```

## Still open

1. **Cover fields.** Jury president, defence date and topic code are blank fill-in lines
   (`\jurypresident`, `\defencedate`, `\topiccode` in `thesis/latex/main.tex`). Fill them in when
   the department sets them; never put invented values there.
2. **Merge.** All work is on `python-v2-replication`. Merge it into `main` when the author agrees.
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
