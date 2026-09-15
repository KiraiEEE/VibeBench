<p align="center">
  <img src="logo.png" alt="VibeBench" width="190">
</p>

<h1 align="center">VibeBench</h1>

<p align="center">
  <b>A code quality benchmark on coding agents' real pull requests.</b><br><br>
</p>

VibeBench measures whether code written by autonomous coding agents (Claude Code, GitHub Copilot,
OpenAI Codex) follows the five SOLID design principles. It analyses real pull requests from the
AIDev dataset, merged, rejected and open, with one deterministic detector per principle, and
validates every detector against a reading judge. The study was run on 4,245 Java pull requests and
replicated on 4,946 Python pull requests.

* **Results and conclusions:** [`FINDINGS.md`](FINDINGS.md)
* **Thesis:** `thesis/latex/` (Research Master's, ISSAT Sousse, 2025/2026)
* **Defence presentation:** `thesis/presentation/`
* **Pipeline:** `vibebench/`, run orders in [`CLAUDE.md`](CLAUDE.md)
* **Check the thesis against the data:** `python vibebench/07_verify/verify_thesis_numbers.py`

### Licence
