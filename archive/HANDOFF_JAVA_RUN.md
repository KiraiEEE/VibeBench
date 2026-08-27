
[V1 CANCELLED 2026-08-25] Superseded by REDESIGN_V2.md (outcome-balanced corpus).
The defect fixes, rubric, tooling and Python results in this file and RERUN_LOG.md all
still apply. The corpus DESIGN in this file does not.
# HANDOFF, VibeBench Java rerun (paste this into a new session)

## Context

VibeBench benchmarks AI coding agents on SOLID adherence. The **Python** half is
finished and validated. The **Java** half is mid-run on the new `AIDev-7.6M`
dataset with 3 agents (OpenAI_Codex, Copilot, Claude_Code).

Read `RERUN_LOG.md` first. It has the full provenance, the 10 defect fixes made
on 2026-08-19, the authoritative remaining-work checklist, and at the end the
section **"PYTHON RESULTS, FINAL"**, which SUPERSEDES every Python number in
`CLAUDE.md` (that file still describes the old AIDev-full run: 546 PRs, 5 agents).
Do NOT re-read conversation history; those two files contain everything.

**Background jobs were deliberately stopped by the author.** Nothing is running.
Calibration is checkpointed at 1,314 PRs (1,117 `ok`) and resumes on re-run.

## State on disk (all persisted, nothing to redo)

| Item | State |
|---|---|
| `corpus/gold_set_java.parquet` | 2,091 PRs / 464 repos / 3 agents, repo-stratified |
| `corpus/clone_manifest_java.parquet` | COMPLETE, 2,091/2,091 PRs, 23,945 ok files |
| `corpus/ai_files_java/` | 26,992 extracted files |
| Analysable PRs | **1,452**, Codex 500 · Copilot 489 · Claude_Code 463 |
| `corpus/java_pr_results.parquet` etc. | calibration-mode rows (flags null) |
| Caches | `tree_cache/` + `java_facts_cache/` populated |

## What is running RIGHT NOW

```
python vibebench/03_principles/java_lang/run_java.py --calibrate --workers 6
 log: C:/Users/KiraiEEE/AppData/Local/Temp/java_calib2.log
```
It resumes from checkpoint (1,114 PRs already permanent). When it finishes it
prints SRP / OCP / ISP percentile distributions.

Check progress:
```
tail -3 "C:/Users/KiraiEEE/AppData/Local/Temp/java_calib2.log"
```


## ⚠️ IF THE PC WAS RESTARTED (or calibration is not running)

Background jobs do NOT survive a reboot. Calibration checkpoints every 25 PRs,
so nothing is lost -- just re-issue the SAME command and it resumes:

```
cd D:\VibeBench
python vibebench/03_principles/java_lang/run_java.py --calibrate --workers 6
```

Check whether it still needs running:
```
python -c "import pandas as pd; d=pd.read_parquet('vibebench/corpus/java_pr_results.parquet'); print(len(d),'PRs checkpointed')"
```
If it prints ~1452 or more AND the log ended with the percentile tables, calibration
is DONE -- skip to setting thresholds. Otherwise re-run the command above.

Do NOT delete the parquets before re-running: the resume depends on them, and the
harness auto-evicts calibration rows when you later run WITHOUT --calibrate.

## NEXT STEPS (in order)

### 1. When calibration finishes, set thresholds
Read the printed percentiles, then edit `vibebench/config.py`:
- `SRP_JAVA_THRESHOLDS`, size knobs at ~p90 of the PRODUCTION, entity-deduplicated
  distribution (`class_loc`, `class_wmc`, `class_methods`, `func_ccn`). Keep the
 scale-free knobs (`class_lcom` 0.8, `c3_suppress` 0.4, `min_hits` 2).
- `JAVA_DELTA_THRESHOLDS["ocp"]`, `type_branches` from the instanceof/type_eq
  distribution, `value_branches` from the switch distribution.
- `JAVA_DELTA_THRESHOLDS["isp"]`, `fat_members` at ~p90 of contract width.

The CURRENT values were calibrated on the OLD 27-repo single-agent corpus and
must be replaced. This is a hard rigor gate in `CLAUDE.md`.

### 2. Full flagged run
```
python vibebench/03_principles/java_lang/run_java.py --workers 6
```
The harness now AUTO-EVICTS calibration rows, so you do not have to delete the
parquets, but deleting them is still safe. Fast: the facts cache is
content-hashed and calibration populated it.

Writes all five: `srp_/ocp_/lsp_/isp_/dip_results_java.parquet` + `java_pr_results.parquet`.

### 3. Report
```
python vibebench/04_report/report_java.py
```
Gives per-agent pooled + macro/repo with Wilson CIs, a `status x agent`
cross-tab, and a symbol-resolution warning. **Lead with MACRO for any agent
comparison**, pooled and macro disagreed for Codex in Python and reversed the
DIP ranking.

### 4. STOP HERE and report flag counts to the author
That is the handoff point. The author will switch to **Sonnet** for labelling.

### 5. Labelling (Sonnet session)
```
python vibebench/labels/make_java_census.py --all
python vibebench/labels/extract_java_label_batches.py --principle srp --file java_srp_census.csv
```
**READ `vibebench/labels/JAVA_JUDGING_RUBRIC.md` FIRST AND FREEZE IT.** Python's
SRP judging drifted 3x mid-run and cost a 480-row re-judge.

Design: 100% of flagged entities + 10% unflagged, same as Python, so precision
is comparable. Precision is therefore EXACT, not estimated; recall is estimated.

Then per principle:
```
python vibebench/labels/check_label_file.py --file java_<p>_census.csv --pred-col <c> --label-col llm_<p>_risk
python vibebench/labels/score_labels.py --file java_<p>_census.csv --pred-col <c> --label-col llm_<p>_risk
```
pred cols: srp=`srp_risk` ocp=`smell` lsp=`violation` isp=`flagged` dip=`dip_risk`
The guard must print PASS before quoting any number.

### 6. External cross-check
```
python vibebench/05_crosscheck/run_crosscheck_java.py --self-check
python vibebench/05_crosscheck/run_crosscheck_java.py --workers 4
```
CK + PMD + DesigniteJava vs our SRP/LSP/DIP, plus the new `quality_profile`
block (7 PMD design rules, 17 Designite smells, CK CBO/RFC/DIT/TCC/LCOM*).

### 7. Martin zones, BOTH languages
Python is ALREADY current (`corpus/arch_results.parquet`, 122,453 packages).
Java still to do:
```
python vibebench/03_principles/arch/run_arch.py --lang java
python vibebench/04_report/report_arch.py
```
CAVEATS: descriptive only, no violation flag, no agent ranking (Martin's D
validated at precision 0.000 as a change signal). Do NOT use the
touched-vs-untouched contrast; it pointed in OPPOSITE directions across languages.

### 8. CROSS-LANGUAGE COMPARISON, this is the scientific payoff

The 3 agents x 2 languages design exists for this. Do not skip it: everything
before this step is measurement; this step is the RESULT.

Compare Java against the Python numbers in `RERUN_LOG.md` -> "PYTHON RESULTS".

**Hypotheses to test explicitly (state each as confirmed / refuted / unresolved):**

1. **Intra- vs inter-unit** (the study's headline). Python: 443 Liskov violations
   PRESENT in touched code, **5** introduced; 533 OCP smells present, 44
 introduced. If Java replicates near-zero LSP/ISP introductions, the finding
 holds in a language where classes are mandatory and `interface` is
 unambiguous, which kills the "your languages don't express SOLID" objection.
 This is the single most important comparison.
2. **Is agent ranking language-stable?** Python macro: Claude_Code worst on
   OCP (6.18%) and SRP; Copilot cleanest on DIP (2.12%). If Java reorders them,
 ranking is NOT a stable property of an agent, itself a headline result.
3. **Does the compiler shape which violations are expressible?** Java's LSP uses
   only 3 signals because `javac` enforces the other four; Python uses 5;
 TypeScript dropped `param_removed` because its methods are bivariant. Compare
 the LSP signal mix, not just the rate.
4. **Authored vs edited.** Python: every agent's AUTHORED classes flag LOWER than
   the pre-existing classes they edit (Claude 12.38% vs 13.33% pooled). Does Java
 agree?

**Comparability rules, apply all of them or the comparison is invalid:**
- Use MACRO-per-repo on both sides. Pooled and macro disagree for OpenAI_Codex
  and REVERSE the DIP ranking.
- Precision is only comparable because both languages use the SAME labelling
  method (100% flagged + ~10% unflagged). Never compare against a
 30-per-stratum sampled figure: same detector, same corpus, Python SRP read
 **0.33** sampled and **0.064** enumerated.
- **Disclose the sampling asymmetry:** the Java corpus is repo-STRATIFIED
  (`JAVA_REPO_STRATIFIED_DRAW=True`), Python is a natural draw. Macro-averaging
 makes rates unbiased either way, but say so in the methods section.
- LSP/ISP recall is bounded by symbol resolution, which is repo-dependent
  (measured 25-95%). Do not compare LSP/ISP recall across languages without
 quoting the resolution figure beside it.

### 9. Documentation pass (author deferred until the experiment is done)
Items 16-21 in the `RERUN_LOG.md` checklist. Most important: the symbol-resolution
figure. Docs say 93-100%; MEASURED 25-95% (median 66-78%), because plugin and
framework-heavy repos inherit from external jars that source-only resolution
cannot see. This bounds LSP and ISP recall and must not be quoted as 93-100%.

### 10. AFTER the cross-language section, remaining science
See `RERUN_LOG.md` -> **"FUTURE WORK, beyond the Java run"**. Two items there are
worth doing and one is nearly free:

- **Python SRP second operating point (ZERO re-run, ~1 hour).** The shipped rubric
  is a screen at P=0.064/R=1.000. A holdout-validated tighter bar
 (`loc>=400, wmc>=50, methods>=20`) gives **P=0.183/R=0.884**, 3.5x the precision
 for 12% of the recall. Every metric is already in `srp_results.parquet`. Report
 BOTH points. Repeat the same split on Java once its labels exist; do not copy
 Python's numbers across.
- **OCP churn signal** (CLAUDE.md improvement programme #5), modification
  frequency as a width-independent openness signal. Designed, not built; needs its
 own label round.

## Working rules the author has set

- Do not stop to ask permission for routine steps; act and report.
- Verify claims against code/data before stating them, including claims in
  `CLAUDE.md`, which has been wrong at least once (the resolution figure).
- Always print counts beside rates.
- Lead with macro-per-repo, not pooled.
- Never let a transient failure become a permanent status.
- TypeScript is CANCELLED, do not work on it, but do not spend effort removing it.


## WHAT "DONE" LOOKS LIKE (acceptance criteria)

The Java half is complete when ALL of these hold:

- [ ] `report_java.py` prints per-agent pooled + macro + Wilson CIs for all FIVE
  principles, and the `status x agent` cross-tab shows no status that hits
 one agent disproportionately (that would be a data-loss bias, not a result)
- [ ] Corpus balance is within ~15% across agents (currently 500/489/463)
- [ ] Every one of the five principles has: a flag count, a per-agent breakdown,
  a labelled sample scored with `score_labels.py`, and `check_label_file.py`
 printing PASS
- [ ] The labelling split-half consistency check passes (see JAVA_JUDGING_RUBRIC.md)
- [ ] Cross-check run on the NEW corpus; kappas reported for SRP/LSP/DIP plus the
  `quality_profile` block
- [ ] Martin zones computed for Java; Python already current
- [ ] Cross-language section answers the four hypotheses above
- [ ] Symbol-resolution figure quoted from THIS corpus, not the pilot's 93-100%

## FINAL DELIVERABLES FOR THE THESIS

1. **Corpus table**, PRs, repos, top-1/top-3 concentration, per agent, per language
2. **Violation table**, all five principles x 3 agents x 2 languages, macro-first,
   counts beside rates
3. **Validation table**, P/R/F1 per principle per language, with n, plus the
   per-agent precision evenness check that licenses the ranking claims
4. **Present-vs-introduced contrast**, the headline
5. **Quality profile**, PMD rules, Designite smells, CK metrics (context, not SOLID)
6. **Martin A/I plane**, both languages, descriptive only
7. **Limitations**, stated once, in proportion: LLM-judged labels, symbol-resolution
   ceiling on LSP/ISP, SRP screen precision, ISP's small positive count, sampling
 asymmetry between languages
