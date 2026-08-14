<p align="center">
  <img src="logo.png" alt="VibeBench" width="190">
</p>

<h1 align="center">VibeBench</h1>

<p align="center">
  <b>Do AI coding agents write code worth maintaining?</b><br>
  A design-quality benchmark on real merged pull requests.
</p>

---

Every benchmark for coding agents asks *"does the code work?"* — SWE-bench, HumanEval,
LiveCodeBench all measure functional correctness. This one asks whether the code is worth
maintaining, by measuring adherence to the five SOLID design principles on pull requests that
five agents actually got **merged** into real repositories.

Each principle is measured by a deterministic structural proxy, computed on the **before/after
delta** of each PR, and validated against a reading judge and three independent published tools.

## How it works

```mermaid
flowchart TD

  subgraph S1["1 · Curate"]
    direction LR
    A1[("AIDev dataset<br>931,785 PRs")] --> A2["merged only<br>permissive licence<br>25+ stars"]
    A2 --> A3["cap 150 per agent"]
    A3 --> A4[("gold_set.parquet<br>repo · pr · merge_sha")]
  end

  subgraph S2["2 · Acquire — per pull request"]
    direction LR
    B1["resolve merge commit<br>and its first parent"] --> B2["git archive<br>BEFORE tree · AFTER tree"]
    B2 --> B3[("tree cache<br>keyed by repo + sha")]
  end

  subgraph S3["3 · Parse to structural facts"]
    direction LR
    C1["<b>Python</b><br>ast · lizard · radon"]
    C2["<b>TypeScript</b><br>ts-morph sidecar"]
    C3["<b>Java</b><br>JavaParser + SymbolSolver<br><i>91% symbols, no build</i>"]
  end

  subgraph S4["4 · Detect — five pure functions, run on BOTH trees"]
    direction LR
    D1["<b>SRP</b><br>size · complexity<br>LCOM · C3"]
    D2["<b>OCP</b><br>type-switch<br>branch width"]
    D3["<b>LSP</b><br>override vs base<br>3 signals"]
    D4["<b>ISP</b><br>contract width<br>forced stubs"]
    D5["<b>DIP</b><br>import graph<br>Tarjan cycles"]
  end

  subgraph S5["5 · Delta — attribution"]
    direction LR
    E1["smells in AFTER tree"] --> E2{{"set difference<br>on named entities"}}
    E3["smells in BEFORE tree"] --> E2
    E2 --> E4["charged to this PR"]
  end

  subgraph S6["6 · Report"]
    direction LR
    F1["pooled + macro per repo"] --> F2["Wilson intervals<br>test vs production<br>authored vs edited"]
    F2 --> F3[("summary CSVs")]
  end

  subgraph S7["7 · Validate — 741 rows"]
    direction LR
    G1["stratified sample"] --> G2["extract source<br><i>blind: verdict hidden</i>"]
    G2 --> G3["judge reads and labels"] --> G4["P / R / F1<br>tuning, then holdout"]
  end

  subgraph S8["8 · Cross-check — Java"]
    direction LR
    H1["CK · PMD · DesigniteJava<br>same trees, same classes"] --> H2["Spearman rho<br>Cohen kappa"]
  end

  CAL["<i>thresholds set from this run's own<br>percentile distribution, BEFORE any<br>labelling — keeps validation non-circular</i>"]

  S1 --> S2 --> S3 --> S4 --> S5 --> S6 --> S8
  S5 --> S7
  S4 -.-> CAL

  classDef note fill:none,stroke-dasharray:4 3
  class CAL note
```

**Detect** is pure — no file access, no git, no network. It takes a `{path: source}` dict and
returns findings, so it is testable in isolation, and every threshold lives in one config file.
Each detector self-checks when run as a script.

**Delta** is a *set* comparison, not a subtraction: named smell entities present after the merge
but absent at its parent. That is what attributes a violation to the pull request that created it
rather than to the repository that already had it — and it is why a PR that *removes* a module
from a cycle is not charged for one.

**Both halves are scoped by ripple**, not just by the edited file: a base-class change that breaks
an untouched subclass is still attributed, since the relation is what the principle is about.