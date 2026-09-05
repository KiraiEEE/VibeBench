# VibeBench , AI Coding Agent Code-Quality Benchmark

**Purpose of this doc:** a self-contained brief so a new session (human or AI) can pick this
project up with zero prior context. If you're starting fresh, read this file first, then
`vibebench/config.py` (the single source of truth for paths/thresholds).

---

## 1. Research goal

Evaluate the **code quality of AI coding agents** , software architecture, SOLID design
principle adherence, code smells, and related SE quality metrics , using **real merged pull
requests from the AIDev dataset**, comparing agents (Claude Code, Copilot, Cursor, Devin,
OpenAI Codex , **all five since 2026-07-19**, see §2 Copilot note) against each other on
**objective, reproducible, tool-computed metrics**.

Scope is intentionally flexible , criteria may be added/removed as results come in. The
working method: **implement and fully validate one principle at a time**, on one language
first, before widening. Keep the system simple; avoid over-engineering.

## 2. The dataset (AIDev)

Location: `d:/VibeBench/AIDev-full/*.parquet`. Source: SAILResearch AIDev
(`https://github.com/SAILResearch/AI_Teammates_in_SE3`, arXiv:2507.15003), a fork that adds
full commit/repo data on top of the original AIDev dataset.

Key tables actually used by this pipeline:
- `repository.parquet` , `id, full_name, url, language, stars, forks, license`
- `pull_request.parquet` , `id, number, repo_id, repo_url, agent, merged_at, html_url, ...`
- `pr_commit_details.parquet` , per-file diff rows: `pr_id, sha, filename, status, additions,
  deletions, patch`. **This is where "AI-touched files" comes from.**
- `pr_timeline.parquet` , event log per PR; the `merged` event's `commit_id` is the merge SHA
  (used as a clone fallback).
- `pr_commits.parquet` , commit SHAs per PR (not currently used directly; timeline covers it).

### Verified facts that shaped the design (don't re-derive these, they're already checked)

- **116,003 unique repos, 931,785 PRs, 789,364 merged.** Merged-PR counts per agent are wildly
  imbalanced: OpenAI_Codex 714k, Copilot 29k, Cursor 23k, Devin 19k, **Claude_Code 3.6k**.
  Any per-agent comparison must sample, not use raw counts, or Codex dominates everything.
- **Community-adoption filter is aggressive.** Of 116,003 repos: 2,821 have ≥100 stars, 1,071
  ≥1,000, 461 ≥5,000. A "credible" gold set is naturally small (hundreds–low thousands of
  repos) , cheap to fully clone.
- **License is missing for ~65%** of repos (only 39,983/116,003 populated) , required filter
  for legal cloning + a reproducible/publishable benchmark.
- **Copilot has ZERO usable file-level attribution in the dataset.** `pr_commit_details
  .filename`/`status` are **null for 100% of Copilot rows** (vs ~99.9% populated for the other
  four agents) , an AIDev data-collection gap, not a code property.
  **RESOLVED (2026-07-19): Copilot is now included** , Step 2 derives its touched files from
  git itself: primarily `git diff --name-only merge_sha^1 merge_sha` (correct for merge AND
  squash merges); when the timeline has no merge SHA (48/150 sampled Copilot PRs), it
  reconstructs from the PR's own commit SHAs (`pr_commits.parquet`): union of each non-merge
  commit's diff vs its first parent (merge commits skipped so merged-in main changes never
  pollute attribution). Two shallow-clone traps cost a debugging cycle: parents must be read
  via `git cat-file -p` (`git rev-list` hides parents behind the shallow graft), and a plain
  base..head tree diff over-attributes whenever the agent merged main mid-PR.
- AI-generated code is stored as **diffs** (`patch` column), which is fine for *knowing which
  files/lines an agent touched* but too partial to run most static analyzers on directly. We
  always analyze **whole files at a real commit** (the PR head), using the diff only for
  attribution , never re-parse raw patches.
- PR head commits are recoverable via GitHub's retained ref `refs/pull/<number>/head`, **even
  after the source branch is deleted** , verified working. Falls back to the timeline's merge
  SHA if that ref is gone.

## 3. Key design decisions (and why)

| Decision | Choice | Rationale |
|---|---|---|
| Clone strategy | **Manifest-driven, scope-per-principle**: curate a small gold set, clone only what's needed per principle's declared scope (`file` vs `repo_delta`). SRP needs only file-scope, not full clones. | Gold set is small so full clones are affordable when needed later (DIP/ISP), but SRP doesn't even need them , extracting individual file blobs via `git show <sha>:<path>` is enough and much cheaper than a full checkout. |
| First principle | **SRP** (not DIP, despite TOOLS.md's own conclusion that DIP/ISP are "most automatable") | Pressure-tested during planning: DIP-at-scale needs either hand-written per-repo `import-linter` contracts (doesn't scale to hundreds of arbitrary repos) or Martin's A/I metrics (sparse signal on typical Python code) , and needs the heaviest harness (full clone + before/after delta) for slice #1. SRP has the **cleanest AI attribution** (score exactly the file/class the agent wrote, one commit, no repo graph), **simplest turnkey tooling**, and the **highest reader recognition**. Full order: **SRP → DIP → ISP → OCP → LSP**. |
| SRP definition | A **transparent numeric rubric** (class LOC, WMC, method count, LCOM, file LOC, max CCN, max params; flag when ≥2 thresholds crossed) **plus a semantic signal: C3 conceptual cohesion** (Marcus & Poshyvanyk, TSE 2008 , TF-IDF over per-method identifier/docstring vocabularies, mean pairwise cosine; deterministic, non-LLM). A rubric-flagged class with C3 ≥ `SRP_C3_SUPPRESS` (0.4) is unflagged: its methods demonstrably share one domain vocabulary , the "well-written large class" FP that size proxies can't see. A stricter `srp_risk_strict` (≥3 hits) is reported alongside. | Avoids pretending to judge semantic responsibility while still capturing a semantic cohesion axis; report raw distributions + two operating points so the paper survives threshold debates. Holdout (see §6) showed the flag is a high-recall screen, not a precise detector. |
| First language | **Python only** | Richest turnkey tooling (lizard+radon+ast all zero-config), proves the full pipeline fastest. TypeScript next (biggest slice of the dataset), then JS, then Java (best SOLID tooling but only ~3.5k repos here). |
| Tools | **lizard** (multi-language CCN/length/params , also covers future JS/TS/Java) + **radon** (Python MI/LOC) + custom **AST-based LCOM**. `pylint` OO checks as secondary signal, DPy/PyExamine as optional confirmation later. | Zero per-repo config, all local/deterministic/free, no GPU. |
| Star/license filter | `stars >= 25` (was 100 until 2026-07-19), license in a permissive allowlist (MIT, Apache-2.0, BSD-2/3, ISC, 0BSD, Unlicense, CC0-1.0, MPL-2.0) | Claude_Code's sample is dataset-bound (only 30 merged Python PRs existed at stars>=100; 42 at >=25) , the lower floor trades a little adoption-credibility for ~40% more small-agent PRs. Legal to clone/redistribute unchanged. |
| Balanced sampling | Keep every PR from small agents; cap large agents at `PER_AGENT_PR_CAP` (150), seeded random. **Sticky across re-curation**: PRs already in a previous gold set are kept first, new draws only top up. | Codex would otherwise be ~95% of the sample. Stickiness keeps clone caches and labeling samples valid when filters widen. |
| Attribution scope | **SRP: file/class scope** , analyze only the AI-touched files at the PR head commit, no delta needed. **DIP/ISP (later): before/after PR delta** on the AI-touched module subgraph , checks out parent + merge commits, reports the *change* the PR caused. Whole-repo metrics as a documented fallback for predominantly-AI-built repos. | SRP's causal claim is cleanest at file scope. Architecture principles need the delta to avoid conflating pre-existing human-written architecture with what the agent did. |

### A methodological fix worth knowing about (LCOM + constructors)

Naive LCOM computation (connect two methods if they share access to an instance attribute)
initially scored a **synthetic 3-responsibility god class as LCOM=0.0 ("fully cohesive")**,
because `__init__` touches every attribute and acts as a hub connecting all methods into one
component. Fixed by **excluding `__init__`/`__new__`/`__post_init__`** from the cohesion graph
(a documented convention in the LCOM literature) , the same synthetic god class then correctly
scores LCOM=0.4. See `vibebench/03_principles/srp/metrics.py::compute_lcom` docstring. If you
touch this function, re-run the inline sanity check (a clean 2-method class vs a 3-cluster god
class) before trusting new numbers.

Also non-obvious: **lizard's `long_name`/`name` are not class-qualified** (`"method_one(...)"`,
not `"Foo.method_one(...)"`). Methods are matched to their enclosing class by
`(function_name, start_line)` against the AST, not by name , `start_line` was verified to equal
`ast.FunctionDef.lineno` (the `def` line, decorators excluded) by direct test.

### Clone-harness resilience (learned the hard way)

The first full run of `clone_and_extract.py` crashed the entire 362-PR batch at PR #18
(`mlflow/mlflow`) because a single `git fetch` hung past a 180s timeout and the uncaught
`subprocess.TimeoutExpired` killed the whole process , losing nothing on disk (extraction is
resumable) but wasting the run. Fixed by: (1) catching timeouts per-git-call instead of letting
them propagate, (2) wrapping each PR's processing in try/except so one bad repo degrades to a
few `error`-status rows instead of aborting, (3) checkpointing the manifest to parquet every 25
PRs, (4) lowering the per-git-call timeout to 60s, (5) `flush=True` on progress prints so
background-run output is visible incrementally instead of buffered until exit. **Takeaway for
any future long-running network loop in this project: never let one item's failure be
unrecoverable for the whole batch.**

## 4. Pipeline architecture

```
vibebench/
  config.py                    # ALL paths + thresholds + policy knobs. Read this first.
  01_curate/curate.py           # Step 1: parquet -> corpus/gold_set.parquet
  02_clone/clone_and_extract.py # Step 2: git fetch PR heads -> corpus/ai_files/<pr_id>/...
                                 #         + corpus/clone_manifest.parquet
  03_principles/
    srp/metrics.py              # pure function: analyze_file(path, source) -> FileMetrics
                                 #   (incl. LCOM + C3 conceptual cohesion; standalone testable)
    srp/run_srp.py               # Step 3: manifest -> corpus/srp_results.parquet
                                 #   (applies SRP_THRESHOLDS + C3 suppressor + strict flag)
    dip/graph.py                 # pure functions: import graph, abstractness, cycles,
                                 #   Martin metrics over {module: source} dicts (self-check:
                                 #   run the file directly)
    dip/run_dip.py               # Step 3-DIP: before/after delta per PR around the merge
                                 #   commit -> corpus/dip_results.parquet (resumable,
                                 #   retries transient failures on re-run)
    ocp/dispatch.py              # pure functions: type-dispatch "switch statement" chains
                                 #   + add-vs-modify def profile over {module: source}
                                 #   (self-check: run the file directly)
    ocp/run_ocp.py               # Step 3-OCP: before/after delta per PR -> corpus/
                                 #   ocp_results.parquet (ISP-style harness, resumable)
    lsp/overrides.py            # pure functions: override signature-compat (mypy [override]
                                 #   rule replicated on AST) + type-variance + refused-bequest
                                 #   over {module: source} (self-check: run the file directly)
    lsp/run_lsp.py               # Step 3-LSP: before/after delta per PR -> corpus/
                                 #   lsp_results.parquet (OCP-style harness, resumable)
  labels/
    make_labeling_sample.py      # Step 4a: stratified sample for reference labeling
                                 #   (--holdout: fresh seed, excludes tuning rows)
    extract_label_batches.py     # Step 4a-helper: dump class sources into reviewable batches
    score_labels.py               # Step 4b: precision/recall vs LLM reference labels
                                 #   (--file to score the holdout)
  04_report/report.py            # Step 5: SRP aggregates + repo-mix controls -> CSVs
  04_report/report_dip.py        # Step 5-DIP: DIP delta aggregates -> CSVs
  corpus/                        # ALL GENERATED DATA (gitignore-worthy, large)
    gold_set.parquet
    clones/<owner>__<repo>/      # bare-ish local git dirs, one per repo, cached/resumable
    ai_files/<pr_id>/<path>      # extracted AI-authored file blobs at PR head
    ai_files/<pr_id>/_touched.json  # git-derived touched list (Copilot), cached
    clone_manifest.parquet
    srp_results.parquet
    dip_results.parquet
  labels/srp_labeling_sample.csv # 120-row TUNING set - LLM-labeled (Claude), NOT human
  labels/srp_holdout_sample.csv  # 60-row HOLDOUT - LLM-labeled (Claude), NOT human
```

Design principle: **each principle evaluator is a pure function over source text**
(`analyze_file(path, source) -> metrics`), decoupled from how the source was obtained. This is
what lets SRP run on individually-extracted files today while DIP/ISP later reuse the same
`corpus/clones/` git dirs for full-repo graph analysis, without changing the evaluator
interface.

## 5. How to run the pipeline (in order)

```bash
cd d:/VibeBench
python vibebench/01_curate/curate.py                 # -> corpus/gold_set.parquet (sticky)
python vibebench/02_clone/clone_and_extract.py        # -> corpus/ai_files/, clone_manifest.parquet
python vibebench/03_principles/srp/run_srp.py          # -> corpus/srp_results.parquet
python vibebench/labels/make_labeling_sample.py        # -> labels/srp_labeling_sample.csv
python vibebench/labels/score_labels.py                # P/R vs LLM labels (tuning set)
python vibebench/labels/make_labeling_sample.py --holdout --n-per-stratum 6  # fresh holdout
python vibebench/labels/score_labels.py --file srp_holdout_sample.csv        # out-of-sample P/R
python vibebench/04_report/report.py                   # -> srp_summary.csv, srp_by_class.csv, srp_within_repo.csv
python vibebench/03_principles/dip/run_dip.py          # -> corpus/dip_results.parquet (slow, resumable)
python vibebench/04_report/report_dip.py               # -> dip_summary.csv, dip_by_pr.csv
python vibebench/03_principles/isp/run_isp.py          # -> corpus/isp_results.parquet (resumable)
python vibebench/04_report/report_isp.py               # -> isp_summary.csv, isp_by_pr.csv, isp_interfaces.csv
python vibebench/labels/make_isp_labeling_sample.py    # -> labels/isp_labeling_sample.csv
python vibebench/labels/extract_isp_label_batches.py --file isp_labeling_sample.csv --out-dir vibebench/labels/isp_batches
python vibebench/labels/score_labels.py --file isp_labeling_sample.csv --pred-col isp_risk --label-col llm_isp_risk
python vibebench/labels/make_isp_labeling_sample.py --holdout   # then extract/label/score the same way
python vibebench/03_principles/ocp/run_ocp.py          # -> corpus/ocp_results.parquet (resumable)
python vibebench/04_report/report_ocp.py               # -> ocp_summary.csv, ocp_by_pr.csv, ocp_dispatch.csv
python vibebench/labels/make_ocp_labeling_sample.py    # -> labels/ocp_labeling_sample.csv
python vibebench/labels/extract_ocp_label_batches.py --file ocp_labeling_sample.csv --out-dir vibebench/labels/ocp_batches
python vibebench/labels/score_labels.py --file ocp_labeling_sample.csv --pred-col smell --label-col llm_ocp_risk
python vibebench/labels/make_ocp_labeling_sample.py --holdout   # then extract/label/score the same way
python vibebench/03_principles/lsp/run_lsp.py          # -> corpus/lsp_results.parquet (resumable)
python vibebench/04_report/report_lsp.py               # -> lsp_summary.csv, lsp_by_pr.csv, lsp_overrides.csv
python vibebench/labels/make_lsp_labeling_sample.py    # -> labels/lsp_labeling_sample.csv
python vibebench/labels/extract_lsp_label_batches.py --file lsp_labeling_sample.csv --out-dir vibebench/labels/lsp_batches
python vibebench/labels/score_labels.py --file lsp_labeling_sample.csv --pred-col violation --label-col llm_lsp_risk
python vibebench/labels/make_lsp_labeling_sample.py --holdout   # then extract/label/score the same way
```

Windows console note: some commit messages contain emoji; scripts don't print raw commit
messages, but if you add debug prints of dataset text fields, run with
`PYTHONIOENCODING=utf-8 PYTHONUTF8=1` to avoid `UnicodeEncodeError` on the default cp1252
console.

`clone_and_extract.py` is **resumable** , it skips PRs whose files are already extracted, so
re-running after a partial failure or an interrupted run is safe and cheap.

## 6. Current status (update this section as the project progresses)

**LABEL PROVENANCE (applies to everything below): all reference labels are LLM-labeled ,
produced by Claude reading each class's actual source. They are NOT human-expert labels, and
all P/R numbers are rubric-vs-LLM-judge agreement, not human ground truth. The CSV column is
`llm_srp_risk` to keep this explicit. Human expert labels remain future work; if obtained,
they go in a separate `human_srp_risk` column.**

- [x] **Step 1** , Gold set (2026-07-19 re-curation, sticky): **546 merged PRs, 147 repos,
      5 agents**: Claude_Code 37, Copilot 150, Cursor 81, Devin 128, OpenAI_Codex 150.
      All 362 previously sampled PRs retained (sticky sampling), so earlier labels/caches
      stayed valid.
- [x] **Step 2** , Clone/extract complete: **2,921 AI-touched `.py` files extracted ok**
      (Claude_Code 280, Copilot 513, Cursor 657, Devin 1,083, OpenAI_Codex 388); 434
      `fetch_failed` files are permanent attrition (verified: dai-motoki/haconiwa repo
      deleted from GitHub; appdotbuild/agent PR refs gone after repo transfer), plus 91
      `file_missing`, 53 Copilot PRs touching no Python. Every attempt is recorded in
      `clone_manifest.parquet`.
- [x] **Step 3** , SRP evaluator: **6,618 classes analyzed across 475 PRs, 20 parse errors.**
      Now includes **C3 conceptual cohesion** per class (semantic, non-LLM; see §3) and two
      flags: `srp_risk` (≥2 hits, C3-suppressed) and `srp_risk_strict` (≥3 hits).
- [x] **Step 4 , Tuning validation (120 LLM-labeled rows, 16 positives).** Baseline
      P=0.27/R=1.00 → after two principled fixes (LCOM saturation guard ≥4 methods;
      `file_loc` removed from class scope) + C3≥0.4 suppressor: **P=0.824 / R=0.875
      in-sample.** Labeling criterion: mixed *unrelated* responsibilities (God Class), not
      mere size.
- [x] **Step 4b , HOLDOUT validation (2026-07-19): the tuning numbers were overfit.**
      60 fresh stratified rows (5 agents, tuning rows excluded, seed `RANDOM_SEED+1000`),
      labeled the same way: **P=0.333 / R=1.000 out-of-sample.** Every real violation was
      caught (10/10 , e.g. browser-use `Controller`, mlflow `FileStore`, crewAI `Crew`,
      `AzurePlatform`), but 2/3 of flags were false positives , large-but-cohesive classes
      (test suites, template-method hierarchies like zfit `BaseModel`, single-domain engines
      like a 625-LOC rate-limiting `ModelClient`) that structural proxies cannot tell apart
      from god classes. Sensitivity (diagnostic only, NOT re-tuned): min_hits=3 →
      P=0.43/R=0.90; min_hits=4 → P=0.64/R=0.70. **Consequence: `srp_risk` must be presented
      as a high-recall screen; `srp_risk_strict` is reported as the higher-precision
      operating point. Any further rubric change requires a third fresh sample.**
- [x] **Step 5 , SRP report** (`04_report/srp_summary.csv`, 6,618 classes):

  | Agent | PRs | Repos | Classes | Risk rate (screen) | Risk rate (strict) | Macro (per-repo) | Top-repo share | Median C3 |
  |---|---|---|---|---|---|---|---|---|
  | Copilot | 78 | 38 | 1,081 | **12.4%** | 7.0% | 10.4% | 0.23 | 0.20 |
  | Cursor | 64 | 25 | 1,907 | **8.3%** | 4.9% | 13.8% | 0.44 | 0.37 |
  | Devin | 106 | 21 | 2,134 | **7.5%** | 5.2% | 12.3% | 0.66 | 0.20 |
  | Claude_Code | 28 | 22 | 737 | **7.3%** | 5.6% | 13.6% | 0.23 | 0.29 |
  | OpenAI_Codex | 93 | 20 | 759 | **3.7%** | 2.4% | 16.5% | 0.52 | 0.12 |

  **The repo-mix control changes the story**: pooled, Codex looks best (3.7%) , but
  macro-averaged per repo it is *worst* (16.5%), because its sample is concentrated
  (52% of classes from one repo, MontrealAI). Within-repo comparisons (repos with ≥2 agents,
  `srp_within_repo.csv`): mlflow Claude_Code 9.5% vs Copilot 6.1%; agentops Cursor ≈ Devin.
  Ranking claims should lead with macro + within-repo numbers, not pooled rates.
- [x] **DIP evaluator + report (first full run, 2026-07-19)** , before/after delta around each
      PR's merge commit (custom AST import graph + Martin A/I/D + cycles + abstract-dependency
      ratio; see `03_principles/dip/`). 389/546 PRs analyzable (70 touched no modules, 57 had
      no merge SHA, 30 fetch/archive failures). `04_report/dip_summary.csv`:

  | Agent | PRs | DIP-risk rate | Cycle-creating PRs | Mean ΔD | Macro DIP-risk | Top-repo share |
  |---|---|---|---|---|---|---|
  | Devin | 101 | **4.0%** | 1.0% | −0.033 | 7.2% | 0.29 |
  | Claude_Code | 32 | **3.1%** | 3.1% | −0.017 | 2.2% | 0.16 |
  | Copilot | 46 | **2.2%** | 0.0% | −0.070 | 1.7% | 0.20 |
  | OpenAI_Codex | 148 | **2.0%** | 0.7% | −0.036 | 4.6% | **0.78** |
  | Cursor | 62 | **0.0%** | 0.0% | −0.031 | 0.0% | 0.23 |

  Headline: **architectural deltas from single PRs are small and mostly benign** , mean
  distance-from-main-sequence *improves* slightly for every agent, and new import cycles are
  rare (5 PRs total). Codex's top_repo_share=0.78 (MontrealAI) makes its pooled number nearly
  meaningless , macro first.

  **DIP validation (2026-07-27) , the last principle to get a label loop.** 30-row stratified sample
  (all 9 flagged PRs + high-delta non-flagged + benign), judged from the surfaced import-graph delta
  (new internal edges + whether each target is an abstraction/concretion + new cycles;
  `labels/extract_dip_label_batches.py`). Result: **P=0.22 / R=0.67 / F1=0.33** (3 genuine violations).
  **Indicative, small-sample, but the qualitative finding is decisive: the `mean_d_worse` threshold is
  NOISE** , 4 of the 7 false positives were `mean_d`-worsening flags with ZERO new dependencies (the
  average distance moves because the module SET changed, not because a bad dependency was added). Both
  true positives came from the `new_cycle` hit (opendbc generic `packer`→8 concrete car modules + a new
  cycle; PocketManus `agent.react`↔`agent.toolcall`). One false negative below threshold , a genuine
  layering inversion (AGI-Alpha `core.orchestrator`→8 concrete `demos.*` agents). **Takeaway: keep
  `new_cycle`, treat `mean_d_worse` as low-value (drop or raise it).** All 5 principles now have a label
  loop. (The extractor uses the graft-safe parent read; run_dip.py itself still has the `rev-parse ^1`
  bug and its 26 fetch_failed , port the fix on a future re-run.)

- [x] **ISP evaluator + validation (2026-07-20)** , `03_principles/isp/` (stdlib-ast interface
      inventory: abc/protocol/implicit tiers, import-alias + repo-wide symbol table resolution,
      inherited abstract contracts, implementer coverage/stub stats, annotation-based client
      usage as raw-only secondary). DIP-style before/after delta harness; **398/546 PRs
      analyzable** (70 no_touched_modules, 57 no_merge_sha, 21 permanent fetch/archive attrition).
      A shallow-graft trap cost 24 PRs until fixed: `rev-parse SHA^1` fails when a later fetch
      re-grafts the shallow boundary at that commit , parents must be read via `git cat-file -p`
      (run_isp.py does; run_dip.py still has the latent issue, fix during DIP validation and
      retry its 26 fetch_failed).

  **Headline: zero PR-level ISP delta flags for every agent** (no PR created a flagged
  interface, widened a fat one, or introduced a forced-stub implementer) , matching the DIP
  finding that single-PR architectural deltas are small and benign. The signal lives in the
  head-state entity inventory: 203 in-scope interfaces (167 abc / 38 protocol / 21 implicit),
  543 implementer pairs, 15 flagged interfaces, 3 forced-stub pairs. fat_iface>10 ~= p93 of
  observed n_abstract_methods (p50=2, p90=6, p95=12, max=31), calibrated BEFORE labeling.

  **Validation (LLM-labeled by Claude, same provenance caveat as SRP):** tuning 74 entity rows
  (14 pos) -> P=1.000/R=1.000; fresh holdout 74->64 rows (4 pos) -> P=1.000/R=1.000. **Read the
  caveats before headlining:** (a) positives are dominated by entity duplication , the same
  interfaces recur across PRs (crewAI `BaseAgent` is 10 of 18 positives; also mlflow
  model-registry `AbstractStore`/`BaseRestStore`, gateway `ProviderAdapter`, datachain
  `AbstractUDF`) , ~6 distinct violating interfaces total, so precision evidence is thin;
  (b) the judge saw the rubric_flag in the review batches (same protocol as SRP labeling) ,
  anchoring risk; (c) the strongest genuine evidence is the 120 labeled NEGATIVES containing
  zero missed violations , the ≥2-hits design correctly passes wide-but-cohesive interfaces
  (zfit ZfitPDF 19 abstracts/coverage 1.0, template frameworks like zfit BaseModel, capability
  mixins like mlflow MultipartUploadMixin, oaklib's default-raising capability interfaces).
  A sampler bug found mid-validation: the holdout exclusion keyed on a column (`impl_id`)
  absent from the CSV, so it excluded nothing , first holdout draw was contaminated with
  tuning entities and was discarded; keys now use only columns present in both files
  (make_isp_labeling_sample.py::row_key).

- [x] **OCP evaluator + validation (2026-07-24)** , `03_principles/ocp/` (stdlib-ast
      type-dispatch detector: `isinstance`/`type()==`/`match`-class-pattern and value/tag
      `if-elif`/`match`-value ladders over one common subject; Robert Martin's canonical
      "switch statement" OCP anti-pattern). ISP-style before/after delta harness (same
      graft-safe `fetch_merge_pair`); **398/546 PRs analyzable** (70 no_touched_modules,
      57 no_merge_sha, 21 fetch/archive attrition). Two row types: `pr` (delta reasons
      `new_dispatch_smell`/`dispatch_widened` + a RAW add-vs-modify def profile that never
      drives the flag) and `dispatch` (every in-scope chain, so the branch-count threshold is
      calibrated non-circularly). Thresholds **calibrated on the first run BEFORE labeling**
      (621 in-scope chains: type-based p90=3, value-based p90=6) → `dispatch_min_branches=3`
      (also Martin's minimum switch), `value_switch_min_branches=6`.

  **Headline: OCP has real (if small) PR-level delta signal , unlike ISP's all-zero.**
  `04_report/ocp_summary.csv`, PR-level OCP-risk rate (pooled / macro per-repo):

  | Agent | PRs | Pooled | Macro | Smells in scope (head) | modify:add |
  |---|---|---|---|---|---|
  | Claude_Code | 33 | **6.1%** | 5.6% | 18 | 0.74 |
  | Cursor | 62 | **3.2%** | 2.2% | 32 | 0.53 |
  | Devin | 103 | **1.9%** | 1.9% | 47 | 0.59 |
  | Copilot | 52 | **0.0%** | 0.0% | 14 | 0.61 |
  | OpenAI_Codex | 148 | **0.0%** | 0.0% | 8 | 0.56 |

  The delta is dominated by `new_dispatch_smell` (a PR introducing a new wide switch) over
  `dispatch_widened` (editing an existing one). 119 of 621 head-state chains are flagged.

  **Validation (LLM-labeled by Claude, same provenance caveat as SRP/ISP):** branch-count rule alone ,
  tuning 80 rows (40 smell/40 not, 31 pos) → P=0.60/R=0.77; fresh holdout 54 rows (10 pos) → P=0.33/R=0.80.
  **2026-07-27 upgrade , a DETERMINISTIC closed-set suppressor** (`config.OCP_CLOSED_SET_SUPPRESS=True`,
  now shipped): a type-dispatch over all-builtins (JSON `str`/`dict`/`list`) or one fixed stdlib grammar
  (`ast.*`) is exhaustive-not-open → not flagged. Result: **tuning P 0.60→0.75, holdout P 0.33→0.44,
  recall kept** (every suppressed smell was a FP), head-state smells 119→100. **CONCLUSION: `smell` is a
  high-recall SCREEN whose FP ceiling is PART idiomatic (the all-builtin/`ast.*` slice, now cleared
  deterministically) and PART semantic (the remainder).** Read before headlining: (a) the residual
  error after suppression is the SEMANTIC FPs , mixed builtin+user-type normalization (`dict`-or-
  `BaseModel`), custom JSON encoders/serializers , where the open-vs-closed call needs meaning (the
  embedding attempt to clear these FAILED the gate, §7.A). (b) Recall is ~0.8, NOT perfect: the FNs are
  narrow but genuine factories/routers below threshold (2–5 branch LLM-provider factories, command (b) Recall is ~0.8, NOT perfect: the FNs are narrow but
  genuine factories/routers below threshold (2–5 branch LLM-provider factories, command
  routers, cloud-credential→upload dispatch, format→reader factories). (c) The strongest
  genuine positives are canonical: `instructor.from_provider` (17-provider factory),
  pioreactor's `struct.Action` dispatched in THREE functions (shotgun surgery), crewAI
  `Printer.print` (12 colors → `_print_<color>`), safety-tooling `model_id`→context-length
  and open-instruct `experiment_group` (21-branch lookup tables that should be data),
  levanter dataset-format and marimo `TransformType` codegen factories. (d) Anchoring risk:
  the judge saw `rubric_smell` in the review batches (same protocol as SRP/ISP).

- [x] **LSP evaluator + validation (2026-07-26) , the fifth and final principle.**
      `03_principles/lsp/` (stdlib-ast override-compatibility analyzer). For every internal
      subclass→nearest-ancestor override pair, three signal families: STATIC signature
      incompatibility (`param_required_added`/`param_removed` = a deterministic AST replication of
      mypy's `[override]` / Pyright's reportIncompatibleMethodOverride rule , chosen over shelling
      out to mypy because that needs a resolvable import environment per repo, the same grimp
      fragility DIP rejected); SEMANTIC type-annotation variance (`param_type_narrowed`
      contravariance / `return_type_widened` covariance, proven over the in-repo class hierarchy);
      BEHAVIORAL `refused_bequest` (a working base method overridden by a sole-`raise` body).
      Constructors excluded (not substitutable call sites). OCP-style before/after delta harness
      (same graft-safe `fetch_merge_pair`); **398/546 PRs analyzable** (70 no_touched_modules,
      57 no_merge_sha, 21 fetch/archive attrition).

  **Headline: zero PR-level LSP delta flags for every agent , LSP joins ISP as all-zero-delta.**
  All violating override pairs in the head-state inventory (of 1,188 in scope) pre-existed in
  the parent snapshot; no PR introduced a new incompatible override. This is the ISP mechanism: an
  LSP violation is a relation between a subtype and its (usually pre-existing) ancestor, so one PR
  rarely authors the whole pair. Head-state violating pairs by agent (post-suppressor): Devin 22,
  Claude_Code 9, Copilot 4, Cursor 2, OpenAI_Codex 1 (was 45/12/7/4/1 = 69 before suppression;
  density tracks which codebases the agent works in , Devin lives in crewAI's `Memory` hierarchy ,
  NOT the agent's own diffs). Post-suppressor signal frequency: param_removed 24, param_required_added
  11, refused_bequest 8, param_type_narrowed 1, return_type_widened 0.

  **Validation (LLM-labeled by Claude, same provenance caveat) , naive rule vs shipped suppressor:**
  the signature/variance rule alone is a high-recall screen (tuning 82 rows/16 pos → P=0.50/R=1.00;
  holdout 62 rows/5 pos → P=0.42/R=1.00). **The 2026-07-27 upgrade:** a DETERMINISTIC (no-ML) FP
  suppressor (`config.LSP_SUPPRESS_FPS=True`, now the shipped default) targeting the 4 FP categories
  → **tuning P=0.80/R=1.00/F1=0.89, holdout P=1.00/R=1.00/F1=1.00** (FPs 16→4 tuning, 7→0 holdout;
  ZERO true positives lost). **CONCLUSION: LSP is now a HIGH-PRECISION detector, not a screen , the
  one per-entity flag whose ceiling was IDIOMATIC, not semantic.** The 4 suppressed categories (all
  structurally detectable): (a) `*args/**kwargs`-only placeholder bases (plugin pattern , crewAI
  BaseTool._run, ray ResourceKillerActor._kill_resource); (b) variance against `Any`/unbound typevar
  (mypy treats Any compatible both ways); (c) `**kwargs`-absorbed OPTIONAL param drops; (d) factory
  classmethods (constructor-like). A liberal refusal check (a `del`/log line before an unconditional
  `raise` is still caught) closed the old sole-statement-raise FN (e.g. marimo sidebar.batch).
  **Notable:** several strongest TPs carry the devs' OWN `# type: ignore # Signature of "…"
  incompatible with supertype` comments (crewAI Memory.search/save) , rare human corroboration of a
  deterministic flag.

- [x] **TYPESCRIPT PORT , SRP end-to-end (2026-07-28). The second language begins.**
      Corpus (`curate.py --lang ts`): **682 merged TS PRs / 225 repos**, same policy knobs as
      Python (stars≥25, permissive license, cap 150/agent, seed 42) , Copilot/Cursor/Devin/Codex
      all reach the cap, Claude_Code 82. **TypeScript is the best-balanced corpus in AIDev** and
      fixes Python's structural weakness (Python: Codex 6,017 eligible vs Claude 42). Extraction
      6,240 files ok; analysis **624 PRs, 1,213 classes, 0 parse errors**.

  **Instrument.** A thin Node **sidecar** (`03_principles/ts_sidecar/analyze_srp.js`, ts-morph
  28.0.0 + typescript 5.9.3, both pinned with a committed lockfile) emits structural **facts as
  JSON**; **all metric math stays in Python**, reusing the already-validated cores , `compute_c3`
  and `compute_lcom` were refactored into `c3_from_token_bags()` / `lcom_from_access_sets()` so
  the TS evaluator runs the *same* arithmetic (the Python self-check still passes unchanged).
  Tool choice was made against our real constraints (bare extracted trees, no `npm install`, 225
  arbitrary repos, deterministic, free): CodeQL rejected (needs buildable code), SonarQube
  (server), ESLint/Biome (lint rules, not structural facts), tree-sitter (no type info , would
  kill the future LSP/ISP ports), Understand (commercial ⇒ irreproducible). `dependency-cruiser`
  is the recorded pick for DIP-TS. **Instrument pilot passed before the full run:** manual CCN
  verification against real source, **lizard agreed on every overlapping function**, and TSX +
  decorator-heavy classes parsed. (lizard's TS support is a documented *partial parser*, so it is
  used only as a cross-check, never as the primary instrument.)

  **Thresholds** were percentile-calibrated from the first run's 1,213-class distribution
  **before any labeling** (the non-circular OCP protocol), anchored near p90: class_loc 400,
  wmc 50, methods 14, ccn 17, params 6; the scale-free cohesion knobs kept their Python-validated
  values. Python's numbers were deliberately not copied , TS classes are much larger at the tails
  (Python's 200-LOC bar sits at TS p78).

  **Validation (LLM-judge, same provenance caveat).** Tuning 120 rows (16 pos) →
  **P=0.267 / R=1.000**; fresh holdout 59 rows (6 pos) → **P=0.207 / R=1.000**. Strict (≥3 hits,
  diagnostic only): tuning P=0.361/R=0.812, holdout P=0.250/R=1.000. **CONCLUSION: SRP-TS behaves
  like SRP-Python , perfect recall, semantic precision ceiling , but at LOWER precision (0.21 vs
  0.33).** Besides the familiar "large but cohesive" FPs, TS adds a language-specific family:
  **framework-shaped classes whose size is imposed by the framework**, not by mixed
  responsibility (Lit/React widget classes full of `getPropertyPaneConfig`/`getDefaults`
  boilerplate, and provider/SDK client classes that are wide by contract).

  **Results (macro per-repo first , the mix-controlled number):** Cursor **28.7%** (worst),
  Claude_Code 28.4%, Devin 26.3%, Copilot 18.6%, **OpenAI_Codex 8.1%** (best). Pooled: Cursor
  23.6%, Codex 14.6%, Devin 14.2%, Copilot 13.1%, Claude_Code 13.1%.
  **Headline cross-language finding: agent ranking is NOT stable across languages.** OpenAI_Codex
  is the *worst* agent on Python macro-SRP (16.5%) and the *best* on TypeScript macro-SRP (8.1%).
  Any claim of the form "agent X writes better-structured code" must therefore be qualified by
  language; this is a direct argument against single-language agent leaderboards.

  **Biggest methodological finding of the port: TypeScript agent code is 87% class-free**
  (0.24 classes/file vs Python's 3.22 , a 13× difference); only **184 of 624** analyzable PRs
  contained a class at all. A *class-scoped* SRP proxy has far less surface in TS/React/hooks
  code, so SRP-TS speaks for a smaller share of the corpus than SRP-Python does. Future work: a
  module- or function-scoped SRP proxy for TS (the same rubric applied to a "module" unit).

  **Perf/reliability pass shipped alongside** (user-requested): `clone_and_extract.py` gained
  `--lang` + `--workers` and now parallelizes across *repos* with a thread pool (one git dir is
  owned by one thread , git dirs tolerate no concurrent commands), and `run_srp_ts.py` uses a
  ProcessPool with **one sidecar call per PR tree** (process spawn is the overhead, so it is paid
  once per PR, not once per file). Checkpointing/resumability/timeout-lock-clearing conventions
  are unchanged. The validated Python harnesses were deliberately NOT retrofitted in the same
  change (never mix refactoring with new-feature work).

- [x] **OCP-TypeScript (2026-07-28) , the second TS principle, and the first TS *delta* harness.**
      Infrastructure: `03_principles/gitsnap.py` (new, shared) factors the harness helpers that had
      been copy-pasted into all four Python delta scripts and adds the long-deferred
      **per-(repo,sha) gzipped tree cache**, so the remaining TS principles never re-fetch a commit
      another principle already read; it is thread-safe (one lock per repo, because git tolerates no
      concurrent commands in a repo dir). `ts_sidecar/analyze_ocp.js` extracts dispatch chains;
      `ocp_ts/run_ocp_ts.py` runs the before/after delta over a thread pool and is resumable.

  **Detector.** Four chain kinds, chosen from what TypeScript actually does: `switch_tag`
  (discriminated-union `switch` , by far the dominant idiom, 337 of 545 chains), `value_switch`
  (if/else-if tag ladder), `instanceof`, and `typeof`. The **closed-set suppressor** is the direct
  TypeScript analogue of the Python all-builtins rule: a `typeof` ladder ranges over JavaScript's
  **eight language-fixed** typeof results, and an `instanceof` ladder over built-in constructors is
  likewise exhaustive , neither is the open-ended extension OCP guards against (18 of 545 chains
  suppressed). Thresholds were calibrated from the observed 545-chain distribution **before any
  labeling**: type-based p90=3 → 3 (also Martin's minimum switch, matching Python), tag-based p90=8
  → 8. The tag bar is deliberately higher than Python's 6 because TypeScript's discriminated-union
  switches are legitimately wider; copying Python's number would have flagged the median idiom.

  **Validation: tuning P=0.816/R=0.775; holdout P=0.800/R=0.800.**
  **CONCLUSION: OCP-TypeScript is a HIGH-PRECISION detector, where OCP-Python is only a screen
  (holdout P=0.44 even after its own suppressor) , nearly double the precision for the same
  principle.** The explanation is linguistic, and it matters: TypeScript's
  `switch (x.type)` over a string-literal union is a much cleaner signal of an open-ended variant
  family than Python's `isinstance` chain, whose false positives were dominated by *input
  normalization* (`dict`-or-`BaseModel`) , a job TypeScript does with type guards and union types
  instead, so it never enters the detector. Residual FPs are sets that look open but are closed by
  nature: the eight compass directions of a resize handle, modifier-key names, and DOM input
  normalization (`NodeList|string|HTMLElement`).

  **Results (macro per-repo first).** Claude_Code **10.2%** (worst), OpenAI_Codex 1.7%, Cursor
  1.3%, Devin 0.1%, **Copilot 0%** (best). Pooled: Claude_Code 6.3%, Codex 2.1%, Devin 0.9%,
  Cursor 0.8%, Copilot 0%. Head-state inventory: 71 smells among 545 in-scope chains.

  **Cross-language finding #2 , OCP's agent ranking replicates, where SRP's did not.**
  ⚠️ **Weakened by the 2026-07-28 audit; state it weakly or not at all.** It originally rested on
  two supports that the audit removed: after recovering the lost PRs, Copilot is **not** 0% in
  Python (1 of 85), and *pooled* Python now puts Cursor first. What survives is the macro
  number , Claude_Code highest in both (PY 5.3% / TS 10.5%) , on a base of **11 flagged PRs
  total**, which cannot carry a ranking claim.

  The robust half is the opposite direction: **agent ranking is NOT language-stable**, shown by
  SRP, where OpenAI_Codex flipped from worst in Python (macro 16.5%) to best in TypeScript
  (8.1%). A single cross-language "agent quality" ranking is not defensible; rankings must be
  qualified by principle *and* language, and reported with counts.

- [x] **The TypeScript port completed + a full-system audit (2026-07-28 → 08-01).**
  `AUDIT_2026-07-28.md` is the authoritative record , **read it before quoting any number**.
  13 findings; six changed published results. Every Python and TypeScript principle was
  re-run afterwards. The short version:

  **Corpus.** A missing merge SHA was silently dropping PRs and the loss was **agent-biased
  17×** (Copilot 22.7% → Codex 1.3%). `gitsnap.delta_pair` now reconstructs the before/after
  pair from the PR's own commits. Python delta principles: 398 → **452** analyzable PRs;
  TypeScript OCP: 516 → **618**. The 18 Python PRs still unextractable were traced to
  **upstream decay** , two repos deleted from GitHub, one history rewritten , not to the
  harness. That is a reproducibility bound worth stating: a PR-level benchmark decays.

  **Test-code confound.** Test classes are 34.7% of the Python corpus and agents differ ~4× in
  how much test code they write, so heavy-test agents were diluted. Reports now lead with
  production-only rates, and **this changed the Python SRP ranking**: Copilot loses "best" to
  Devin (its advantage was partly that it writes little test code). Codex stays worst.

  **The per-agent signal is tiny and must be reported as counts.** The entire Python OCP
  signal is **11 flagged PRs** across five agents; ISP is 2, LSP is 1. The defensible claim is
  "OCP produces a measurable per-PR signal where the inter-unit principles produce none",
  never a leaderboard.

  **Three ports needed their signal set re-derived, not just their parser swapped.**
  TypeScript methods are **bivariant** (probed with tsc 5.9.3 under `strict:true`: dropping a
  trailing param, narrowing a param, and making an optional param required all give **zero**
  diagnostics), so `param_removed` , safe in both the type system and the JS runtime, but a
  `TypeError` in Python , was dropped from LSP-TS. It had fired on 15 of 23 pairs. ISP-TS
  needed three scope cuts (behavioural members, ≥1 internal implementer, three stub-detection
  idioms), taking its inventory 3,733 → 648 → **109** and exposing the earlier
  "Claude_Code 11.5%" result as a React-`Props`-bag artefact (now 3 PRs of 78). DIP-TS was
  blind to monorepo self-imports, a loss ranging from **0% (vscode) to 61% (Effect-TS)**.

  **Validation added.** LSP-TS **P=0.727 / R=0.889 / F1=0.800** (41 rows) , high-precision,
  with OCP-TS. DIP-TS (40 rows) splits totally by signal: **`new_cycle` P=1.000 (5/5)**,
  **`mean_d_worse` P=0.000 (0/10)** , two languages now agree `mean_d` is noise, and all five
  true positives are the same **barrel-import cycle** shape, one per agent.
  ISP-TS **P=0.167 / R=1.000** (44 rows, tuning only, pre-fix) , a screen whose
  residual FPs are wide-but-cohesive service contracts, the same semantic wall SRP hit.

  **The headline results survive.** LSP-TS gives **zero PR-level flags for every agent**,
  replicating Python exactly, and DIP-TS finds 6 new-cycle PRs against Python's 5. The
  intra-vs-inter finding is now confirmed in two languages.

### Remaining open items
- Human expert labels (every sample in this project is LLM-labeled; a human should
  spot-check ~15–20 rows per principle minimum before publication , LSP partly corroborated by
  devs' own `# type: ignore` comments).
- ~~DIP rubric validation~~ **DONE 2026-07-27** (P=0.22/R=0.67, 30-row sample) , remaining: drop
  or raise the noisy `mean_d` threshold. DIP-TS independently reproduces the diagnosis
  (13 `mean_d_worse` vs 6 `new_cycle`), so the case for dropping it is now two-language.
- Holdout validation for ISP-TS and DIP-TS (samplers + extractors exist; LSP-TS is done at
  tuning stage, ISP-TS has a 44-row tuning sample, DIP-TS is unlabeled).
- A third fresh label sample if the SRP or OCP rubric is ever changed again (both holdouts are
  now burned as diagnostics).
- LSP-TS precision upgrade IF pursued: resolve base type *parameters* against the subclass's
  `extends` type arguments before comparing parameter types. Both of its false positives are
  that one idiom (`DOMRenderer extends Renderer<Node>` declaring `root: Node` where the base
  says `root: TRoot`), so it would reach ~0.9 , but it needs a re-run and a fresh sample.
- OCP precision upgrade IF pursued: a non-LLM branch-cohesion signal to separate open-ended
  type switches from closed structural recursion (the FP source) , the C3-suppressor pattern
  applied to OCP; more branch-count tuning cannot help (all residual error is semantic FPs).
- ~~**LSP is the next principle**~~ **DONE 2026-07-26** , all five principles are implemented
  and validated for Python, and all five now run on TypeScript.
- ~~Performance pass~~ **DONE with the TS port**: `gitsnap.py` factors the harness helpers and
  adds a gzipped per-(repo,sha) tree cache, clone/extract parallelises across repos, and the
  TS harnesses use thread/process pools. The four Python delta harnesses were migrated to
  `gitsnap` during the audit.

### A real bug found and fixed mid-pipeline (worth knowing about)

The first full Step 3 run scored `func_ccn`/`func_params` rubric hits using the **file-wide**
max complexity/param-count (`FileMetrics.max_func_ccn/max_func_params`) for every class row in
that file, instead of each class's **own** methods (`ClassMetrics.max_method_ccn/max_params`,
which were already computed but not read). This meant one huge unrelated function anywhere in
a file inflated the SRP-risk flag for every other class in it. Fixed in
`03_principles/srp/run_srp.py::srp_risk_hits` (now takes `func_ccn`/`func_params` as explicit
scoped arguments instead of reading file-level keys off the row dict). **Effect of the fix was
large**: risk rates dropped substantially on re-run (e.g. Cursor 54.5%→25.3%, Devin 24.7%→12.9%,
Claude_Code 43.3%→27.5%, Codex 29.1%→14.9%) , confirming the bug was a major distortion, not
noise. **Takeaway: always re-verify a metric pipeline's per-row scope matches the row's actual
subject before trusting aggregate output , a plausible-looking summary table is not proof the
underlying joins/scoping are correct.**

### A second bug class worth knowing about: killed git processes poison their repo

When a git fetch is killed on timeout (Windows especially), it can leave `.git/shallow.lock`
behind , and then **every subsequent git command in that repo fails instantly** with "Another
git process seems to be running". This silently converted one slow mlflow fetch into 114
`fetch_failed` files (more than half of Claude_Code's corpus) across two runs before being
diagnosed. Fix: `run_git` now clears `*.lock` under `.git/` after any timeout (safe because
the clone dirs are exclusively pipeline-owned). **Takeaway: a mass failure concentrated in
one repo is a repo-state problem, not a network problem , go look at the repo dir.**

**Immediate next action for a new session:** the pipeline is **complete for all five SOLID
principles / Python** (SRP+DIP+ISP+OCP+LSP, all 5 agents; SRP/ISP/OCP/LSP label-validated, DIP
now VALIDATED too). **All 5 principles now have a label loop (DIP was the last, 2026-07-27).**
**Precision work RUN 2026-07-27**: LSP deterministic FP suppressor SHIPPED (holdout P 0.42→1.00);
**OCP deterministic closed-set suppressor SHIPPED** (holdout 0.33→0.44); SRP embedding-C3, OCP
embedding branch-cohesion, and an SRP trained classifier all TESTED and REJECTED (see §7.A);
**DIP validated** (P=0.22/R=0.67, `mean_d` threshold shown noisy). Two HTML deliverables at repo root
are current as of 2026-07-27. **The TypeScript port then began 2026-07-28 and SRP-TS is complete
and validated** (682 PRs / 225 repos; holdout P=0.207/R=1.000; ts-morph sidecar; see §6). Next up,
in order of value: (1) **the remaining 3 TS principles** (LSP/ISP/DIP-TS) , the delta harness now
exists (`gitsnap.py` + `run_ocp_ts.py` as the template, tree cache already warm), so each needs
only a sidecar facts-extractor; LSP-TS should be *stronger* than Python's because the TypeScript
compiler gives real structural-subtype information, and DIP-TS can feed `dependency-cruiser` JSON
into the existing validated Tarjan/Martin code; (2) **DIP
`mean_d` cleanup** , validation showed it's mostly artifacts; drop/raise it, re-report on
`new_cycle`, and port the graft-safe parent read (already in the DIP extractor) into run_dip.py to
recover its 26 fetch_failed; (3) **human spot-checks** (~15–20 rows/principle) , all samples are
LLM-labeled; and more DIP labels (its sample is only 30 rows / 3 positives); (4) **a module- or
function-scoped SRP proxy for TS** , 87% of TS files have no class at all, so the class-scoped
rubric only speaks for a minority of the corpus; (5) **a better instrument for SRP/OCP precision**
, a different encoder (StarCoder2/UniXcoder) or a classifier once labels are thicker +
human-checked, NOT another GraphCodeBERT pass; needs a fresh 3rd sample (both holdouts burned);
(6) the deferred **shared snapshot cache** , DIP/ISP/OCP/LSP still re-fetch and re-archive the same
commits independently; `config.TREE_CACHE_DIR` is reserved and a factored
`03_principles/gitsnap.py` is the obvious win (the TS port already parallelizes fetch + analysis).

## 7. Roadmap after SRP/Python is validated

1. ~~Extend SRP to TypeScript~~ **DONE 2026-07-28** (see §6) , implemented as a pinned **ts-morph
   sidecar** emitting structural facts + the *reused* Python C3/LCOM cores, not a rewritten
   evaluator. lizard turned out to be the wrong primary instrument for TS (partial parser) and is
   kept only as a cross-check. Remaining TS work: the other four principles, and a module-scoped
   SRP variant for the 87% of TS files that contain no class.
2. ~~Move to **DIP**~~ **DONE 2026-07-19** , implemented as `03_principles/dip/` with a
   custom AST import-graph builder rather than grimp (grimp needs importable packages on
   sys.path per repo , fragile across 147 arbitrary repo layouts; the custom builder handles
   absolute/relative imports + longest-prefix resolution and has an inline self-check).
   Delta harness diffs merge_sha^1 vs merge_sha. Thresholds still need validation.
3. ~~Then **ISP**~~ **DONE 2026-07-20** (see §6). ~~Then **OCP**~~ **DONE 2026-07-24**
   (see §6) , implemented as `03_principles/ocp/` with a type-dispatch "switch statement"
   detector (isinstance/type_eq/match/value chains) over the same ISP-style before/after
   delta harness; primary delta reasons `new_dispatch_smell`/`dispatch_widened`, plus a raw
   add-vs-modify def profile. Thresholds calibrated on the first full run, label-validation
   pending. Then **LSP** (per TOOLS.md's conclusion that it is, with SRP, the hardest to
   automate , mypy/Pyright override-compatibility is the main signal).
4. ~~Revisit Copilot inclusion~~ **DONE 2026-07-19** , see §2.
5. Consider Java as a 4th language once JS/TS are done , best SOLID-specific tooling
   (ArchUnit, DesigniteJava, PMD) but the smallest sample in this dataset (~3.5k repos).

### Position: static vs semantic vs ML/LLM (updated 2026-07-26 , read before "improving" the metrics)

**Policy change (2026-07-26): LLM/AI models are ALLOWED inside metrics.** The earlier "no LLM
inside a metric, ever" rule is retired. It began partly as a cost constraint (no LLM-API budget)
and partly as a methodology preference; the owner has lifted the ban. The *preference* that
remains is a recommendation, NOT a prohibition:

- **Deterministic is the DEFAULT, for reproducibility + explainability** , every headline number
  today is a pure function of source text, re-runnable to identical output and auditable to the
  exact rule that fired. Keep a deterministic baseline reported alongside any learned metric so a
  reader can always reproduce *something*.
- **LLM/ML signals are permitted** , as a validated secondary signal (the C3-suppressor pattern)
  OR as a primary metric where it clearly wins. **Prefer LOCAL models** (code embeddings; small
  local LLMs via Ollama/llama.cpp) over paid APIs: free, repeatable, and runnable on modest
  hardware (a 4GB-VRAM GPU comfortably runs GraphCodeBERT/CodeBERT/StarCoder2 embeddings and
  3–7B quantized LLMs). If a hosted API is used, pin the model version and cache outputs so a run
  is reproducible.
- **Whatever the metric, VALIDATE it** the same way (tuning + fresh holdout vs labels) and report
  its P/R. An LLM/ML metric that isn't validated is not shippable , that discipline is unchanged.

What each principle currently uses (still all deterministic , the door is now open, not yet walked
through):

- **SRP = structural metrics + ONE semantic (non-ML) signal.** lizard/radon/AST-LCOM are
  static-structural; C3 conceptual cohesion is *lexical semantics* (TF-IDF + cosine over
  method identifier/docstring vocabularies , classical IR, deterministic, no model). Claude is
  currently used only as the *label judge* during validation.
- **DIP = purely static** (AST import graph, Martin A/I/D, cycles, abstraction-dependency
  ratio).

**Where the remaining error lives (holdout evidence):** SRP recall is 1.00 , structure misses
nothing. ALL residual error is false positives (P=0.33), and reading those 20 FPs shows they
are uniformly "large but single-topic" classes (test suites, template-method hierarchies,
single-domain engines). Telling those apart from god classes is by definition a semantic
judgment, so **semantic signal is the only route to better SRP precision** , more structural
thresholds cannot get there.

**GATE RUN 2026-07-27 , RESULT (the upgrades were coded, gated, then actually run against the labels):**
a shared `03_principles/embeddings.py` (local GraphCodeBERT, guarded/optional, deterministic, cached)
plus three gated hooks. torch 2.13.0+cpu + transformers 5.14.1 installed; GraphCodeBERT cached in
`corpus/embed_cache.sqlite`. Outcome:
1. **LSP FP suppressor (`LSP_SUPPRESS_FPS`) , PASSED, now SHIPPED DEFAULT (True).** Deterministic, no
   ML. Holdout P 0.42→**1.00**, recall kept. See §6.
2. **SRP embedding-C3 (`SRP_C3_MODE="embed"`) , FAILED the gate, reverted to "tfidf".** Ran GraphCodeBERT
   over all 6,618 classes; mean-pairwise embedding cosine does NOT separate god classes (median 0.934)
   from cohesive ones (0.954) , the label distributions overlap almost completely and recall collapsed
   to 0. **TF-IDF (shared domain vocabulary) beats the dense encoder for conceptual cohesion** , a real
   negative result. `compute_c3_embed` stays wired but off.
3. **OCP embedding branch-cohesion (`OCP_BRANCH_COHESION_SUPPRESS`) , FAILED, kept None; BUT a
   deterministic closed-set suppressor SHIPPED instead.** Embedding diagnostic: closed-set FPs have
   *lower* mean branch cohesion (0.842) than genuine smells (0.860), premise INVERTED, no threshold works.
   The DETERMINISTIC alternative , `config.OCP_CLOSED_SET_SUPPRESS=True`, `dispatch.py::is_closed_type_set`
   (all-builtins / one fixed stdlib grammar like `ast.*` = exhaustive, not open) , **PASSED: tuning P
   0.60→0.75, holdout 0.33→0.44, recall kept**, head-state smells 119→100. Clears the finite-idiom FP
   slice; the mixed builtin+user-type remainder stays semantic (unrecoverable by the embedding).
4. **SRP trained classifier (LogReg/RF/GB over the metric vector, sklearn, 5-fold CV) , FAILED, not
   shipped.** No precision gain at SRP's high-recall design point (~0.34-0.43 at R≈1.0 vs the rule);
   feature importance shows it only re-weights size (class_loc/wmc), no new semantic signal. Thin labels
   (26 pos) + reproducibility cost. Rule + TF-IDF C3 stays shipped.

**Why the ML lost (and where deterministic won):** an off-the-shelf masked-LM code encoder compresses all
Python source into a narrow ~0.85–0.99 cosine band that drowns the axes that matter (SRP "shared
responsibility vocabulary", OCP "open vs closed type set"); a thin-label classifier finds only size. But
where the residual FPs are a FINITE, nameable idiom (all-builtin closed sets for OCP; the 4 LSP
escape-hatches) a DETERMINISTIC rule clears them cleanly , which is exactly the LSP (full) and OCP
(partial) wins. The purely-semantic remainder (SRP; OCP's mixed-type slice) resisted every tool tried.

**Remaining sanctioned path (harder, gated the same way):**
1. **A small classifier over the metric vector** for SRP/OCP , only after more labels + human spot-checks
   (today's ~26 positives are too thin to train on honestly). Or a **different encoder** (StarCoder2 /
   UniXcoder) if one demonstrably separates the axes GraphCodeBERT couldn't. Both SRP/OCP holdouts are
   BURNED, so any headline change there needs a fresh 3rd sample.
2. **DIP: mypy/Pyright type resolution** (is the depended-upon name an abstraction *as used*) , semantic
   but static/deterministic. **ML is the wrong tool for DIP**: the property is graph-structural and
   positives are too sparse to train on (5 cycle-creating PRs in 389). DIP's bottleneck is *threshold
   validation on sparse events* (labeling), not signal richness.

**Two standing caveats (now that LLM/ML is allowed):** (a) any learned component is validated
against LLM-produced labels, so "higher agreement" is not automatically "more accurate" , human
spot-checks become MORE important once ML/LLM enters, and consider a second local-LLM judge to
cross-check the labels; (b) reproducibility is a *recommendation* not a gate , a deterministic
baseline should stay reported alongside any learned metric, and hosted-API metrics should pin the
model version + cache outputs so a run stays repeatable. On weak hardware (e.g. RTX 3050 4GB), the
highest-ROI AI addition is **local code embeddings** (GraphCodeBERT/StarCoder2) for the SRP/OCP/LSP
precision upgrades , small, fast, deterministic-at-inference, no API cost.

## 8. Reference: full tool list considered

See `TOOLS.md` in the repo root for the exhaustive 40+ tool survey (by principle, with
strengths/limitations/maintenance status) that informed the choices above. Notable additions
adopted beyond that list: **import-linter + grimp** (Python DIP), **Madge** (lighter JS/TS dep
graph than dependency-cruiser), **Lizard** (multi-language complexity, used for SRP now).
