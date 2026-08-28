<p align="center">
  <img src="logo.png" alt="VibeBench" width="190">
</p>

<h1 align="center">VibeBench</h1>

<p align="center">
  <b>A code quality benchmark on coding agents real pull requests.</b><br><br>
</p>

Every benchmark for coding agents asks *"does the code work?"* , SWE-bench, HumanEval and
LiveCodeBench all measure functional correctness. This one asks whether the code is worth
maintaining, by measuring adherence to the five SOLID design principles on pull requests that
coding agents actually opened against real repositories.

Each principle is a deterministic structural proxy computed on the PR's **before/after delta**,
so what is reported is what a PR *introduced*, not what was already there. Curated from
[**AIDev**](https://huggingface.co/datasets/hao-li/AIDev) (`AIDev-7.6M`), filtered to permissive
licences and `stars >= 25`.

**Rejected PRs are included on purpose.** A benchmark built only on merged code measures what
reviewers accepted, not what agents produce.

---

### Corpus

3,936 analysable PRs across 729 repositories. Top-1 repository share <= 8.7%, median 330 stars,
and **200 repositories contribute both merged and rejected**, which is what makes a paired
within-repo contrast possible.

| agent | merged | rejected | open* | total |
|---|---:|---:|---:|---:|
| **Claude_Code** | 457 | 283 | 199 | 939 |
| **Copilot** | 1,381 | 676 | 252 | 2,309 |
| **OpenAI_Codex** | 409 | 219 | 60 | 688 |
| **total** | **2,247** | **1,178** | **511** | **3,936** |

<sub>*supplementary only, never a headline: an open PR's head can move after extraction.</sub>

### Principles

| principle | unit | deterministic signal |
|---|---|---|
| **SRP** | class | size / WMC / method count / LCOM4 + C3 conceptual cohesion |
| **OCP** | dispatch site | type- and tag-dispatch chain width, closed-set suppressor |
| **LSP** | override pair | refused bequest, precondition strengthening, postcondition weakening |
| **ISP** | interface | contract width, implementer stub ratio, forced stubs |
| **DIP** | module graph | Tarjan cycles, Martin A/I/D, cross-package scope |

Facts come from **JavaParser + SymbolSolver** on bare source trees, with no build and no
classpath. Cross-checked against **CK**, **PMD** and **DesigniteJava**.

### Pipeline

| # | stage | what it does |
|---|---|---|
| 1 | `01_curate` | filter AIDev, stratify by repo, balance agent x outcome |
| 2 | `02_clone` | resolve before/after trees, extract source |
| 3 | `07_verify` | assert no agent- or outcome-biased loss before any number is computed |
| 4 | `03_principles` | parse every tree, emit all entities **unflagged**, print percentiles |
| 5 | , | set thresholds from those percentiles, then re-run to flag |
| 6 | `04_report` | counts beside rates, Wilson intervals, macro-per-repo, outcome contrast |

Thresholds are calibrated on this corpus *before* labelling, in a pass that flags nothing.
Fitting a threshold on the same data it then judges would be circular.

---

```bash
pip install pandas pyarrow lizard radon          # plus `git` and a JDK on PATH
python vibebench/01_curate/curate.py --lang java
python vibebench/02_clone/clone_and_extract.py --lang java --workers 8
mvn -o package -f vibebench/03_principles/java_sidecar/pom.xml
python vibebench/03_principles/java_lang/run_java.py --calibrate   # then set thresholds
python vibebench/03_principles/java_lang/run_java.py               # flagged run
python vibebench/04_report/report_java.py
```

Third-party sources are not committed; `gold_set_java.parquet` records every
`(repository, pr_number, merge_sha)` needed to rebuild them. Every pure module self-checks when
run directly, and that is the test suite.

Reference labels come from an LLM judge, never human experts, so agreement figures are
**rubric-vs-judge agreement**, not ground truth. Judged samples are blinded to agent, outcome and
the detector's own verdict.
