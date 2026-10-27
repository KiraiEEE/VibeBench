<p align="center">
  <img src="logo.png" alt="VibeBench" width="190">
</p>

<h1 align="center">VibeBench</h1>

<p align="center">
  <b>A code quality benchmark on coding agents' real pull requests.</b><br><br>
</p>

VibeBench measures whether code written by autonomous coding agents (Claude Code, GitHub Copilot and
OpenAI Codex) follows the five SOLID design principles. Most agent benchmarks only check whether the
code passes tests. Here we look at the design of the code the agents actually submitted to open-source
projects, using pull requests from the [AIDev](https://arxiv.org/abs/2507.15003) dataset: merged,
rejected and still open.

The study was run on **4,245 Java pull requests** (790 repositories) and repeated on **4,946 Python
pull requests** (2,921 repositories). A result counts as general only if it shows up in both
languages.

This is the code and data behind the Research Master's thesis *Evaluating Coding Agent Quality:
Application to Software Design Principles* (ISSAT Sousse, 2025/2026).

## What we found

- **Agents rarely introduce SOLID violations.** After adjusting for detector precision, 0.4% to 4.1%
  of Java pull requests and 0.3% to 3.5% of Python pull requests introduce a violation of a given
  principle. The code they touch already contains far more. For example, Java has 183
  refused-bequest violations in the touched code, and only 16 pull requests introduced one.
- **Change size matters more than which agent made the change.** Each doubling of the files touched
  roughly doubles the odds of a flag (odds ratio 1.88 to 2.18). Only one agent difference holds in
  both languages: Claude Code gets more Open/Closed flags (odds ratio 2.22 in Java, 2.09 in Python).
- **Two principles can be measured well, three can only be screened.** New import cycles
  (Dependency Inversion) and refused bequest (Liskov) agree closely with the judge. The Single
  Responsibility, Open/Closed and Interface Segregation detectors point at code worth reading, but
  most of their flags are not real violations.
- **Merged code looks like unreviewed code in Java.** In Python, merged code has fewer flags than
  abandoned code.

Detector precision against the reading judge:

| Principle | Java | Python |
|---|---|---|
| SRP, Single Responsibility | 0.350 | 0.110 |
| OCP, Open/Closed | 0.330 | 0.390 |
| LSP, Liskov Substitution | 0.420 (1.000 for refused bequest only) | 0.510 |
| ISP, Interface Segregation | 0.335 | 0.425 |
| DIP, Dependency Inversion | 0.892 | 1.000 |

The full results, with recall, the statistical model and the limitations, are in
[`FINDINGS.md`](FINDINGS.md) and the thesis.

## How it works

1. **Curate.** Pull requests that change Java or Python files are drawn from AIDev (repositories
   with at least 25 stars and a permissive licence). Rejected and open pull requests are
   over-sampled so the outcomes can be compared.
2. **Rebuild the before and after trees.** For each pull request, the repository is recovered
   immediately before and after the change, with four fallback methods for deleted branches.
3. **Detect.** One deterministic detector per principle runs on both trees, and the pull request is
   charged only with the difference. Java is parsed with JavaParser and its symbol solver, and Python
   with the standard `ast` module. The detectors themselves are shared.
4. **Validate.** For each principle, 100 flagged and 100 unflagged entities are labelled by an LLM
   judge that reads the source and writes a reason for every label. In total, 2,475 entities were
   labelled. Recall is weighted by stratum size. The Java detectors are also compared with CK, PMD
   and DesigniteJava.
5. **Model.** A logistic model (GEE, clustered by repository) separates the agent from change size
   and time.

## Repository layout

```
vibebench/
  config.py           every threshold, path and policy setting in one place
  01_curate/          corpus sampling from AIDev
  02_clone/           cloning, file lists, before/after recovery
  03_principles/      the five detectors (java_lang/, py_lang/) and the Java parser sidecar
  04_report/          per-agent and per-outcome reports
  05_crosscheck/      comparison with CK, PMD and DesigniteJava
  06_model/           the joint statistical model
  07_verify/          audits, and the script that checks the thesis numbers against the data
  labels/             judging rubrics, labelled samples and the scoring scripts
thesis/
  latex/              the thesis (XeLaTeX)
  presentation/       the defence deck (PowerPoint)
  paper/              the research paper (ACM format, pdfLaTeX)
tools/                CK, PMD and DesigniteJava setup for the cross-check
logs_archive/         logs of the runs the reported numbers come from
FINDINGS.md           results and conclusions in one page
```

## Running it

You need Python 3.11 with `pandas`, `pyarrow` and `statsmodels`, plus `git` and a JDK 17 on the
path. Maven is only needed to build the Java parser sidecar. The AIDev tables go in
`datasets/AIDev-7.6M/`.

Run the scripts from the repository root, for example:

```
python vibebench/01_curate/curate.py --lang java
python vibebench/02_clone/clone_and_extract.py --lang java --workers 8
python vibebench/03_principles/java_lang/run_java.py --workers 6
python vibebench/06_model/fit_mixed_model.py --lang java
```

The full run order for both languages, including labelling and scoring, is in
[`CLAUDE.md`](CLAUDE.md). A full Java run clones about a thousand repositories and needs a lot of
disk. The working tree reaches about 17 GB, but only about 35 MB of it is committed: clones, caches
and extracted third-party source are rebuilt by the scripts (see `.gitignore`).

To check that the numbers in the thesis still match the data:

```
python vibebench/07_verify/verify_thesis_numbers.py
```

It should report 61 checks and 0 failures.

## Limitations in short

Every precision and recall figure is agreement with an LLM judge, not ground truth. The detectors
measure structures that violations tend to leave, not the principles themselves. The study is
observational, so agent differences are associations. Both corpora come from one dataset. The thesis
has a full limitations section.

## Licence

[MIT](LICENSE): free to use, copy and modify, for research or anything else, as long as the
copyright notice is kept. If you use VibeBench in a paper, please cite the thesis. The AIDev dataset
and the analysed repositories keep their own licences.
