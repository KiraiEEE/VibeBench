# RESUME HERE — VibeBench, state as of 2026-09-06

**Read this first if context was lost.** Verified against the files on disk.

## THE EXPERIMENT IS FINISHED. The thesis is rewritten. One job is running.

### What is done

**Corpus and analysis (V2, Java).** 6,956 curated pull requests over 1,010
repositories, three agents (Claude Code, Copilot, OpenAI Codex) balanced on
agent x outcome (merged / rejected / open). **4,245 analysable** after
before/after tree reconstruction. Committed, verified, `verify_v2_java.py`
exits 0.

**Validation round 2, complete.** 1,000 rows judged, 200 per principle
(100 flagged + 100 unflagged), fresh seed 777. Every row carries a written
reason in `notes`. `audit_label_batches.py` reports 0 FAIL, 0 warn.

| principle | n | TP | FP | FN | precision | recall |
|---|---|---|---|---|---|---|
| DIP | 200 | 89 | 11 | 0 | **0.890** | 1.000 |
| LSP | 200 | 40 | 60 | 0 | 0.400 | 1.000 |
| SRP | 200 | 35 | 65 | 1 | 0.350 | 0.972 |
| OCP | 200 | 33 | 67 | 2 | 0.330 | 0.943 |
| ISP | 200 | 32 | 68 | 3 | 0.320 | 0.914 |

Two refinements earned from the labels, both **in-sample** and stated as such:
* LSP restricted to `refused_bequest`: **31/31, P=1.000** [0.890-1.000], R=0.775.
  Confirmed by round 1 on a different sample (51/62). The other two signals stay
  weak in both rounds: `postcondition_weakened` 6/36, `precondition_strengthened` 3/33.
* ISP with `members >= 30`: P 0.320 -> **0.862** [0.694-0.945], R=0.714.

Intra-rater agreement, computed only after round 2 was locked: **89.7%**
(156/174 overlapping rows).

**Thesis rewritten (commit `c46ec96`).** All V1 content deleted: the Python
corpus, the single-agent Java arm, the machine-learning half of RQ3. Every
chapter, every figure and the appendix now describe one study. Caveats live in
ONE section after the conclusion (`sec:limitations`). Compiles under **XeLaTeX**
(not pdflatex, fontspec), **77 pages, 0 undefined references**.

Prose pass done: em dashes 212 -> 21, filler adverbs -> 0.

### The one job still running

```
VB_DESIGNITE_CONCURRENCY=2 VB_DESIGNITE_HEAP=4g \
python vibebench/05_crosscheck/run_crosscheck_java.py --sample 150 --workers 6 \
  > <scratch>/xcheck150.log 2>&1
```

CK 0.7.0 + PMD 7.7.0 + DesigniteJava 1.1.2 over the same head-state trees, on a
**seeded random sample of 150 repositories** (`--sample`, seed 42). This is RQ2's
second validation axis: tools built by other people cannot be wrong in this
study's direction.

**Bottleneck, if it needs restarting:** `DESIGNITE_CONCURRENCY` defaults to **1**,
and every repository needs Designite, so more `--workers` does nothing on its
own. The env vars above raise it to 2 at a 4g heap. `--limit` takes the
ALPHABETICAL head and is a biased draw; `--sample` is the seeded random one and
was added for this run.

`--rebuild` only works after a full pass completes, because it reads
`java_crosscheck_classes.csv`, which is written at the end. A killed run leaves
nothing to salvage.

### What to do when it finishes

1. Read `vibebench/04_report/java_crosscheck_summary.json`.
2. Fill the numbers into Chapter 4 `sec:validation-independent` (the section
   currently describes the method and reports no figures) and into Chapter 5's
   limitations paragraph on judge independence.
3. State in the thesis that the cross-check runs on a **seeded random subset of
   150 repositories**, not the full corpus.
4. Recompile with `xelatex` twice, confirm 0 undefined references, commit.

**Report honestly whatever it says.** If our detector sits outside the spread
the three published tools show among themselves, that is the finding. In V1 this
cross-check caught a real LCOM bug, so a surprise is plausible.

## Headline results (for the write-up)

Flag rates by agent, pooled with macro-per-repo in brackets:

| principle | Claude Code | Copilot | OpenAI Codex |
|---|---|---|---|
| SRP (authored classes) | 9.5% (8.4%) | **5.1%** (3.5%) | 6.8% (7.4%) |
| OCP | 10.6% (10.1%) | 6.4% (3.0%) | **2.1%** (2.2%) |
| ISP | 8.7% (6.0%) | 6.7% (4.0%) | **2.9%** (5.0%) |
| DIP | 6.5% (5.1%) | 4.1% (2.8%) | **3.2%** (5.4%) |
| LSP | 0.7% (0.3%) | 0.3% (0.1%) | **0.0%** (0.0%) |

**Merged vs rejected, tier-matched (tiers 2+3+4 only):** OCP 8.3 / 8.0, ISP
7.5 / 6.6, DIP 4.6 / 6.6, LSP 0.5 / 0.5. No consistent difference. Review does
not appear to select on SOLID adherence.

**Locus of risk:** roughly 4,100 genuine SRP violations and 270 LSP violations
are *present* in the touched code; pull requests *introduced* a violation in
2-4% of cases. 681 refused-bequest relationships exist, 16 were introduced.

**Authored vs edited (SRP):** 7.7% vs 13.5% pooled, 5.6% vs 20.9% macro. Agent
code is flagged at about half the rate of the code it edits.

**Cross-principle result:** interface width predicts ISP violations sharply
(0.071 below 30 members, 0.900 above 50) and branch width predicts OCP not at
all (flat 0.27-0.40). Width IS the ISP concept; it is not the OCP concept.

## Known weaknesses to keep stating

* Labels are model-produced, not human ground truth. The judge and one
  benchmarked agent are both Claude; the cross-check is the intended defence.
* No holdout. Both refinements are in-sample.
* Agent and era confounded: median PR dates seven months apart.
* "Rejected" is mostly `closed_unreviewed` (superseded/stale), not refusal.
* Codex's validation slice is 15-28 rows per principle.

## House rules

* Never write an em dash; use a comma. This applies to the thesis too.
* Report all FIVE principles in every table.
* Lead with macro-per-repo, print counts beside rates.
* Verify against code and data before stating anything.
* Do not stop for permission on routine steps.

## Traps this project has paid for

* `ps -W` reports zero python processes while several run. Use PowerShell
  `Get-Process`, and `Get-CimInstance Win32_Process` for command lines.
* Killing a background bash script does NOT kill its child python, and the
  script advances to the next stage.
* `cd` in a Bash tool call PERSISTS into later calls and breaks relative paths.
  It has twice looked exactly like data loss.
* **Bash heredocs mangle backslashes.** Write patch scripts to a file with the
  Write tool and run them; `python - <<'PY'` corrupts LaTeX and regex.
* A partial result that looks complete is THE recurring bug here: an empty diff,
  an empty file list, a truncated API page, a partial batch directory, a status
  counter reporting 200/200 on a file with none.
* When rewriting LaTeX programmatically, never capitalise "the first word on a
  line". Source wraps mid-sentence; it capitalises the whole document.
