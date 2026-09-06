# CLAUDE.md — session orientation

**Rule #1. THE CODE AND THE DATA ARE THE SOURCE OF TRUTH. This file is a map, and maps go stale.**

Use it to find things quickly, then verify anything you are about to depend on against the file or
the parquet itself. Any number quoted here without a measurement date should be re-measured before
it is used. This rule has earned its place twice: an earlier version of this document was wrong on
eight separate claims, and the correction table that replaced them was itself wrong within three
weeks. `python vibebench/07_verify/verify_thesis_numbers.py` exists because of it.

---

## STATE: the Java experiment is FINISHED. The thesis is written. (2026-09-06)

| | |
|---|---|
| Corpus | **6,956 curated → 4,245 analysable** pull requests over **1,010 repositories** |
| Agents | Claude_Code, Copilot, OpenAI_Codex |
| Outcomes | merged, rejected, open, balanced on agent × outcome |
| Language | Java only. Python V2 is possible and not started. |
| Validation | **1,000 rows judged**, 200 per principle (100 flagged + 100 unflagged), seed 777 |
| Cross-check | CK + PMD + DesigniteJava over 150 sampled repositories, 111,029 shared types |
| Thesis | 85 pages, compiles under **XeLaTeX** (not pdflatex, fontspec), 0 undefined refs |
| GitHub | **https://github.com/KiraiEEE/VibeBench** — PRIVATE. Code AND thesis are pushed. |

`REDESIGN_V2.md` is now a **historical record of how V2 was planned**, not an authority. Where it
disagrees with this file or with the data, it is wrong. `archive/PROJECT.md` and
`archive/RERUN_LOG.md` describe V1 and are superseded.

### Results

| principle | flag rate | validated P | validated R |
|---|---|---|---|
| SRP | 7.7% of authored classes (5.6% macro) | 0.350 | 0.972 |
| OCP | 6.7% of PRs | 0.330 | 0.943 |
| ISP | 6.5% of PRs | 0.320 | 0.914 |
| DIP | 4.6% of PRs | **0.890** | 1.000 |
| LSP | 0.4% of PRs | 0.400 | 1.000 |

**Two refinements, both derived from the labels they are scored on, so both IN-SAMPLE:**
LSP restricted to `refused_bequest` → P=**1.000** (31/31), R=0.775. ISP with `members>=30` →
P=**0.862**, R=0.714.

**Per agent** (pooled, macro/repo in brackets):

| principle | Claude_Code | Copilot | OpenAI_Codex |
|---|---|---|---|
| SRP authored | 9.5 (8.4) | 5.1 (3.5) | 6.8 (7.4) |
| OCP | 10.6 (10.1) | 6.4 (3.0) | 2.1 (2.2) |
| ISP | 8.7 (6.0) | 6.7 (4.0) | 2.9 (5.0) |
| DIP | 6.5 (5.1) | 4.1 (2.8) | 3.2 (5.4) |
| LSP | 0.7 (0.3) | 0.3 (0.1) | 0.0 (0.0) |

**The ordering is conditional on change size.** Mean Java files touched: Claude_Code 46.4, Copilot
18.5, Codex 10.9. Within size bands the pooled ordering holds only for the largest (11+ files);
in the smallest band Copilot leads on OCP and ISP and Codex leads on DIP. Never quote the pooled
per-agent table without this.

**Merged vs rejected, tier-matched (tiers 2+3+4):** OCP 8.3/8.0, ISP 7.5/6.6, DIP 4.6/6.6,
LSP 0.5/0.5. No consistent difference. Review does not select on SOLID adherence.

**Locus of risk:** ~4,100 genuine SRP violations and 270 LSP violations are *present* in touched
code; PRs *introduced* a violation in 2-4% of cases. 681 refused-bequest relationships exist, 16
were introduced.

**Cross-check (2026-09-06, 150 repos, production types):**
ours~PMD κ=**0.574**, ours~Designite κ=**0.531**, PMD~Designite κ=**0.384**. 68.0% of our flags
corroborated by ≥1 independent tool. Spearman vs CK: wmc 0.977, methods 0.969, loc 0.944; cohesion
weak (0.288) but CK's own variants agree only 0.490 with each other. LSP κ=0.000 because Designite
charges the supertype and we charge the subclass. DIP κ=0.389 at file level, 0.080 at package level.
*(The older figures 0.625/0.539/0.430 were V1, 27 repos. Do not quote them.)*

### Known weaknesses, to keep stating

* Labels are model-produced, not human ground truth. Judge and one agent are both Claude; the
  cross-check is the defence, and it covers SRP only, since no published source-only Java tool has
  an OCP or ISP rule.
* **No holdout.** Both refinements are in-sample. ISP's width threshold has no independent support;
  LSP's has partial support from an earlier round on a different sample (51/62).
* Agent and era confounded: median PR dates seven months apart.
* "Rejected" is mostly `closed_unreviewed` (superseded/stale), not refusal on quality.
* Codex's validation slice is 15-28 rows per principle.

---

## What to do next

1. **Targeted holdout, ISP and LSP only.** ~400 rows, ~2h. The other three detectors had their
   thresholds set from corpus percentiles *before* labelling, so there is nothing fitted to
   re-test. 310 fresh LSP and 538 fresh ISP flagged rows are available.
2. **Python V2**, if wanted. The framework is language-parameterised: only the fact-extraction
   layer is language-specific, so it is a new extractor rather than a second study.
3. `java_crosscheck_classes.csv` is 63 MB and regenerable. Consider untracking it.

---

## House rules (author's, non-negotiable)

* **Never write an em dash.** Use a comma. This applies to the thesis too; a pass took it from 212
  to 21.
* **Report all FIVE principles in every table.** Leaving SRP out was a repeated complaint.
* Lead with macro-per-repo, print counts beside rates.
* Every caveat goes in ONE limitations section after the conclusion, never scattered through the
  body.
* No citation enters the thesis without its arXiv or publisher page being read first: author list,
  year, and the specific claim. This caught two errors in a user-supplied summary on 2026-09-06.
* Do not stop for permission on routine steps.
* Tone: lead with the result, not the journey. Bugs fixed are not results. The author is not a
  native English speaker and is under exam stress; be concrete and never patronise.

---

## Setup

No `requirements.txt`. The pipeline needs `pandas`, `pyarrow`, plus `git` and a JDK on PATH.
`pip install pandas pyarrow`. No test framework: pure modules self-check when run as scripts.

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
#   thresholds are calibrated from printed percentiles, then the parquets are
#   DELETED and the pass re-run, so no flag predates its threshold
python vibebench/04_report/report_java.py
python vibebench/labels/make_java_labeling_sample.py --principle X --n-per-stratum 100 --seed N
python vibebench/labels/extract_java_label_batches.py --principle X --file java_X_labeling_sample.csv --only-missing
python vibebench/07_verify/audit_label_batches.py          # GATE, must be 0 FAIL before judging
python vibebench/labels/score_labels.py --file java_X_labeling_sample.csv --pred-col <col> --label-col llm_X_risk
python vibebench/05_crosscheck/run_crosscheck_java.py --sample 150 --workers 6
python vibebench/07_verify/verify_v2_java.py               # whole-corpus invariants
python vibebench/07_verify/verify_thesis_numbers.py        # thesis text vs the data
```

**A threshold change requires DELETING the parquets.** Resume keeps permanent-status rows, so a
re-run alone will not recompute old flags.

## The validation guards (these are why round 2 is trustworthy)

Round 1 was voided on 2026-09-05: 294 of 400 ISP labels had been written for rows whose batch file
did not exist, and 200 more at 3 seconds a row. The `notes` column was empty for all 1,995 rows, so
none of it was visible on disk. Three guards now make that impossible rather than merely discouraged.

* **`vibebench/labels/record_labels.py`** — the ONLY sanctioned way to write a label. Refuses a row
  with no source on disk, refuses a label with no written reason, writes atomically per call.
* **`vibebench/07_verify/audit_label_batches.py`** — six mechanical checks per principle, exit 1 on
  any FAIL. Run it as a gate between extraction and judging.
* **`extract_java_label_batches.py`** — writes `_manifest.json` LAST and only on a clean exit, so a
  killed run cannot pass for a finished one; names a source-less batch `UNJUDGEABLE_*`.

## Java file cheat sheet

* `03_principles/java_sidecar/` — Maven, JavaParser+SymbolSolver 3.25.10. `Facts.java` is the single
  fact source for all five principles. Self-check: `java -cp target/...jar vb.Facts --self-check`.
  **`Facts.sourceRoots()` infers roots from the files' own `package` lines.** Rooting the solver at
  the repo root instead drops resolution from 93% to 17% on a real project.
* `03_principles/java_bridge.py` — `analyze_tree(files) -> dict`, one JVM per tree, gzipped cache
  keyed by tree CONTENT hash. `VB_SIDECAR_TIMEOUT_S` env override.
* `03_principles/java_lang/detectors.py` — all five principles as pure functions over facts.
* `03_principles/java_lang/run_java.py` — ONE delta pass for all five (a tree costs 10-20s to parse).
* `03_principles/gitsnap.py` — four-tier before/after recovery. **Tier 1 is merge-commit only, which
  is why the outcome contrast must be tier-matched.**
* `05_crosscheck/run_crosscheck_java.py` — `--sample N` is the SEEDED RANDOM draw; `--limit N` takes
  the alphabetical head and is biased. **`DESIGNITE_CONCURRENCY` defaults to 1 and every repo needs
  Designite, so `--workers` alone does nothing.** Use `VB_DESIGNITE_CONCURRENCY=2 VB_DESIGNITE_HEAP=4g`.
  No resume: it writes its CSV only at the end.

## Traps this project has paid for

* `ps -W` reports ZERO python processes while several run. Use PowerShell `Get-Process`, and
  `Get-CimInstance Win32_Process` for command lines.
* Killing a background bash script does NOT kill its child python, and the script advances to the
  next stage.
* **`cd` in a Bash tool call PERSISTS** into later calls and breaks relative paths. It has twice
  looked exactly like data loss.
* **Bash heredocs mangle backslashes.** Write patch scripts to a file with the Write tool and run
  them; `python - <<'PY'` corrupts LaTeX and regex.
* When rewriting LaTeX programmatically, never capitalise "the first word on a line". Source wraps
  mid-sentence, so it capitalises the whole document.
* RAM is the ceiling, not CPU. 15.4 GB total; five to six workers.
* **A partial result that looks complete is THE recurring bug here**: an empty diff, an empty file
  list, a truncated API page, a partial batch directory, a status counter reporting 200/200 notes
  on a file with none. Every one cost real data.

## Dataset trap

In `AIDev`, `AIDev-2.7M` and `AIDev-7.6M`, `pull_request.parquet` / `repository.parquet` are ONLY
the >100-star curated subset. **The master tables are `all_pull_request.parquet` /
`all_repository.parquet`.** Reading the former while believing it is the latter under-counts by an
order of magnitude.

Per-file attribution is missing for *some* agent in every release, and **which** agent changes
between releases. Never branch on an agent name; recover missing file lists from the GitHub API for
every agent alike.

## Superseded, kept only as record

* **V1** (Python 546 PRs / 147 repos; Java 867 PRs / 27 repos, Codex only) — every number
  superseded. `archive/`.
* **TypeScript** — cancelled by the author. A class appears in only 27% of its PRs, so the
  class-scoped proxies have no surface. Code stays in the tree, is not run, do not delete it.
* **Machine-learning precision experiments** (GraphCodeBERT embeddings, trained SRP classifier) —
  run in V1, all failed their gate, removed from the thesis. Do not re-propose without new evidence.
* **Validation round 1** — archived at `vibebench/labels/VOID_ROUND1_2026-09-05/`, not deleted.
