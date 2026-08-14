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
DATA_DIR = ROOT_DIR / "AIDev-full"                  # the parquet dataset

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

# Community-adoption floor. Lowered 100 -> 25 (2026-07) as a sample-size fix:
# Claude_Code has only 30 merged Python PRs in the whole dataset at stars>=100
# (permissive license) vs 42 at stars>=25 -- the small-agent samples are
# dataset-bound, so the floor trades a little "adoption credibility" for ~40%
# more Claude_Code/Cursor PRs. Verified counts at stars>=25:
# Claude_Code 42, Copilot 288, Cursor 108, Devin 183, OpenAI_Codex 6017.
MIN_STARS = 25

# Permissive licenses only: legal to clone + redistribute a reproducible benchmark.
# Values match the `license` column (SPDX-ish) in repository.parquet, case-insensitive.
PERMISSIVE_LICENSES = {
    "mit", "mit-0", "apache-2.0", "bsd-3-clause", "bsd-2-clause",
    "0bsd", "isc", "unlicense", "cc0-1.0", "mpl-2.0",
}

# Balanced sampling: keep every PR from small agents, cap the large ones so a
# single agent (OpenAI_Codex) cannot dominate the comparison. Seeded for repro.
PER_AGENT_PR_CAP = 150
RANDOM_SEED = 42

# A PR is in-scope only if it touches at least one source file in this set.
# (Copilot exception: pr_commit_details has NO filenames for Copilot, so its
# PRs are sampled without this pre-filter and the touched-file list is derived
# in Step 2 from the merge commit's git diff; PRs that turn out to touch no
# source file are dropped there.)
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
# Path fragments that mark generated/vendored code -- excluded from extraction
# and analysis (checked as substrings of the repo-relative path).
TS_EXCLUDED_PATH_PARTS = ("node_modules/", "dist/", "build/", ".min.", ".generated.")

GOLD_SET_TS = CORPUS_DIR / "gold_set_ts.parquet"
CLONE_MANIFEST_TS = CORPUS_DIR / "clone_manifest_ts.parquet"
EXTRACT_DIR_TS = CORPUS_DIR / "ai_files_ts"
SRP_RESULTS_TS = CORPUS_DIR / "srp_results_ts.parquet"

# ---------------------------------------------------------------------------
# JAVA (3rd language, 2026-08-02) -- a WITHIN-AGENT language study, not an
# agent comparison. Measured on AIDev-full: only OpenAI_Codex has usable Java
# (878 eligible PRs). The other four have 1, 2, 2 and 0, so a five-agent Java
# comparison is impossible and is not attempted.
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
JAVA_EXCLUDED_PATH_PARTS = (
    "target/", "build/", "generated/", "gen/", "/out/", ".generated.",
)
# Only this agent has enough Java to measure; recorded here so the restriction
# is declarative rather than buried in a script.
JAVA_ONLY_AGENTS = ["OpenAI_Codex"]
# NO CAP. Considered and rejected 2026-08-02 after building it: capping per
# repository and macro-averaging per repository do the SAME job, and only the
# second one is free. Every reported rate is already computed inside each repo
# and then averaged, so a repo with 200 PRs and one with 3 carry equal weight
# whatever the cap is. The cap would have discarded ~60% of the corpus to buy a
# control we already have. Concentration is instead made visible in the report
# (`top_repo_share`) rather than engineered away in the sample.
JAVA_PER_REPO_PR_CAP = None

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
    # SIZE knobs: ~p90 of the production, deduplicated distribution
    # RECALIBRATED 2026-08-04 after the CRLF line-doubling fix (JAVA_STATUS 8.5).
    # Every LOC the Java layer produced was 2x the truth, so the original p90 of
    # 926 was really 463. The corrected production p90 is 463.5 and 465 fires on
    # 10.0% -- the SAME design point. That 930 = 2 x 465 almost exactly is the
    # confirmation that the doubling was uniform across the corpus, which is why
    # the flag set barely moves. Only this knob was affected: the other five are
    # structural (wmc, method count, CCN, params, LCOM) and never used line numbers.
    "class_loc": 465,          # p90=463.5  fires on 10.0%   (was 930 pre-fix)
    "class_wmc": 58,           # p90=58    fires on 10.1%
    "class_methods": 27,       # p90=27    fires on 10.8%
    "func_ccn": 11,            # p90=11    fires on 10.5%
    # SCALE-FREE knobs keep their Python-validated values, exactly as the TS port
    # did. Java's p90 for params is 4, which is not a god-class signal in any
    # taxonomy -- a percentile is a tail heuristic, not a licence to flag
    # ordinary code.
    "func_params": 6,
    "class_lcom": 0.8,
    "lcom_min_methods": 4,
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
LSP_JAVA_STRONG_SIGNALS = [
    "refused_bequest",              # override body is only `throw new UnsupportedOperationException`
    "precondition_strengthened",    # override adds an argument guard that throws
    "postcondition_weakened",       # override returns null where the base does not
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
JAVA_DELTA_THRESHOLDS = {
    "ocp": {"type_branches": 3, "value_branches": 11},
    "isp": {"fat_members": 18, "min_stubs": 2, "stub_ratio": 0.5},
    "dip": {"new_touched_cycles": 0, "delta_mean_d": 0.15},
}

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
ANALYZE_WORKERS = 8    # concurrent PR-tree analyses (CPU-bound, processes; 12 cores)

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
    "class_loc": 200,        # class source lines of code
    "class_wmc": 47,         # weighted methods per class (sum of method CCN)
    "class_methods": 20,     # number of methods in a class
    "class_lcom": 0.8,       # LCOM4-style lack-of-cohesion (0=cohesive, 1=split)
    "file_loc": 400,         # module-level LOC (file-level rows only, see above)
    "func_ccn": 15,          # max cyclomatic complexity of any function
    "func_params": 6,        # max parameter count of any function
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
