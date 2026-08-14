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


```
VibeBench/
├── vibebench/
│   ├── 01_curate/          # Curate PRs from the AIDev dataset
│   ├── 02_clone/           # Clone repos & extract PR file trees
│   ├── 03_principles/      # SOLID principle detectors
│   │   ├── srp/            # Single Responsibility (Python)
│   │   ├── ocp/            # Open-Closed (Python)
│   │   ├── lsp/            # Liskov Substitution (Python)
│   │   ├── isp/            # Interface Segregation (Python)
│   │   ├── dip/            # Dependency Inversion (Python)
│   │   ├── srp_ts/         # SRP (TypeScript)
│   │   ├── ocp_ts/         # OCP (TypeScript)
│   │   ├── lsp_ts/         # LSP (TypeScript)
│   │   ├── isp_ts/         # ISP (TypeScript)
│   │   ├── dip_ts/         # DIP (TypeScript)
│   │   ├── java_lang/      # All 5 principles (Java)
│   │   ├── java_sidecar/   # JavaParser + SymbolSolver (Java)
│   │   ├── ts_sidecar/     # ts-morph fact extractors (TypeScript)
│   │   ├── arch/           # Martin zone classifier (architecture)
│   │   ├── gitsnap.py      # Shared git delta helpers + tree cache
│   │   ├── embeddings.py   # GraphCodeBERT encoder (optional)
│   │   └── java_bridge.py  # JVM bridge for Java sidecar
│   ├── 04_report/          # Generate CSV summaries & reports
│   ├── 05_crosscheck/      # Convergent validity (CK, PMD, Designite)
│   ├── corpus/             # Results parquets, gold sets, clone manifests
│   ├── labels/             # LLM-judge labeling samples & batches
│   └── config.py           # All paths, thresholds, and policy knobs
├── tools/                  # CK, PMD, DesigniteJava configs & patches
├── PROJECT.md              # Full project narrative & status
├── README.md
└── logo.png
```

