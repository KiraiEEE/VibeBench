# RESUME HERE, VibeBench worklog

**Read `CLAUDE.md` first for the finished results.** This file is the live worklog.

## STATE (2026-09-07): Java study, thesis AND defence deck all finished

Everything is committed and pushed to **https://github.com/KiraiEEE/VibeBench**.

| | |
|---|---|
| Thesis | 89 pages, 9 figures, 23 tables, XeLaTeX, 0 undefined refs |
| Deck | 20 slides, `thesis/presentation/main.tex`, XeLaTeX |
| Verifier | `verify_thesis_numbers.py`, **28 checks, 0 FAIL** |
| Judged | 1,400 entities (1,000 round 2 + 400 holdout) |

### What the holdout round settled

```
                                   fitted sample     HOLDOUT      pooled
LSP restricted to refused_bequest   1.000 (31/31)  1.000 (33/33)  1.000 (64/64)
ISP restricted to >=30 members      0.862 (25/29)  0.704 (19/27)  0.786 (44/56)
```

A **categorical** rule survived being fitted; a **numeric threshold** did not.
The ISP fitted value falls outside its own holdout interval [0.515, 0.841].

### The recall error the holdout exposed, now fixed everywhere

Recall was computed raw on a 50/50 sample while the corpus is nowhere near
50/50. Weighted (each stratum scaled to its population, Wilson CI):

```
        raw R   weighted R      95% CI          judged
SRP     0.972     0.875     [0.562, 0.975]        200
OCP     0.943     0.733     [0.440, 0.909]        200
DIP     1.000     1.000     [0.410, 1.000]        200
ISP     0.914     0.552     [0.400, 0.700]        400
LSP     0.966     0.352     [0.159, 0.614]        400
```

Flag rates, the agent comparison and merged-vs-rejected are UNAFFECTED: one
detector applied uniformly, so the ratios stand. What broke was the claim that
every flag rate is an upper bound, which now holds only for SRP, OCP and DIP.

### The V1 Python residue that was still in the proxy chapters (fixed)

The thesis described the **Python** evaluators in a Java-only study:

* LSP claimed five signals "mirroring mypy's `[override]` rule". The Java
  detector computes **three**. `javac` already rejects a changed parameter
  list (an overload), narrowed parameter types (invariance) and reduced
  visibility, so those faults cannot exist in code that compiles.
* OCP described `if/elif` and `match` ladders, `isinstance`, and a closed-set
  rule over Python builtins and `ast.*`. Java: `instanceof`/`switch`, and the
  closed set is **the subject resolving to an enum** (6,073 chains).
* ISP described `Protocol`s and implicit interfaces. Java: interfaces and
  abstract classes, 20,705 contracts, >=13 members (86th percentile).
* The toolchain ruled out `grimp`, `import-linter` and `Pyright`. The tools
  that actually needed ruling out are ArchUnit, jdeps, SonarQube and Error
  Prone, all of which read bytecode and would need 1,010 repos to build.

Verified thresholds now in the thesis: OCP type >=3 (62nd pct), tag >=8 (86th),
41,880 chains; ISP >=13 members, 2,944 flagged, 744 forced stubs; LSP 1,546
flagged / 79,689 unflagged pairs. All guarded by the verifier.

### Other fixes this round

* Three finished figures were in `figures/` but never `\input` anywhere. Now
  placed as Figures 3.2, 4.3 and 4.4; the validation one was rebuilt on pooled
  and weighted numbers.
* Figure 4.2 is the three-band TikZ pipeline, not the old PNG. Collisions fixed.
* Abstracts (AR/EN/FR) cut ~40% to fit ONE page. Budget: ~11 lines each.
* Research-question table lost its `Status` column, every row of which said
  "Answered".
* All 21 remaining em dashes gone.
* Per-agent precision table pooled over both rounds, with flag counts, and its
  caption no longer claims a recall range the weighting invalidated.
* Sign-off robot in the appendix redrawn; chest LEDs are the five principle
  colours.
* Tools table now lists the local toolchain (MiKTeX 25.12, XeTeX 4.16,
  latexmk 4.88) beside Overleaf.

### Defence deck, rewritten from scratch

The old one was V1: "Instrument validation, Python", the cut ML experiment,
V1 numbers. 20 new slides. **Beamer traps recorded in the file header:**
do NOT load `enumitem` (its `leftmargin=*` recurses against beamer's
`\labelenumi` and overflows TeX's input stack), and the pipeline figure must
be fitted to frame HEIGHT, not width.

## What is left

1. **Python V2**, if wanted. New corpus, new fact extractor; the framework is
   language-parameterised so only the extraction layer is language-specific.
2. Optional: three figures never design-reviewed (`fig_martin_plane`,
   `fig_delta_harness`, `fig_solid_wheel`).

## Disk: decision recorded 2026-09-06

Project is **352 GB**; 88 GB free on the disk.

```
vibebench/corpus/clones/            338 GB   1,988 git repos
vibebench/corpus/java_facts_cache/  7.7 GB   8,713 parsed fact records
vibebench/corpus/tree_cache/        4.3 GB   6,454 cached source trees (76% of what analysis used)
vibebench/corpus/ai_files_java/     2.1 GB
datasets/                          12.7 GB
```

**DECISION: keep `clones/` for now.** Deleting it frees 338 GB and breaks nothing
that is published, every thesis number regenerates from the parquets. But it is
the only local snapshot of the exact commits, and upstream decay is irreversible:
18 pull requests were already unrecoverable during this study. Revisit only if
space actually blocks work. If deleted, KEEP `tree_cache/` and `java_facts_cache/`.

## What is left after the holdout

1. Fold the holdout numbers into Ch4 `sec:instrument-validation` and Ch5
   limitations, which currently say the refinements are in-sample.
2. Optional: Python V2. Different corpus, so the Java clones do not help it much.
3. Optional polish: 21 remaining em dashes; three figures never inspected for
   design (`fig_martin_plane`, `fig_delta_harness`, `fig_solid_wheel`).

## House rules

* Never write an em dash; use a comma. Applies to the thesis too.
* Report all FIVE principles in every table.
* Lead with macro-per-repo, print counts beside rates.
* Every caveat in ONE limitations section after the conclusion.
* No citation without reading its arXiv page first.
* Do not stop for permission on routine steps.

## Traps (each cost real time)

* `ps -W` reports zero python while several run. Use PowerShell `Get-Process`.
* Killing a background bash script does not kill its child python.
* `cd` in a Bash call PERSISTS and breaks relative paths. Twice looked like data loss.
* **Bash heredocs mangle backslashes.** Write patch scripts to a file, then run them.
* Never capitalise "first word on a line" in LaTeX; source wraps mid-sentence.
* A partial result that looks complete is THE recurring bug here.
