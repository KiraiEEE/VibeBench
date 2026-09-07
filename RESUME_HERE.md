# RESUME HERE

**`CLAUDE.md` loads automatically in every session and holds the full picture.**
This file is only the live worklog: what is half-done right now.

## Paused mid-task (2026-09-07)

**DIP census, 23 of 95 batches extracted.**

DIP flags only 195 pull requests in the whole corpus; 100 are already judged.
Judging the last 95 makes DIP precision **exact** rather than an estimate with
an interval. No other principle can do this: SRP would need 28,756 more rows.

```
state   23/95 batches, NO _manifest.json  (correct: a paused run must not look finished)
cost    ~1 minute per PR, so ~70 minutes remain

resume  python vibebench/labels/extract_java_label_batches.py --principle dip \
            --file java_dip_labeling_sample_census.csv --only-missing
gate    python vibebench/07_verify/audit_label_batches.py --principle dip --round _census
judge   record_labels.py, after R.use_round('_census')
score   rescore DIP over all 195, then update the thesis, the deck and the verifier
```

The exact figure may move off 0.890 in either direction once the remaining 95
are read. Report whatever it is. **Nothing else depends on this**, so dropping
it entirely is a valid choice.

## Everything else is DONE and PUSHED

| | |
|---|---|
| Thesis | 94 pages, 0 undefined refs, builds under XeLaTeX |
| Deck | 20 slides |
| Verifier | 28 checks, 0 FAIL |
| Title | Evaluating Coding Agent Quality: Application to Software Design Principles |
| Remote | https://github.com/KiraiEEE/VibeBench, working tree clean |

Recent rounds, newest first:

1. **Em dashes in `figures/`.** The first pass scanned `chapters/` and `front/`
   only, so nine printed em dashes survived in four figures. Verified now
   against the built PDFs, not the source: zero U+2014 in either document.
2. **Retitle**, and a scoping paragraph in Chapter 1 naming the level of
   analysis, since all five proxies operate below the package.
3. **External review acted on.** Five real errors fixed, including one of mine
   (Chapter 4 claimed Claude Code's precision was "equal or higher on every
   principle"; DIP is 0.778 against Copilot's 0.943). The outcome table was
   split by stratum, which changed the finding. Added: the SOLID-is-not-quality
   paragraph, the judge appendix, non-random attrition, preprint status.
4. **The joint model** (`vibebench/06_model/fit_mixed_model.py`). Change size
   dominates; only OCP survives adjustment; era is inert.
5. **The holdout**, 400 rows. The categorical refinement held at 1.000; the
   numeric one fell from 0.862 to 0.704. It also exposed the raw-recall error.

## Two decisions the author has already made, do not reopen

* **No human labelling.** The supervisor has ruled it out of scope. LLM judging
  of SOLID is cited as a validated technique (Pehlivan et al. 2025). Do not
  reintroduce "no human ground truth" as a limitation anywhere.
* **Title is settled** as above. Do not revert it to "Software Architectures".

## What is left

1. Finish or drop the DIP census.
2. **Python V2**, if asked. `CLAUDE.md` has the plan: new fact extractor, every
   threshold recalibrated, expect a larger LSP signal set than Java's three.
3. Optional: three figures never design-reviewed (`fig_martin_plane`,
   `fig_delta_harness`, `fig_solid_wheel`).
