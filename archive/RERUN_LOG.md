# RERUN LOG , 3-agent × 2-language benchmark on AIDev-7.6M

**Purpose.** State file for a resumable re-run, and the provenance record for the methods
section. If work is interrupted, read this file first: the phase table is the current state.
Nothing here supersedes `CLAUDE.md`'s technical cheat sheet; this file is *status only*.

**Prime directive for this run:** report results exactly as they come out. If a number gets
worse, a ranking flips, or a principle stays at near-zero signal, that is the result. If a new
result contradicts an old one, state both and say which run produced which , never silently
pick the better-looking one.

---

## 1. Dataset provenance (methods note , cite these exact figures)

| | value |
|---|---|
| Source | Hugging Face `hao-li/AIDev-7.6M` (CC-BY-4.0) |
| Pulled | 2026-08-17, via `huggingface_hub.snapshot_download` (Xet backend disabled , it stalled) |
| Local path | `datasets/AIDev-7.6M/` (13 GB, 22 files) |
| Snapshot cutoff | 2026-03-31 (per dataset card) |
| Master tables used | **`all_pull_request.parquet` (7,685,281 rows)**, **`all_repository.parquet` (957,209 rows)** |
| Agents in source | Claude_Code, Copilot, Cursor, Devin, Google_Jules, OpenAI_Codex |
| Upstream paper | AIDev, arXiv:2602.09185 (MSR 2026 Mining Challenge). The 7.6M release itself has **no dedicated paper yet** , state this as a limitation. |

**Prior run for comparison** (do NOT delete; kept until the new results are accepted):
`AIDev-full`, 932,791 PRs / 116,211 repos, at repo root, still wired as `config.DATA_DIR`.

> ⚠️ **TABLE TRAP , this already caused one wrong analysis.** In `AIDev`, `AIDev-2.7M` and
> `AIDev-7.6M`, `pull_request.parquet` / `repository.parquet` contain ONLY the >100-star
> "AIDev-pop" subset. `AIDev-full` is the exception (its fork expanded `pull_request.parquet`
> to ~the full set), so reading the same filename across datasets silently under-counts the
> newer ones. **Always read the `all_*` tables.** Verified row counts above.

### Eligible-PR counts under the project's own filter chain
(merged, `stars>=25`, `PERMISSIVE_LICENSES`, language match , i.e. `curate.py::load_candidates`)

| | AIDev-full (prior) | AIDev-7.6M (this run) |
|---|---|---|
| Python-eligible PRs / repos | 6,673 / 362 | **51,872 / 4,075** |
| Java-eligible PRs / repos* | 944 / 47 | **6,357 / 636** |
| Claude_Code Python eligible | 66 | **26,055** |
| merged-rate (dilution check) | 84.7% | 80.9% |

*Java figures are the repo-primary-language PROXY. Real curation is extension-based
(`JAVA_FILTER_BY_FILE=True`) and finds MORE Java. Directional only , recompute at curation.

**Repo-concentration check (done BEFORE accepting the dataset).** Python: Claude_Code top-1 repo
**4.9%** / top-3 8.9% over 2,266 repos (genuinely broad, not one mega-repo); OpenAI_Codex still
concentrated at top-1 **26.6%** (MontrealAI, unchanged at every dataset size); Copilot 10.3%.
Java: Claude_Code 6.7%/14.0%, Copilot 11.9%/28.5%, OpenAI_Codex 31.5% (`nagisa77/OpenIsle`)/52.7%.

---

## 2. Design (locked 2026-08-17)

- **Agents: `OpenAI_Codex`, `Copilot`, `Claude_Code`** , the same three in BOTH languages, giving a
 clean 3 × 2 factorial. Every cross-language claim is only ever computable on agents present in
 both corpora, so the symmetric set sharpens that claim rather than costing anything.
- **Excluded from this run, with reasons:** `Cursor` (Java n=94, **72.3% in its top-3 repos**) and
 `Devin` (Java n=6) , data volume, not preference. `Google_Jules` , new to this dataset
 generation, no track record, would need PR-attribution validated from scratch.
- Prior 5-agent Python results are **kept** as a supplementary single-language observation.
 Nothing is deleted.
- Principles: SRP, OCP, LSP, ISP, DIP + the Martin A/I/D zone descriptor (descriptive only ,
 it is NOT judge-validated as a violation flag and must not be reported as one).
- **TypeScript is out of scope** (cancelled by the author). TS code stays in the tree; do not run
 or report it.

---

## 3. Phase table , CURRENT STATE

Legend: ☐ not started · ◐ in progress · ☑ done

| # | Phase | State | Notes |
|---|---|---|---|
| 0.1 | LSP extractor `<<source unavailable>>` bug | ☑ **fixed 2026-08-17** | root cause + fix below; measured 40% → **0%** |
| 0.2 | Backport same SHA fix to ISP / OCP / DIP Python extractors | ☐ | same defect, confirmed present |
| 0.3 | Backport blind-labelling to ISP / OCP / DIP Python extractors | ☐ | same leak, confirmed present |
| 0.4 | Drop DIP `mean_d_worse` from the composite flag | ☐ | P=0.000; see §4 |
| 0.5 | Add `is_new` (authored-vs-edited) to Python SRP | ☐ | Java has it, Python does not , see §4 |
| 0.6 | Wire `config.DATA_DIR` → `datasets/AIDev-7.6M` + `all_*` table names | ☑ **done** | `PR_TABLE`/`REPO_TABLE` added so the pop-subset trap can't recur |
| 0.7 | Java 3-agent config (`JAVA_ONLY_AGENTS`) | ☑ **done** | now `[OpenAI_Codex, Copilot, Claude_Code]` |
| 0.8 | `git_diff` fallback for all agents in `curate.py` | ☑ **done** | LEFT join replaces INNER; sampler prefers exact lists, over-draws git_diff by `GIT_DIFF_OVERDRAW=1.4` |
| 0.9 | `gitsnap` tier-3 `refs/pull/N/head` fallback | ☑ **done + verified** | see §4b; this is what makes 3-agent Java possible |
| 0.10 | `PER_AGENT_PR_CAP` 150 → 500 | ☑ **done** | labelling cost is fixed by `n-per-stratum`, so extra corpus costs only compute |
| 1 | Curate Python (3 agents) | ☐ | |
| 2 | Clone + extract Python | ☐ | resumable; reuses `corpus/clones/` |
| 3 | Threshold recalibration Python (calibration mode, BEFORE any labels) | ☐ | do NOT reuse old thresholds |
| 4 | Run 5 principles + Martin zones, Python | ☐ | |
| 5 | Draw fresh tuning + holdout samples, Python (no overlap with burned) | ☐ | blind protocol |
| 6 | Label + score Python, all 5 principles | ☐ | the real bottleneck |
| 7 | Curate → run → recalibrate → label → score, Java | ☐ | same sequence |
| 8 | Reports + old-vs-new contradiction table | ☐ | |

**Ordering rationale (Python before Java).** The dataset switch is an untested code path;
Python is far cheaper to debug it in (no JVM, no sidecar, no 10 20 s/tree parse). Shared modules
(`gitsnap`, `dip/graph.py`, `metrics.py`) get exercised by Python first either way.

**Safety rule:** the existing `vibebench/corpus/*.parquet` and `04_report/*.csv` from the prior
run are NOT deleted or overwritten in place. New outputs go to new paths (or the old ones are
copied aside first) until the author accepts the new results.

---

## 4. Defect log

### 0.1 , LSP extractor, FIXED 2026-08-17
**Symptom (documented):** ~40% of flagged LSP rows extracted as `<<source unavailable>>`,
blocking the Python LSP validation entirely.
**Root cause (measured, not guessed):** `extract_lsp_label_batches.py` called
`git show <merge_sha>:<path>` directly. **140 of 202 violating override rows (69%) have
`merge_sha = NaN`** → `str(nan)` = `"nan"` → `git show nan:path` fails. Worst hit: zfit 84
violating rows with only 4 usable SHAs; onnxscript 50 with **zero**.
**Why the harnesses were fine:** they resolve the after-state via `gitsnap.delta_pair`, which
falls back to the PR's own `commit_shas` when no merge event was recorded. The extractor never
got that fallback.
**Systemic, not local:** the TS extractors (`extract_{lsp,isp,dip}_ts_*`) already had the fix
from the 2026-07-31 port; it was never backported to the four Python extractors. `extract_ocp`,
`extract_isp` and `extract_dip` (Python) still carry the same defect → phase 0.2.
**Fix:** resolve the after-tree through `gitsnap.delta_pair` + `gitsnap.src_tree`, and locate
modules with `dip/graph.py::path_to_module` so the judge sees exactly the tree the detector
analysed. This also removed the old path-guessing heuristic (`<mod>.py` / `src/<mod>.py` only),
which silently missed any other layout.
**Verified:** 82-row sample, `0 rows missing one or both sides (0.0%)`, down from ~40%.

### Second defect found in the same file , blind labelling, FIXED 2026-08-17
The batch header printed `signals=[...]` and `rubric_violation=...` **before** the code,
anchoring the judge toward the tool's verdict. Java was fixed for this on 2026-08-04
(`--show-verdict`); Python never was. Blind is now the default here, `--show-verdict` restores
the old behaviour for debugging. **Any figure from a pre-2026-08-17 Python batch is an UPPER
BOUND.** Same backport needed for ISP/OCP/DIP → phase 0.3.

### 0.4 , DIP `mean_d_worse`: drop from the composite
Validated **P=0.000** in Python (0/7) and TypeScript (0/10) , 17 flags, zero true positives, two
languages independently agreeing. Python DIP is 20 flagged PRs = 13 `new_cycle` + 7 `mean_d`,
and validation was 13/20 = 0.650 precision. Dropping `mean_d` gives **13 flags at P=1.000 with
no recall loss** (the 7 were not violations at all). Reporting the composite *understates* the
project's own best detector. Cascades into every DIP sentence/table in Ch4.

### 0.5 , Python SRP has no authored-vs-edited flag (proposed fix)
`srp_results.parquet` has no `is_new`, so a Python SRP finding can only be stated as "present in
touched code", never "introduced by the PR". Java HAS this, which is what makes its headline
**0/17 authored** finding possible. Adding it to Python makes the two languages commensurable and
gives the strongest available fidelity statement a Python counterpart. Worth doing in a fresh run.

### Regression guards , verify still fixed, do not reintroduce
CRLF LOC-doubling (`write_text` turning CRLF into CR-CR-LF; 100% of the corpus is CRLF) ·
LCOM meaningless for edgeless types · DIP `top_package` returning `"net"` for reverse-DNS Java
packages · DIP novelty tested on the touched subset only · ripple attribution (charge a relation
when the BASE is edited, not only the flagged entity) · merge parents read via `git cat-file -p`,
never `rev-parse ^1` · `-c core.protectNTFS=false` on `git archive`.

---

## 4b. THE COVERAGE PROBLEM AND ITS FIX (2026-08-17) , read before trusting any count

**Discovered:** AIDev-7.6M is 8x larger in PRs but its **per-PR tables did not scale**. All three
stop at the ~197k ">100 star" AIDev-pop subset:

| table | distinct PRs covered | of 7,685,281 |
|---|---|---|
| `pr_commit_details` (file lists) | 197,330 | 2.6% |
| `pr_commits` (commit SHAs) | 197,335 | 2.6% |
| `pr_timeline` (merge events) | 110,246 | 1.4% |

Coverage below 100 stars is **exactly zero**; above it only ~50%. AIDev-full had near-complete
coverage, which is why this never bit before. **The same ~197k PRs are covered by all three
tables**, so a PR either has file list + SHAs + timeline, or none of them.

**Consequence, measured (usable PRs after the full filter chain, dataset-supplied data only):**

| | Claude_Code | Copilot | OpenAI_Codex |
|---|---|---|---|
| Python | **505** | 3,790 | 5,313 |
| Java | **45** | 1,139 | 1,207 |

The loss is severely **agent-biased** , the same defect class as the old no-merge-SHA drop. At 45
Java PRs a balanced 3-agent Java corpus would be 135 PRs, worse than the 867 it replaces.

**FIX , TIER 3 of `gitsnap.delta_pair`: `refs/pull/<N>/head`.** GitHub serves a PR's head ref for
any PR, needing nothing from the dataset but the PR number (which `all_pull_request.parquet` has
for every row). Implemented as `gitsnap.pull_ref_pair`. Recovers **1,906 additional Claude_Code
Java PRs (45 -> ~1,951)** and removes an undocumented selection effect (the corpus was silently
restricted to the star-correlated pop subset).

**Two correctness traps handled, do not "simplify" these away:**
1. **NOT the tip's first parent.** For a multi-commit PR that parent is the previous commit *of the
 same PR*, so diffing them captures only the final commit and MISSES violations introduced
 earlier. That would bias every delta principle toward "no new violation" , the exact result
 this benchmark reports, making the bias self-confirming. The branch point is resolved with
 `merge-base` against the default branch instead. Verified: fess#2998 spans **2** commits, so
 the naive parent would have captured half the PR.
2. **Depth ladder starts at 500, not 50.** Measured on fess#2998: depth 50 leaves the branch point
 outside both shallow boundaries and `merge-base` fails (rc=1, empty); depth 500 succeeds. Cost
 is amortised , the clone cache is per-REPO and reused across all of that repo's PRs.
 Unresolvable after depth 3000 returns None, so the PR gets a retryable status rather than a
 silently wrong pair.

**Bug found and fixed in the fix itself:** the first version put tier 3 only at the end of the
tier-2 block, but tier 2 `return None`s early when `commit_shas` is absent , the common case here
, so the fallback was unreachable and every test returned None. All tier-2 failure exits now fall
through via `_fb()`. **Verified after fixing:** fess#2998 -> (a064adeece, 98a5bfad48), 2 PR
commits, 1,420 java files; DataSQRL/sqrl#1785 -> (94398f3a78, c09f641dda), 1 commit, 798 java
files; both `merge-base --is-ancestor` true. Existing `merge_sha` path regression-tested OK.

**Cost this adds:** deeper fetches (depth 500 vs 2) on the tier-3 path only, amortised per repo.
Raises the disk estimate , budget for clones growing well past the earlier 45-65 GB guess and
watch free space (114 GB at time of writing).

## 5. Open decisions (need an answer before the phase that uses them)

1. **`PER_AGENT_PR_CAP` for Python.** Currently 150. With 3 agents all far above the cap, the
 corpus would be 450 PRs , *smaller* than the current 546. Raising to **250 300** directly
 attacks the run's main scientific motive (four of five principles have single-digit PR-level
 flag counts, so no per-agent claim is possible). Recommend **300**.
2. **Java sampling.** `JAVA_ONLY_AGENTS` → the 3-agent list. `JAVA_PER_REPO_PR_CAP` is `None`
 today (uncapped, justified when there was one agent). With 3 agents, uncapped hands the corpus
 to whoever has most Java (Copilot). Recommend a **per-agent** cap matching Python's, with
 macro-per-repo reporting retained for within-agent concentration.
3. Whether `MIN_STARS` stays at 25. Recommend **unchanged** , changing two variables at once
 makes old-vs-new differences uninterpretable.

---

## RESUME POINT (updated 2026-08-18 02:25)

**Everything is resumable with ATOMIC checkpoints.** A forced shutdown (`shutdown /s /f /t`),
power cut, or closed lid loses at most: 1,000 files (SRP) or 25 PRs (delta harnesses). Checkpoints
are written to a temp file then `os.replace`d, so a crash can never leave a corrupt parquet.

### To resume after a shutdown, run exactly this:
```
cd /d/VibeBench
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1 PYTHONUNBUFFERED=1
for stage in dip ocp lsp isp; do python "vibebench/03_principles/$stage/run_$stage.py"; done
python vibebench/03_principles/arch/run_arch.py --lang py
```
Each script skips PRs already recorded with a permanent status, so re-running is safe and
idempotent. **Do NOT delete the result parquets** -- that is what makes resume work.
**Do NOT run the four in parallel**: `gitsnap.repo_lock` is a `threading.Lock` (per-process only),
so concurrent processes would issue simultaneous git commands on the same repo dirs. Sequential
is also nearly as fast, because all four share the gzipped tree cache.

### State as of this write
| Item | Status |
|---|---|
| Dataset | AIDev-7.6M, wired via `config.DATA_DIR` + `PR_TABLE`/`REPO_TABLE` |
| Corpus | **1,464 analyzable PRs / 484 repos / 13,310 files** -- FINAL, do not re-curate |
| Agent balance | OpenAI_Codex 499 - Copilot 496 - Claude_Code 469 |
| SRP (Python) | **DONE** -- 35,767 rows, 53 parse errors, thresholds verified stable |
| SRP thresholds | production-p90, RE-VERIFIED against the final corpus (p90 moved <=1 unit) |
| DIP / OCP / LSP / ISP / arch | **IN PROGRESS** -- chain started 02:22 |
| Java | not started |
| Labelling | not started -- **STOP and hand back for the Sonnet switch first** |

### Old results preserved (never delete until the new run is accepted)
`corpus/*.PREV-AIDevFull.parquet` (gold sets, srp, ocp, lsp, isp, dip, arch),
`corpus/srp_results.OLDTHRESH.parquet`.

### Known corpus property to report (NOT a bug -- belongs in the thesis)
The three agents operate in very differently-sized repos: median stars Claude_Code **2,185**,
Copilot **330**, OpenAI_Codex **283**. Macro-per-repo averaging controls repo mix but not this
popularity gradient. Report a star-band sensitivity check alongside the headline rates.

## JUDGE-DRIFT DEFECT (found 2026-08-18) -- MUST FIX BEFORE REPORTING SRP P/R

`srp_labeling_sample.csv` (tuning, 240 rows) scored **P=0.083**; `srp_holdout_sample.csv`
scored **P=0.296** on its first 150 rows. Same detector, same corpus, same thresholds.
The gap is NOT sampling error -- it is **judge drift**:

* The tuning file was judged EARLY in the session, before the LCOM correction. At that point
 a low LCOM was being treated as near-sufficient evidence of cohesion, which suppressed
 positives (the `ControllerState` case exposed this: its own docstring named three separate
 concern categories while LCOM was low).
* The holdout is being judged under the SHARPENED standard: "does each part have an
 INDEPENDENT REASON TO CHANGE", with LCOM demoted to one weak input among several.

**Consequence:** the two samples are not comparable, so the holdout cannot currently be
described as confirming (or contradicting) the tuning number.

**Required fix:** re-judge `srp_labeling_sample.csv` in full under the final standard once
the holdout is complete, then score both. Do NOT report either P until this is done.
Under the audit's own rule, the pre-fix tuning labels are an UNDER-estimate of precision.

**Prevention:** the standard is now written down (below) instead of being carried implicitly.

### SRP judging standard (final -- apply to every row)
POSITIVE (real smell) when the class bundles parts that have INDEPENDENT reasons to change:
 - persistence/IO bolted onto domain logic (e.g. read/write parquet on a validation class)
 - visualization/plotting bolted onto computation
 - a second algorithm/subsystem embedded in a service (clock sync inside an audio player)
 - unrelated capability generation (SKILL.md authoring inside mount management)
 - several distinct business capabilities (forecast + dispatch + hedge)
 - extreme absolute size (>600 loc) WITH nameable separate concerns
NEGATIVE (not a smell) even when metrics are high:
 - test suite classes (size scales with case count)
 - framework-mandated interface surface (HomeAssistant entity, LM eval harness, pathlib mirror)
 - pure delegating wrappers over sub-objects
 - visitor/dispatch idiom (all visit_*), operator families (add/mul/min/max)
 - value objects with parsers+formatters for ONE concept
 - config/enum/dataclass/schema holders with 0-2 methods
 - official/comprehensive SDK clients that mirror one external REST API 1:1 by design
 (BasicKeyCommands, RedisCluster, VectorStore, MCP Client, PrefectClient, HfApi-style ,
 large because the external protocol is large, not because responsibilities were bolted on)
 - repeated per-target/per-entity-type structural repetition (same op x N types), not
 orthogonal responsibilities
POSITIVE even for "just a big official client" when the class spans genuinely SEPARATE
PRODUCTS/PLATFORMS under one roof (e.g. HfApi: repos+files+commits AND Spaces hosting AND
Inference Endpoints AND compute Jobs AND a separate Buckets storage product) -- breadth across
unrelated product surfaces still counts even inside a "well-known SDK".

### DIP census judging note (2026-08-18)
`dip_census.csv` (197 rows: 47 new_cycle positives + 150 negatives) judged via
`extract_dip_label_batches.py` batches. IMPORTANT: that extractor's own "NEW CYCLE:" trace
print uses an OLDER whole-SCC-set comparison (`c not in cb`), NOT the shipped
`cyclic_pairs`-based definition the `dip_risk` column is actually computed from -- the trace
can print (or omit) misleadingly relative to what actually earned the flag. Do not trust the
trace label alone; instead check whether "NEW internal dependencies added by touched modules"
contains an edge that plausibly closes a loop back into the printed cycle's membership list
(before=X/after=X+1 is the clean signal; before=X/after=X same-count-but-new-trace usually
means an existing SCC absorbed a newly-touched module via a genuine new edge -- verify the
edge's target is actually listed inside the printed cycle chain before crediting it).
Result: 187/197 judged (10 skipped, extractor could not resolve before/after pair --
`notes` column flags these for retry), **P=0.786 (33/42)** on genuinely new_cycle-only
flags, 0 false negatives found among 145 judged negatives. This is far above the old
mean_d-diluted P=0.22 0.65 estimates and confirms `new_cycle` alone is the reliable DIP
signal at the larger AIDev-7.6M-based corpus scale, consistent with dropping `mean_d`
from the reported flag (see IMPROVEMENT PROGRAMME item 4 in CLAUDE.md).

---

## Java pre-flight audit , 2026-08-19

Every phase of the Java pipeline was read before the rerun. Six defects found;
all six fixed. Three would have silently produced wrong numbers that looked
normal. Recorded here because the thesis needs the provenance, not because the
journey belongs in the report.

### D1 , no language gate on `git_diff` rows (curate.py, CRITICAL)
`filter_by_file` correctly skips the repo-language filter so cross-language Java
is not missed, and rows with a dataset file list are then filtered by `.java`
extension. Rows WITHOUT one were never language-checked at all. Measured pool:
**253,523 candidate PRs, of which 6,239 (2.46%) were even in a Java repo.**
`balanced_sample` over-draws git_diff rows by 2.3x to survive Step-2 attrition,
so it drew ~1,046 essentially random PRs per short agent.
Projected corpus: OpenAI_Codex 500, Copilot 500, **Claude_Code ~60** , an **8x
agent bias**, the same family as the no-merge-SHA drop (17x) and the
pr_commit_details inner join. Fixed: git_diff rows now require the repository's
primary language to be in scope.

### D2 , a hardcoded assumption false on the new dataset (curate.py)
Every Copilot PR was forced onto the git_diff path, commented *"Copilot never
has usable filenames"*. True of AIDev-full. On AIDev-7.6M, filenames are
**97.3% non-null for Copilot** (2,039,969 of 2,097,609 rows), in line with every
other agent (99.4 100%). The exception discarded free exact file lists and
self-inflicted the 56.3% Copilot attrition previously recorded as unexplained.
Removed; routing is now data-driven for every agent. Copilot median touched
files went 0 -> 3.

### D3 , a failed BEFORE tree scored as an empty one (run_java.py, CRITICAL)
`if not bf.get("ok"): bf = {"types": [], "modules": [], ...}`. Every delta here
is "present in AFTER, absent in BEFORE", so an empty BEFORE makes the whole
after-tree look newly authored: OCP `new_dispatch_smell`, the new LSP
violations, ISP `new_flagged_iface` and the new DIP cyclic pairs all fire at
once. That inflates *violations the PR INTRODUCED* , the headline metric ,
silently, with no status to notice it by. A genuinely empty parent does not
reach this branch (`src_tree` returns `{}` and the sidecar succeeds on it), so
this only ever fired on real failures. Now recorded as `before_sidecar_failed`
and retried.

### D4 , report never grouped by agent (report_java.py, CRITICAL)
No `groupby("agent")` anywhere, while the title read "OpenAI Codex only". Run
unchanged on the 3-agent corpus it would have pooled three agents into one
number and labelled it as one agent's. Rewritten: per-agent pooled + macro/repo
with Wilson CIs for all five principles, per-agent concentration, per-agent
analysable/drawn, and a `status x agent` cross-tab so a future agent-skewed
status cannot hide.

### D5 , a `dataset` row with an empty file list vanished (clone_and_extract.py)
The `not touched` guard was inside the `needs_diff` branch, so a dataset row
that filtered to zero in-scope files produced **no manifest row at all** , no
status, no count. Guard now covers both routes.

### D6 , `mean_d_worse` still counted toward `dip_risk` (config + run_java.py)
Python full census: `new_cycle` 13/13 (P=1.000), `mean_d_worse` 0/7 (P=0.000).
TypeScript: `new_cycle` 5/5, `mean_d_worse` 0/10. Zero true positives in 17
flags across two corpora. Combining them adds false positives and understates
the best detector in the project. `DIP_FLAG_INCLUDES_MEAN_D = False`; the value
is still computed and written as a descriptor.

### Improvement , repo-stratified draw (`JAVA_REPO_STRATIFIED_DRAW`)
Same 500 PRs per agent, taken round-robin across repositories:

| agent | natural | stratified |
|---|---|---|
| Claude_Code | 145 repos, top-3 11.4% | 271 repos, top-3 3.8% |
| Copilot | 153 repos, top-3 25.2% | 170 repos, top-3 4.8% |
| OpenAI_Codex | 65 repos, top-3 48.8% | 73 repos, top-3 27.0% |

Macro-averaging already controls the RATE; it cannot create independent units
that do not exist. This doubles the repositories at no cost in PRs.
**Disclose in methods: Java is stratified, Python is not.**

### Verified still fixed (the six from JAVA_STATUS 2026-08-04)
CRLF line-doubling (`write_bytes`, java_bridge.py) · LCOM `None` for edgeless
types · `java_pkg` replacing `top_package` for reverse-DNS packages · DIP
novelty at compilation-unit level · pair-level (not whole-SCC) novelty ·
ripple-aware attribution. All four self-checks PASS (sidecar 23 assertions,
java_bridge, detectors, check_label_file).

### State reset before the rerun
Archived, not deleted: `PREV_java_void_1agent/` (5 result parquets +
java_pr_results, all single-agent), `labels/PREV_java_AIDevFull_1agent/`
(6 judged label files from the void corpus), `gold_set_java.PREV-*`.
`run_java.py` resumes by keeping permanent-status rows, so leaving the old
parquets would have merged 867 void single-agent PRs into the new corpus.

### Still required before results are quotable
1. **Recalibrate thresholds.** `SRP_JAVA_THRESHOLDS` and the OCP/ISP entries in
 `JAVA_DELTA_THRESHOLDS` were percentile-calibrated on the OLD 27-repo,
 single-agent corpus (n=976). The new corpus is 3 agents over 464 repos.
 Run `--calibrate`, set from the printed percentiles, DELETE the parquets,
 re-run. Non-negotiable per the project's own rigor gate.
2. **Fresh label rounds.** Every Java holdout is burned; the old labels describe
 a different corpus.
3. **Match Python's labelling method** before comparing precision across
 languages , Python's SRP read P=0.33 sampled and P=0.064 on full census.

---

## Labelling method , what the Python "census" actually is (2026-08-19)

The Python label files are named `*_census.csv`, and that name is wrong in a way
that UNDERSELLS them. Measured against the full populations:

| principle | flagged judged | unflagged sampled |
|---|---|---|
| OCP | 533 / 533 , **100%** | 21.0% |
| LSP | 443 / 443 , **100%** | 5.5% |
| ISP | 144 / 144 , **100%** | 9.4% |
| DIP | 47 / 47 , **100%** | 8.6% |
| SRP | 2,625 / 2,865 , 91.6% | 6.5% |

So the design is **complete enumeration of the flagged side plus a sample of the
unflagged side**, not a census and not a stratified sample.

WHY THIS MATTERS FOR THE WRITE-UP, and it is a strengthening not a caveat:

* **Precision carries no sampling error.** Every flagged entity was inspected
 (91.6% for SRP), so P is a measurement of this corpus, not an estimate of it.
 No confidence interval is needed on the precision denominators.
* **Recall IS an estimate**, from a 5.5%-21% sample of unflagged entities. The
 reported R=1.000 for SRP and DIP means "no false negative appeared in that
 sample", which is weaker than the precision claim and should be stated as such.
* This is also the correct efficient design: judging more unflagged rows cannot
 move precision at all, so effort belongs on the flagged side.

Java replicates the method exactly (`labels/make_java_census.py`): 100% of
distinct flagged entities, plus a 10% unflagged sample capped at 1,200 per
principle. Entity deduplication happens first -- Java repeats a class once per PR
that touched it, at 1.86x, the worst of the three languages.

Do NOT compare a precision figure from this method against one from the old
30-per-stratum sampler. Same detector, same corpus, Python SRP: **P=0.33**
sampled, **P=0.064** enumerated.

---

## External Java tools , coverage decision (re-verified 2026-08-19)

The 2026-08-04 survey was re-checked against DesigniteJava's published smell
list. It holds, and the current mapping is already the correct one.

**DesigniteJava detects 17 design smells** (Imperative / Multifaceted /
Unnecessary / Unutilized Abstraction · Deficient / Unexploited Encapsulation ·
Broken / Cyclic-Dependent / Insufficient / Hub-like Modularization · Broken /
Cyclic / Deep / Missing / Multipath / Rebellious / Wide Hierarchy).
**"Fat Interface" is NOT among them**, confirming that the claim Designite
covers ISP is false. No mapping is invented for ISP.

| principle | external instrument | status |
|---|---|---|
| SRP | PMD `GodClass` + Designite `Insufficient Modularization`, `Multifaceted Abstraction` + CK metrics | integrated |
| LSP | Designite `Rebellious Hierarchy`, `Broken Hierarchy` | integrated |
| DIP | Designite `Cyclic-Dependent Modularization` | integrated |
| OCP | none exists | documented gap |
| ISP | none exists | documented gap |

`Deep`, `Wide`, `Multipath` and `Cyclic Hierarchy` are deliberately NOT mapped to
LSP: they are structural properties of a hierarchy, not statements about
substitutability, and claiming them would be the same invented-correspondence
error the ISP claim makes.

**Rejected again, with reasons:** SonarQube (needs a running server; its
god-class rules duplicate PMD's; no OCP or ISP rule either) · ArchUnit (a
conformance DSL , you write the rules, so "validating" our detector against
rules we wrote ourselves is circular; also bytecode-based, and our trees are
bare `git archive`) · JDepend (bytecode-only, and computes exactly the Martin
A/I/D and cycles that `dip/graph.py` already shares across languages).

**The OCP/ISP gap is a field-level gap, not a project one**, and it has a
technical cause worth stating: both need a cross-file semantic graph, which
single-file linters never build and compiler-backed tools only obtain from a
classpath. The JavaParser+SymbolSolver sidecar obtains it from bare source at
91.3% resolution with no build step.

**Improvement made.** 6 of the 7 PMD design rules and 12 of the 17 Designite
smells were being computed by the same tool invocations and then discarded.
`build_report` now emits a `quality_profile` block: every PMD design-rule rate,
every Designite smell frequency, and CK's coupling (CBO), response-for-class
(RFC), inheritance depth (DIT) and cohesion (TCC, LCOM*) distributions over
production types. Zero extra tool runtime, no invented SOLID mapping, and it
gives the thesis a broader code-quality dimension beside the SOLID result.

---

## Correction: symbol-resolution rate is NOT 93-100% (measured 2026-08-19)

`CLAUDE.md` and `JAVA_STATUS.md` record the JavaParser+SymbolSolver pilot at
"93-100% supertype resolution" and "91.3% resolution, no build". Those figures
come from the Phase-0 pilot on server-side Java (Chronicle-Wire and similar).
**They do not generalise to this corpus.** Measured on a 12-PR smoke run:

| repository | kind | resolved supertypes |
|---|---|---|
| ArcadeData/arcadedb | server-side Java | 95.1% |
| Apicurio/apicurio-data-models | server-side Java | 89.1% |
| Apicurio/apicurio-registry | server-side Java | 77.7% |
| AnySoftKeyboard | Android app | 75.3% |
| **Cap-go/capacitor-inappbrowser** | **Android/Capacitor plugin** | **25.0%** |
| **Cap-go/capacitor-updater** | **Android/Capacitor plugin** | **28.6%** |

Median 77.7%, mean 66.0%. The distribution is BIMODAL BY REPOSITORY KIND, not
noisy: plugin and framework-heavy code inherits from types that live in external
jars/aars, and source-only resolution can never see those. This is correct
behaviour for the instrument, not a defect.

**Why it matters.** Unresolved supertypes mean override pairs and implementer
links are never constructed, so **LSP and ISP UNDER-COUNT exactly where
resolution is low**. Recall for those two principles is therefore repo-dependent
and, because agents drew different repository mixes, potentially agent-dependent.
In the smoke sample the per-agent medians were Copilot 95.1%, Claude_Code 77.7%,
OpenAI_Codex 25.0% -- n=12, so indicative only, but large enough to matter.

**What was done.** `report_java.py` now prints resolution as median + p10/p90 and
per agent, counts PRs resolving <50%, and prints an explicit warning when the
per-agent spread exceeds 15 percentage points. The corpus-wide figure is unknown
until the full run; it must be READ OFF the report, not carried over from the
pilot.

**For the write-up:** quote the measured corpus figure, state that LSP/ISP recall
is bounded by it, and do not repeat 93-100% -- that number describes six pilot
repositories, not this corpus.

---

## REMAINING WORK CHECKLIST (as of 2026-08-20)

Authoritative running order. Tick as completed. Nothing here is optional.

### Java pipeline
- [x] 1. Curate , 2,091 PRs / 464 repos / 3 agents, repo-stratified
- [x] 2. Extract pass 1 , 19,925 files ok
- [ ] 3. Extract pass 2 (quiet, 8 workers) , recovers contention failures
 pass 1 left 1,931 `fetch_failed` files; pass 2 showed 0 at the 450-PR mark
- [ ] 4. `run_java.py --calibrate` , full-corpus distributions for SRP/OCP/ISP
 (LSP and DIP have no calibrated threshold: categorical signals)
- [ ] 5. Set `SRP_JAVA_THRESHOLDS` (~p90) + `JAVA_DELTA_THRESHOLDS` ocp/isp
 from the printed percentiles. Old values were calibrated on 27 repos /
 1 agent; the new corpus is 464 repos / 3 agents.
- [ ] 6. Full flagged run (`run_java.py`) , writes ALL FIVE parquets:
 `srp_/ocp_/lsp_/isp_/dip_results_java.parquet` + `java_pr_results.parquet`
- [ ] 7. `report_java.py` , per-agent pooled + macro/repo, Wilson CIs,
 status x agent cross-tab, symbol-resolution spread warning

### Validation , ALL FIVE PRINCIPLES
- [ ] 8. `make_java_census.py --all` , 100% of flagged entities + 10% unflagged,
 matching Python's method so precision is comparable
- [ ] 9. `extract_java_label_batches.py --principle {srp,ocp,lsp,isp,dip}`
 (blind by default; `--show-verdict` only to debug)
- [ ] 10. Judge all five. FIX THE RUBRIC IN WRITING FIRST -- Python's SRP round
 drifted ~3x between passes and needed 480 rows re-judged.
- [ ] 11. `check_label_file.py --all` then `score_labels.py` per principle

### External cross-check
- [ ] 12. `run_crosscheck_java.py --workers 4` on the NEW corpus (previous run
 was the void single-agent one). Produces CK/PMD/Designite agreement for
 SRP, LSP, DIP + the new `quality_profile` block (all 7 PMD design rules,
 all 17 Designite smells, CK CBO/RFC/DIT/TCC/LCOM*).

### Martin zones (A/I plane) , BOTH LANGUAGES
- [x] 13. Python `arch_results.parquet` , already current on AIDev-7.6M
 (122,453 packages, median A=0.000, pain 18.5%, main-seq 17.9%,
 unplaceable 54.2%)
- [ ] 14. `run_arch.py --lang java` on the new Java corpus
- [ ] 15. `report_arch.py` + cross-language A/I comparison
 CARRY THESE CAVEATS: descriptive only, no violation flag, no agent
 ranking (Martin's D validated at P=0.000 as a change signal); and do NOT
 use the touched-vs-untouched contrast -- it pointed in OPPOSITE
 directions across the two languages.

### Documentation (deferred by the author until the experiment is done)
- [ ] 16. Correct the symbol-resolution figure (docs say 93-100%, measured 25-95%)
- [ ] 17. Rewrite the CLAUDE.md Java section: it still describes a single-agent
 within-agent study
- [ ] 18. Update the Java thresholds block (old 27-repo calibration)
- [ ] 19. Rename/reframe the Python "census": it is complete-flagged enumeration
 + unflagged sample, which is STRONGER than the name suggests
- [ ] 20. Record the sampling asymmetry (Java stratified, Python natural draw)
- [ ] 21. Record the 10 defect fixes and their measured impacts

---

# PYTHON RESULTS, FINAL (AIDev-7.6M, 3 agents)

Regenerated from the parquets 2026-08-20. These SUPERSEDE every Python number in
`CLAUDE.md`, which still describes the old AIDev-full run (546 PRs, 5 agents,
452 analysable). Nothing below is from the old corpus.

## Corpus
1,786 PRs / 621 repos.

| agent | PRs | repos | top-1 repo | top-3 |
|---|---|---|---|---|
| Claude_Code | 500 | 168 | 9.4% | 22.0% |
| Copilot | 786 | 355 | 11.2% | 19.7% |
| OpenAI_Codex | 500 | 132 | **41.4%** | **50.0%** |

OpenAI_Codex is heavily concentrated (MontrealAI). **Lead with macro-per-repo.**

## Violations INTRODUCED (PR-level delta)

| principle | analysable | flagged | Claude_Code | Copilot | OpenAI_Codex |
|---|---|---|---|---|---|
| DIP | 1,401 | **47** | 26/444 (macro 5.34%) | 14/466 (2.12%) | 7/491 (**3.52%**) |
| OCP | 1,402 | **44** | 27/445 (macro 6.18%) | 10/466 (2.20%) | 7/491 (1.61%) |
| LSP | 1,399 | **5** | 3/444 | 2/465 | 0/490 |
| ISP | 1,395 | **4** | 4/445 | 0/461 | 0/489 |

⚠️ **DIP pooled and macro DISAGREE for OpenAI_Codex** (pooled 1.43% vs macro
3.52%) purely from repo concentration, and the pooled number REVERSES the
ranking, on macro, **Copilot is cleanest on DIP, not Codex**. Quote macro.

## SRP (per production class, entity-level)
20,210 production class rows, 2,406 flagged.

| agent | pooled | macro/repo | AUTHORED (`is_new`) |
|---|---|---|---|
| Claude_Code | 13.33% (1506/11296) | 22.78% | 12.38% (103/832) |
| Copilot | 11.53% (527/4571) | 21.00% | 9.33% (49/525) |
| OpenAI_Codex | 8.59% (373/4343) | 20.72% | 5.65% (19/336) |

Authored < edited for every agent: agents write cleaner classes than the ones
they modify.

## Violations PRESENT vs INTRODUCED, the headline contrast

| principle | present in touched code | introduced by a PR |
|---|---|---|
| LSP | **443** violating override pairs (of 14,919) | **5** |
| OCP | **533** smelly chains (of 4,336) | 44 |
| ISP | 32 flagged interfaces (of 1,431) | 4 |

**Of 443 Liskov violations present, agents introduced 5.** Inter-unit smells are
inherited, not created. This is the strongest result in the study.

## Validation, complete-flagged enumeration + unflagged sample

7,927 rows judged. **Precision is EXACT** (every flagged entity inspected);
recall is estimated from the unflagged sample.

| principle | n | precision | recall | F1 |
|---|---|---|---|---|
| **DIP** | 197 | **0.702** | 1.000 | 0.825 |
| **OCP** | 1,333 | 0.394 | 0.644 | 0.489 |
| **LSP** | 1,243 | 0.264 | 0.873 | 0.406 |
| ISP | 744 | 0.097 (18 positives) | 0.778 | 0.173 |
| SRP | 4,410 | 0.064 | 1.000 | 0.120 |

## Is the agent ranking safe? Per-agent precision

A detector that over-flags one agent more than another would fake a difference.
Checked:

| principle | Claude_Code | Copilot | OpenAI_Codex | even? |
|---|---|---|---|---|
| SRP | 0.068 | 0.051 | 0.064 | ✅ |
| OCP | 0.384 | 0.388 | 0.423 | ✅ |
| DIP | 0.654 | 0.714 | 0.857 | ✅ |
| LSP | 0.335 | 0.094 | 0.500 | ❌ |
| ISP | 0.133 | 0.000 | 0.000 | ❌ |

SRP, OCP and DIP over-flag all agents equally ⇒ **their rankings are valid.**
LSP and ISP are uneven, but produced 5 and 4 PR flags, never rankable anyway.

## What may be claimed

- **DIP (P=0.702)**, quote individual flags directly
- **OCP (0.394), LSP (0.264)**, rank agents; quote flags with care
- **SRP (0.064)**, a SCREEN. Rank agents ✅. Never call one class a violation ❌
- **ISP**, always print "18 positives" beside the rate

## Known residual issue
SRP judging drifted between passes; after a 480-row re-judge the two halves
still differ at **p=0.033** (was p<1e-9). Small, disclosed, changes no conclusion.

---

## DO NOT UNDO: `PERMANENT` was deliberately narrowed (2026-08-19)

`run_java.py` now has:

```python
PERMANENT = {"ok", "repo_too_large"}
```

It previously also contained `no_touched_files` and `no_delta_pair`. Both were
REMOVED on purpose. Do not put them back as a speed optimisation.

**Why.** `gitsnap.delta_pair` reaches `no_delta_pair` only after THREE git
fetches (merge SHA, the PR's own commits, `refs/pull/N/head`). Any transient
network failure therefore lands on the same status as "this PR genuinely cannot
be reconstructed", and a permanent status freezes that transient failure
forever, because the PR is never retried.

Measured directly: a 12-PR smoke run with extraction concurrently at 16 workers
lost 4 PRs to `no_delta_pair`, **Copilot 3/4, OpenAI_Codex 0/4**. Re-fetching
those exact SHAs by hand afterwards succeeded immediately. After the fix and a
re-run, 8 `ok` became 11 `ok`, and the agent bias disappeared.

The same effect showed at corpus scale: extraction pass 1 (16 workers) ended
with **1,931 `fetch_failed` files**; the quiet pass 2 (8 workers, nothing else
running) brought that to **15**.

`no_touched_files` is cheap to recompute (a file read, no network) but depends on
Step 2 having extracted the PR, so caching it can freeze a Step-2 gap too.
`repo_too_large` stays permanent because it is a deterministic size check.

**Operational rule that follows from this:** run the pipeline stages SERIALLY.
Do not run `run_java.py` while extraction is going, the contention manufactures
failures that look like data problems.

## Optional analysis not yet run
`vibebench/05_crosscheck/probe_layering.py`, read-only layering feasibility
probe. On the OLD corpus it found >=2 distinct layer tiers in 45/114 Python repos
(39.5%) and 10/25 Java repos (40.0%), dominated by `api/` and `core/`, which are
weak evidence of real layering discipline. A layering DETECTOR would need its own
judge-validation round because "wrong-way dependency" is a violation claim. Not
part of the current scope.

---

# FUTURE WORK, beyond the Java run

## A. Python SRP has a MUCH better operating point (found 2026-08-19, not yet shipped)

The shipped SRP rubric (`min_hits>=2`) is a high-recall screen: full-census
**P=0.064, R=1.000**. That is honest but it forbids ever calling one class a
violation.

The 2,625 labelled flagged classes were split IN HALF BY PR (no leakage),
thresholds chosen on one half, and measured ONCE on the other. Holdout results:

| operating point | precision | recall | flags |
|---|---|---|---|
| current (`min_hits>=2`) | 0.052 | 1.000 | 1,321 |
| `class_loc>=400` | 0.102 | 0.942 | 637 |
| **`loc>=400, wmc>=50, methods>=20`** | **0.183** | **0.884** | 334 |
| `loc>=600, wmc>=50, methods>=20` | 0.204 | 0.768 | 260 |
| `loc>=600, wmc>=50, methods>=30` | 0.301 | 0.681 | 156 |
| `loc>=1000, wmc>=120, methods>=30` | 0.360 | 0.580 | 111 |

Why it works: among FLAGGED classes, true violations are ~3x larger on every
axis (median class_loc 1,246 vs 379; methods 42 vs 14; wmc 171 vs 52). The
current bar is simply set too low.

**RECOMMENDED: report TWO operating points**, which is standard practice,
- *Screen* (current): P=0.05, R=1.00, "guarantees nothing is missed"
- *Tightened* (`loc>=400, wmc>=50, methods>=20`): **P=0.18, R=0.88**, the number
 to quote, 3.5x the precision for 12% of the recall

**Cost: ZERO re-run.** Every metric is already in `srp_results.parquet`; this is
a threshold change plus regenerating the report.
**Methods note:** thresholds were calibrated on labelled data and validated on a
held-out half, the same tuning+holdout protocol used everywhere else in this
project. State which half was used for what.
**Not yet applied to Java**, repeat the same split on the Java labels once they
exist; do NOT copy Python's numbers across.

## B. OCP churn signal (CLAUDE.md improvement programme #5, still open)

Java OCP recall was the weakest validated number (0.533 on the old corpus), and
validation diagnosed why: the false negatives are few-branch, author-owned
dispatch sets. **Openness is not a function of branch width.**

The proposed fix is recorded in Ch4 Finding 6b: **modification frequency**, a
class edited across many PRs is by definition not closed for modification. It is
width-INDEPENDENT, so it complements the structural rule rather than replacing
it, and both corpora already carry the change history.

Status: designed, not built. Would need its own label round before shipping.

## C. Thesis / defence (from CLAUDE.md, unchanged by this rerun)

- `thesis/` is LaTeX only, NOT on GitHub (gitignored, local only). Do not create
 `.md` chapter files.
- `presentation.tex`, Beamer defence deck, 24 frames.
- `AUTHOR_VOICE_QUESTIONS.md`, 4 passages awaiting the author's own words.
- Supervisor meeting + defence date: see CLAUDE.md; the author must confirm.
- **NOT backed up anywhere: `thesis/` and the root HTML guides.**

## D. Closed by decision, do NOT reopen
- Human expert labels (out of scope, decided 2026-08-04). The honest framing is
 "agreement with a reading of the principle, not ground truth".
- TypeScript, cancelled permanently. Do not work on it; do not spend effort
 deleting it either.
- `Google_Jules`, excluded, no track record in any earlier AIDev release.

## SHIPPED CODE FIXES, do not revert (they look like cruft, they are not)

- `labels/score_labels.py::coerce_labels()`, accepts `0/1/0.0/1.0/True/False/yes/no`
  and RAISES on anything unrecognised. The old one-line `.isin([0,1,"0","1"])`
  filter silently dropped all 4,425 SRP rows because the file stored `'0.0'`/`'True'`.
  A silent drop is the worst failure mode in this project: it prints a clean
  precision for a subset you did not choose. Never replace this with a filter.
- `labels/check_label_file.py::KEY_CANDIDATES` includes `type_id`. Without it the
  duplicate-entity check fires falsely on Java, where two types legitimately share
  one file path.
- `02_clone/clone_and_extract.py`, the `if not touched:` guard sits OUTSIDE the
  `needs_diff` branch, so a dataset row carrying an empty file list emits a
  `no_source_files` manifest row instead of vanishing. Silent disappearance is
  agent-biased, because the file-source route differs by agent.
