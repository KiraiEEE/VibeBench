"""
Shared configuration for the VibeBench pipeline.

Single source of truth for paths and the curation policy so every stage
(01_curate -> 02_clone -> 03_principles -> 04_report) agrees on the corpus.

Keep this file boring and declarative. No logic beyond path resolution.
"""
from __future__ import annotations

from pathlib import Path

# --- Paths -----------------------------------------------------------------
# Repo root = the folder that contains this file's parent (vibebench/).
VIBEBENCH_DIR = Path(__file__).resolve().parent
ROOT_DIR = VIBEBENCH_DIR.parent                     # d:/VibeBench
# --- Source dataset --------------------------------------------------------
# SWITCHED 2026-08-17: AIDev-full -> AIDev-7.6M (see RERUN_LOG.md for the full
# provenance record and the measured comparison).
#   AIDev-full : 932,791 PRs / 116,211 repos.  Claude_Code had 66 eligible
#                Python PRs, so its corpus was dataset-bound at 37.
#   AIDev-7.6M : 7,685,281 PRs / 957,209 repos, cutoff 2026-03-31, 6 agents.
#                Claude_Code eligible Python 26,055.
# DELETED 2026-08-27: the old AIDev-full download is gone. V1 is cancelled, so
# nothing will regenerate it, its RESULT parquets are preserved for the V1-vs-V2
# comparison, and the raw dataset is re-downloadable if ever needed. The OLD results stay
# in corpus/ until the new run is accepted -- nothing is deleted.
#
# ⚠️ TABLE NAMES DIFFER BY DATASET AND THIS ALREADY CAUSED ONE WRONG ANALYSIS.
# In AIDev / AIDev-2.7M / AIDev-7.6M, `pull_request.parquet` and
# `repository.parquet` hold ONLY the >100-star "AIDev-pop" subset; the master
# tables are `all_pull_request.parquet` / `all_repository.parquet`. AIDev-full is
# the exception (its fork expanded pull_request.parquet to ~the full set), so
# reading the same filename across datasets silently under-counts the newer ones.
# PR_TABLE / REPO_TABLE below make the choice explicit instead of implicit.
DATA_DIR = ROOT_DIR / "datasets" / "AIDev-7.6M"     # the parquet dataset
PR_TABLE = "all_pull_request.parquet"               # master PR table
REPO_TABLE = "all_repository.parquet"               # master repo table
DATASET_NAME = "AIDev-7.6M"                         # for the methods note
DATASET_PULLED = "2026-08-17"                       # snapshot download date
DATASET_CUTOFF = "2026-03-31"                       # upstream collection cutoff

CORPUS_DIR = VIBEBENCH_DIR / "corpus"
CLONES_DIR = CORPUS_DIR / "clones"                  # per-repo git checkouts (cache)
EXTRACT_DIR = CORPUS_DIR / "ai_files"               # extracted AI-touched files
LABELS_DIR = VIBEBENCH_DIR / "labels"
REPORT_DIR = VIBEBENCH_DIR / "04_report"

GOLD_SET = CORPUS_DIR / "gold_set.parquet"          # output of 01_curate
CLONE_MANIFEST = CORPUS_DIR / "clone_manifest.parquet"  # output of 02_clone
SRP_RESULTS = CORPUS_DIR / "srp_results.parquet"    # output of 03_principles/srp
DIP_RESULTS = CORPUS_DIR / "dip_results.parquet"    # output of 03_principles/dip
ISP_RESULTS = CORPUS_DIR / "isp_results.parquet"    # output of 03_principles/isp
OCP_RESULTS = CORPUS_DIR / "ocp_results.parquet"    # output of 03_principles/ocp
LSP_RESULTS = CORPUS_DIR / "lsp_results.parquet"    # output of 03_principles/lsp

# --- Curation policy (Step 1) ----------------------------------------------
# First slice targets Python only. lizard already supports JS/TS/Java for later.
TARGET_LANGUAGES = ["Python"]

# The 3-agent set, applied to BOTH languages as of 2026-08-17 (previously Python
# took every agent and Java took OpenAI_Codex only). Same three in both corpora
# makes the cross-language comparison a clean factorial design; every
# cross-language claim is only ever computable on agents present in both anyway.
# Cursor and Devin are excluded on Java data volume (n=94 with 72.3% in its top-3
# repos, and n=6) -- their Python results from the prior run are KEPT as a
# supplementary single-language observation, not deleted. Google_Jules is excluded
# as unvalidated: it is new to this dataset generation with no track record in any
# earlier AIDev release, so its PR-attribution would need validating from scratch.
PY_ONLY_AGENTS = ["OpenAI_Codex", "Copilot", "Claude_Code"]

# Community-adoption floor. Lowered 100 -> 25 in 2026-07 as a sample-size fix,
# back when Claude_Code had 42 eligible merged Python PRs in the whole of
# AIDev-full and the small-agent samples were dataset-bound.
#
# THE ORIGINAL JUSTIFICATION NO LONGER HOLDS, and the counts it quoted are from
# a dataset this project no longer uses. On AIDev-7.6M, measured 2026-08-25 at
# stars>=25 with a permissive license, eligible MERGED Python PRs are
# Claude_Code 26,055 / Copilot 9,097 / OpenAI_Codex 14,276, so no agent is
# anywhere near dataset-bound and the floor could be raised without costing
# sample size.
#
# It is KEPT AT 25 anyway, deliberately: changing the star floor at the same
# time as switching to an outcome-balanced corpus would make every old-vs-new
# difference uninterpretable, since two variables would have moved at once. If
# a jury objects that 25 stars is a low bar for "real project", the answer is a
# star-band sensitivity check in the report, not a silent re-cut of the corpus.
MIN_STARS = 25

# Permissive licenses only: legal to clone + redistribute a reproducible benchmark.
# Values match the `license` column (SPDX-ish) in repository.parquet, case-insensitive.
PERMISSIVE_LICENSES = {
    "mit", "mit-0", "apache-2.0", "bsd-3-clause", "bsd-2-clause",
    "0bsd", "isc", "unlicense", "cc0-1.0", "mpl-2.0",
}

# Balanced sampling: keep every PR from small agents, cap the large ones so a
# single agent (OpenAI_Codex) cannot dominate the comparison. Seeded for repro.
#
# RAISED 150 -> 500 on 2026-08-17, with the dataset switch. The cap was never
# what limited the weakest agent -- the DATASET was (Claude_Code had 66 eligible
# Python PRs in AIDev-full, so it landed at 37 regardless of the cap). Now that
# every agent in the 3-agent set clears 500, the cap finally does what it is for.
# WHY 500 AND NOT MORE: the binding scientific motive for this re-run is that
# four of five principles produced single-digit PR-level flag counts, making
# per-agent claims impossible. More PRs is the direct fix, and LABELLING COST
# DOES NOT GROW WITH CORPUS SIZE (samplers draw a fixed --n-per-stratum), so the
# extra cost is unattended compute only. The real ceiling is DISK: clones run
# ~70 MB/repo and 114 GB was free at the time of writing.
PER_AGENT_PR_CAP = 500
RANDOM_SEED = 42

# --- V2: OUTCOME-BALANCED CORPUS (2026-08-25) -------------------------------
# V1 curated MERGED PRs only. Merged PRs passed human review, so the corpus was
# filtered by the very quality signal the study measures -- survivorship bias,
# and a legitimate attack on the result. V2 adds PRs that were NOT merged so
# every rate can be reported merged-only, not-merged-only and pooled.
#
# OUTCOME derivation (vectorised, never row-wise .apply -- 7.6M rows):
#     merged   : merged_at is not null            6,216,792  80.9%
#     rejected : state == "closed" and not merged    733,321   9.5%
#     open     : state == "open"                     735,168   9.6%
# Verified against all_pull_request.parquet 2026-08-25.
#
# WHICH OUTCOMES GET BALANCED CELLS. Java `open` is too thin to balance
# (OpenAI_Codex has 83), so balancing three outcomes would crush every Java cell
# to 83. Java therefore balances merged vs rejected only and carries `open` as a
# supplementary, unbalanced observation. Python matches Java's design so the
# cross-language statement stays clean.
V2_OUTCOMES = ["merged", "rejected"]          # the balanced axis
V2_SUPPLEMENTARY_OUTCOMES = ["open"]          # carried, never in the headline

# Per (agent x outcome) CELL cap, the V2 replacement for PER_AGENT_PR_CAP.
#
# THE CAP IS PER LANGUAGE BECAUSE THE CEILING IS PER LANGUAGE, and conflating
# the two would throw away an order of magnitude of Python data for no reason.
#
# JAVA is DATASET-BOUND, not policy-bound. Measured 2026-08-25 on the real
# curation path, the eligible rejected pools are Claude_Code 485, Copilot 1,272,
# OpenAI_Codex **315**. That last cell is the binding constraint on a balanced
# 3-agent x 2-outcome design and it cannot be raised without dropping a filter,
# so 300 sits just under it and no cell is capped by policy below what the data
# allows.
#
# PYTHON is not bound at all: its smallest cell is Copilot rejected at 3,716.
# Running Python at Java's 300 would leave a factor of 12 unused. 750 is chosen
# rather than the ceiling because the cost that actually scales is unattended
# compute (clone, fetch, parse), while LABELLING COST DOES NOT SCALE AT ALL --
# the samplers draw a fixed --n-per-stratum, and the census design enumerates
# the flagged side, which grows only with the number of flags, not with corpus
# size. 750/cell gives 4,500 Python PRs against V1's 1,786.
CELL_PR_CAP = 300               # Java, and the default (legacy equal-cell mode)
CELL_PR_CAP_PY = 750            # Python, where the pool allows far more

# --- V2.1 DESIGN: WITHIN-AGENT OUTCOME BALANCE (2026-08-26) -----------------
#
# SUPERSEDES the equal-cell design above as the default. The equal-cell design
# capped every cell at the SMALLEST cell in the whole corpus (OpenAI_Codex
# rejected = 315), which threw away most of the eligible pool to buy equal n
# ACROSS agents -- a control the reporting already provides by other means.
#
# THE ARGUMENT, stated so it can be checked rather than trusted:
#   * The comparison that genuinely needs equal n is merged vs rejected WITHIN
#     one agent, because that is the V2 contrast and unequal n there costs power
#     and invites a "you compared 300 to 1,272" objection.
#   * The comparison ACROSS agents is made on macro-per-repo RATES, and a rate
#     is not biased by its denominator. Equal n across agents buys only equal
#     precision, and it costs an order of magnitude of data to buy it.
#   * REJECTED IS THE SCARCE SIDE in every agent, so taking all of it and
#     matching merged to it maximises the corpus at exactly the point where the
#     study is data-limited.
#
# Measured eligible pool, Java, 2026-08-26 (merged / rejected):
#   Claude_Code 1,964 / 485    Copilot 2,676 / 1,272    OpenAI_Codex 1,989 / 315
# so the balanced corpus is 970 + 2,544 + 630 = 4,144 PRs, against 1,800 under
# equal cells, plus ~740 supplementary `open`.
#
# A side effect worth naming: taking ALL rejected PRs automatically captures
# every `changes_requested` PR that exists in the eligible pool, which is the
# only stratum carrying an explicit maintainer objection and is far too small to
# sample for.
V2_WITHIN_AGENT_BALANCE = True

# OVER-DRAW ON THE MERGED SIDE, so within-agent balance survives attrition.
#
# The rejected side CANNOT be over-drawn: the design already takes every
# eligible rejected PR, so its pool is exhausted by construction. Step 2 then
# drops PRs that turn out to touch no Java (the git_diff route is not
# pre-filtered), and the two sides do NOT attrit equally -- merged PRs carry
# exact file lists far more often, so they survive better. Drawing merged
# 1:1 with rejected would therefore leave MORE merged than rejected analysable,
# and the balance the design exists to create would be lost silently between
# curation and analysis.
#
# Drawing merged at 2x lets the balance be restored on ANALYSABLE PRs, which is
# the population every rate is actually computed over. The surplus is not
# wasted: it improves precision on the merged side and enlarges the head-state
# inventory ("violations PRESENT"), which is the denominator of the study's
# headline contrast.
#
# WHY 2x AND NOT THE WHOLE ELIGIBLE POOL. The full pool would give 6,629 merged
# against 2,072 rejected, roughly 9,400 PRs, but the marginal scientific value
# of merged PRs beyond 2x is small (that side is already well powered) while the
# compute cost is linear in PRs and dominated by two JavaParser passes per PR.
# 2x is where extra data stops buying extra confidence.
MERGED_OVERDRAW = 2.0

# Hard ceiling per (agent, outcome) cell. A COMPUTE guard, not a design choice,
# so it is set above what the design actually asks for and should never bind.
# It bound once at 1,500 (Copilot's merged side wants 2x1,272 = 2,544) and
# silently clipped that agent, which is exactly the kind of quiet truncation a
# cap should not do.
CELL_HARD_CAP = 2600

# DRAW PRIORITY, applied inside each repository bucket of the stratified draw.
# The repository round-robin still controls concentration; this decides WHICH of
# a repository's PRs are taken, and it is where three jury attacks are answered:
#
#   sticky        already extracted, so re-curation never discards paid work
#   dataset       exact file list, ~0% Step-2 attrition (vs the git_diff route)
#   multi_agent   the repository contains PRs from >= 2 agents, which is what
#                 makes a WITHIN-REPOSITORY cross-agent comparison possible.
#                 Measured: only 13 of 730 repositories held all three agents
#                 and 6.7% of PRs sat in them, so a cross-agent claim was almost
#                 entirely a BETWEEN-PROJECT claim. 111 eligible repositories
#                 have >= 2 agents, holding 2,266 eligible PRs.
#   recent        newest first. The agents worked in different ERAS (median PR
#                 date OpenAI_Codex 2025-07-10 against Claude_Code 2026-02-21),
#                 so a cross-agent comparison was partly a comparison of
#                 repository eras and of older agent versions. Preferring recent
#                 PRs narrows that gap AND measures the agents as they are now,
#                 which is what the study is actually about.
V2_DRAW_PRIORITY = ["sticky", "dataset", "multi_agent", "recent"]

# A BALANCED CORPUS IS NOT A REPRESENTATIVE ONE. 50/50 merged-to-rejected
# maximises power for the COMPARISON, but the real population is 80.9% merged.
# Any PER-OUTCOME rate is valid as drawn; any OVERALL "agents violate X% of the
# time" figure computed on the balanced corpus is wrong for the real world and
# must be reweighted to the natural mix or explicitly labelled balanced-design.
# These are the weights for that reweighting.
V2_NATURAL_MIX = {"merged": 0.809, "rejected": 0.095, "open": 0.096}

# REJECTION STRATA. "Rejected" does NOT mean "rejected for quality": measured on
# the eligible pool, rejected PRs are HALF as likely to have any recorded review
# (Java 10.8% vs 25.3% merged; Python 6.2% vs 12.1%). Most were closed with no
# review at all -- superseded, stale, duplicate, abandoned or bulk-closed. So
# not-merged is stratified and the strata are reported separately; a bare
# merged-vs-rejected violation rate is never published as if rejection meant
# quality refusal.
#   changes_requested   : has a CHANGES_REQUESTED review  (strongest signal)
#   reviewed_then_closed: has some review, closed unmerged
#   closed_unreviewed   : no review recorded              (the majority)
V2_REJECTION_STRATA = ["changes_requested", "reviewed_then_closed", "closed_unreviewed"]

# THE DATASET'S REVIEW TABLE IS NOT THE LAST WORD, AND ITS GAP IS AGENT-BIASED.
# `pr_reviews` covers 6-11% of rejected PRs, and `pr_timeline`'s close event is
# worse: Claude_Code 2.8% against OpenAI_Codex 64.4% in Java. Deciding the
# strata from it alone would have decided them by metadata coverage rather than
# by what happened. `06_enrich/fetch_pr_outcomes.py` therefore re-fetches state,
# every review state and the close actor from the GitHub API for the SELECTED
# corpus only, and `apply_enrichment.py` writes `rejection_stratum_api` beside
# the dataset's own `rejection_stratum`. Measured on the Java V2 corpus
# 2026-08-25: close-actor coverage went to 99.4-100% with a 0.8pp per-cell
# spread, and `changes_requested` went from 25 to 85 PRs.
#
# REPORTS MUST PREFER `rejection_stratum_api` AND SAY SO. The dataset column is
# kept only so the two can be compared.
#
# DROPPED, and recorded so it is not re-attempted: "author-closed = abandonment".
# It reads Claude_Code 78.2% / OpenAI_Codex 80.6% / Copilot 0.5%, which is not
# behaviour but PR-ACCOUNT OWNERSHIP -- Copilot opens PRs from the
# `copilot-swe-agent` bot (941 of 964) while the other two open from the
# developer's own account, so a human closing a Copilot PR can never be "the
# author". Replaced by `closed_by_automation` (bot / policy service), which does
# not depend on account identity: Claude_Code 2.7%, Copilot 6.4%, Codex 2.0%.

# CALIBRATED FROM A REAL PASS, not guessed: the first AIDev-7.6M run used 1.4 and
# under-filled Copilot badly -- 641 drawn -> 280 analyzable, a 56.3% loss, against
# 9.2% (Claude_Code) and 11.2% (OpenAI_Codex) for agents whose file lists come from
# the dataset. 1/0.437 = 2.29, hence 2.3. Re-running curate with survival-aware
# stickiness tops the shortfall up rather than restarting.
# Over-draw factor for PRs whose file list must be resolved from git in Step 2
# (files_source="git_diff"). Those PRs have not been pre-filtered for "touches
# >=1 in-scope source file", so Step 2 discovers and drops some of them; drawing
# exactly `cap` of them would land the corpus below the cap. Applied ONLY to the
# git_diff portion of a draw -- dataset-sourced rows are already pre-filtered and
# are taken first. See curate.py::balanced_sample.
GIT_DIFF_OVERDRAW = 2.3

# A PR is in-scope only if it touches at least one source file in this set.
#
# CORRECTED 2026-08-25. This note used to describe a "Copilot exception"
# (pr_commit_details has no filenames for Copilot, so its PRs skip the
# pre-filter). That was TRUE OF AIDev-full AND IS FALSE OF AIDev-7.6M, where
# Copilot's filenames are 97.3% non-null and it is CLAUDE_CODE that lacks file
# attribution (2.3% in Java, 3.1% in Python). The routing has been data-driven
# since 2026-08-19 and no agent is named anywhere in it: a PR with a usable
# in-scope file list becomes `dataset`, one without becomes `git_diff`, and
# Step 2 derives the list from git and drops PRs that touch no source file.
# The general rule this illustrates: WHICH agent has a metadata gap changes
# between dataset releases, so a fallback selected by agent name is a bug
# waiting for the next release.
SOURCE_EXTENSIONS = {".py"}

# Per-git-command timeout for the clone stage. 60s proved too short for huge
# repos (mlflow/mlflow PR-head fetches timed out and silently cost Claude_Code
# more than half its corpus -- 114 of 140 fetch_failed files). One slow repo
# still cannot stall the batch; it just gets a generous budget now.
GIT_TIMEOUT_S = 300

# --- Test-code classification (AUDIT FIX, 2026-07-28) ------------------------
# WHY THIS EXISTS. Test classes are 34% of the Python corpus and agents differ
# ~4x in how much test code they write (Claude_Code 48.6% of its classes vs
# Copilot 11.6%). Test classes are flagged at ~1/3 the rate of production
# classes, so agents that write many tests get their god-class rate DILUTED --
# and excluding tests measurably CHANGES the agent ranking (pooled and macro).
# That is a confound: we would be partly measuring test-writing propensity
# rather than design quality. Fix: classify every row, and report PRODUCTION
# code as the headline number with test code reported separately.
# NOTE: this is a REPORTING fix, not a detector fix -- excluding tests barely
# moves precision (Python SRP holdout 0.333 -> 0.357), so tests were never the
# main false-positive source.
TEST_PATH_PATTERNS = {
    "py": r"(?:^|/)tests?/|_test\.py$|(?:^|/)test_|conftest",
    "ts": r"(?:^|/)(?:tests?|__tests__|e2e|spec)/|\.test\.|\.spec\.",
    # Java: Maven/Gradle put tests under src/test/java by convention; the
    # *Test / *Tests / *IT suffixes are the JUnit + Failsafe naming standard.
    # NOTE measured 2026-08-03: 72.2% of Java types in this corpus are tests
    # (vs 34.7% Python, ~5% TypeScript), so this classification carries far
    # more weight in Java than anywhere else and MUST be stored per row.
    "java": r"(?:^|/)src/test/|(?:^|/)tests?/|Test\.java$|Tests\.java$|IT\.java$",
}


def is_test_path(path: str, lang: str = "py") -> bool:
    """True if `path` looks like test code (see TEST_PATH_PATTERNS rationale)."""
    import re
    if not isinstance(path, str) or not path:
        return False
    return re.search(TEST_PATH_PATTERNS[lang], path, re.IGNORECASE) is not None


# --- MACHINE-GENERATED CODE (added 2026-09-01) -------------------------------
# Found while auditing the V2 Java label sample: 18% of the drawn SRP rows and
# 25% of the ISP rows were protoc output, not code any human or agent wrote.
#
# THE MEASUREMENT THAT FORCED THIS (distinct production classes, V2 Java):
#     in a generated directory : 3,832 of 55,822  (6.9%)
#     SRP flag rate, generated : 60.0%
#     SRP flag rate, real code : 11.8%
# A protoc-generated message class is thousands of lines with hundreds of
# methods, so it trips every size threshold by construction. It is a true
# positive about the FILE and a meaningless one about the AGENT: the agent did
# not choose that design, protoc did.
#
# IT IS ALSO AGENT-BIASED, which is what makes it a correctness bug rather
# than cosmetic noise. Generated share of each agent's touched classes:
#     Copilot 13.6%  |  Claude_Code 0.5%  |  OpenAI_Codex 0.0%
# because 3,793 of the 3,832 sit in just two repositories (kubernetes-client/
# java, mlflow/mlflow) that Copilot's PRs happen to touch. Uncorrected, Copilot
# reads 17.2% and looks WORST of the three; corrected it reads 10.4% and is
# BEST. Repository correlates with agent, so any repo-concentrated artefact
# silently becomes an agent finding.
#
# MATCHING RULE: whole DIRECTORY COMPONENTS only, never substrings. The first
# version of this check used a regex and matched AutoGeneratedKeysType.java,
# GenerateDaoMojo.java and ProtobufJsonTranscoder.java, all hand-written. That
# is the same substring bug that cost 8,994 files in JAVA_EXCLUDED_PATH_PARTS;
# it is written as a set membership test here so it cannot recur.
GENERATED_DIR_COMPONENTS = {
    "proto", "protos", "generated", "generated-sources",
    "gen-src", "autogenerated", "build-generated",
}


def is_generated_path(path: str) -> bool:
    """True if `path` sits inside a machine-generated source directory.

    Directory components only: the FILE NAME is deliberately never inspected,
    because names like `GeneratedKeysType.java` are ordinary hand-written code.
    """
    if not isinstance(path, str) or not path:
        return False
    return any(c in GENERATED_DIR_COMPONENTS
               for c in path.lower().replace("\\", "/").split("/")[:-1])


# --- TypeScript corpus (2nd language, added 2026-07) -------------------------
# Same policy knobs as Python (MIN_STARS, PERMISSIVE_LICENSES, PER_AGENT_PR_CAP,
# RANDOM_SEED) -- only the language filter, extensions, and output paths differ,
# so the two corpora stay directly comparable. Verified permissive-merged-PR
# counts at stars>=25: Claude_Code 108, Copilot 417, Cursor 264, Devin 2039,
# OpenAI_Codex 1958 -- every agent at/near the 150 cap => TS is the
# best-balanced corpus in the dataset (fixes Python's imbalance problem).
TS_TARGET_LANGUAGES = ["TypeScript"]
# .d.ts is EXCLUDED: declaration-only files carry no implementation, so they are
# not "code the agent wrote" for SRP purposes. (They matter later for interface
# inventories; the delta harness trees can include them when ISP-TS arrives.)
TS_SOURCE_EXTENSIONS = {".ts", ".tsx"}
# Generated/vendored code, excluded from extraction and analysis.
#
# MATCHING RULE (corrected 2026-08-27, see JAVA_EXCLUDED_PATH_PARTS for the
# measurement that forced it): an entry ending in "/" names a DIRECTORY
# COMPONENT and is matched against whole components, never as a substring.
# Anything else is matched inside the FILE NAME only.
TS_EXCLUDED_PATH_PARTS = ("node_modules/", "dist/", "build/", ".min.", ".generated.")

GOLD_SET_TS = CORPUS_DIR / "gold_set_ts.parquet"
CLONE_MANIFEST_TS = CORPUS_DIR / "clone_manifest_ts.parquet"
EXTRACT_DIR_TS = CORPUS_DIR / "ai_files_ts"
SRP_RESULTS_TS = CORPUS_DIR / "srp_results_ts.parquet"

# ---------------------------------------------------------------------------
# JAVA (V2) -- a THREE-AGENT, TWO-OUTCOME study.
#
# SUPERSEDED TEXT REMOVED 2026-08-27. This block used to read "a WITHIN-AGENT
# language study, not an agent comparison ... only OpenAI_Codex has usable
# Java". That was true of AIDev-full and is false of AIDev-7.6M, and it is
# exactly the kind of stale premise that makes a later session design the wrong
# experiment. Measured on the built V2 corpus: 3,880 usable PRs over 716
# repositories, Claude_Code 724 / Copilot 2,044 / OpenAI_Codex 621, with both
# merged and rejected present for every agent and 263 repositories
# contributing BOTH outcomes (the paired within-repo contrast).
#
# WHY JAVA IS WORTH RUNNING ANYWAY. The headline finding of the Python and
# TypeScript studies is that SOLID violations are rare per PR. The obvious
# attack is "your languages don't express SOLID well" -- and it has teeth,
# because TS agent code is 87% class-free and 82.6% of its `interface`
# declarations are data shapes. Java removes that objection: classes are
# mandatory, `interface` always means a real contract, subtyping is nominal,
# and `@Override` is explicit. Holding the AGENT constant and varying the
# LANGUAGE isolates the language effect with no confound from agent mix.
JAVA_TARGET_LANGUAGES = ["Java"]
JAVA_SOURCE_EXTENSIONS = {".java"}
# Build output, generated sources, and vendored code. `target/` (Maven) and
# `build/` (Gradle) hold compiled + generated java that no human wrote.
#
# THESE ARE DIRECTORY COMPONENTS, NOT SUBSTRINGS (fixed 2026-08-27).
# They used to be matched with `part in path`, which silently excluded any path
# merely CONTAINING the fragment. Measured over 304,342 .java files in 133
# cloned repositories: 521 files (0.17%) were dropped for no reason, because
# "gradlebuild/" contains "build/" and "riksdagen/" contains "gen/". The loss
# was concentrated in whole modules of gradle/gradle, Hack23/cia,
# conductor-oss/conductor and several apache/* repositories, and it was mildly
# agent-biased (Copilot 6.9% of PRs in an affected repo vs Codex 3.4%), so it
# could move a per-agent rate rather than merely shrinking the corpus.
#
# The "-gen"/"_gen" SUFFIX rule is deliberate and separate: `src-gen/` is the
# conventional name for generated sources (vavr generates its whole `src-gen`
# tree from a Scala generator), so it must stay excluded, while `codegen/` --
# which holds the hand-written GENERATOR -- must not be.
JAVA_EXCLUDED_PATH_PARTS = (
    "target/", "build/", "generated/", "gen/", "out/", ".generated.",
)
# Directory-component suffixes that mark generated sources (src-gen, model_gen).
GENERATED_DIR_SUFFIXES = ("-gen", "_gen")
# WIDENED 2026-08-17 to the 3-agent set. Under AIDev-full only OpenAI_Codex had
# usable Java (the others had 1, 2, 2 and 0), which is why this was a
# within-agent study. Under AIDev-7.6M all three clear it: eligible Java PRs are
# OpenAI_Codex 1,790 / Copilot 2,495 / Claude_Code 1,954.
# NOTE the git_diff fallback in curate.py::attach_touched_files is what makes
# Claude_Code viable here -- restricted to dataset-supplied file lists it has only
# 45 usable Java PRs, because AIDev-7.6M's pr_commit_details covers just 2.6% of
# the dataset and that loss is agent-biased.
# Cursor (n=94, 72.3% in its top-3 repos) and Devin (n=6) are still excluded on
# volume; Google_Jules is excluded as unvalidated (no track record in any earlier
# AIDev release).
JAVA_ONLY_AGENTS = ["OpenAI_Codex", "Copilot", "Claude_Code"]
# NO CAP. Considered and rejected 2026-08-02 after building it: capping per
# repository and macro-averaging per repository do the SAME job, and only the
# second one is free. Every reported rate is already computed inside each repo
# and then averaged, so a repo with 200 PRs and one with 3 carry equal weight
# whatever the cap is. The cap would have discarded ~60% of the corpus to buy a
# control we already have. Concentration is instead made visible in the report
# (`top_repo_share`) rather than engineered away in the sample.
JAVA_PER_REPO_PR_CAP = None

# REPO-STRATIFIED DRAW (added 2026-08-19). Within an agent's cap, take PRs
# round-robin across that agent's repositories instead of uniformly at random.
# Same number of PRs, spread over far more repositories.
#
# Measured on the AIDev-7.6M Java pool (500 PRs/agent either way):
#     agent          natural draw            stratified draw
#     Claude_Code    145 repos, top-3 11.4%  271 repos, top-3  1.8%
#     Copilot        153 repos, top-3 25.2%  373 repos, top-3  1.2%
#     OpenAI_Codex    65 repos, top-3 48.8%  122 repos, top-3  9.6%
#
# WHY THIS IS WORTH A DESIGN CHANGE. Macro-per-repo averaging already stops a
# mega-repo from dominating an agent's RATE, and that is why JAVA_PER_REPO_PR_CAP
# stayed None. But macro-averaging cannot create independent units it does not
# have: at 65 repos the macro mean for OpenAI_Codex is an average over 65 units,
# and roughly half its PRs come from three of them. Stratifying doubles the
# repositories at no cost in PRs, which widens the base the cross-agent
# comparison rests on rather than merely re-weighting a narrow one.
#
# WHAT IT CHANGES, STATE THIS IN THE METHODS SECTION. The corpus no longer
# mirrors the natural distribution of where each agent works -- OpenAI_Codex
# really is concentrated, and the stratified corpus deliberately does not show
# that. Concentration of the ELIGIBLE POOL is therefore still reported
# (`top_repo_share`) alongside the drawn corpus, so the fact is disclosed rather
# than sampled away. Set to False to recover the natural draw.
JAVA_REPO_STRATIFIED_DRAW = True

# Python V2 takes the same stratified draw, for the same reason. Its eligible
# pool is roughly seven times Java's (65,121 PRs against 9,193), so the natural
# draw would concentrate even harder on the few repositories each agent works in
# most. A per-repo cap is still NOT used: as argued above, macro-per-repo
# averaging already stops one repository dominating a rate, and a cap would
# discard corpus to buy a control the report already has.
PY_REPO_STRATIFIED_DRAW = True

# CORPUS SIZE FOR PYTHON V2.
#
# The within-agent balance path uses n = min(rejected pool, hard cap) and draws
# 2n merged, then appends the whole `open` pool. Those constants were tuned on
# Java, where every rejected pool sits below the 2,600 hard cap so the cap never
# binds, and where the open pool is only 740 PRs. Python is a different shape:
# rejected pools of 3,716 to 6,566 and an open pool of 5,240. Left alone the
# same code draws 20,840 PRs, three times the Java corpus.
#
# That is not worth having. Java's 4,245 ANALYSABLE pull requests supported
# every result in the thesis, including the mixed model, and a corpus three
# times larger costs three times the fetch, the parse and the disk while making
# the two studies harder to compare. These caps land Python at roughly 7,000
# curated, which is Java's 6,956, so the two are like for like.
# Two ceilings, not one. The balance path clamps BOTH the rejected draw and the
# merged draw with the same `hard` value. In Java that was harmless because
# every rejected pool sits below the cap, so merged could still be drawn at 2x
# rejected. In Python the rejected pools exceed any sane cap, so a single
# ceiling forces merged down to the rejected count and destroys the 2x
# over-draw the design relies on to survive Step-2 attrition.
PY_REJECTED_CELL_CAP = 700   # rejected drawn per agent
PY_CELL_HARD_CAP = 1400      # ceiling for the merged draw, i.e. 2x the above
PY_OPEN_CELL_CAP = 250       # per agent, matching Java's ~247 average

# Java scopes by FILE, not by the repository's headline language. The unit of
# analysis is "Java the agent wrote", and a repo whose primary language is Kotlin
# or Go can still contain real Java. Measured on Python, the primary-language
# filter under-counted by ~10-30% per agent; the same correction is applied here
# from the start rather than being discovered later.
JAVA_FILTER_BY_FILE = True

# SRP-JAVA thresholds: percentile-calibrated from the first full run's 953-type
# distribution BEFORE any labelling -- the same non-circular method used for OCP
# and for the TypeScript port. Python's numbers were NOT copied.
#
# MEASURED (n=953): class_loc  p50=119  p75=380  p90=878  p95=1634
#                   wmc        p50=9    p75=33   p90=86   p95=145
#                   methods    p50=6    p75=21   p90=42   p95=64
#                   method ccn p50=2    p75=5    p90=11   p95=18
#                   params     p50=1    p75=3    p90=4    p95=5
#                   lcom       p50=0.12 p75=0.67 p90=1.00
#                   c3         p50=0.16 p75=0.26 p90=0.47
#
# Java classes are far larger at the tails than either earlier language: the
# Python bar of 200 LOC sits near Java's p60, and TypeScript's 400 near its p76.
# Carrying either over would have flagged the median Java class.
#
# SIZE knobs are anchored at ~p90 (size is language-dependent). SCALE-FREE knobs
# keep their Python-validated values, exactly as in the TS port -- lcom 0.8,
# c3_suppress 0.4, lcom_min_methods 4. `func_params` also keeps 6 rather than
# Java's p90 of 4: four parameters is not a god-class signal in anyone's
# taxonomy, and the percentile anchor is a tail heuristic, not a licence to
# flag ordinary code.
# !! VOID 2026-08-03 -- these numbers were calibrated on the tree-sitter
# extractor's distribution, which FOLDED NESTED CLASSES INTO THEIR OUTER TYPE
# (defect #7). Every size metric was therefore inflated, so the percentiles they
# were anchored to describe a population that does not exist. Set to None to
# force calibration mode against the JavaParser extractor, then refilled below
# from the new distribution. The old values are kept in the comment as a record,
# not as a default.
#   VOID: class_loc 520 / class_wmc 52 / class_methods 24 / func_ccn 10
SRP_JAVA_THRESHOLDS_VOID = {
    # size knobs: ~p90 of the FULL 4,302-type Java distribution
    "class_loc": 520,          # p90=514
    "class_wmc": 52,           # p90=52
    "class_methods": 24,       # p90=24
    "func_ccn": 10,            # p90=10
    # scale-free knobs: keep the Python-validated values (as the TS port did).
    # Java's p90 for params is 3, which is not a god-class signal in any
    # taxonomy -- the percentile anchor is a tail heuristic, not a licence to
    # flag ordinary code.
    "func_params": 6,
    "class_lcom": 0.8,
    "lcom_min_methods": 4,
    "c3_suppress": 0.4,
    "min_hits": 2,
    "strict_hits": 3,
}

# PHASE 4, 2026-08-03: calibrated from the JavaParser extractor's own
# distribution, BEFORE any labelling existed -- the non-circular method used for
# OCP and for the TypeScript port.
#
# Calibrated on PRODUCTION, ENTITY-DEDUPLICATED types (n=976), not on all 6,453
# rows. Two biases are removed by doing so: 59.9% of Java type rows are TESTS
# (vs Python 34.7%) and have a different size distribution, and the uncapped
# Java corpus repeats a class once per PR that touched it.
#
# MEASURED (n=976 production, deduplicated):
#   class_loc   p50=117  p75=340  p90=926   p95=1787
#   wmc         p50=6    p75=20   p90=58    p95=124
#   methods     p50=3    p75=10   p90=27    p95=45
#   method ccn  p50=2    p75=5    p90=11    p95=18
#   params      p50=2    p75=3    p90=4     p95=5
#   lcom        p50=0.25 p75=0.89 p90=1.00
#   c3          p50=0.17 p75=0.26 p90=0.41
#
# Java classes run far larger at the tails than either earlier language --
# Python's 200-LOC bar sits near Java's p57 and TypeScript's 400 near its p78 --
# so carrying either over would have flagged ordinary Java code.
# Each size knob below fires on ~10% of production types, as a p90 anchor should.
SRP_JAVA_THRESHOLDS = {
    # RECALIBRATED 2026-08-29 ON THE V2 CORPUS. The previous values were derived
    # from V1 (867 PRs / 27 repos / one agent, merged-only). V2 is 3,823
    # analysable PRs over 729 repositories with rejected and open PRs included,
    # so the size distribution is a different population and the old bar no
    # longer sits at p90 of it. Carrying V1 numbers over would have UNDER-flagged
    # -- every one of them is above the V2 p90.
    #
    # SOURCE: the PRODUCTION, ENTITY-DEDUPLICATED distribution (n=53,182), never
    # the raw row set (n=148,361). The raw set is 25.4% test code and carries
    # 2.08x duplication because a class recurs once per PR that touched it;
    # calibrating on it puts class_loc at 519 instead of 362, i.e. ~30% too high,
    # and the flag would then fire on far fewer than the 10% a p90 anchor means.
    "class_loc": 362,          # V2 p90=362   (V1 was 465)
    "class_wmc": 45,           # V2 p90=45    (V1 was 58)
    "class_methods": 20,       # V2 p90=20    (V1 was 27)
    "func_ccn": 11,            # V2 p90=11    (unchanged)
    # SCALE-FREE knobs. `func_params` moves to the V2 p90 of 5; `class_lcom` to
    # 0.86. A percentile is a tail heuristic, not a licence to flag ordinary
    # code, so these stay anchored rather than chasing a lower tail.
    "func_params": 5,          # V2 p90=5     (V1 was 6)
    "class_lcom": 0.86,        # V2 p90=0.86  (V1 was 0.8)
    "lcom_min_methods": 4,
    # c3_suppress is a SUPPRESSOR, not a p90 anchor: it withholds the flag from a
    # class whose vocabulary is cohesive despite its size. 0.4 sits around p80-85
    # of the V2 c3 distribution (p75=0.32, p90=0.51), which is the intended
    # "clearly cohesive" region. Kept.
    "c3_suppress": 0.4,
    "min_hits": 2,
    "strict_hits": 3,
}

# LSP-JAVA signal set. `javac` ENFORCES three of Python's five signals, so they
# cannot occur in code that compiles: a changed parameter list is an OVERLOAD
# not an override; parameter types are invariant (no contravariance to detect);
# visibility cannot narrow and checked exceptions cannot broaden. Covariant
# returns are legal AND safe. So Java keeps only the signals a type system
# cannot check. This mirrors the TypeScript port dropping `param_removed`, for
# the opposite reason: TS methods are bivariant and permit everything, Java is
# invariant and permits nothing. What the compiler enforces determines which
# LSP violations are even expressible -- a cross-language result in its own right.
# VALIDATED AND NARROWED 2026-09-04 on 400 hand-judged override pairs. The flag
# used to combine all three signals below and scored P=0.280 overall, which is
# an average of one excellent detector and two that find nothing:
#
#     refused_bequest             62 flags, 51 true, P=0.823
#     precondition_strengthened   62 flags,  5 true, P=0.081
#     postcondition_weakened      76 flags,  0 true, P=0.000
#
# 76 flags with ZERO true positives is not a weak signal, it is an inert one.
# Both dropped signals fire on ordinary polymorphism -- covariant returns,
# specialised return values, a subclass validating its own narrower domain --
# which is what overriding is FOR.
#
# WHY THIS IS A RESULT AND NOT A PATCH. javac already enforces parameter
# compatibility, visibility and checked exceptions, so the only LSP violations
# EXPRESSIBLE in compiling Java are behavioural. The two contract-variance
# signals find nothing because the compiler prevented those violations before
# the code ever reached us; the one behavioural signal finds them at 82%
# precision. What a compiler enforces determines which violations can exist.
#
# refused_bequest alone (0.823) is the most precise detector in this study,
# ahead of OCP (0.480) and SRP (0.270). Reporting the three together buried it.
#
# Same remedy as V1 applied to DIP's `mean_d` (0/17 across two languages).
LSP_JAVA_STRONG_SIGNALS = [
    "refused_bequest",              # override body is only `throw new UnsupportedOperationException`
]

# Kept for the report's per-signal table and for anyone re-running the
# validation: these are COMPUTED and STORED on every override row, they simply
# no longer raise the flag.
LSP_JAVA_REPORTED_ONLY_SIGNALS = [
    "precondition_strengthened",    # P=0.081 on 62 flags
    "postcondition_weakened",       # P=0.000 on 76 flags
]

# Thresholds for the four Java delta principles.
#
# OCP  type_branches 3 = Martin's own minimum for a "switch", the same bar used
#      in Python and TypeScript. value_branches 6 matches Python; TypeScript
#      needed 8 because of wide discriminated-union switches, an idiom Java
#      does not have. `switch` over an ENUM is suppressed regardless of width:
#      it is exhaustive by construction (Java's analogue of Python's
#      all-builtins rule).
# ISP  fat_members 6 and the stub rule carry over from TypeScript; Java's
#      `interface` needs none of the data-shape filtering TS required.
# DIP  identical to both other languages so the numbers compare directly.
#      NOTE: `mean_d` scored 0/17 precision across Python AND TypeScript
#      validation. It is emitted for comparability, but the report leads with
#      new_cycle and marks mean_d indicative only.
# !! `ocp.value_branches = 6` was COPIED FROM PYTHON AND NEVER CALIBRATED, and
# measurement showed Java's switch widths are p50=3 p75=6 p90=15 -- so the bar
# sat at p75 and flagged a quarter of all switches. That measurement came from
# the void extractor, so it is re-measured against JavaParser in PHASE 4.
# `ocp` and `isp` are None until then (calibration mode); `dip` is threshold-free
# for its trustworthy half (a new cycle is a new cycle) so it stays set.
# PHASE 4, 2026-08-03: calibrated from Java's own distributions.
#
# OCP  MEASURED over 461 distinct chains (43 closed-set):
#        instanceof   n=103  p50=3  p75=4.5  p90=6
#        type_eq      n=18   p50=3  p75=4    p90=5
#        switch_tag   n=43   p50=3  p75=5    p90=10.4
#        value_switch n=297  p50=3  p75=5    p90=11
#      `type_branches` stays 3 -- Martin's own minimum for a "switch", used
#      unchanged in Python and TypeScript, so type-based bars remain comparable
#      across all three languages.
#      `value_branches` is 11, NOT the 6 copied from Python: Java's p90 is 11,
#      so 6 sat at Java's p75 and flagged a quarter of all switches. Python
#      needed 6, TypeScript 8, Java 11 -- all three genuinely differ, which is
#      precisely why per-language calibration is not optional.
#      A `switch` over an ENUM is suppressed regardless of width: it is
#      exhaustive by construction (Java's analogue of Python's all-builtins rule
#      and TypeScript's `typeof` rule), decided by RESOLVING the selector's type
#      rather than guessing from label casing as the void run did.
#
# ISP  MEASURED over 120 distinct contracts: n_members p50=2 p75=5 p90=18 max=97.
#      18 is far above TypeScript's 7 because Java interfaces are legitimately
#      wide -- `java.util.List` alone declares 25+ methods and is nobody's design
#      smell. CAVEAT for the write-up: interface WIDTH is the weaker half of ISP.
#      The semantically meaningful signal is `forced_stub` (an implementer that
#      must stub methods it does not need), which is scale-free and carried over.
#
# DIP  identical to both other languages so the numbers compare directly.
#      NOTE: `mean_d` scored 0/17 precision across Python AND TypeScript
#      validation. It is emitted for comparability, but the report leads with
#      new_cycle and marks mean_d indicative only.
# RECALIBRATED 2026-08-29 on the V2 corpus (see SRP_JAVA_THRESHOLDS for why V1
# values could not be carried over).
#
# OCP. Calibrated per KIND on DISTINCT chains (n=13,938 of 32,744 rows), because
# type-based and tag-based dispatch have different natural widths:
#   instanceof p90=5 | switch_tag p90=12 | type_eq p90=19 | value_switch p90=8
# `type_branches` stays 3 -- Martin's own minimum for a switch, used unchanged in
# all three languages so the type-based bar stays cross-language comparable.
# `value_branches` moves to the V2 p90 of the value_switch family: 11 -> 8.
# Note 2,078 of 13,938 chains have an enum subject and are exhaustive by
# construction; the closed-set suppressor already excludes those.
#
# ISP. Contract width on DISTINCT contracts (n=8,089 of 18,783 rows): p90=13.
# `fat_members` 18 -> 13. WIDTH IS THE WEAKER HALF OF ISP and should be reported
# as such: the semantically meaningful signal is `forced_stub` (an implementer
# obliged to stub methods it does not need), which is scale-free and needs no
# threshold at all.
#
# DIP is unchanged: `new_touched_cycles` is a count, not a percentile, and
# `delta_mean_d` does not feed the flag (DIP_FLAG_INCLUDES_MEAN_D = False).
JAVA_DELTA_THRESHOLDS = {
    "ocp": {"type_branches": 3, "value_branches": 8},
    "isp": {"fat_members": 13, "min_stubs": 2, "stub_ratio": 0.5},
    "dip": {"new_touched_cycles": 0, "delta_mean_d": 0.15},
}

# DIP: DOES `mean_d_worse` COUNT TOWARD THE FLAG? (added 2026-08-19 -- False.)
#
# It does not, and the evidence is two independent label rounds in two
# languages. Python full census: `new_cycle` 13/13 correct (P=1.000),
# `mean_d_worse` 0/7 (P=0.000). TypeScript, 40 rows: `new_cycle` 5/5 (P=1.000),
# `mean_d_worse` 0/10 (P=0.000). Zero true positives in 17 flags across two
# corpora is not a weak signal, it is an absent one -- most of those flags had no
# new dependency at all and moved only because the package graph was recomposed.
#
# Averaging a perfect detector with an empty one does not produce a cautious
# number, it produces a worse one: it adds false positives and UNDERSTATES the
# best result this project has. `delta_mean_d` is still computed and still
# written to the parquet, because Martin distance is a fair DESCRIPTOR of where a
# package sits; it just no longer decides `dip_risk`.
#
# Set True only to reproduce the pre-2026-08-19 combined flag.
DIP_FLAG_INCLUDES_MEAN_D = False

GOLD_SET_JAVA = CORPUS_DIR / "gold_set_java.parquet"
CLONE_MANIFEST_JAVA = CORPUS_DIR / "clone_manifest_java.parquet"
EXTRACT_DIR_JAVA = CORPUS_DIR / "ai_files_java"
SRP_RESULTS_JAVA = CORPUS_DIR / "srp_results_java.parquet"
OCP_RESULTS_JAVA = CORPUS_DIR / "ocp_results_java.parquet"
LSP_RESULTS_JAVA = CORPUS_DIR / "lsp_results_java.parquet"
ISP_RESULTS_JAVA = CORPUS_DIR / "isp_results_java.parquet"
DIP_RESULTS_JAVA = CORPUS_DIR / "dip_results_java.parquet"
# Shared per-(repo,sha) tree snapshot cache (gitsnap.py) -- the perf fix for the
# "every delta harness re-fetches the same commit" problem. TS harnesses use it
# from day one; Python harnesses migrate in a later, output-checked pass.
TREE_CACHE_DIR = CORPUS_DIR / "tree_cache"

# Parallelism for the TS pipeline (new code is fast from day one; the validated
# Python scripts are NOT retrofitted in the same change -- see PROJECT.md 7.C).
FETCH_WORKERS = 6      # concurrent repos in clone/fetch (network-bound, threads)
# CONCURRENT PR-TREE ANALYSES. Lowered 8 -> 4 on 2026-08-26, for two reasons.
#
# MEMORY, and this was a real misconfiguration: each worker launches a Java
# sidecar with a 3 GB heap (JAVA_SIDECAR_HEAP), so 8 workers is 24 GB of
# potential heap on a 15.4 GB machine. The comment justifying 8 cited the core
# count and never checked the RAM. Under load that thrashes, and an
# out-of-memory JVM is recorded as an ANALYSIS FAILURE rather than as a resource
# problem -- a wrong result dressed as a data problem, which is this project's
# recurring failure mode.
#
# USABILITY: these passes run for hours on a machine somebody is also using.
#
# RAISED 4 -> 5 on 2026-08-27, on measurement rather than on the core count that
# justified the original 8. With the JVM flags added in java_bridge.JVM_TUNING
# (the heap now shrinks back after each collection instead of staying at its
# high-water mark) resident memory per sidecar fell from 2,953 MB peak / 2,029 MB
# mean to 2,042 MB peak / 1,035 MB MEAN, sampled once per 1.2 s across a
# 54-PR pilot. Five workers is therefore ~5.2 GB in the typical case against
# 8.8 GB available on this machine.
#
# Note what does NOT change with worker count: OOM is governed by each JVM's own
# -Xmx ceiling, not by system RAM, so adding workers costs PAGING (slower), never
# a spurious analysis failure. That is why the safe ceiling is set by measured
# MEAN residency and not by worst-case heap.
ANALYZE_WORKERS = 3
# SET TO 5 on 2026-08-27, after measuring where the time actually goes.
# This pass is NETWORK-BOUND, not CPU-bound: sampled live at 5 workers there
# were 12 concurrent git processes but only 1-2 live JVMs, CPU at ~40% and the
# disk queue at 0. Workers spend most of their life fetching trees and only a
# short burst parsing them, which is why raising the worker count costs almost
# no extra memory (JVMs rarely overlap) and buys parallel fetches.
#
# The fetching is unavoidable here: the cache-key repair (see gitsnap
# `_cache_key`) correctly invalidated every previously cached tree, so this run
# rebuilds the tree cache from scratch. A later re-run reads it back and is
# CPU-bound instead -- at which point this number should come back DOWN to ~5,
# because then the JVMs really do run concurrently.
#
# WHY 7 AND NOT 10. Ten workers did saturate the machine (CPU 100%, 5-6 live
# JVMs) but drove free RAM down to 0.8 GB. That is not a JVM out-of-memory risk
# -- OOM is bounded by each JVM's own -Xmx -- it is a PAGING risk, and heavy
# paging makes a sidecar exceed its timeout, which IS recorded as an analysis
# failure and so becomes silent data loss.
#
# SEVEN WAS ALSO TOO MANY, measured live with the desktop in normal use: JVMs
# peaked at 7 concurrent and free RAM fell to 0.4 GB. The pipeline is not the
# only tenant -- an editor, a browser, the AV service and the OS hold ~5 GB of
# the 15.4 GB before any worker starts. Five keeps CPU high (85-100%) with a
# measured floor of ~2.2 GB free, which is the setting that leaves the machine
# usable and the run safe.
#
# LOWERED AGAIN TO 4 on 2026-08-28. Five was measured safe when the desktop held
# ~3.5 GB of apps; with Discord, VS Code and two browsers open it is not. Sampled
# live: java alone at 7.6 GB across 5 workers (larger trees give larger heaps)
# against 4.6 GB of other applications, leaving 0.2 GB free. The right number
# depends on what else is running, so it is set from the MEASURED floor rather
# than fixed once -- and the floor that matters is free RAM under load, not the
# core count.
#
# LOWERED TO 3 on 2026-08-28 after measuring the SECOND-ORDER cost. At 4 workers
# free RAM sat at 0.16-0.4 GB, and that is not merely "tight": paging slows every
# sidecar, and a slowed sidecar crosses its 900 s timeout and is recorded as an
# ANALYSIS FAILURE. Observed together, and almost certainly the same cause:
# throughput fell 3.67 -> 1.78 PRs/min and the failure rate rose 0.15% -> 1.88%.
# Fewer workers that all run at full speed beat more workers that page -- the
# extra worker was buying negative throughput AND manufacturing data loss.
#
# RAISED TO 6 for the FLAGGED pass (2026-08-29). The bottleneck is different in
# this phase and the right worker count follows the bottleneck, not the machine.
# Calibration was CPU+RAM bound: it PARSED every tree, so 3 concurrent JVMs
# saturated memory. The flagged pass reads those parses back from the facts
# cache, so measured live it runs at CPU 41%, 4.6-5.9 GB RAM free, and only
# 0.8 live JVMs against 2.8 concurrent git processes -- it is NETWORK bound,
# waiting on tree fetches. Extra workers therefore buy parallel fetches at
# almost no memory cost.
#
# RAISED AGAIN TO 10 while the tree cache is still cold. Measured at 6: CPU 30%,
# 4.7 GB RAM free, 2.27 MB/s network against the 3.5 MB/s this connection
# sustained during extraction -- headroom in all three.
#
# THIS SETTING IS ONLY VALID WHILE FETCHING DOMINATES. The moment the tree cache
# warms, the phase flips to parsing and ten concurrent JVMs would demand 15-30 GB
# on a 15.4 GB machine -- precisely the thrash that cost hours in calibration.
# LOWERED TO 4 (2026-08-29) -- SAFE IN BOTH REGIMES, which is the property that
# actually matters for an unattended run.
#
# 10 was correct for the phase as it is RIGHT NOW (network-bound, ~1 live JVM),
# but that state is temporary: the tree cache is refilling, and when it is warm
# the fetches stop and the phase becomes parsing-bound -- at which point ten
# concurrent JVMs would demand far more than this machine has. A monitor can
# warn about that flip, but a warning only helps if someone is watching, and
# this run is meant to be left alone.
#
# 4 is the largest count measured safe while PARSING (calibration: 3 gave a
# 3.05 GB floor, 4 was workable with a light desktop, 5+ collapsed to 0.16 GB and
# cost 44% throughput plus a 5x failure rate). Choosing it costs speed while the
# phase is still fetch-bound, and buys the guarantee that no bottleneck flip can
# thrash the machine unsupervised. The safe-by-construction setting beats the
# fast setting plus a promise to intervene.
#
# LOWERED TO 3 mid-run (2026-09-01) when the guard fired REPEATEDLY, which is the
# trigger that distinguishes a pattern from an outlier. The first trip was a
# single transient and was correctly ignored; by the second, sampling showed free
# RAM below 2 GB in 13 of 30 consecutive samples (43% of the time) with a floor of
# 0.57 GB, and JVM residency had risen from 2.3 GB to 6.0 GB across three
# processes as the pass reached larger trees.
#
# Acted BEFORE damage rather than after: the failure rate was still 0.26% at this
# point. Waiting for failures to appear would have meant waiting for exactly the
# silent, size-correlated (and therefore agent-correlated) loss this project
# exists to avoid.

# Java sidecar heap per worker. 3g is what one large tree needs; the number of
# WORKERS is the knob to turn for total memory, not this.
JAVA_SIDECAR_HEAP = "8g"

# Seconds a single sidecar call may take before it is abandoned.
#
# 900 is right for the MAIN pass: it bounds the cost of one pathological tree so
# a single repository cannot stall a 25-hour run. It is NOT right for a cleanup
# pass, because the trees that exceed it are not broken, only large.
#
# Measured on the V2 run: the timeouts concentrate in a handful of very large
# repositories (kubernetes-client/java, opentelemetry-java-instrumentation,
# open-metadata/OpenMetadata), and repository size CORRELATES WITH AGENT, so
# leaving them out turns a size-driven loss into an agent-driven one -- the exact
# bias family this project keeps having to hunt down. Large codebases are also
# where SOLID pressure is most interesting, so they are the worst PRs to drop.
#
# RAISED TO 2400 for the FLAGGED pass (2026-08-29). The calibration pass ran at
# 900 and finished with 19 `sidecar_failed`, concentrated in a handful of very
# large repositories. Because `sidecar_failed` is deliberately NOT a permanent
# status, those PRs are retried automatically by the next run -- so raising the
# ceiling here recovers them inside the flagged pass instead of needing a
# separate cleanup run. The trees are already parsed and cached, so the extra
# ceiling costs nothing on the PRs that were already succeeding.
JAVA_SIDECAR_TIMEOUT_S = 2400

# Node sidecar (03_principles/ts_sidecar): ts-morph 28.0.0 + typescript 5.9.3,
# pinned via package-lock.json. The sidecar emits structural FACTS as JSON; all
# metric math/thresholds/suppressors stay in Python.
TS_SIDECAR_DIR = VIBEBENCH_DIR / "03_principles" / "ts_sidecar"
TS_SIDECAR_TIMEOUT_S = 120   # per PR-tree analysis call

# SRP-TS thresholds: percentile-calibrated from the FIRST full run's observed
# distribution over 1,213 classes (2026-07, BEFORE any labeling -- the OCP
# non-circular calibration method). Size/complexity thresholds anchor at ~p90
# (observed: class_loc p90=424, wmc p90=53, methods p90=14, ccn p90=17,
# params p90=6); the scale-free cohesion knobs (lcom 0.8, c3 0.4, saturation
# guard 4) keep their Python-validated values since their semantics don't
# change across languages. TS classes run larger than Python's at the tails
# (p90 loc 424 vs Python thr 200 ~= TS p78), which is why Python's numbers
# were NOT copied.
# OCP-TS thresholds: percentile-calibrated from the first full run's 545 in-scope
# chains (2026-07-28) BEFORE any labeling -- the non-circular method used for
# OCP-Python. Both bars sit at the observed p90 of their family:
#   type-based (instanceof/typeof): p50=2 p75=3 p90=3 p95=4 max=5   -> 3
#     (also Robert Martin's minimum "switch statement", same as Python's bar)
#   tag-based (switch_tag/value_switch): p50=3 p75=5 p90=8 p95=10 max=64 -> 8
# The tag bar is HIGHER than Python's (6) because TypeScript leans much harder on
# discriminated-union `switch` (337 switch_tag vs 171 value_switch chains) and
# those ladders are legitimately wider; copying Python's 6 would flag the median
# idiom. Kept deliberately conservative for the same reason Python's was.
OCP_TS_THRESHOLDS = {
    "type_min_branches": 3,
    "tag_min_branches": 8,
}
# Deterministic closed-set suppressor, the TS analogue of OCP_CLOSED_SET_SUPPRESS:
# a `typeof` ladder ranges over JavaScript's 8 language-fixed typeof results, and
# an `instanceof` ladder over only built-in constructors is likewise exhaustive --
# neither is the open-ended extension OCP guards against. The sidecar computes the
# `closed_set` flag; this switch decides whether to honour it.
OCP_TS_CLOSED_SET_SUPPRESS = True
OCP_RESULTS_TS = CORPUS_DIR / "ocp_results_ts.parquet"

# LSP-TS: the deterministic false-positive suppressor, TS twin of
# LSP_SUPPRESS_FPS (which lifted LSP-Python holdout precision 0.42 -> 1.00).
# Silences the same four idioms: `...args: any[]` placeholder bases, variance
# against any/unknown, rest-absorbed optional-param drops, factory statics.
LSP_TS_SUPPRESS_FPS = True

# LSP-TS: which signals actually count as a violation IN TYPESCRIPT.
#
# The Python rule was ported literally at first, and that was wrong. Probed with
# ts-morph + typescript 5.9.3 under `strict: true` (2026-07-31): a subclass that
#   (a) drops a trailing parameter,
#   (b) narrows a parameter's type,
#   (c) makes an optional parameter required
# produces **zero** diagnostics. TypeScript methods are deliberately BIVARIANT --
# `strictFunctionTypes` exempts method declarations -- so the type checker is not
# the arbiter here that mypy was for Python.
#
# `param_removed` is dropped as a signal because it is safe at BOTH levels: the
# type system accepts it (a function of fewer parameters is assignable to one of
# more) and so does the runtime (JavaScript silently ignores extra arguments).
# In Python the same edit raises TypeError at the call site -- a genuine break.
# This is a real language difference, not a threshold to tune.
#
# The other three are KEPT: the type checker permits them, but the runtime can
# still break -- a base-typed client that omits an argument, or passes a member
# of the base's declared union, hands the override a value it does not handle.
# refused_bequest is behavioural and language-neutral.
#
# Effect on the head-state inventory: 23 violating pairs -> 11 (param_removed
# fired on 15 of the 23 and was the SOLE signal on 12 of them -- by far the
# dominant one, which is why leaving it in would have defined the whole result).
LSP_TS_STRONG_SIGNALS = [
    "param_required_added",
    "param_type_narrowed",
    "return_type_widened",
    "refused_bequest",
]
LSP_RESULTS_TS = CORPUS_DIR / "lsp_results_ts.parquet"

# ISP-TS thresholds: percentile-calibrated from the first full run BEFORE any
# labeling (same non-circular method as OCP).
#
# IMPORTANT TS-SPECIFIC FINDING (2026-07-28): of 3,733 declared interfaces /
# abstract classes, **82.6% have ZERO behavioural members** -- in TypeScript an
# `interface` is overwhelmingly a DATA SHAPE (`interface User { name: string }`),
# i.e. a structural type alias, not a behavioural contract. ISP is about being
# forced to IMPLEMENT methods you don't use, so a zero-method shape cannot cause
# it. Those are therefore excluded from the contract inventory entirely
# (min_members), leaving 648 behavioural contracts.
#
# SECOND CUT (2026-07-31, after inspecting what the first run actually flagged):
# only **80 of those 648 (12.3%) have an internal implementer**. The rest are
# React/hook idioms whose members are callback-typed FIELDS, not declarations
# anyone implements -- component `Props` bags (MonacoProps, 20 "members"),
# context values, zustand store shapes, hook return types. No client can be
# forced to supply an unused method by a type it never implements, so those are
# out of ISP's scope too (enforced in run_isp_ts.py::snapshot). Without this cut
# 43 of 63 flagged interfaces had zero implementers and the whole PR-level
# signal was a props-bag artefact.
#
# Widths of the 80 REAL contracts: p50=3 p75=5 p90=7 p95=7 p99=12 max=17
#   -> fat bar 7 (~p90, the anchor used for every other TS threshold).
#   The old bar of 6 was calibrated on the polluted 648 and sits at ~p80 here.
# Forced stub: >=2 stubbed members AND >=50% of what the class implements.
ISP_TS_THRESHOLDS = {
    "min_members": 1,          # a contract must declare >=1 behaviour to count
    "fat_iface_members": 7,
    "min_stubs": 2,
    "stub_ratio": 0.5,
}
ISP_RESULTS_TS = CORPUS_DIR / "isp_results_ts.parquet"

# DIP-TS: same two signals as Python -- a NEW import cycle among touched
# modules, or mean Martin distance worsening by more than delta_mean_d.
# NOTE: the Python DIP validation (P=0.22/R=0.67) showed `new_cycle` is the
# reliable half and `mean_d` is mostly graph-recomposition noise, so the TS
# report leads with cycles.
DIP_TS_THRESHOLDS = {"new_touched_cycles": 0, "delta_mean_d": 0.15}
DIP_RESULTS_TS = CORPUS_DIR / "dip_results_ts.parquet"

SRP_TS_THRESHOLDS = {
    "class_loc": 400,
    "class_wmc": 50,
    "class_methods": 14,
    "class_lcom": 0.8,
    "lcom_min_methods": 4,
    "func_ccn": 17,
    "func_params": 6,
    "risk_min_hits": 2,
    "strict_min_hits": 3,
    "c3_suppress": 0.4,
}

# --- SRP rubric thresholds (Step 3) ----------------------------------------
# Transparent numeric rubric. We report raw distributions too, so these
# thresholds only drive the binary "SRP risk" flag, not the underlying data.
#
# Validated against 120 LLM-labeled classes (labels/srp_labeling_sample.csv,
# labeled by Claude reading each class's source -- NOT human-expert labels;
# treat P/R below as rubric-vs-LLM agreement, not human ground truth):
# baseline rubric scored P=0.27/R=1.00/F1=0.42; two principled fixes brought it
# to P=0.78/R=0.88/F1=0.82 on the same set:
#   1. An LCOM hit requires >= LCOM_MIN_METHODS non-constructor methods --
#      LCOM-family metrics saturate to 0/1 for tiny classes (esp. test classes
#      and classmethod-only DTOs), the largest false-positive source.
#   2. `file_loc` does NOT count toward class-level risk -- it's file context,
#      not a property of the class (a clean class in a big file was being
#      flagged identically to the actual bloated class). It still counts for
#      file-level rows (modules with no classes), where it IS the subject.
# NOTE: tuned in-sample on those 120 labels; confirm on a fresh holdout sample
# before publishing headline numbers.
SRP_THRESHOLDS = {
    # RECALIBRATED 2026-08-17 on the AIDev-7.6M corpus's own distribution,
    # BEFORE any label existed (the non-circular method used for OCP, TS and
    # Java). Anchored at the ~p90 of PRODUCTION classes, n=19,658 of 29,345.
    #
    # WHY PRODUCTION-ONLY, a deliberate change from earlier calibrations.
    # Test classes are 33% of the Python corpus and are systematically smaller,
    # so calibrating on ALL classes drags every threshold DOWN and makes
    # production code cross them more readily -- while the production-only rate
    # is what the report leads with. Calibrating on the population that is
    # actually reported removes that mismatch. (Java is affected far more: 72.2%
    # of its types are tests, so the same change is applied there.)
    #
    # MEASURED, production classes (n=19,658):
    #   class_loc   p90=260  p95=490
    #   wmc         p90=36   p95=69
    #   methods     p90=11   p95=17
    #   method ccn  p90=11   p95=17
    #   params      p90=6    p95=9
    #   file_loc    p90=871  p95=1325   (n=7,472 production files)
    "class_loc": 260,       # was 200
    "class_wmc": 36,        # was 47
    "class_methods": 11,    # was 20 -- the old bar sat above this corpus's p95
    "class_lcom": 0.8,      # UNCHANGED and NOT percentile-derived: on the
                            # population it applies to (>=4 methods, 31.7% of
                            # classes) LCOM reads p75=p90=p95=1.000. A metric
                            # pinned at its own maximum cannot be calibrated by
                            # percentile, so the validated 0.8 is kept.
    "file_loc": 871,        # was 400
    "func_ccn": 11,         # was 15
    "func_params": 6,       # was 6 -- unchanged, p90 landed on the same value
}
# A file/class is flagged as an SRP risk when it exceeds this many rubric metrics.
#
# HOLDOUT FINDING (2026-07-19, labels/srp_holdout_sample.csv, 60 fresh
# LLM-labeled classes from the expanded 5-agent corpus): the tuning-sample
# numbers were OVERFIT. Out-of-sample the shipped flag scores P=0.33 / R=1.00
# (in-sample: P=0.82 / R=0.88). Recall holds perfectly -- every real violation
# was flagged -- but 2/3 of flags are false positives, mostly large-but-
# cohesive classes (test suites, template-method hierarchies, single-domain
# engines) that size/structure proxies cannot distinguish from god classes.
# Sensitivity on the same holdout: min_hits=3 -> P=0.43/R=0.90;
# min_hits=4 -> P=0.64/R=0.70. Conclusion: treat srp_risk as a HIGH-RECALL
# SCREEN, not a precise detector; both operating points are reported so
# consumers can pick their tradeoff. Do NOT tune further on the holdout --
# any new rubric change needs a third fresh sample.
SRP_RISK_MIN_HITS = 2
# Stricter secondary flag (reported alongside, same suppressor applies).
SRP_RISK_STRICT_MIN_HITS = 3
# Minimum non-constructor methods for an LCOM hit to count (saturation guard).
LCOM_MIN_METHODS = 4
# Semantic suppressor: a rubric-flagged class whose C3 conceptual cohesion
# (see srp/metrics.py compute_c3 -- TF-IDF over method vocabularies, not an
# LLM) is >= this value gets UNFLAGGED: its methods demonstrably share one
# domain vocabulary, i.e. the "well-written large class" false positive the
# size-based rubric cannot see. In-sample effect on the 120 LLM labels:
# P 0.778 -> 0.824, R unchanged, F1 0.824 -> 0.848. Evidence is thin (based on
# suppressing 1 of 4 false positives) -- treat as tentative until the holdout
# sample confirms it. Set to None to disable.
SRP_C3_SUPPRESS = 0.4

# --- DIP rubric thresholds (Step 3-DIP) -------------------------------------
# Delta-based: a PR is flagged only for what it CHANGED (before vs after the
# merge commit), never for the repo's pre-existing architecture.
#   new_touched_cycles -- flag if the PR creates more than this many NEW import
#                         cycles through AI-touched modules (0 = any new cycle)
#   delta_mean_d       -- flag if the mean Martin distance-from-main-sequence
#                         of touched packages worsens by more than this
# Unvalidated defaults; must go through the same label-validation loop SRP got
# before headline use.
DIP_THRESHOLDS = {
    "new_touched_cycles": 0,
    "delta_mean_d": 0.15,
}

# Does the Martin-distance delta count toward dip_risk? NO, as of 2026-08-17.
# VALIDATED PRECISION 0.000 IN TWO LANGUAGES INDEPENDENTLY: Python 0/7 true
# positives, TypeScript 0/10. Four of the seven Python false positives had ZERO
# new dependencies, so the signal fires on graph RECOMPOSITION (a package's Ce/Ca
# shifting because modules moved) rather than on a real dependency inversion.
# Effect of this switch on the shipped Python result: 20 flagged PRs at P=0.650
# -> 13 flagged PRs at P=1.000, with NO recall loss (the 7 dropped were not
# violations under any reading). `new_cycle` carries the whole signal.
# The raw delta and a `mean_d_worse` boolean are still WRITTEN to the parquet, so
# this is reversible and the negative result stays auditable -- it is a real
# finding about Martin's D as a change signal and belongs in the write-up.
# Martin A/I/D remains sound as a STATIC package descriptor; see the arch/ zone
# work. That is a different claim from "this PR made D worse".
DIP_MEAN_D_COUNTS_AS_RISK = False

# --- ISP rubric thresholds (Step 3-ISP) --------------------------------------
# Interface Segregation: "no client should be forced to depend on methods it
# does not use." Delta-based like DIP: a PR is debited only for interfaces /
# implementers it created or changed, never for pre-existing architecture.
#
# Entity rows (head-state at the merge commit, restricted to AI-touched
# modules) carry the rubric; PR rows carry the delta reasons
# (new_flagged_iface / widened_fat_iface / new_forced_stub).
#
# Interface = ABC/ABCMeta class, typing.Protocol class, or (inventory-only,
# NOT flagged in v1) an implicit base with >= ISP_IMPLICIT_MIN_STUBS stub-body
# methods and an internal subclass. Implementer signals:
#   forced_stub -- an implementer overrides >impl_stub_ratio of the interface's
#                  abstract methods with stub bodies (pass/.../NotImplementedError)
#                  AND at least impl_stub_min methods (absolute guard, same
#                  philosophy as LCOM_MIN_METHODS) -- the textbook ISP smell.
# Interface signals (flag at >= ISP_RISK_MIN_HITS of 3):
#   fat_iface    -- more than iface_abstract_methods abstract methods.
#                   Calibration (first full run 2026-07-20, 203 in-scope
#                   interface rows, BEFORE any labeling): n_abstract_methods
#                   p50=2, p90=6, p95=12, max=31 -- threshold 10 ~= p93
#                   (15/203 rows exceed it). Kept at 10: a 6-8 method
#                   interface is not "fat" in the ISP-literature sense, and
#                   the anchors behave correctly -- mlflow AbstractStore
#                   (15-24 abstracts) hits on width, zfit ZfitModel/ZfitPDF
#                   (12-19 abstracts but implementer coverage 1.0) hit width
#                   only and are NOT flagged (need >=2 hits).
#   low_coverage -- mean implementer coverage < iface_mean_coverage over
#                   >= iface_min_implementers internal implementers.
#   stubbed_impl -- >= 1 in-scope implementer with forced_stub.
# UNVALIDATED until the ISP label loop (make_isp_labeling_sample.py) runs --
# same discipline as SRP: tune on the tuning sample only, confirm on a fresh
# holdout, never re-tune on a burned holdout.
ISP_THRESHOLDS = {
    "iface_abstract_methods": 10,   # fat interface: > this many abstract methods
    "impl_stub_ratio": 0.25,        # implementer stubs > 25% of iface methods...
    "impl_stub_min": 2,             # ...and at least this many stubbed (guard)
    "iface_mean_coverage": 0.75,    # mean implementer coverage below this = low
    "iface_min_implementers": 2,    # coverage signal needs >= 2 implementers
}
ISP_RISK_MIN_HITS = 2               # interface-row flag (of the 3 signals)
# Implicit-interface tier (stub-body methods, no abc/Protocol marker): detected
# and reported with kind="implicit" but excluded from the flag in v1 --
# template-method hooks that intentionally raise are a known FP source.
ISP_IMPLICIT_MIN_STUBS = 2

# --- OCP rubric thresholds (Step 3-OCP) --------------------------------------
# Open/Closed: "open for extension, closed for modification." Delta-based like
# DIP/ISP -- a PR is debited only for dispatch it CREATED or WIDENED, never for
# pre-existing switch statements.
#
# Primary proxy = Robert Martin's canonical OCP violation, the "switch
# statement": a type-/tag-discriminating if/elif or match ladder over one
# subject that must be EDITED to add a variant. `03_principles/ocp/dispatch.py`
# finds these; kinds are isinstance / type_eq / match_type (type-based) and
# value_switch / match_value (tag-based, noisier -> higher bar).
#
# Entity rows (head-state dispatch chains in AI-touched modules) carry the
# dispatch_smell flag; PR rows carry the delta reasons:
#   new_dispatch_smell -- PR introduced a flagged chain absent (by
#                         module+qualname+subject) from the parent snapshot.
#   dispatch_widened   -- a chain present in both grew its branch count (the
#                         textbook "had to edit the conditional to add a case").
# The add-vs-modify def profile (n_defs_added/modified/removed, classes_added)
# is reported RAW on every PR row and NEVER drives the flag: bug fixes
# legitimately modify existing code and static analysis cannot read intent
# (same discipline as ISP's raw client_usage / inventory-only implicit tier).
#
# A chain needs >= 2 branches on one subject to count as dispatch at all; it is
# a smell at:
#   dispatch_min_branches     -- type-based chains (isinstance/type_eq/match_type)
#   value_switch_min_branches -- value/tag chains (value_switch/match_value),
#                                set higher because a value comparison ladder is
#                                a much weaker OCP signal than a type switch.
# CALIBRATED on the first full run (2026-07-24, 398 analyzable PRs, 621 in-scope
# chains, BEFORE any labeling -- same percentile method ISP used for fat_iface):
#   type-based  (n=266): n_branches p50=2, p90=3, p95=4, max=13
#   value-based (n=355): n_branches p50=3, p90=6, p95=9, max=35
# dispatch_min_branches=3 is p90 of type chains AND Robert Martin's canonical
# minimum "switch statement" -- doubly justified. value_switch_min_branches
# raised 4 -> 6 (= value p90): the weaker signal gets the conservative bar, so
# only the largest open-coded value switches flag (a 4-5 branch value ladder is
# too often a legitimate parser/state-machine to headline as an OCP violation).
#
# VALIDATED 2026-07-24 (LLM-labeled by Claude, same provenance caveat as
# SRP/ISP -- NOT human ground truth): tuning 80 rows P=0.60/R=0.77/F1=0.68;
# fresh holdout 54 rows P=0.33/R=0.80/F1=0.47. CONCLUSION: `smell` is a
# HIGH-RECALL SCREEN, not a precise detector -- like SRP, not like ISP. The
# residual error is almost all FALSE POSITIVES: isinstance chains over CLOSED
# type sets (JSON str/dict/list recursion, stdlib AST node grammar, input
# type-normalization, custom serializers/encoders) that a width proxy cannot
# tell from a genuine open-ended type switch. That open-vs-closed-family call
# is SEMANTIC -- the sanctioned precision upgrade (see PROJECT.md section 7) is
# a non-LLM branch-cohesion signal, not more branch-count tuning. Do NOT re-tune
# on the burned holdout; a rubric change needs a third fresh sample.
OCP_THRESHOLDS = {
    "dispatch_min_branches": 3,       # type switch is a smell at >= 3 branches (p90)
    "value_switch_min_branches": 6,   # value/tag switch needs >= 6 (value p90)
}
OCP_RISK_MIN_HITS = 1  # PR flagged if it has >= this many delta reasons

# --- LSP rubric thresholds (Step 3-LSP) --------------------------------------
# Liskov Substitution: "objects of a supertype shall be replaceable with objects
# of a subtype without altering correctness." Delta-based like DIP/ISP/OCP -- a
# PR is debited only for override relationships it CREATED or made incompatible,
# never for pre-existing bad overrides.
#
# LSP is, with SRP, the hardest SOLID principle to automate (TOOLS.md); the
# literature's canonical static signal is a TYPE CHECKER's override-compatibility
# rule (mypy's `[override]` / Pyright's reportIncompatibleMethodOverride):
# an override that STRENGTHENS a precondition or WEAKENS a postcondition breaks
# substitutability. We replicate that rule DETERMINISTICALLY on the AST -- no
# importable per-repo environment (the grimp/mypy-at-scale fragility DIP
# rejected), no LLM -- across every internal subclass->ancestor override pair
# in AI-touched modules. `03_principles/lsp/overrides.py` computes the signals;
# they group into three families (each an established LSP violation):
#
#   STATIC  (structural signature incompatibility -- the mypy [override] rule):
#     param_required_added -- override adds a required parameter a base-contract
#                             caller would not pass -> TypeError (strengthened
#                             precondition).
#     param_removed        -- override drops/cannot accept a parameter the base
#                             accepts (no *args/**kwargs to absorb it).
#   SEMANTIC (type-annotation variance over the internal class hierarchy):
#     param_type_narrowed  -- a parameter is annotated with a strict SUBtype of
#                             the base's type (contravariance violation:
#                             strengthened precondition).
#     return_type_widened  -- the return is annotated with a strict SUPERtype of
#                             the base's (or adds None/Optional/object -- a
#                             covariance violation: weakened postcondition).
#   BEHAVIORAL (contract refusal -- the textbook "refused bequest" smell):
#     refused_bequest      -- the base method has a real working implementation
#                             and the override's body only raises
#                             NotImplementedError/TypeError/... -> the subtype
#                             refuses an inherited contract (Square.set_width,
#                             ReadOnlyList.append, Ostrich.fly).
#
# `strong_signals` drive the per-override `violation` flag; every one is
# individually a recognized substitutability break, so there is no branch-count-
# style continuous threshold to calibrate (unlike OCP) -- the calibration
# question is per-signal PRECISION, answered by the label loop. The harness
# still emits EVERY in-scope override pair (violating or not) so signal
# frequency and precision are measured on the full population, non-circularly.
# `__init__`/`__new__`/`__post_init__` etc. are excluded: constructors are not
# substitutable call sites, so signature drift there is idiomatic, not an LSP
# break (same "exclude constructors" convention as SRP's LCOM).
#
# UNVALIDATED until the LSP label loop (make_lsp_labeling_sample.py) runs --
# same discipline as SRP/ISP/OCP: emit all overrides, label a stratified sample,
# confirm on a fresh holdout, never re-tune on a burned holdout.
LSP_THRESHOLDS = {
    "strong_signals": [
        "refused_bequest",
        "param_required_added",
        "param_removed",
        "param_type_narrowed",
        "return_type_widened",
    ],
}
LSP_RISK_MIN_HITS = 1  # PR flagged if it has >= this many delta reasons

# --- Optional semantic / non-LLM-ML upgrades (ALL OFF by default) ------------
# These gate the sanctioned precision upgrades so the shipped, validated,
# deterministic results NEVER change until a run explicitly opts in. LLMs are
# intentionally not used anywhere; "embeddings" here means small LOCAL non-LLM
# code encoders (e.g. GraphCodeBERT), deterministic at inference. Turning any of
# these on requires `pip install torch transformers` and a RE-RUN + RE-VALIDATION
# of that principle's label loop (see PROJECT.md §7 and the handoff prompt).
#
# Which local encoder embeddings.py loads (Hugging Face id). GraphCodeBERT is
# small (~500MB), data-flow-aware, runs on CPU or a 4GB GPU. Alternatives:
# "microsoft/codebert-base", "microsoft/unixcoder-base", "bigcode/starcoder2-3b".
EMBED_MODEL = "microsoft/graphcodebert-base"
EMBED_CACHE = CORPUS_DIR / "embed_cache.sqlite"   # persistent {sha1: vector}

# SRP C3 backend: "tfidf" = shipped/validated (deterministic, no deps);
# "embed" = mean pairwise cosine over per-method code embeddings (sees meaning,
# not just shared tokens -- the sanctioned SRP precision upgrade). If "embed" is
# requested but torch/transformers is unavailable, run_srp falls back to "tfidf".
SRP_C3_MODE = "tfidf"  # embed-C3 FAILED the gate (2026-07-27): GraphCodeBERT
# cosine does NOT separate god vs cohesive classes (positives 0.914-0.968 sit
# inside negatives 0.856-0.999; recall collapsed to 0). TF-IDF retained. See §7.

# OCP: suppress a dispatch smell whose branch BODIES are semantically homogeneous
# (a closed set -- JSON/AST handlers -- the dominant OCP false-positive source).
# None = off (shipped behavior). A float in [0,1] enables it: suppress when mean
# pairwise branch-embedding cosine >= this (start ~0.75). Needs embeddings.
OCP_BRANCH_COHESION_SUPPRESS = None  # FAILED the gate (2026-07-27): GraphCodeBERT
# branch-cohesion does NOT separate closed-set FPs (mean 0.842) from genuine smells
# (mean 0.860) -- premise inverted, no threshold keeps recall. Kept off. See §7.

# OCP: deterministic (NO ML) closed-set suppressor -- the DETERMINISTIC replacement
# for the failed embedding attempt. A type-dispatch (isinstance/type()==/match-class)
# over a CLOSED type set (all builtins like JSON str/dict/list, or one fixed stdlib
# grammar like ast.*) is exhaustive by construction, not the open-ended extension OCP
# guards against -> not flagged. PASSED the gate 2026-07-27 (a-priori builtins list,
# NOT label-tuned): tuning P 0.60->0.75, holdout P 0.33->0.44, recall kept (all
# suppressed smells were FPs). True = shipped default.
OCP_CLOSED_SET_SUPPRESS = True

# LSP: deterministic (NO embeddings) false-positive suppressor for the 4
# categories the label loop found (holdout P=0.42 -> expected materially higher).
# All 4 are structurally detectable, so this is a pure-rule precision fix:
#   - type-variance against Any / an unbound TypeVar (mypy treats Any compatible)
#   - a param "removed" that is optional AND the override's **kwargs absorbs it
#   - a base that is a pure *args/**kwargs plugin placeholder (stub / sole-raise)
#   - constructor-like factory classmethods (create / from_* / build / new / of)
# False = shipped/validated behavior; True = higher-precision rule (RE-VALIDATE
# on the next full run -- LSP's holdout is NOT burned since signals are categorical).
LSP_SUPPRESS_FPS = True
