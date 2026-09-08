# CLAUDE.md, session orientation

**Rule #1. THE CODE AND THE DATA ARE THE SOURCE OF TRUTH. This file is a map, and maps go stale.**

Use it to find things quickly, then verify anything you are about to depend on against the file or
the parquet itself. Any number quoted here without a measurement date should be re-measured before
it is used. This rule has earned its place three times now: an earlier version of this document was
wrong on eight separate claims, the correction table that replaced it was wrong within three weeks,
and in September 2026 the *thesis itself* was still describing the Python detectors in a Java-only
study. `python vibebench/07_verify/verify_thesis_numbers.py` exists because of it. Run it.

---

## STATE (2026-09-07): Java study, thesis and defence deck are FINISHED

| | |
|---|---|
| Title | **Evaluating Coding Agent Quality: Application to Software Design Principles** |
| Corpus | **6,956 curated -> 4,245 analysable** pull requests over **1,010 repositories** |
| Agents | Claude_Code (1,109), Copilot (2,366), OpenAI_Codex (770) analysable PRs |
| Outcomes | merged, rejected, open, balanced on agent x outcome |
| Language | Java study COMPLETE. **Python V2 IN PROGRESS** since 2026-09-08, phase 2 of 7. |
| Validation | **1,495 rows judged**: 200 per principle, a 200-row holdout for LSP and ISP, and a **CENSUS of all 195 flagged DIP pull requests** |
| Cross-check | CK + PMD + DesigniteJava over 150 repositories, 111,029 shared types |
| Thesis | **95 pages**, `thesis/latex/`, **XeLaTeX only** (fontspec), 0 undefined refs |
| Deck | **20 slides**, `thesis/presentation/`, XeLaTeX |
| Verifier | `verify_thesis_numbers.py`, **28 checks, 0 FAIL** |
| GitHub | **https://github.com/KiraiEEE/VibeBench**, PRIVATE. Code, thesis and deck all pushed. |

`REDESIGN_V2.md` is a **historical record of how V2 was planned**, not an authority.
`archive/PROJECT.md` and `archive/RERUN_LOG.md` describe V1 and are superseded.

### Headline results

| principle | flag rate | precision | weighted recall |
|---|---|---|---|
| SRP | 7.7% of authored classes (5.6% macro) | 0.350 | 0.875 [0.562, 0.975] |
| OCP | 6.7% of PRs | 0.330 | 0.733 [0.440, 0.909] |
| ISP | 6.5% of PRs | 0.335 | 0.552 [0.400, 0.700] |
| DIP | 4.6% of PRs | **0.892** exact | 1.000 [0.537, 1.000] |
| LSP | 0.4% of PRs | 0.420 | 0.352 [0.159, 0.614] |

**RECALL IS STRATUM-WEIGHTED.** The samples are 100 flagged + 100 unflagged; the corpus is nowhere
near 50/50 (LSP: 1,546 flagged against 79,689 unflagged). Counting the strata as equally common
gives LSP recall 0.966 instead of 0.352. Never quote a raw recall from this study.

**The two refinements, and what the holdout did to them:**

```
                                   fitted sample     HOLDOUT      pooled
LSP restricted to refused_bequest   1.000 (31/31)  1.000 (33/33)  1.000 (64/64)
ISP restricted to >=30 members      0.862 (25/29)  0.704 (19/27)  0.786 (44/56)
```

A categorical rule survived being fitted; a numeric threshold did not. ISP's fitted 0.862 falls
outside its own holdout interval [0.515, 0.841].

**Per agent, pooled (macro/repo in brackets):**

| principle | Claude_Code | Copilot | OpenAI_Codex |
|---|---|---|---|
| SRP authored | 9.5 (8.4) | 5.1 (3.5) | 6.8 (7.4) |
| OCP | 10.6 (10.1) | 6.4 (3.0) | 2.1 (2.2) |
| ISP | 8.7 (6.0) | 6.7 (4.0) | 2.9 (5.0) |
| DIP | 6.5 (5.1) | 4.1 (2.8) | 3.2 (5.4) |
| LSP | 0.7 (0.3) | 0.3 (0.1) | 0.0 (0.0) |

**DO NOT QUOTE THAT TABLE AS AN AGENT RANKING.** `vibebench/06_model/fit_mixed_model.py` fits
agent + log1p(files) + months + repository jointly (GEE, cluster-robust). Result:

* change size dominates, OR **1.9 per doubling of files touched**, every principle, p < 1e-4;
* only **OCP** survives adjustment (Claude_Code OR 2.22, 1.21-4.07, p=0.010). ISP and DIP fall to
  1.11 and 1.29 with intervals straddling 1;
* **era is inert** (OR 1.00-1.03, all p > 0.5), so the seven-month date spread is a confound in
  principle and not in fact. It is no longer carried as a limitation.
* LSP is not fitted: 16 flagged PRs cannot support four parameters.

**Outcome axis, REPORTED BY STRATUM, never as one merged-vs-rejected number:**

| stratum | n | OCP | ISP | DIP | LSP |
|---|---|---|---|---|---|
| merged | 1,521 | 8.3 | 7.5 | 4.6 | 0.5 |
| changes_requested | 36 | 5.6 | 16.7 | 13.9 | 0.0 |
| reviewed_then_closed | 135 | 12.6 | 12.6 | 12.6 | 0.7 |
| closed_unreviewed | 1,006 | 7.5 | 5.5 | 5.6 | 0.5 |
| open | 490 | 8.8 | 9.2 | 5.9 | 0.2 |

Only **171** of the 1,177 not-merged PRs were ever reviewed. Against the 1,006 unreviewed ones
merged code is indistinguishable, which is the claim the corpus supports. The two reviewed strata
run HIGHER on ISP and DIP, which is suggestive only (n=36 and 135; 6 of 17 flagged
`reviewed_then_closed` come from one repo).

**Locus of risk:** ~4,100 genuine SRP violations *present* in touched code; PRs *introduced* one in
2-4% of cases. 681 refused-bequest relationships exist, 16 were introduced. Attribution is
**touched, not authored**, for the three relation-level principles.

**Cross-check (150 repos, production types):** ours~PMD k=0.574, ours~Designite k=0.531,
PMD~Designite k=0.384. 68.0% of our flags corroborated by >=1 independent tool. Spearman vs CK:
wmc 0.977, methods 0.969, loc 0.944; cohesion weak (0.288) but CK's own variants agree only 0.490.

### Weaknesses that stay stated

* Agreement is **rubric vs reading judge**, and the judge can err in the same direction as the
  rubric. LLM judging of SOLID is a validated technique (Pehlivan et al. 2025, 240 manually checked
  examples), so this is the design, NOT a missing human study. **The author and supervisor have
  ruled that human labelling is out of scope; do not reintroduce it as a limitation.**
* Judge and one agent are both Claude. Tested in Table 4.9: on 4 of 5 principles Claude_Code's
  flags are confirmed MORE often, the opposite of leniency. DIP is the exception (0.792 vs 0.959
  over the census).
* Source-only analysis: median supertype resolution 87.0%, so LSP and ISP under-count in the 718
  PRs below 50%. Direction is favourable (a missed candidate, never an invented one).
* `repo_too_large` drops 150 PRs, non-randomly: the biggest repositories.
* "Rejected" is mostly `closed_unreviewed`, superseded or stale, not refusal on quality.
* SOLID adherence is not design quality. Nothing here observes an outcome.

---

## DIP CENSUS: DONE (2026-09-08)

**DIP precision is now EXACT, not an estimate.** All 195 flagged pull requests in
the corpus have been judged, so the flagged stratum is a census: no sampling
error, no interval.

```
round 2 sample : 100 flagged judged, 89 true  -> 0.890 estimate [0.814, 0.937]
census round   :  95 flagged judged, 85 true  -> 0.895
CENSUS TOTAL   : 195 flagged judged, 174 true -> 0.892  EXACT
```

The estimate was 0.890 and the truth is 0.892, so the sampling procedure used
for the other four principles is very nearly unbiased on the one principle where
that can be checked. No other principle admits this: SRP would need 28,756 more
rows.

Weighted recall becomes 1.000 [0.537, 1.000]; the interval TIGHTENED (from
[0.410, 1.000]) because the numerator is now exact rather than scaled up from a
sample. It is still driven by the 100-row unflagged stratum.

Per agent, over the census: Claude_Code 0.792 (57/72), Copilot 0.959 (94/98),
OpenAI_Codex 0.920 (23/25).

**Judging rule used, recorded so it is auditable.** TRUE when the batch shows at
least one newly cyclic pair involving a module the PR changed. FALSE when there
is no charged pair AND no new internal import edge at all, which was 10 of the
95: the detector had charged a cycle to a PR whose declared file list names a
module whose text is identical between the reconstructed trees. That gap between
"declared as touched" and "text actually differs" is the only systematic source
of DIP false positives found.

## What to do next

**Python V2 is in progress.** See `PROMPT.txt` section 1 for the full prompt,
`RESUME_HERE.md` for the phase list, and the section below for the design.
Phase 1 (curate) is done: 7,050 PRs / 3,974 repos. Phase 2 (clone + extract) has
run only a 20-PR smoke test.

## Python V2, if the author asks for it

The framework is **language-parameterised**: only fact extraction is language-specific. A V2 is a
new extractor plus a re-run, not a second study.

* **Reuse unchanged:** `01_curate/curate.py` (takes `--lang`), the delta harness
  (`03_principles/gitsnap.py`), all five detector *shapes*, `04_report/report_java.py` as a
  template, the whole `labels/` guard chain, `06_model/fit_mixed_model.py`, `07_verify/`.
* **Write new:** a Python fact extractor producing the same fact stream the Java sidecar emits
  (`03_principles/java_sidecar/Facts.java` is the contract to match). No JVM needed; use `ast`.
* **Recalibrate:** every threshold. Java's numbers (OCP 3/8 branches, ISP >=13 members, SRP
  loc 362 / wmc 45 / methods 20) are Java percentiles and mean nothing in Python. Calibrate from
  corpus percentiles BEFORE labelling, then DELETE the parquets and re-run so no flag predates its
  threshold.
* **Expect a different LSP signal set.** Java's three signals exist because `javac` already rejects
  the other faults. Python has no such compiler, so the Python detector legitimately needs the
  signature/variance signals that would be dead weight in Java. The V1 Python evaluator had five.
* **Disk:** the Java corpus alone is ~350 GB of clones. Check free space first.
* **Do NOT** touch the thesis until Python results exist and are validated. The thesis is currently
  a correct, complete, Java-only document.

### V2 progress and the traps already paid for (2026-09-08)

* Phase 1 done. `PY_REJECTED_CELL_CAP` 700, `PY_CELL_HARD_CAP` 1400 (the MERGED
  ceiling, deliberately 2x), `PY_OPEN_CELL_CAP` 250, repo-stratified draw on.
  Left at Java's constants the same code drew **20,840** PRs and silently
  cancelled the merged over-draw.
* `V2_WITHIN_AGENT_BALANCE` **ignores `cell_cap`**. Read that branch, not the
  cell loop above it, before changing any size.
* Sticky sampling re-inherits the gold set on disk, so a bad draw perpetuates
  itself until the file is moved aside.
* **`record_labels.py` and `audit_label_batches.py` default to `java`.** Python
  V2 must call `R.use_lang('py2')` and pass `--lang py2`, or it writes labels
  into the finished Java study.
* Four separate V1 delta passes exist (ocp, lsp, isp, dip). Replace them with
  ONE `run_python.py`, both for speed and because four resume states means four
  chances at a partial result that looks complete.
* Calibration exists only in `run_ocp.py` and `run_lsp.py`; SRP, ISP and DIP
  have none, and phase 4 cannot happen without it.
* V1 artefacts are preserved as `gold_set.V1.parquet`,
  `clone_manifest.V1.parquet` and `*_results.V1.parquet`.

---

## House rules (author's, non-negotiable)

* **Never write an em dash.** Not `---`, not the character. Use a comma, or a colon for label
  pairs. En dashes in numeric ranges (`0.330--0.890`) are correct and stay. **Check `figures/`
  too**, that directory was missed once and printed nine of them.
* **Report all FIVE principles in every table.** Leaving SRP out was a repeated complaint.
* Lead with macro-per-repo, print counts beside rates.
* Every caveat goes in ONE limitations section after the conclusion, never scattered.
* No citation enters the thesis without its arXiv or publisher page being read first.
* Do not stop for permission on routine steps.
* Tone: lead with the result, not the journey. Bugs fixed are not results. Do not let the prose
  argue for its own design; let the numbers do it. The author is not a native English speaker and
  is under exam stress. Be concrete, never patronise.

## Setup

No `requirements.txt`. Needs `pandas`, `pyarrow`, `statsmodels`, plus `git` and a JDK on PATH.
No test framework: pure modules self-check when run as scripts.

Stage scripts do `sys.path.insert(0, <vibebench dir>); import config as C`. Run from the repo root
as `python vibebench/<stage>/<script>.py`, never with `-m`.

## Java run order

```
python vibebench/01_curate/curate.py --lang java
python vibebench/02_clone/clone_and_extract.py --lang java --workers 8
mvn -o package -f vibebench/03_principles/java_sidecar/pom.xml
java -cp .../java-sidecar-1.0-jar-with-dependencies.jar vb.Facts --self-check
python vibebench/03_principles/java_lang/detectors.py      # self-check
python vibebench/03_principles/java_lang/run_java.py --workers 6
#   thresholds calibrated from printed percentiles, then the parquets are
#   DELETED and the pass re-run, so no flag predates its threshold
python vibebench/04_report/report_java.py
python vibebench/06_model/fit_mixed_model.py               # the joint model for RQ1
python vibebench/labels/make_java_labeling_sample.py --principle X --n-per-stratum 100 --seed N
python vibebench/labels/extract_java_label_batches.py --principle X --file <sample.csv> --only-missing
python vibebench/07_verify/audit_label_batches.py          # GATE, must be 0 FAIL before judging
python vibebench/labels/score_labels.py --file <sample.csv> --pred-col <col> --label-col llm_X_risk
python vibebench/05_crosscheck/run_crosscheck_java.py --sample 150 --workers 6
python vibebench/07_verify/verify_v2_java.py               # whole-corpus invariants
python vibebench/07_verify/verify_thesis_numbers.py        # thesis text vs the data
```

**A threshold change requires DELETING the parquets.** Resume keeps permanent-status rows.

## The validation guards (why the rounds are trustworthy)

Round 1 was voided on 2026-09-05: 294 of 400 ISP labels had been written for rows whose batch file
did not exist, and 200 more at 3 seconds a row. Three guards now make that impossible.

* **`vibebench/labels/record_labels.py`**, the ONLY sanctioned way to write a label. Refuses a row
  with no source on disk, refuses a label with no written reason, writes atomically. Round-aware:
  call `use_round('_holdout')` or `use_round('_census')` first.
* **`vibebench/07_verify/audit_label_batches.py`**, six mechanical checks, exit 1 on any FAIL.
  Takes `--round`. Run it as a gate between extraction and judging.
* **`extract_java_label_batches.py`** writes `_manifest.json` LAST and only on a clean exit, so a
  killed run cannot pass for a finished one. `--only-missing` makes resumption cheap.

## Java file cheat sheet

* `03_principles/java_sidecar/`, Maven, JavaParser+SymbolSolver 3.25.10. `Facts.java` is the single
  fact source for all five principles. **`Facts.sourceRoots()` infers roots from the files' own
  `package` lines.** Rooting at the repo root drops resolution from 93% to 17%.
* `03_principles/java_bridge.py`, `analyze_tree(files) -> dict`, one JVM per tree, gzipped cache
  keyed by tree CONTENT hash. `VB_SIDECAR_TIMEOUT_S` env override.
* `03_principles/java_lang/detectors.py`, all five principles as pure functions over facts.
  **LSP has THREE signals, not five**: `javac` already rejects a changed parameter list (that is an
  overload), narrowed parameter types (invariance) and reduced visibility.
* `03_principles/java_lang/run_java.py`, ONE delta pass for all five (a tree costs 10-20s to parse).
* `03_principles/gitsnap.py`, four-tier before/after recovery. **Tier 1 is merge-commit only**,
  which is why the outcome contrast must be tier-matched.
* `05_crosscheck/run_crosscheck_java.py`, `--sample N` is the SEEDED RANDOM draw; `--limit N` takes
  the alphabetical head and is biased. **`DESIGNITE_CONCURRENCY` defaults to 1**, so `--workers`
  alone does nothing. Use `VB_DESIGNITE_CONCURRENCY=2 VB_DESIGNITE_HEAP=4g`. No resume.

## Traps this project has paid for

* `ps -W` reports ZERO python processes while several run. **`pgrep` does not exist here either**,
  and a shell loop using it exits instantly. Use PowerShell `Get-Process`, and
  `Get-CimInstance Win32_Process` for command lines.
* Killing a python parent does NOT kill its **java sidecar child**. Kill both.
* **`cd` in a Bash tool call PERSISTS** into later calls and breaks relative paths. Twice looked
  exactly like data loss.
* **Bash heredocs mangle backslashes.** Write patch scripts to a file with the Write tool and run
  them; `python - <<'PY'` corrupts LaTeX and regex. This cost several retries in one session alone.
* When rewriting LaTeX programmatically, never capitalise "the first word on a line". Source wraps
  mid-sentence, so it capitalises the whole document.
* **Beamer + enumitem**: do NOT load `enumitem` in the deck. Beamer defines `\labelenumi` through
  its own templates, so `leftmargin=*` recurses while measuring the label and overflows TeX's input
  stack. The pipeline figure must be fitted to frame HEIGHT, not width.
* RAM is the ceiling, not CPU. 15.4 GB total; five to six workers.
* **A partial result that looks complete is THE recurring bug here.** Every one cost real data.

## Dataset trap

In `AIDev`, `AIDev-2.7M` and `AIDev-7.6M`, `pull_request.parquet` / `repository.parquet` are ONLY
the >100-star curated subset. **The master tables are `all_pull_request.parquet` /
`all_repository.parquet`.** Reading the former while believing it is the latter under-counts by an
order of magnitude.

Per-file attribution is missing for *some* agent in every release, and **which** agent changes
between releases. Never branch on an agent name; recover missing file lists from the GitHub API for
every agent alike.

## Superseded, kept only as record

* **V1** (Python 546 PRs / 147 repos; Java 867 PRs / 27 repos, Codex only), every number
  superseded. `archive/`.
* **TypeScript**, cancelled by the author. A class appears in only 27% of its PRs. Code stays in
  the tree, is not run, do not delete it. **Never mention TS in the thesis or the deck.**
* **Machine-learning precision experiments** (GraphCodeBERT embeddings, trained SRP classifier),
  run in V1, all failed their gate, removed from the thesis. Do not re-propose.
* **Validation round 1**, archived at `vibebench/labels/VOID_ROUND1_2026-09-05/`, not deleted.
