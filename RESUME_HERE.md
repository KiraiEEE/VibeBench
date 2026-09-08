# RESUME HERE

## Starting a new session? Paste this. It works whatever the state is.

```
Read CLAUDE.md and RESUME_HERE.md before doing anything else.

CLAUDE.md describes a FINISHED Java-only thesis and defence deck. Do not change
any published result, number, or wording unless I ask you to.

Verify the state from the DATA, not from those documents, which go stale:
  python vibebench/07_verify/verify_thesis_numbers.py     -> expect 28 checks, 0 FAIL
  cd thesis/latex && latexmk -xelatex main.tex            -> expect 0 undefined refs

This file says whether anything is in progress. Tell me what you found before
you change anything.

House rules that are not negotiable:
  never write an em dash, and check figures/ too, not only chapters/
  report all five principles in every table
  do not reintroduce human labelling as a limitation, the supervisor ruled it out
  do not mention TypeScript or the V1 Python study anywhere in the thesis
```

Add one line at the end if you want something specific, for example
"then start Python V2, see the Python V2 section of CLAUDE.md" or
"then finish the DIP census".

## Nothing is half-done. The Java study is complete.

The DIP census finished on 2026-09-08. All 195 flagged pull requests in the
corpus have been judged, so DIP precision is **exact** rather than an estimate:

```
round 2 sample : 100 flagged, 89 true   -> 0.890 estimate [0.814, 0.937]
census round   :  95 flagged, 85 true   -> 0.895
CENSUS TOTAL   : 195 flagged, 174 true  -> 0.892  EXACT, no interval
```

The estimate was 0.890 and the truth is 0.892, which also says the sampling used
for the other four principles is very nearly unbiased on the one principle where
it can be checked against the whole population.

| | |
|---|---|
| Thesis | 95 pages, 0 undefined refs, XeLaTeX |
| Deck | 20 slides |
| Verifier | 28 checks, 0 FAIL |
| Judged rows | 1,495 |
| Title | Evaluating Coding Agent Quality: Application to Software Design Principles |
| Remote | https://github.com/KiraiEEE/VibeBench, working tree clean |

## Two decisions the author has already made, do not reopen

* **No human labelling.** The supervisor ruled it out of scope. LLM judging of
  SOLID is cited as a validated technique (Pehlivan et al. 2025). Do not
  reintroduce "no human ground truth" as a limitation anywhere.
* **The title is settled** as above. Do not revert it to "Software Architectures".

## What is left

1. **Python V2**, if asked. `CLAUDE.md` has the plan and `PROMPT.txt` has the
   prompt: new fact extractor matching the Java sidecar's contract, every
   threshold recalibrated from Python percentiles before labelling, and expect a
   larger LSP signal set than Java's three.
2. Optional polish: three figures never design-reviewed (`fig_martin_plane`,
   `fig_delta_harness`, `fig_solid_wheel`).
