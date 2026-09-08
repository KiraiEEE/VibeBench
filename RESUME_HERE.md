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

## IN PROGRESS: Python V2 (started 2026-09-08)

**`PROMPT.txt` section 1 is the prompt to paste into a new session.** It carries
the phase list, the resume commands and the traps. This is the summary.

```
PHASE 1 curate ................ DONE   7,050 PRs / 3,974 repos
PHASE 2 clone + extract ....... 357 of 7,050 PRs done (smoke test only)
PHASE 3 run_python.py ......... NOT WRITTEN, the main build task
PHASE 4 calibrate thresholds .. NOT STARTED
PHASE 5 report + joint model .. NOT STARTED
PHASE 6 label extract + gate .. NOT STARTED
PHASE 7 judge ~1,000 rows ..... NOT STARTED, the long pole
```

Resume phase 2 with:
```
python vibebench/02_clone/clone_and_extract.py --lang py --workers 8
```

### Bugs found and fixed on 2026-09-08, do not reintroduce

1. **The corpus came out at 20,840 PRs**, three times Java. The balance path
   `V2_WITHIN_AGENT_BALANCE` ignores `cell_cap` entirely and uses
   `CELL_HARD_CAP`. Java's rejected pools sit below that cap so it never bound;
   Python's are 3,716 to 6,566 so it bound three times.
2. **`open` was appended uncapped**, 5,240 PRs against Java's 740.
3. **One ceiling clamped both** the rejected and the merged draw, cancelling the
   2x merged over-draw that exists so balance survives Step-2 attrition.
   Fixed with `PY_REJECTED_CELL_CAP` 700 and `PY_CELL_HARD_CAP` 1400.
4. **Sticky sampling re-inherits whatever gold set is on disk**, so a bad draw
   perpetuates itself across reruns until the file is moved aside.
5. **The label guards hardcoded a `java_` prefix.** Judging Python through
   `record_labels.py` would have written labels into the finished Java study's
   CSVs, and the audit gate would have reported clean over the wrong files.
   Both now take a language and default to `java`; Python V2 must use
   `R.use_lang('py2')` and `--lang py2`.

### Checked and deliberately NOT changed

* Vendored and generated Python is under 0.5% of files, so no path exclusions
  were added. Java needed them (protoc output was 5.8% and agent-biased).
* Test code is 28% of files and `report.py` already separates it.
* Python keeps FIVE LSP signals. Java has three only because `javac` rejects
  the other two; Python has no compiler.

### Watch this at scale

The 20-PR smoke test produced 130 `fetch_failed` rows, **all on Claude_Code**.
If that persists it is agent-biased attrition and must be measured and
disclosed, not silently dropped.

## The Java study is finished and must not be touched

95-page thesis, 20-slide deck, verifier 28 checks 0 FAIL, DIP precision exact at
0.892 over a census of all 195 flagged requests. Do not change any Java result
while Python V2 is in progress. See `PROMPT.txt` section 3.

## Two decisions already made, do not reopen

* **No human labelling.** The supervisor ruled it out of scope. LLM judging of
  SOLID is cited as validated (Pehlivan et al. 2025).
* **The title is settled**: Evaluating Coding Agent Quality: Application to
  Software Design Principles.
