# CLAUDE.md, session orientation

**Rule #1. THE CODE AND THE DATA ARE THE SOURCE OF TRUTH. This file is a map, and maps go stale.**

Use it to find things quickly, then verify anything you are about to depend on against the file or
the parquet itself. Any number quoted here without a measurement date should be re-measured before
it is used. This rule has earned its place four times: an earlier version of this document was
wrong on eight claims, the correction table that replaced it was wrong within three weeks, the
thesis once described Python detectors in a Java-only study, and on 2026-09-15 the thesis headline
"681 refused-bequest violations present" turned out to come from no script at all (the data gives
183). `python vibebench/07_verify/verify_thesis_numbers.py` exists because of this. Run it.

---

## STATE (2026-09-26): both studies FINISHED, thesis and deck rebuilt

| | |
|---|---|
| Title | **Evaluating Coding Agent Quality: Application to Software Design Principles** |
| Java | 6,956 curated -> **4,245 analysable** PRs, 1,010 repos; 1,495 judged entities |
| Python | 7,050 curated -> **4,946 analysable** PRs, 2,921 repos (3,974 curated); 980 judged entities |
| Agents | Claude_Code, Copilot, OpenAI_Codex in both languages |
| Outcomes | merged, rejected, open, balanced on agent x outcome |
| Thesis | `thesis/latex/`, **97 pages**, XeLaTeX only, 0 undefined refs |
| Deck | `thesis/presentation/`, **50 slides** (7 parts + backup), XeLaTeX, figures `\input` from the thesis |
| Verifier | `verify_thesis_numbers.py`, **61 checks, 0 FAIL** |
| Review | `thesis/JURY_REVIEW.md` lists every correction made on 2026-09-15 and 2026-09-26 |
| Summary | `FINDINGS.md`, results and conclusions for the topic |
| GitHub | https://github.com/KiraiEEE/VibeBench, PRIVATE. One branch only: `main`. Do not create side branches. |

Open items are in `RESUME_HERE.md` (cover fields left blank on purpose, merge to main).

### Headline results (checked by the verifier unless marked)

Precision / stratum-weighted recall:

| principle | Java | Python |
|---|---|---|
| SRP | 0.350 / 0.828 | 0.110 / 1.000 |
| OCP | 0.330 / 0.742 | 0.390 / 0.364 |
| LSP | 0.420 / 0.386 | 0.510 / 0.545 |
| ISP | 0.335 / 0.464 | 0.425 (census of 80) / 1.000 |
| DIP | **0.892** census of 195 / 1.000 | 1.000 / 1.000 |

**RECALL IS STRATUM-WEIGHTED, AND THE WEIGHTS ARE THE SAMPLED POPULATION.** Samples are 100 flagged +
100 unflagged; the corpus is not 50/50. Weight by the population the sampler drew from, which is the
deduplicated, filtered frame in `labels/make_java_labeling_sample.py:SPEC`, NOT the raw parquet rows
(Java LSP 574 / 25,594, not 1,546 / 79,689). Using raw rows was a bug, corrected 2026-09-26; it moved
every Java recall. Unweighted LSP recall would read 0.966 instead of 0.386. Never quote a raw recall.
`labels/score_java_labels.py` and `labels/score_py2_labels.py` are the only sources for these figures;
logs in `logs_archive/java_validation.txt`, `py2_validation.txt`.

**Refinements:** LSP restricted to refused bequest 1.000 fitted, 1.000 holdout (64/64 pooled),
0.857 (6/7) in Python. ISP >= 30 members 0.862 fitted, 0.704 holdout, 0.200 (1/5) in Python. A
categorical rule transfers; a numeric threshold does not.

**Joint model** (`06_model/fit_mixed_model.py --lang {java,py}`, GEE, exchangeable within repo,
cluster-robust): change size dominates, OR per doubling 1.88-1.95 Java, 2.01-2.18 Python. Only
**Claude_Code on OCP** replicates: Java 2.22 [1.21, 4.07] p=0.010 (fails Bonferroni over 6
coefficients alone), Python 2.09 [1.44, 3.04] p=0.0001 (passes over 10). Python DIP 1.88 p=0.0096
does NOT pass and is reported as unconfirmed. Time is inert for delta principles; Python PR-level
SRP rises 1.049/month but that flag has precision 0.110. Java LSP not fitted (16 flags); Java has no
PR-level SRP flag.

**Outcome axis, by stratum, tier-matched only.** Java merged = closed_unreviewed (1,521 vs 1,006);
reviewed strata 36 and 135 run higher on ISP/DIP, suggestive only. Python merged is cleaner:
unmerged OR 1.57 OCP, 2.43 LSP after size and agent. Only 171 (Java) and 196 (Python) unmerged PRs
were ever reviewed, so neither result says what reviewers select on.

**Locus of risk (present vs introduced).** Present = distinct flagged production entities, the
samplers' population definition. Java: SRP 7,501, OCP 2,343, ISP 816, refused bequest **183**
against **16** PRs introducing. Python: SRP 8,208, OCP 2,580, ISP 80, LSP 2,069, refused bequest
**176** against **5**. Precision-corrected PRs introducing a violation: Java 0.4-4.1%, Python
0.3-3.5%. Authored vs edited SRP: Java 7.7% vs 13.5%, Python 8.9% vs 12.1% (pooled).

**Cross-check (Java, 150 repos, production types):** ours~PMD kappa 0.574, ours~Designite 0.531,
PMD~Designite 0.384; 68.0% of our flags corroborated. LSP kappa 0.000 (different unit: we flag the
subclass, Designite the supertype). DIP 0.389 by compilation unit, 0.080 by package.
**OCP vs Designite measured 2026-09-26** (`05_crosscheck/compare_ocp_designite.py`, 138 repos, 63,326
types): kappa **0.122**, ours 886 flags (1.40%) against Designite 299 (0.47%), 76 shared. So OCP has
no external corroboration and rests on the judge. Do not write that no tool has an OCP check, and do
not write that the comparison was never run.

### Weaknesses that stay stated

* Agreement is detector vs LLM reading judge. LLM judging of SOLID is a validated technique
  (Pehlivan et al. 2025). **The author and supervisor ruled human labelling out of scope; do not
  reintroduce it as a limitation.**
* Judge and one agent are both Claude. Per-agent precision shows no leniency (DIP is the exception,
  0.792 vs 0.959). Judge self-consistency measured in Java only (89.7% on 174 entities).
* Source-only analysis: Java median supertype resolution 87.0%; Python resolves in-repo bases only.
* 7.0% of Python PRs lost their source, unevenly (rejected 11.0%, Copilot 14.3%); not topped up.
* SOLID adherence is not design quality; nothing observes an outcome.

---

## House rules (author's, non-negotiable)

* **Never write an em dash.** Not `---`, not the character. Use a comma, or a colon. En dashes in
  numeric ranges (`0.330--0.890`) are correct. **Check `figures/` too.**
* **Report all FIVE principles in every table** (say why when one cannot be computed).
* Lead with macro-per-repo, print counts beside rates.
* Every caveat goes in ONE limitations section after the conclusion.
* **No citation or number enters the thesis without being checked** against its arXiv/publisher page
  or the data. No invented values anywhere, including cover fields: leave a blank line instead.
* **Plain academic prose.** No AI-writing tells: no "not X but Y" or "rather than" chains, no
  "precisely", "honest", "is itself a finding", no aphorism-style closing sentences, bold only for
  table headers and defined terms. The 2026-09-15 rewrite cut "rather than" from 137 uses to 6.
* Figures: named nodes, no overlaps, render and look at every figure before accepting it.
* Do not stop for permission on routine steps.
* Tone: lead with the result. Bugs fixed are not results. The author is not a native English speaker
  and is under exam stress. Be concrete, never patronise.
* Never mention TypeScript or the V1 Python study in the thesis or deck.
* Commit only when asked; if on main, branch first.

## Setup

No `requirements.txt`. Needs `pandas`, `pyarrow`, `statsmodels`, plus `git` and a JDK on PATH.
No test framework: pure modules self-check when run as scripts.

Stage scripts do `sys.path.insert(0, <vibebench dir>); import config as C`. Run from the repo root
as `python vibebench/<stage>/<script>.py`, never with `-m`.

Thesis and deck: MiKTeX 25.12, XeTeX 4.16, `latexmk` 4.88 (Perl 5.42), BibTeX (natbib). Not Overleaf.

## Java run order

```
python vibebench/01_curate/curate.py --lang java
python vibebench/02_clone/clone_and_extract.py --lang java --workers 8
mvn -o package -f vibebench/03_principles/java_sidecar/pom.xml
python vibebench/03_principles/java_lang/detectors.py      # self-check
python vibebench/03_principles/java_lang/run_java.py --workers 6
#   thresholds calibrated from printed percentiles, then the parquets are
#   DELETED and the pass re-run, so no flag predates its threshold
python vibebench/04_report/report_java.py
python vibebench/06_model/fit_mixed_model.py
python vibebench/labels/make_java_labeling_sample.py --principle X --n-per-stratum 100 --seed N
python vibebench/labels/extract_java_label_batches.py --principle X --file <sample.csv> --only-missing
python vibebench/07_verify/audit_label_batches.py          # GATE, must be 0 FAIL before judging
python vibebench/labels/score_labels.py --file <sample.csv> --pred-col <col> --label-col llm_X_risk
python vibebench/labels/score_java_labels.py               # THE source for Java precision/recall
python vibebench/05_crosscheck/run_crosscheck_java.py --sample 150 --workers 6
python vibebench/05_crosscheck/compare_ocp_designite.py    # OCP vs Designite, needs the tree cache
python vibebench/07_verify/verify_v2_java.py
python vibebench/07_verify/verify_thesis_numbers.py        # thesis text vs the data
```

## Python run order

```
python vibebench/01_curate/curate.py --lang py
python vibebench/02_clone/clone_and_extract.py --lang py
python vibebench/03_principles/py_lang/run_python.py --calibrate
python vibebench/03_principles/py_lang/propose_py_thresholds.py   # then DELETE calibration parquets
bash vibebench/run_py2_finish.sh          # repository-disjoint shards, merge, report, model
python vibebench/labels/make_py2_labeling_sample.py --principle X
python vibebench/labels/extract_py2_label_batches.py --principle X
python vibebench/07_verify/audit_label_batches.py --lang py2      # GATE
python vibebench/labels/score_py2_labels.py --principle X
python vibebench/04_report/py2_cross_language.py
```

`run_python.py` threads are GIL-bound (measured 1.04 of 12 cores), which is why the finish chain
runs separate shard processes. Per-shard parquets and pilots are gitignored; the merged
`*_results.parquet` files are canonical.

## The validation guards

* **`labels/record_labels.py`** is the ONLY way to write a label. Refuses a row with no source on
  disk and a label with no reason; writes atomically. Round-aware (`use_round`), language-aware
  (`use_lang('py2')`). **It defaults to `java`.**
* **`07_verify/audit_label_batches.py`** mechanical checks, exit 1 on any FAIL. Takes `--round` and
  `--lang`. Run it as a gate between extraction and judging.
* Extractors write `_manifest.json` LAST, only on a clean exit.

## File cheat sheet

* `03_principles/java_sidecar/`, JavaParser+SymbolSolver 3.25.10. `Facts.sourceRoots()` infers
  roots from `package` lines (repo-root rooting drops resolution from 93% to 17%).
* `03_principles/java_lang/detectors.py`, all five Java principles. LSP has THREE signals.
  SRP rubric is SIX metrics (loc 362, wmc 45, methods 20, ccn 11, params 5, LCOM 0.86), >= 2 hits,
  C3 >= 0.4 suppresses only when hits < 3.
* `03_principles/py_lang/run_python.py`, one delta pass for Python, reusing `srp/metrics.py`,
  `ocp/dispatch.py`, `lsp/overrides.py` (five signals), `isp/interfaces.py`, `dip/graph.py`.
* `03_principles/gitsnap.py`, four-tier before/after recovery. **Tier 1 is merge-commit only**, so
  outcome contrasts must be tier-matched.
* `05_crosscheck/run_crosscheck_java.py`, `--sample N` is the seeded random draw; DesigniteJava
  heap 5g, concurrency 1 by default. No resume.
* `05_crosscheck/compare_ocp_designite.py`, re-reads each head tree with
  `gitsnap.src_tree(gitsnap.repo_dir(full), sha, ...)` and joins on `type_id` against
  `04_report/java_crosscheck_classes.csv`. ~8 min for 138 repos. Writes
  `04_report/ocp_designite_comparison.json`, which the verifier reads.
* `labels/score_java_labels.py` and `labels/score_py2_labels.py`, the ONLY sources for precision and
  weighted recall. Both weight by the sampler's population, not raw rows.
* `logs_archive/py2_model.txt`, `py2_report.txt`, `py2_cross_language.txt` are tracked; the
  verifier parses the model log.

## Traps this project has paid for

* **Bash heredocs AND `python -c` mangle backslashes** (`\r`, `\t`, `\\t` all corrupted LaTeX on
  2026-09-15, twice). Edit LaTeX only with the Edit or Write tools.
* **A number in the thesis is not evidence.** The Java present counts and the "forty to one" ratio
  were copied between documents for weeks; no script produced them. Recompute or delete.
* **arXiv papers get revised.** SlopCodeBench's v2 changed every figure (36 problems, 196
  checkpoints, 15 agents, 14.8%, 2.3x). Quote the version current when you checked.
* `ps -W` reports ZERO python processes while several run; `pgrep` does not exist. Use PowerShell
  `Get-CimInstance Win32_Process`.
* Killing a python parent does NOT kill its java sidecar or git children. Kill both.
* **`cd` in a Bash tool call PERSISTS** into later calls.
* When rewriting LaTeX programmatically, never capitalise "the first word on a line".
* **Beamer + enumitem**: do NOT load `enumitem` in the deck.
* In figures at 12pt, `\tiny` is 6pt and wraps sooner than expected; give nodes fixed widths and
  render at 150 dpi to check.
* RAM is the ceiling, not CPU. 15.4 GB total.
* **A partial result that looks complete is THE recurring bug here.**

## Dataset trap

In AIDev releases, `pull_request.parquet` / `repository.parquet` are only the >100-star subset. **The
master tables are `all_pull_request.parquet` / `all_repository.parquet`** (AIDev-7.6M: 7,685,281 PRs,
957,209 repos). Per-file attribution is missing for some agent in every release, and which agent
changes; never branch on an agent name.

## Superseded, kept only as record

* **V1** (Python 546 PRs / 147 repos; Java 867 PRs / 27 repos), every number superseded. `archive/`.
* **TypeScript**, cancelled by the author. Code stays in the tree, is not run. Never mention it.
* **Machine-learning precision experiments**, all failed their gate. Do not re-propose.
* **Validation round 1**, `vibebench/labels/VOID_ROUND1_2026-09-05/`.
* Python V2 run log and prompts: `archive/RESUME_HERE_python_v2_log.md`, `archive/PROMPT_python_v2.txt`.
