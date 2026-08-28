<p align="center">
  <img src="logo.png" alt="VibeBench" width="190">
</p>

<h1 align="center">VibeBench</h1>

<p align="center">
  <b>A code quality benchmark on coding agents real pull requests.</b><br><br>
</p>

Every benchmark for coding agents asks *"does the code work?"* , SWE-bench, HumanEval,
LiveCodeBench all measure functional correctness. This one asks whether the code is worth
maintaining, by measuring adherence to the five SOLID design principles on pull requests that
coding agents actually opened against real repositories.

Each principle is measured by a deterministic structural proxy, computed on the **before/after
delta** of each PR, and validated against a reading judge and three independent published tools.

**Merged PRs are not the whole story.** A benchmark built only on merged code measures what
reviewers *accepted*, not what agents *produce*. This corpus deliberately includes **rejected**
and **open** pull requests, balanced within each agent, so that adherence can be split by
outcome and the review filter can be separated from the generator.

---

## 1. Source dataset

Curated from [**AIDev**](https://huggingface.co/datasets/hao-li/AIDev), a public dataset of
pull requests opened by autonomous coding agents on GitHub.

| | |
|---|---|
| Release used | `AIDev-7.6M` |
| Pull requests | 7,685,281 |
| Repositories | 957,209 |

> **Table trap.** In every AIDev release, `pull_request.parquet` and `repository.parquet` contain
> only the curated >100-star subset. The master tables are **`all_pull_request.parquet`** and
> **`all_repository.parquet`**. Reading the wrong pair silently under-counts by ~8x.

### Eligibility filter

| filter | rule | why |
|---|---|---|
| Language | PR touches >= 1 `.java` file | extension-based, not repo-language proxy |
| Stars | `>= 25` | excludes toy and scratch repositories |
| License | permissive (MIT / Apache-2.0 / BSD / ISC / Unlicense) | redistribution of measurements |
| Generated code | `target/`, `build/`, `generated/`, `gen/`, `out/`, `*-gen/` excluded | machine-written code is not agent-authored |

Exclusions are matched on **whole path components**, never substrings, so `gradlebuild/` is not
mistaken for `build/` and `riksdagen/` is not mistaken for `gen/`.

---

## 2. Curated corpus (Java)

**6,956 PRs drawn, 3,936 analysable across 729 repositories.** The shortfall is dominated by
PRs that touch no Java at all (AIDev supplies no file list for roughly half of them, so they are
drawn by repository language and filtered on the real diff); the technical failure rate is 3.1%.

### Design: 3 agents x 3 outcomes

| agent | merged | rejected | open* | total |
|---|---:|---:|---:|---:|
| **Claude_Code** | 457 | 283 | 199 | 939 |
| **Copilot** | 1,381 | 676 | 252 | 2,309 |
| **OpenAI_Codex** | 409 | 219 | 60 | 688 |
| **total** | **2,247** | **1,178** | **511** | **3,936** |

<sub>*`open` is a **supplementary** outcome, never part of a headline claim. An open PR's head can
move after extraction, and `OpenAI_Codex/open` (n=60) is too small to carry a per-cell rate.</sub>

Rejected PRs carry a stratum, so "rejected" is never treated as one uniform thing:

| stratum | n | meaning |
|---|---:|---|
| `closed_unreviewed` | 1,005 | closed without a review |
| `reviewed_then_closed` | 137 | reviewed, then closed |
| `changes_requested` | 36 | changes requested, never merged |

### Why this corpus resists the usual attacks

| attack | answer |
|---|---|
| *"One repo dominates your results."* | Top-1 repo share <= 8.7%, top-3 <= 20.8% for every agent |
| *"You compared different repositories."* | **200 repositories contribute BOTH merged and rejected**, enabling a paired within-repo contrast |
| *"Only toy projects."* | Median 330 stars; 110 repositories above 5,000 stars |
| *"Your extraction was biased."* | Where file attribution exists, usable-rate spread across agents is **2.2 pp** |

### Repository profile

| stars | repositories |
|---|---:|
| 25 , 100 | 206 |
| 100 , 1k | 285 |
| 1k , 5k | 128 |
| 5k , 20k | 80 |
| **20k+** | **30** |

Median 330 stars. The corpus spans from small maintained projects to some of the largest Java
codebases in existence (TensorFlow, Flutter, Spring Boot, Spring Framework, Kafka, Spark,
Keycloak, Selenium, Ghidra, RxJava).

---

## 3. What each principle measures

All five are computed on the PR's before/after delta, so the reported violations are the ones a
PR **introduced**, distinct from those merely **present** in the code it touched.

| principle | unit | deterministic signal |
|---|---|---|
| **SRP** | class | size / WMC / method count / LCOM4 cohesion + C3 conceptual cohesion |
| **OCP** | dispatch site | type- and tag-dispatch chain width, with a closed-set suppressor |
| **LSP** | override pair | refused bequest, precondition strengthening, postcondition weakening |
| **ISP** | interface | contract width, implementer stub ratio, forced stubs |
| **DIP** | module graph | Tarjan cycles, Martin A/I/D metrics, cross-package scope |

> **Java's type system decides which LSP violations are even expressible.** `javac` already
> enforces parameter invariance, forbids visibility narrowing and broader checked exceptions, and
> treats a changed parameter list as an overload rather than an override. Only the three signals a
> compiler *cannot* check are kept. Which violations a language permits is itself a finding.

**Thresholds are percentile-calibrated on this corpus before any labelling**, in a separate pass
that emits every entity unflagged. Fitting a threshold on the same data it then judges would be
circular.

---

## 4. Workflow

| # | stage | what it does |
|---|---|---|
| 1 | `01_curate` | filter AIDev, stratify by repo, balance agent x outcome |
| 2 | `02_clone` | resolve each PR's before/after trees, extract source |
| 3 | `07_verify` | assert no agent- or outcome-biased loss **before** any number is computed |
| 4 | pilot | rehearsal across all 9 agent x outcome cells |
| 5 | `03_principles` | parse every tree, emit all entities **unflagged**, print percentiles |
| 6 | calibrate | set thresholds from those percentiles |
| 7 | `03_principles` | re-run with thresholds applied, producing flags |
| 8 | `04_report` | counts beside rates, Wilson intervals, macro-per-repo, outcome contrast |

### Resolving a PR's before/after pair

Most PRs in this dataset have no merge commit (every rejected and open PR, and most merged ones,
since `pr_timeline` covers only a fraction of the release). Three tiers are tried in order, and
**the tier used is recorded on every row** so a merged-vs-rejected contrast is never partly a
comparison of how the trees were built:

| tier | method |
|---|---|
| 1 | merge commit, diffed against its first parent |
| 2 | the PR's own commit set |
| 3 | `refs/pull/<N>/head` + merge-base against the target branch |

### Engineering guarantees

- **Atomic writes** , every parquet and CSV is written to a temp file, fsynced, then renamed. A
  truncated parquet is unreadable rather than degraded, so one interrupted checkpoint would
  otherwise destroy a whole run's bookkeeping.
- **Resumable** , checkpointed every 25 PRs; permanent statuses are kept and transient ones retried.
- **Network-outage safe** , git operations pause and retry rather than racing ahead marking failures.
- **Hard-stop safe** , git locks left by a power cut or reboot are swept at start-up. A surviving
  `shallow.lock` otherwise makes every later git call in that repository fail instantly, and those
  failures look exactly like ordinary fetch failures.

---

## 5. Tools

| tool | role |
|---|---|
| **JavaParser + SymbolSolver** | fact extraction with real symbol resolution, on bare source trees with no build and no classpath |
| **lizard**, **radon** | complexity and size metrics (Python) |
| **CK 0.7.0** | independent size/complexity metrics, rank correlation only |
| **PMD 7.7.0** | independent GodClass detection |
| **DesigniteJava 1.1.2** | independent design-smell detection |

Symbol resolution is what makes the Java measurement trustworthy: two classes named `Node` in
different packages must not collapse into one, or every hierarchy built on top is corrupt. The
solver is rooted at each inferred **source root**, derived from the files' own `package`
declarations, which works for Maven, Gradle, multi-module monorepos and flat layouts alike.

ArchUnit and JDepend are deliberately excluded: both are bytecode-only, and JDepend computes the
same Martin A/I/D metrics and cycles already shared across languages here.

---

## 6. Layout

```
VibeBench/
├── vibebench/
│   ├── 01_curate/          # Curate PRs from AIDev, balance agent x outcome
│   ├── 02_clone/           # Resolve before/after trees, extract source
│   ├── 03_principles/      # SOLID detectors
│   │   ├── srp/ ocp/ lsp/ isp/ dip/    # per-principle (Python)
│   │   ├── java_lang/      # all 5 principles (Java), one delta pass
│   │   ├── java_sidecar/   # JavaParser + SymbolSolver fact extractor
│   │   ├── java_bridge.py  # JVM bridge, content-hashed facts cache
│   │   ├── arch/           # Martin zone classifier (descriptive only)
│   │   ├── churn.py        # modification-frequency signal
│   │   ├── gitsnap.py      # shared git layer: delta pairs, tree cache, scope rule
│   │   └── embeddings.py   # local code encoder (tested, not shipped)
│   ├── 04_report/          # Counts, Wilson CIs, macro-per-repo, outcome contrast
│   ├── 05_crosscheck/      # Convergent validity (CK / PMD / DesigniteJava)
│   ├── 06_enrich/          # GitHub API enrichment of PR outcomes
│   ├── 07_verify/          # Per-phase checks; fails loudly before numbers are quoted
│   ├── 08_findings/        # Hypothesis answering
│   ├── corpus/             # Gold sets, clone manifests, result parquets
│   ├── labels/             # Judge samples, blinding sidecars, scoring
│   ├── safeio.py           # Atomic writes + disk guard
│   └── config.py           # All paths, thresholds and policy knobs
├── tools/                  # CK, PMD, DesigniteJava configs & patches
└── archive/                # Superseded V1 corpus and results
```

---

## 7. Reproducing

Third-party sources are **not** committed. The clone directory (~188 GB) and the raw AIDev
release (~13 GB) are both rebuildable from what is here: `gold_set_java.parquet` records every
`(repository, pr_number, merge_sha)` in the corpus.

```bash
pip install pandas pyarrow lizard radon          # plus `git` and a JDK on PATH

python vibebench/01_curate/curate.py --lang java
python vibebench/02_clone/clone_and_extract.py --lang java --workers 8
mvn -o package -f vibebench/03_principles/java_sidecar/pom.xml

python vibebench/07_verify/verify_phase.py --phase extract --lang java
python vibebench/03_principles/java_lang/run_java.py --pilot --cells 6
python vibebench/03_principles/java_lang/run_java.py --calibrate     # then set thresholds
python vibebench/03_principles/java_lang/run_java.py                 # flagged run
python vibebench/04_report/report_java.py
```

Every pure module self-checks when run directly, and this is the test suite:

```bash
python vibebench/03_principles/gitsnap.py
python vibebench/03_principles/java_bridge.py
python vibebench/03_principles/java_lang/detectors.py
python vibebench/safeio.py
java -cp vibebench/03_principles/java_sidecar/target/java-sidecar-1.0-jar-with-dependencies.jar \
     vb.Facts --self-check
```

---

## 8. Reading the results

Three rules, each of which exists because ignoring it produced a wrong answer here at least once.

1. **Counts beside rates, always.** Per-agent violation counts are single-digit for most
   principles. A percentage over n=3 is not a measurement.
2. **Lead with macro-per-repo, not pooled.** They can invert when one repository is
   over-represented in an agent's sample.
3. **Two different numbers exist and must never be conflated:** violations *present* in the code a
   PR touched (which includes pre-existing human code) versus violations the PR *introduced*.
   The contrast between them is the finding.

Reference labels are produced by an LLM judge, never by human experts. Agreement figures are
therefore **rubric-vs-judge agreement**, not ground truth, and are reported as such. Judged
samples are blinded: the agent, the outcome and the detector's own verdict are withheld from the
judge and rejoined afterwards through a sidecar.
