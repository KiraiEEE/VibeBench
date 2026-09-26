# VibeBench findings

**Topic:** *Evaluating Coding Agent Quality: Application to Software Design Principles.*
Do autonomous coding agents write code that follows the five SOLID principles, and can that be
measured reliably at scale?

Every number below is in the thesis (`thesis/latex/`) and the headline ones are recomputed from the
data by `python vibebench/07_verify/verify_thesis_numbers.py` (53 checks, 0 FAIL on 15 September
2026). Nothing here is newer than the thesis.

## What was measured

| | Java | Python |
|---|---|---|
| Curated pull requests | 6,956 (1,010 repos) | 7,050 (3,974 repos) |
| Analysable pull requests | 4,245 | 4,946 (2,921 repos) |
| Agents | Claude Code, GitHub Copilot, OpenAI Codex | same |
| Outcomes | merged, rejected, open | same |
| Entities judged | 1,495 | 980 |

Source: AIDev-7.6M (7,685,281 agent pull requests in 957,209 repositories). One deterministic
detector per principle; each detector checked against an LLM reading judge on 100 flagged and 100
unflagged entities, with recall weighted by stratum size. Java detectors also compared with CK, PMD
and DesigniteJava.

## Result 1: how well SOLID can be measured (RQ2)

Precision / stratum-weighted recall:

| Principle | Java | Python |
|---|---|---|
| SRP | 0.350 / 0.828 | 0.110 / 1.000 |
| OCP | 0.330 / 0.742 | 0.390 / 0.364 |
| LSP | 0.420 / 0.386 | 0.510 / 0.545 |
| ISP | 0.335 / 0.464 | 0.425 / 1.000 (all 80 flags judged) |
| DIP | **0.892** / 1.000 (all 195 flags judged) | **1.000** / 1.000 |

* A new dependency cycle (DIP) is detected precisely in both languages.
* SRP, OCP and ISP detectors are screens: most flags do not survive reading.
* Liskov restricted to **refused bequest** (an override that only throws where the base works):
  64 of 64 in Java over two samples, 6 of 7 in Python. A *categorical* rule held; the numeric ISP
  rule (at least 30 members) fell from 0.862 to 0.704 on the Java holdout and scored 1 of 5 in Python.
* Against published tools (Java, 82,553 types): our God Class rubric agrees with PMD at kappa 0.574
  and with DesigniteJava at 0.531, while PMD and DesigniteJava agree at 0.384.
* The OCP detector, compared with DesigniteJava's *Missing Hierarchy* and *Unexploited
  Encapsulation* over 63,326 types in 138 repositories, agrees at kappa 0.122 only, and fires three
  times as often. It measures something narrower than the published smell.
* The compiler matters: 46 of the 51 confirmed Python Liskov violations are faults `javac` rejects.

## Result 2: agent differences are mostly change size (RQ1)

* Mean files per pull request: Java Claude Code 46.4, Copilot 18.5, Codex 10.9.
* Joint model (agent + log files + month, GEE clustered by repository): each doubling of files
  touched multiplies the odds of a flag by 1.88 to 1.95 in Java and 2.01 to 2.18 in Python.
* One agent difference replicates: **Claude Code on Open/Closed**, odds ratio 2.22 (1.21 to 4.07,
  p = 0.010) in Java and 2.09 (1.44 to 3.04, p = 0.0001) in Python. The Python estimate passes a
  Bonferroni correction; the Java one alone does not, so the claim rests on the replication.
* Python also shows Claude Code introducing more import cycles (1.88, p = 0.0096), but it does not
  pass the correction and Java does not show it: not confirmed.
* Time has no detectable effect on the delta principles in either language.

## Result 3: merged against unmerged code (RQ1)

Only reviewed rejections express a judgement about code: 171 of 1,177 unmerged Java requests and
196 of 1,496 Python ones.

* **Java:** merged code is indistinguishable from code closed without review.
* **Python:** merged code is cleaner. After size and agent, unmerged requests have higher odds of an
  Open/Closed flag (1.57) and a Liskov flag (2.43).
* Neither shows what reviewers select on, because most unmerged requests were never reviewed.

## Result 4: most problems were there before the agent (RQ3)

| | Java | Python |
|---|---|---|
| Refused-bequest relationships present in touched production code | 183 | 176 |
| Pull requests that introduced one | 16 | 5 |
| PRs introducing a genuine violation (precision-corrected, by principle) | 0.4% to 4.1% | 0.3% to 3.5% |
| SRP flag rate, classes agents authored vs existing classes they edited (pooled) | 7.7% vs 13.5% | 8.9% vs 12.1% |

Violations inside one unit (a class, a conditional) can be written by one change. Violations in a
relation (base and override, interface and implementers, module cycle) need both sides, which one
change rarely writes, so they build up over many changes and authors.

## Result 5: what holds in both languages (RQ4)

| Holds in both | Does not hold |
|---|---|
| Change size dominates | Merged equals unreviewed (Java yes, Python no) |
| Claude Code's higher Open/Closed rate | SRP detector precision (0.350 to 0.110) |
| Present violations far exceed introduced ones | |
| Authored classes flagged less than edited ones | |
| Categorical refinement transfers, numeric one does not | |

## Conclusions for the topic

1. **Coding agent quality can be measured on design principles, but only partly with rules.**
   Dependency cycles and refused bequest are reliable signals; responsibility, openness and
   interface width need a reader.
2. **Which agent matters less than how much it changes.** Comparisons of agents that do not adjust
   for change size mostly measure change size.
3. **Agents are not the main source of design debt in the code they touch.** Their new classes are
   flagged less than the classes they edit, and few pull requests introduce violations.
4. **Review of one change at a time cannot see relation-level problems.** They need a view of the
   repository over time.
5. **For an automated review check:** add refused bequest; in dynamic languages also check that an
   override accepts its base's calls; do not rely on interface-width thresholds; treat SRP and OCP
   flags as prompts for discussion.

## Main limitations

SOLID adherence is not measured cost or defects. The judge is an LLM of the same family as one
agent. Only Java has an independent-tool check, and for OCP that check agrees at kappa 0.122 only.
Agents chose their own tasks, so results are associations. Reviewed rejections are few. Full list:
thesis Section 5.5.
