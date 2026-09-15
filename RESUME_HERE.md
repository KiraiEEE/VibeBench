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
PHASE 2 clone + extract ....... DONE   coverage 1.000000
PHASE 3 calibration pass ...... DONE   7,050/7,050, flags null
PHASE 4 thresholds ............ DONE   applied to config.py, re-import verified
PHASE 3' flagged pass ......... DONE   6 shards, merged, 4,946 analysable PRs
PHASE 5 report + joint model .. DONE   logs_archive/py2_report.txt, py2_model.txt
PHASE 6/7 labelling ........... DONE   980 rows judged, every audit gate 0 FAIL
NEXT: write-up per PROMPT.txt section 2 (title, abstract, RQs, Chapter 5)
```

## VALIDATED (2026-09-14)

All five principles judged under `labels/PY2_JUDGING_RUBRIC.md`, recorded only
through `py2_judge_io.py record` (which calls `record_labels` with
`use_lang('py2')`), audited with `--lang py2`, scored with
`labels/score_py2_labels.py`. Findings, clustering and the drift inspections are
in `labels/py2_validation_notes.md`; every borderline decision is a numbered
precedent in `labels/py2_precedents.md` (L1-L28, O1-O11, S1-S7, I1-I9, D1-D3).

```
principle  rows  precision                 weighted recall          Java prec / recall
SRP        200   0.110  [0.063, 0.186]     1.000 (0/100 FN)         0.350 / 0.875
OCP        200   0.390  [0.300, 0.488]     0.364 [0.257, 0.523]     0.330 / 0.733
LSP        200   0.510  [0.413, 0.606]     0.545 [0.315, 1.000]     0.420 / 0.352
ISP        180   0.425  EXACT, census 80   1.000 (0/100 FN)         0.335 / 0.552
DIP        200   1.000  [0.963, 1.000]     1.000 (0/100 FN)         0.892 / 1.000
```

What must travel with the numbers:
* **LSP** outcome spread is two PRs (transformers#44781 30 rows, ToolUniverse#154
  20 rows); without them precision is 0.740.
* **ISP** open-outcome 0.722 is one PR (pylabrobot#976, 8 of 8). The drift check
  flagged ratio 1.50; inspected, no rule applied differently, labels unchanged.
* **OCP** merged precision 0.239 against 0.50-0.56 is NOT clustering (90 PRs).
  Any OCP outcome contrast must carry it.
* **SRP** precision is a third of Java's: in Python, size and complexity flag
  framework-shaped and single-service classes. Diagnostic cuts (strict 0.133,
  5 hits 0.333) were fitted on the sample and are NOT adopted.
* **DIP** 1.000 confirms a real new import cycle through changed code; it does
  not establish a design defect (dominant shape: a leaf importing its package
  root). Word it as "introduced an import cycle".
* 41 of 11,529 flagged SRP classes lie in committed virtualenvs, 469 of the 489
  such rows from one PR; disclosed, not re-run.

### The four PROMPT.txt section 2 questions, answered (2026-09-14)

`python vibebench/04_report/py2_cross_language.py` -> `logs_archive/py2_cross_language.txt`

```
1. Change size dominates?     YES. OR per doubling 2.01 to 2.18, all five, p<1e-4 (Java 1.9)
2. OCP agent effect repeats?  YES. Claude_Code 2.09 [1.44, 3.04] p=0.0001 (Java 2.22 [1.21, 4.07])
                              OCP precision is HIGHEST for Claude_Code (0.444 vs 0.368/0.333),
                              so precision correction strengthens it rather than explaining it.
                              DIP also survives (1.88 [1.17, 3.03] p=0.010; Java did not),
                              read as "introduces import cycles", precision 1.000 for every agent.
3. Present, not introduced?   YES. refused bequest 194 present / 5 PRs introduced (Java 681 / 16).
                              Corrected PRs introducing: OCP 2.5%, DIP 3.5%, SRP 1.0%, LSP 0.5%,
                              ISP 0.3% (Java OCP 2.2, DIP 4.1, ISP 2.1, LSP 0.4).
4. Categorical beats numeric? YES, on an out-of-language holdout (rules fixed on Java first):
                              LSP refused_bequest 6/7 = 0.857 (Java 1.000);
                              ISP >= 30 abstract members 1/5 = 0.200 (Java 0.862 -> 0.704).
                              Small n on both; Java counted all members, Python abstract ones.
```

The model log order is SRP, OCP, ISP, DIP, LSP. The first run of the script assumed
another order and printed LSP's odds ratios under ISP; it now reads the headers.

Three extraction defects were found and fixed during judging, none changing a
label: an OCP module-name collision (row 114 re-extracted), a quadratic set
rebuild in the DIP renderer (30-minute hang), and an audit leak check that
matched a repository variable named `hits`.

## RESULTS (validated above; model and rates unchanged by labelling)

Read `logs_archive/py2_report.txt` and `logs_archive/py2_model.txt`.

Analysable: **4,946 PRs over 2,921 repositories** (Claude_Code 1,662, Copilot
1,470, OpenAI_Codex 1,814). Thresholds in force, all ~p90 of this corpus:
SRP loc 250 / wmc 36 / methods 10 / ccn 12 / params 7, OCP value switch 5, ISP
fat interface 7.

```
PR-level flag rate      Python V2        Java
SRP (prod. classes)     11.6%            7.7%
OCP                     6.4%             6.7%
DIP                     3.5%             4.6%
LSP                     1.0%             0.4%
ISP                     0.6%             6.5%
```

The two cross-language questions PROMPT.txt section 2 names, on UNVALIDATED flags:
* **Change size still dominates**: OR per doubling of files touched 2.01 (OCP),
  2.04 (DIP), 2.17 (ISP), 2.18 (LSP), 2.06 (SRP), all p < 1e-4. Java: 1.9.
* **The OCP agent effect reproduces**: Claude_Code OR 2.09 [1.44, 3.04],
  p = 0.0001. Java: 2.22 [1.21, 4.07]. DIP ALSO survives adjustment in Python
  (Claude_Code 1.88 [1.17, 3.03], p = 0.010), which it did NOT in Java (1.29,
  interval straddling 1). ISP and LSP do not survive; they have 32 and 47
  flagged PRs, so their intervals are wide.
* Era is inert for the four delta principles, but NOT for PR-level SRP (OR
  1.049 per month, p < 1e-4). Java had no PR-level SRP model to compare.

Things to examine before any of this is written up:
* **ISP is 0.6% against Java's 6.5%.** Plausibly real (Python declares far fewer
  explicit interfaces: 4,067 contracts in scope over 4,946 PRs, Java 20,705), but
  it must be checked in labelling, not assumed.
* **The outcome strata are NOT tier-matched.** Merged PRs are reconstructed by
  tier 1 (merge commit and first parent) far more often, and OCP merged 4.2%
  against 9.1% to 10.8% elsewhere may partly be that. `report_java.py` has the
  tier-matched and within-repository contrasts; `report_python.py` does not yet.
* SRP macro-per-repo (~21%) is about twice the pooled rate (~12%): small
  repositories with one flagged class carry high per-repo rates. Worth stating.

### The last 18 exclusions, and why the run was redone for them

The six-shard pass first left 18 `archive_failed` PRs and 9 `repo_too_large`,
against 12 and 14 in the single-process calibration; a deterministic size check
does not move, so it was investigated rather than accepted. Three causes:

1. **Git LFS**: `git archive` launched `git-lfs filter-process`, downloading LFS
   objects over the network into the tar and hitting the 300 s timeout
   (onnxscript, splunk/attack_data). No LFS pattern in any affected repository
   matches `.py`, so `GIT_LFS_SKIP_SMUDGE=1` cannot change analysed code;
   onnxscript went from timing out to 352 files in 1 s.
2. **Load**: giant repositories timed out while six shards competed (ray archives
   in 6 s idle).
3. **Genuine**: one 4.86 GB data tree (nbn-upgrade-map), refused rather than
   loaded, since ~10 GB in memory would starve other programs on a machine that
   does not overcommit; and one commit whose tree object is missing locally
   (maptoposter). These 2 remain `archive_failed`.

`py_lang/rerun_shard_safely.py` applies all three remedies at runtime inside its
own process only (shared gitsnap and config, used by Java, are untouched), and
`vibebench/rerun_contended_shards.sh` reran the affected shards one at a time.
Result: `archive_failed` 18 -> 2, `repo_too_large` 9 -> 14 (exactly the
calibration value), 11 PRs recovered, **and all six flags identical on the 4,935
PRs analysable before and after** (checked PR by PR). None of the 11 carries an
OCP, LSP, ISP or DIP flag.

A resumed shard can store a flag column as 0.0/1.0 instead of bool (pandas
upcasts when concatenating with an all-NaN column); it crashed the re-merge.
`run_python.normalize_flags` now writes every flag as a nullable boolean in both
the shard writer and the merge, and REFUSES any value other than
true/false/0/1/null. Verified on the affected shard: 2,576 of 2,576 comparable
rows had identical truth values; only the type had changed.

### Phase 6/7 commands (DONE 2026-09-14, kept for reference)

```
python vibebench/labels/make_labeling_sample.py --lang py2 ...   # SRP; the other samplers likewise
python vibebench/07_verify/audit_label_batches.py --principle X --round "" --lang py2   # GATE
# record labels ONLY through record_labels.py after R.use_lang('py2')
```
100 flagged + 100 unflagged per principle. Recall is ALWAYS stratum-weighted.
Without `--lang py2` the samplers now REFUSE to overwrite the 240 judged V1 SRP
labels instead of destroying them.

**2026-09-14: the tail of the pipeline moved to `vibebench/run_py2_finish.sh`.**
`run_py2_chain.sh` still describes phase 2 and the calibration pass, but its
flagged pass would have run as ONE process, and that process was measured at
**1.04 of 12 CPU cores busy, machine 82% idle**. Its workers are threads and the
parsers are pure Python, so the interpreter lock serialises them; raising
`--workers` changed nothing. The finish chain runs the flagged pass as N
separate, REPOSITORY-DISJOINT processes instead.

```
bash vibebench/run_py2_finish.sh                  # logs_archive/py2_finish.log
python vibebench/07_verify/py2_health.py --sample 30
cat vibebench/corpus/py2_stage.txt                # where it is
```

Stages, each recorded ONLY after its gate passes, so a restart resumes at the
first unfinished one (restarting from the top after the calibration parquets
were deleted would find no calibration and stop):

```
start               verify calibration covers all 7,050 PRs with null flags,
                    apply thresholds (re-import validated), DELETE parquets
calibrated_applied  sweep git locks ONCE, run N shards (--shard i/N), retry any
                    shard without a completion marker, up to 3 attempts
shards_done         merge_py_shards.py, with full verification
merged              report_python.py  -> logs_archive/py2_report.txt
reported            fit_mixed_model.py --lang py -> logs_archive/py2_model.txt
finished            stop before labelling
```

Why sharding is safe, all of it tested offline before first use:
* Shards own WHOLE repositories (greedy by PR count; 5 shards gave exactly
  1,410 PRs each). A git directory never sees two processes, gitsnap's tree cache
  never sees two writers on one key, and every shard writes its own
  `*.shard{i}of{N}.parquet`.
* A shard never sweeps git locks; the finish chain sweeps once, with no git
  process alive. A shard sweeping would delete another shard's LIVE lock.
* Each shard writes `py_pr_results.shard{i}of{N}.DONE.json` LAST, only when every
  PR in the shard has exactly one row, recording the thresholds it ran under.
* The merge refuses (and writes nothing) on a missing marker, shards that ran
  under different thresholds, a repository in two shards, a duplicated PR, a PR
  set that is not exactly the gold set, orphan entity rows, a schema break, or a
  canonical parquet that already exists. Each refusal was exercised in a test.
* The shard count is written to `py2_nshards.txt` on first launch and reused on
  every resume, so a resume under different free memory cannot reassign
  repositories and silently redo finished shards.

**PAUSE ORDER CHANGED: finish chain, then EVERY run_python.py, then git.**
```
powershell "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'bash.exe' -and $_.CommandLine -match 'run_py2_finish|run_py2_chain' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
powershell "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and $_.CommandLine -match 'run_python|clone_and_extract' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
powershell "Get-CimInstance Win32_Process -Filter \"Name like '%git%'\" | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
```
Resume with `bash vibebench/run_py2_finish.sh`. It waits for any analysis process
still alive, then continues from `py2_stage.txt`.

### Also fixed 2026-09-14, before the finish chain could use them

* **Threshold proposer**: `file_loc` was calibrated over class rows, so a file
  with 200 classes counted 200 times (p90 2,938 against 1,135 over distinct
  files). Now per distinct production file, and marked INERT in V2, because
  `run_python.py` scores classes only, as Java does. The type-kind set is now
  imported from `ocp/dispatch.py` instead of copied. The ISP edit is anchored to
  the `ISP_THRESHOLDS` block, where a bare substitution would have edited the
  first block containing the key. The rewritten SRP block now carries its
  measured percentiles. Rehearsed on the real `config.py` and restored
  byte-identical (hash checked).
* **`fit_mixed_model.py`** hardcoded the Java parquet. From the Python chain it
  would not fail, it would print the JAVA model at the end of a Python log. Now
  `--lang {java,py}`, Java the default and byte-identical apart from a first-line
  `CORPUS:` banner (diffed), and it refuses a calibration parquet.
* **Worker count** is decided on commit headroom, not "Available MBytes", which
  excludes reclaimable cache and read 5,123 MB against 13,019 MB of real
  headroom.
* **`py2_health.py`** judges memory on commit charge for the same reason; it had
  raised a false FAIL at 1,816 MB available with commit charge at 52%.

### Phase 3, what was built (2026-09-11)

`vibebench/03_principles/py_lang/run_python.py`, ONE delta pass for all five
principles, replacing the four V1 passes and the file-driven `run_srp.py`.

* It contains **no detector logic**. The five V1 modules (`srp/metrics.py`,
  `ocp/dispatch.py`, `lsp/overrides.py`, `isp/interfaces.py`, `dip/graph.py`)
  already take `{path: source}` and parse with the stdlib `ast`, so they are
  reused unchanged and every label-validated rubric decision stays where it was
  validated. No JVM, no sidecar, no fact stream: that layer exists in Java only
  because supertype resolution demands it.
* `--self-check` runs offline in about a second and pins the parts that are
  impossible to notice when wrong: repo-wide entity tables scoped by touch,
  ripple-aware either-side attribution for LSP and ISP, novelty decided against
  the FULL before table, pair-level (not SCC-level) DIP novelty, the SRP
  authored-versus-edited split, and that calibration suppresses flags without
  suppressing rows.
* `--pilot --cells N` writes to `*.PILOT.parquet` so a rehearsal cannot freeze
  PRs in the real results.
* **Measured cost: about 5.4 s per PR** on a 349-file tree (four relation passes
  2.6 s per snapshot, two snapshots per PR). Git I/O dominates, so the five
  independent `ast` parses are not worth eliminating.

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

### Fixed on 2026-09-11, do not reintroduce

6. **All five V1 Python result parquets were still sitting at the exact paths
   phase 3 writes to**, and 294 to 339 of their PR ids are also in the V2 gold
   set. Any resume would have silently inherited V1 rows computed with V1
   thresholds on a 546-PR corpus. Moved to `*_results.V1-ORIG-moved-aside.parquet`
   after verifying by hash that the `*_results.V1.parquet` copies are identical.
   **If you ever restore them, move them off the live paths again first.**
7. **`PERMANENT` is narrower in `run_python.py` than in the V1 passes**:
   `{"ok", "repo_too_large"}`, where V1 also kept `no_touched_modules`. That
   status depends on Step 2 having extracted the PR, so caching it freezes a
   Step-2 gap as though it were a property of the pull request. Confirmed live:
   the 18-PR pilot produced 14 `no_touched_files`, every one of them a
   `git_diff`-sourced PR that phase 2 had simply not reached yet. Under V1's
   rule those 14 would have been frozen as unanalysable forever.

### FINDING: unrecoverable PRs, and why topping up the corpus is the wrong fix

Measured at 42% coverage with `07_verify/py2_attrition.py`. Roughly 4 to 5 per
cent of PRs that reach phase 2 yield ZERO usable files. Nearly all are
`diff_failed`, and the cause is exact:

```
files_source   git_diff for 91 of 91   (no file list in the dataset)
merge_sha      Claude_Code 0/46   Copilot 4/37   OpenAI_Codex 1/8
mean commits   0.00               0.49           0.12
state          87 of 91 closed
```

All three derivation tiers need one of those. Tier 1 needs the merge SHA, tier 2
needs the PR's own commits, tier 3 fetches `refs/pull/N/head` and fails when the
source branch has been deleted, which is the normal fate of a closed unmerged
PR. **The trees are gone from GitHub.** No amount of re-cloning, deeper fetching
or retrying recovers them; this is the ceiling of source-only analysis, not a
bug. `diff_failed` IS retryable for transient causes, so the number to report is
the one after the final phase 2 pass, not an interim one.

**DO NOT TOP THE CORPUS UP with replacement PRs.** Loss is not uniform:

```
outcome     reached   lost   loss %
merged         1460     58     3.97
open            167      5     2.99
rejected        506     40     7.91   <- double the merged rate
```

Rejected PRs lose their branches, which is exactly why they are unrecoverable,
so replacing them draws only from the retrievable ones. "Retrievable" then
correlates with the very thing the outcome contrast is measuring, which is
survivorship bias inside the stratum that carries the claim. The gold set is
also balanced and repo-stratified by construction, and topping up after the fact
changes that design. Measure it and state it in the one limitations section,
exactly as the Java thesis does for `repo_too_large` (150 PRs, non-random, the
biggest repositories, disclosed).

### Checking whether a run is alive: use the health script, not the counter

```
python vibebench/07_verify/py2_health.py --sample 30
```

**`[clone] progress: N/7050` IS NOT A HEALTH SIGNAL, and reading it as one cost
ten minutes on 2026-09-11.** It increments only when a whole REPO GROUP
finishes, so eight workers part-way through eight large repositories leave it
frozen for minutes while the run is downloading at full speed. Manifest coverage
is frozen for the same reason: a repo's rows are committed only when its group
completes. Both moved normally a few minutes later, with nothing wrong.

**The signal that works is bytes landing on disk.** During the apparent stall the
clone store was growing 557 MB per minute. The health script samples that first
and treats every counter as context. It also catches what fails silently: a
`*.tmp` parquet (an interrupted checkpoint), a manifest that no longer parses
(the one unrecoverable failure), orphaned git children, two processes writing one
manifest, and free disk against safeio's floor.

Phase 3 is the exception to the disk rule: it reads cached trees and is CPU
bound, so zero disk growth is CORRECT there. The script knows this.

### New trap, found while pausing on 2026-09-11

**Killing the phase 2 python parent does NOT kill its `git` children.** The stop
left **24 orphaned processes** behind (`git.exe` and `git-remote-https.exe`,
roughly three per worker), still holding sockets and repository locks. CLAUDE.md
documents this for the Java sidecar; it is the same failure with a different
child, and it applies to any stage that shells out to git, which is every stage
before analysis.

Kill both, and check afterwards rather than assuming:
```
Get-CimInstance Win32_Process -Filter "Name like '%git%'" |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```
Left alive they keep fetching into repositories the next run is also writing to.
Remember that `ps -W` reports zero python processes here while several run, and
`pgrep` does not exist, so PowerShell is the only reliable check.

### The smoke-test attrition alarm was a MISREADING, corrected 2026-09-11

The earlier note here said 130 `fetch_failed` rows all landed on Claude_Code and
warned of agent-biased attrition. Those 130 rows are **6 pull requests**.
Claude_Code averages 15.6 files per PR against Copilot's 3.4, so counting FILE
rows exaggerates it. At PR level the asymmetry runs the other way:

```
                 PRs   dead (zero usable files)
Claude_Code      163    10  (6.1%)
Copilot          125    40  (32.0%)   <- 35 of these are no_source_files
OpenAI_Codex      69     0
```

Copilot's 35 touched no `.py` file at all, which is the language filter working
as intended. Still measure this at full scale and disclose whatever it shows,
but measure it **per PR**, not per file row, and do not assume the agent.

### Checked and deliberately NOT changed

* Vendored and generated Python is under 0.5% of files, so no path exclusions
  were added. Java needed them (protoc output was 5.8% and agent-biased).
* Test code is 28% of files and `report.py` already separates it.
* Python keeps FIVE LSP signals. Java has three only because `javac` rejects
  the other two; Python has no compiler.

### Disk, re-measured 2026-09-11

Not the blocker the earlier note implied. Clones total 329.9 GB against 329 GB
free, which reads as fatal until it is broken down:

```
java_only  1002 dirs  298.6 GB   (one repo, open-metadata, is 127.9 GB alone)
py_only     723 dirs   22.6 GB   median 3.5 MB, mean 32 MB
orphan      272 dirs    5.8 GB
```

Python repos are roughly a tenth the size of Java ones, so the 3,243 repos still
to clone project to about 110 GB at the mean rate. **Do not delete the Java
clones to make room**; that would cost the finished study its reproducibility,
and it is not needed. `safeio.check_disk` stops the pipeline at a 15 GB floor.

## The Java study is finished and must not be touched

95-page thesis, 20-slide deck, verifier 28 checks 0 FAIL (re-run 2026-09-11),
DIP precision exact at 0.892 over a census of all 195 flagged requests. Do not
change any Java result while Python V2 is in progress. See `PROMPT.txt`
section 3.

## Two decisions already made, do not reopen

* **No human labelling.** The supervisor ruled it out of scope. LLM judging of
  SOLID is cited as validated (Pehlivan et al. 2025).
* **The title is settled** for the Java-only document: Evaluating Coding Agent
  Quality: Application to Software Design Principles. When Python results exist
  AND are validated it becomes a two-language study, and `PROMPT.txt` section 2
  says the title, abstract, research questions and Chapter 5 all get revisited
  then, not just a new results section.
