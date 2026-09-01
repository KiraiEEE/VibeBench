# VibeBench V2, outcome-balanced redesign

Written 2026-08-25. This SUPERSEDES `HANDOFF_JAVA_RUN.md` (the V1 Java rerun, now
CANCELLED). Nothing in V1 is deleted: its defect fixes, tooling, judging rubric and
Python results all carry over. Only the corpus DESIGN changes.

## 1. Why V2 exists

V1 measured merged PRs only. Merged PRs passed human review, so the corpus was
filtered by the very quality signal we are trying to measure. That is
**survivorship bias**, and it is a legitimate attack on the result.

V2 fixes it by adding PRs that were NOT merged, so every rate can be reported
three ways: merged only, not-merged only, and pooled.

**State the motivation accurately.** Merged PRs DID violate SOLID in V1 (Python:
44 OCP introductions, 5 LSP introductions, SRP flagged 12-13% of classes). So the
justification is not "merged code cannot violate, therefore we found nothing". It
is: *we could not tell whether conformance relates to acceptance, because we only
ever saw the accepted side.* V2 turns that assumption into a testable comparison.

## 2. VERIFIED FEASIBILITY (measured 2026-08-25, do not re-derive)

### 2.1 The outcome split exists

`datasets/AIDev-7.6M/all_pull_request.parquet`, 7,685,281 PRs:

| outcome | definition | PRs | share |
|---|---|---|---|
| merged | `merged_at` not null | 6,216,792 | 80.9% |
| rejected | `state=="closed"` and `merged_at` null | 733,321 | 9.5% |
| open | `state=="open"` | 735,168 | 9.6% |

Rejection rate varies **3.6x by agent**: Codex 6.5%, Claude_Code 7.8%,
Cursor 14.3%, Google_Jules 15.6%, Copilot 15.8%, Devin 23.3%.
**That spread is a headline result on its own and needs no code analysis at all.**

### 2.2 Eligible pool after the project filters

stars>=25, permissive license, 3 agents, repo primary language. **Use this route, not
the file-extension route**, for the reason measured in 2.6 below.

| lang | agent | merged | rejected | open |
|---|---|---|---|---|
| Python | Claude_Code | 26,055 | 6,566 | 3,243 |
| Python | Copilot | 9,097 | 3,716 | 935 |
| Python | OpenAI_Codex | 14,276 | 5,411 | 1,062 |
| Java | Claude_Code | 1,954 | 470 | 280 |
| Java | Copilot | 2,495 | 1,173 | 334 |
| Java | OpenAI_Codex | 1,790 | 284 | 83 |

**The binding constraint is Java rejected OpenAI_Codex = 284.** A balanced
3-agent x 2-outcome Java design therefore caps near 280 per cell, giving about
**1,680 Java PRs** (3 agents x 2 outcomes x 280). Do NOT try to raise this with the
file-extension filter: 2.6 shows that route destroys the corpus.

Python is not constrained: 3,716 or more rejected per agent.

### 2.3 Rejected PR code IS still reachable

Rejected and open PRs have NO merge commit, which breaks every existing delta
harness (they all key on `merge_sha`). **Solved:** GitHub keeps `refs/pull/<N>/head`
on the BASE repo even after the fork or source branch is deleted.

Tested live on 4 real rejected Java PRs from this dataset, **4 of 4 fetched**:
macrozheng/mall#941, spring-projects/spring-boot#49594,
NationalSecurityAgency/ghidra#8856, dbeaver/dbeaver#40434.

```
git fetch --depth 2 origin refs/pull/<N>/head     # FETCH_HEAD = the PR tip
```

Measure the real success rate across the full selection during curation and record
it per agent. Deleted or now-private repos will fail, and a per-agent-biased loss
is a bias, not an inconvenience.

### 2.4 THE CONFOUND, and it is serious (measured, not speculated)

"Rejected" does NOT mean "rejected for quality". Coverage of `pr_reviews`:

| lang | outcome | n | has any recorded review |
|---|---|---|---|
| Python | merged | 49,428 | 12.1% |
| Python | rejected | 15,693 | **6.2%** |
| Java | merged | 6,239 | 25.3% |
| Java | rejected | 1,927 | **10.8%** |

Rejected PRs are **half as likely to have been reviewed at all**. Most were closed
with no recorded review: superseded, stale, duplicate, abandoned or bulk-closed,
not judged and refused. Across the entire dataset there are only 13,325
`CHANGES_REQUESTED` reviews.

**Required response, do not skip.** Stratify "not merged" into at least:

- `changes_requested`, has a CHANGES_REQUESTED review, the strongest quality signal
- `reviewed_then_closed`, has some review, closed unmerged
- `closed_unreviewed`, no review recorded, the majority

and report those strata separately. Use `pr_timeline.parquet` (`event`, `actor`) to
separate author-closed (abandonment) from maintainer-closed. If the quality-relevant
strata come out too small, say so and report the comparison as descriptive with the
confound stated once. **Do NOT publish a bare "merged vs rejected" violation rate as
if rejection meant quality refusal.** That is the single biggest jury risk in V2.

### 2.5 Touched-file data is partial, and evenly so

`pr_commit_details.parquet` covers only 26-54% of eligible PRs (Python 30% merged /
26% rejected; Java 54% / 53%). Coverage is nearly IDENTICAL across outcomes, so it
introduces no merged-vs-rejected bias. The remainder uses the git-diff route the
pipeline already has (`GIT_DIFF_OVERDRAW`), which for non-merged PRs means diffing
`refs/pull/N/head` against its merge-base.

### 2.6 FILE ATTRIBUTION IS AGENT-BIASED 25x, and the bias MOVED between releases

`pr_commit_details.parquet` coverage, measured 2026-08-25 on the eligible pool:

| agent | Java | Python |
|---|---|---|
| Claude_Code | **2.5%** | **3.1%** |
| Copilot | 73.7% | 62.3% |
| OpenAI_Codex | 77.5% | 52.4% |

In the older `AIDev-full` it was COPILOT that lacked attribution. In `AIDev-7.6M` it
is CLAUDE_CODE, by 25 to 30x. Any note or memory saying "Copilot has no file
attribution" is now wrong for this dataset.

**Consequence, and it is decisive.** Curating Java by file extension using the
dataset's own file lists yields:

| agent | Java merged, repo-language route | Java merged, file route |
|---|---|---|
| Claude_Code | 1,954 | **47** |
| Copilot | 2,495 | 1,145 |
| OpenAI_Codex | 1,790 | 1,211 |

That is a **40x** artificial suppression of one agent, caused purely by a metadata
gap. It is the same class of bug V1 already fixed once.

**Required:** select the git-diff fallback by measured per-agent coverage via a LEFT
join, NEVER by agent name. `curate.py` had a `Copilot` hardcode; it was removed
2026-08-19 and this measurement proves that was necessary. Print per-agent coverage
before every curation run so the next release's gap is caught immediately.

Therefore the **repo-language route plus git-diff fallback is the correct Java
curation path**, and the section 2.2 pool figures are the real ones.

### 2.7 Design decision: which outcomes get balanced cells

Java `open` is too thin to balance (OpenAI_Codex has only 83). Balancing three
outcomes would crush every Java cell to 83.

- **Java: balance `merged` vs `rejected` only**, near 280 per cell, which is the
  OpenAI_Codex rejected ceiling. Carry `open` as a supplementary, unbalanced
  observation, never in the headline comparison.
- **Python: balance all three** if wanted (worst cell is Copilot open at 935), or
  match Java's two-outcome design for a cleaner cross-language statement. Prefer
  matching Java, and report `open` supplementary in both.

### 2.8 A balanced corpus is NOT a representative one

A 50/50 merged-to-rejected corpus maximises statistical power for the COMPARISON,
but the real population is 80.9% merged. So:

- Any per-outcome rate (merged rate, rejected rate) is valid as-is.
- Any OVERALL "agents violate X% of the time" figure computed on the balanced corpus
  is **wrong for the real world** and must either be reweighted to the natural mix
  (80.9 / 9.5 / 9.6) or explicitly labelled as a balanced-design figure.
- State which one every headline number is. This is a one-line methods sentence that
  removes an easy attack.

## 2.9 Where the PRs and the code come from (settle this once)

**AIDev is the sampling frame, not the source of code.** It supplies PR number,
repo, agent, state, merged_at and timestamps. It contains no source trees.

**All code has always come from GitHub over git**, in both V1 and V2, via
`clone_and_extract.py` and `gitsnap.py`. Nothing about that is new. The single
change in V2 is WHICH REF is fetched:

| outcome | ref |
|---|---|
| merged | the merge commit, as today |
| rejected / open | `refs/pull/<N>/head`, verified fetchable in 2.3 |

`pr_commit_details.parquet` does carry a `patch` column, but its coverage is
agent-biased 25x (2.6), so it must never be the primary source. Use it only as a
cross-check where present.

**The only new network work** is a GitHub API pass for the close reason, because
`pr_reviews` covers just 6-11% of rejected PRs (2.4). Scope it to the SELECTED
corpus only, roughly 1,700 Java PRs, which fits inside the 5,000 requests/hour
authenticated limit in well under an hour. Run it after selection, never before.

No scraping of HTML, and no bulk crawling beyond the selected corpus.

### 2.10 Query gotchas in AIDev-7.6M (each of these cost a wrong answer once)

Every one of these produced a silently WRONG result on the first attempt during the
2026-08-25 analysis. They are properties of the dataset, not of the code.

1. **License casing.** `all_repository.license` stores `MIT`, `Apache-2.0`,
   `BSD-3-Clause`. `config.PERMISSIVE_LICENSES` is lowercase. Comparing directly
   returns **zero rows**, silently. Always `.str.lower()` before the `isin`.
2. **Join dtype mismatch.** `all_repository.id` is `int64` but
   `all_pull_request.repo_id` is `float64`. A naive merge yields an EMPTY frame
   with no error. Cast one side before joining, and assert the row count after.
3. **Table trap.** Use `all_pull_request` / `all_repository`. The plain
   `pull_request.parquet` / `repository.parquet` are only the >100-star curated
   subset and silently under-count.
4. **Row-wise `.apply` over 7.6M rows does not finish** in reasonable time. Use
   vectorised `np.where` for the outcome derivation.

**General rule this illustrates:** in this dataset an empty or tiny result is far
more often a join or casing bug than a real finding. Assert non-zero row counts
after every filter and join before believing any number.

### 2.11 Tables not yet explored

`pr_task_type.parquet` exists and has not been examined. If it classifies PR intent
(feature / bugfix / refactor / test), it would let violation rates be split by task
type, which is a cheap extra dimension and a plausible confounder worth controlling.
Check it during curation. `issue.parquet`, `related_issue.parquet` and
`pr_review_comments.parquet` are likewise unexamined.

## 3. Corpus design

- **Balance on BOTH axes**: agent x outcome, equal cells, repo-stratified draw
  (`JAVA_REPO_STRATIFIED_DRAW=True`, already implemented in `curate.py::_draw`).
- Keep merged and not-merged cells the same size so the comparison is not
  confounded by n.
- Carry `outcome` and `rejection_stratum` as columns on `gold_set_*.parquet` and
  propagate them through every downstream parquet, so any report can split by them.
- Report top-1 and top-3 repo concentration per cell. V1 pulled Codex from 48.8% to
  27.0% top-3 by stratifying; hold that line.
- **Java runs FIRST this time** (author's instruction). Java is the strongest
  language for SOLID: classes mandatory, `interface` unambiguous, `@Override`
  explicit, nominal subtyping. It is where the "your language does not express
  SOLID" objection dies.

## 4. Pipeline changes required

1. **`gitsnap.py`, add a non-merged delta path.** Today `fetch_merge_pair` and
   `delta_pair` assume a merge commit. Add `pull_head_pair(repo, n)`: fetch
   `refs/pull/N/head`, resolve the base as the merge-base with the default branch
   (or the parent of the PR's first commit from `pr_commits.parquet`), and return
   the same `(before_sha, after_sha)` contract so no detector changes.
2. **`curate.py`**: add `--outcome {merged,rejected,open,all}`, outcome-balanced
   draw, emit `outcome` and `rejection_stratum`.
3. **`clone_and_extract.py`**: route non-merged PRs through the new pair function.
4. **All five detectors: NO CHANGE.** They consume before/after trees. Keep it so.
5. **Reports**: add an `outcome` split everywhere, alongside the existing
   pooled/macro and authored/edited splits.
6. **Thresholds must be recalibrated** on the new corpus before labelling. Hard
   rigor gate. Delete the parquets after any threshold change, because resume keeps
   permanent-status rows and a re-run alone will not recompute old flags.

## 5. New metrics the professor asked about

Her example ("many things import a class and it keeps being edited, so it violates
SRP") is real and has a name: high **afferent coupling (Ca, fan-in)** combined with
high **change frequency**. That is the *change-magnet* or *hub-like dependency*
smell, and it is much closer to Martin's ACTUAL definition of SRP, "one reason to
change", than any size proxy. The current rubric measures size and cohesion, which
are only correlates of that definition.

**The project already computes Ca** in `dip/graph.py::martin_metrics`, and both
corpora carry full change history, so this is mostly wiring, not new science.

Candidates, per principle:

| principle | current signal | proposed addition | why it is better |
|---|---|---|---|
| SRP | size, WMC, LCOM, C3 | **Ca (fan-in) x churn**; count of distinct concerns causing edits (co-change / logical coupling) | measures "reasons to change" directly instead of by proxy |
| OCP | dispatch-chain width | **modification frequency** (already planned, CLAUDE.md item 5) | a class edited across many PRs is by definition not closed for modification; width-independent, so it complements rather than replaces |
| LSP | override signals | contract / invariant checks | width-independent |
| ISP | interface width, stub ratio | **Interface Usage Cohesion**, what fraction of members each client actually calls | Martin's real ISP claim is about CLIENTS, not about width |
| DIP | Tarjan cycles, Martin A/I/D | concrete-vs-abstract dependency ratio (already exists as `abstract_dep_ratio`, currently unreported) | a direct DIP statement that costs nothing |

**Gate, non-negotiable.** Any new metric goes through the same tuning plus holdout
validation as the existing ones, with counts printed beside rates. An unvalidated
metric is worse than a stated limitation. Add 2 or 3 validated metrics, not 5
unvalidated ones. `mean_d` is the cautionary tale: it validated at precision 0.000
in two languages and is now excluded from the DIP flag.

## 6. "Python has no interfaces, so how is ISP defined?"

It does, and this is already handled. Python expresses interfaces three ways and
`isp/interfaces.py` detects all three: `abc.ABC` with `@abstractmethod`,
`typing.Protocol` (PEP 544, structural), and implicit duck-typed interfaces found
via stub ratio.

**More importantly, the per-language difference is deliberate and is a RESULT, not
a weakness.** The project already does this and defends it:

- LSP uses 5 signals in Python but **only 3 in Java**, because `javac` already
  enforces the other four: a changed parameter list is an overload rather than an
  override, parameter types are invariant, visibility cannot narrow, and checked
  exceptions cannot broaden.
- TypeScript had to drop `param_removed` for the OPPOSITE reason: its methods are
  bivariant, so it permits everything.
- **What a compiler enforces determines which violations are even expressible.**
  That is a genuine cross-language finding and should be stated as one.

Rule going forward: each language gets the operationalisation its semantics
justify, while the numeric CORE stays shared (`metrics.py::lcom_from_access_sets`,
`metrics.py::c3_from_token_bags`, and `dip/graph.py`'s Tarjan and Martin code are
reused verbatim across languages) so the numbers stay commensurable. Every
deviation is documented with its language reason.

## 7. Dataset enrichment

**The author has authorised backfilling whatever the dataset lacks.** Use it, but
under one rule, because ignoring it is exactly how the 40x bias in 2.6 got in:

> **Measure every gap PER AGENT before filling it, and PER AGENT again after.**
> A gap that is uniform across agents costs recall. A gap that is agent-skewed
> silently rewrites the ranking, which is the actual result. Print both tables.

A backfill that leaves a >2x per-agent coverage spread is not finished, whatever
the average says. Where a gap cannot be closed evenly, drop the affected column
from the analysis rather than reporting a biased one.

Known gaps, in priority order:

1. **Rejection reason** for the 90% or so of rejected PRs with no local review
   data. GitHub API, 5,000 requests/hour authenticated. Needed only for the
   selected corpus (low thousands of PRs), so it is feasible. Do it AFTER
   selection, never before.
2. **Ref availability** for non-merged PRs. Measure it from the fetch itself during
   curation; no separate crawl needed.
3. **Touched files** for the 50-70% without `pr_commit_details`, from git diff.
   This is the big one: it must bring Claude_Code from 2.5% to parity with the
   other two, and that parity must be VERIFIED, not assumed.
4. Anything else found missing mid-run: measure the per-agent spread, decide, and
   record the decision in this file so the next session does not redo the analysis.

Do not re-download anything: `datasets/AIDev`, `AIDev-2.7M` and `AIDev-7.6M` are all
present locally. **Always use the `all_pull_request` and `all_repository` tables**,
never `pull_request.parquet`, which is only the >100-star curated subset.

## 8. What carries over from V1 unchanged

- All 10 defect fixes, and the "SHIPPED CODE FIXES, do not revert" list in `RERUN_LOG.md`
- `vibebench/labels/JAVA_JUDGING_RUBRIC.md`, freeze it before labelling starts
- The complete-flagged plus unflagged-sample labelling design (precision is EXACT,
  recall is estimated)
- `check_label_file.py` guard, must print PASS before any number is quoted
- Cross-check tooling (CK 0.7.0, PMD 7.7.0, DesigniteJava 1.1.2) and `quality_profile`
- Martin zones code (`arch/run_arch.py`, `report_arch.py`)
- Python V1 results, kept as the merged-only baseline that V2 is compared against
- Caches: `tree_cache/` and `java_facts_cache/` are content-hashed and still valid

## 8b. Writing conventions

The author does not want the em dash character used anywhere, in documents, code
comments, or replies. Use a comma or restructure the sentence.

## 9. Order of work

1. Curation V2, Java first, outcome-balanced, with the ref-availability measurement
2. Extraction, with the new non-merged pair path
3. Calibration, then thresholds, then the flagged run
4. Report with the outcome split, then STOP and hand flag counts to the author
5. Labelling on Sonnet, all five principles, rubric frozen
6. Cross-check, Martin zones
7. Python V2, same design
8. Cross-language plus cross-outcome comparison
9. New metrics from section 5, validated, added last so they cannot destabilise
   the core result
10. Documentation and thesis integration (sections 10 to 14 below)

Steps 1 to 9 are measurement. Steps 10 onward are the thesis. Do not treat the
experiment as finished when the last parquet is written.

## 10. Hypotheses to answer explicitly

State each as CONFIRMED, REFUTED or UNRESOLVED, with the number beside it. These
are the reason the design exists; producing the parquets without answering them
delivers nothing.

### Cross-language (3 agents x 2 languages)

1. **Intra- vs inter-unit, the study's headline.** V1 Python: 443 Liskov violations
   PRESENT in touched code, **5** introduced; 533 OCP smells present, 44 introduced.
   If Java replicates near-zero LSP and ISP introductions, the finding holds in a
   language where classes are mandatory, `interface` is unambiguous and `@Override`
   is explicit, which kills the "your language does not express SOLID" objection.
   This is the single most important comparison in the thesis.
2. **Is agent ranking language-stable?** V1 Python macro: Claude_Code worst on OCP
   (6.18%) and SRP; Copilot cleanest on DIP (2.12%). If Java reorders them, ranking
   is NOT a stable property of an agent, which is itself a headline result.
3. **Does the compiler shape which violations are expressible?** Java LSP uses 3
   signals, Python 5, because `javac` enforces the other four. Compare the SIGNAL
   MIX, not only the rate.
4. **Authored vs edited.** V1 Python: every agent's AUTHORED classes flag LOWER than
   the pre-existing classes they edit (Claude 12.38% vs 13.33% pooled). Does Java agree?

### Cross-outcome, new in V2

5. **Does SOLID conformance relate to acceptance?** Compare violation rates in
   merged vs not-merged PRs, per principle, per language. Report it per rejection
   stratum (2.4), never pooled across strata.
6. **Is the merged-only corpus biased, and by how much?** V1's Python numbers are
   the merged-only baseline. Quantify the shift V2 produces. If the shift is small,
   that VALIDATES V1 rather than invalidating it, and is worth stating plainly.
7. **Does rejection rate itself track agent quality?** Rejection varies 3.6x by
   agent (2.1) with no code analysis at all. Does that ordering match the SOLID
   ordering? Agreement is a strong external corroboration; disagreement is more
   interesting still and must not be buried.

## 11. Acceptance criteria, what "done" means

The experiment is complete when ALL of these hold, for BOTH languages:

- [ ] Reports print per-agent pooled + macro + Wilson CIs for all FIVE principles,
      split by outcome, with counts beside every rate
- [ ] `status x agent` cross-tab shows no status hitting one agent
      disproportionately. A skewed loss is a bias, not an inconvenience
- [ ] Per-agent file-attribution coverage is at parity after backfill (2.6), VERIFIED
- [ ] Corpus cells balanced within ~15% on agent x outcome, concentration reported
- [ ] Every principle has: flag count, per-agent breakdown, per-outcome breakdown,
      a labelled set scored by `score_labels.py`, and `check_label_file.py` PASS
- [ ] Labelling split-half consistency check passes (JAVA_JUDGING_RUBRIC.md)
- [ ] Cross-check run on the new corpus, kappas for SRP/LSP/DIP + `quality_profile`
- [ ] Martin zones computed for both languages
- [ ] All seven hypotheses in section 10 answered in writing
- [ ] Symbol-resolution figure quoted from THIS corpus, not the pilot's 93-100%.
      Measured in V1 at 25-95%, median 66-78%. It bounds LSP and ISP recall
- [ ] Any new metric from section 5 either validated and shipped, or dropped and
      recorded as dropped. No unvalidated metric in the results

## 12. Final deliverables for the thesis

1. **Corpus table**, PRs / repos / top-1 / top-3 concentration, per agent, per
   language, per outcome
2. **Violation table**, 5 principles x 3 agents x 2 languages x 2 outcomes,
   macro-first, counts beside rates
3. **Validation table**, P/R/F1 per principle per language with n, plus the
   per-agent precision evenness check that licenses the ranking claims
4. **Present-vs-introduced contrast**, the headline (443 present, 5 introduced)
5. **Merged-vs-rejected contrast**, stratified, the new V2 result
6. **Quality profile**, PMD rules, Designite smells, CK metrics, context not SOLID
7. **Martin A/I plane**, both languages, descriptive only
8. **Rejection-rate-by-agent figure**, free, needs no code analysis
9. **Limitations**, stated once and in proportion: symbol-resolution ceiling on
   LSP/ISP, SRP screen precision, ISP's small positive count, the rejection
   confound, balanced-vs-representative weighting, sampling asymmetry

## 13. Thesis integration (`thesis/`, LaTeX only, gitignored, NOT on GitHub)

Do not create or update `.md` chapter files. LaTeX only.

| Chapter | State entering V2 | What V2 requires |
|---|---|---|
| Ch1 | 2,035 words, thin | The "fidelity" reframe below. Add the survivorship-bias motivation for V2 |
| Ch2 | 3,530 words, citations verified 2026-08-14 | Unchanged unless new metrics need new sources |
| Ch3 | 4,570 words, fine | Add the outcome-balanced design, the confound stratification, and per-language operationalisation (section 6) |
| Ch4 | 9,838 words, carries the thesis | All V2 numbers replace V1. Keep V1 as the merged-only baseline comparison |
| Ch5 | LaTeX draft exists | Rewrite conclusions against the seven hypotheses |
| `presentation.tex` | 24 frames, Overleaf-ready | Update numbers, add the rejection-rate figure |
| `AUTHOR_VOICE_QUESTIONS.md` | 4 passages | Still awaiting the author's own words |

**Title problem, unresolved.** Working title "Fidelity in coding agents: application
to software architectures". Two real gaps: "fidelity" implies faithfulness TO
something, and "architectures" sits a level above SOLID. **The fix is prose, not
experiments:** define fidelity in Ch1 as *conformance of an agent's contribution to
the design discipline of the codebase it enters*; lead the architecture claim with
DIP plus the Martin zones; use the authored-vs-edited split as the fidelity result.

**Citation rule, binding.** No citation enters the thesis without its
arXiv/publisher page being read first: author list, year, and the specific claim.
Correct forms and the deleted-as-unverifiable list are in CLAUDE.md. Never put
working notes in a bib `note = {}` field, it renders in the printed bibliography.

## 14. Documentation and repo hygiene, last

- Work the 21-item checklist in `RERUN_LOG.md`. Highest priority: the
  symbol-resolution figure. Docs say 93-100%, MEASURED 25-95%
- `CLAUDE.md` still describes the old 5-agent AIDev-full run throughout. Rewrite it
  against V2 once the numbers are final, not before
- Update `VibeBench_Guide.html` at the repo root
- **NOT backed up anywhere: `thesis/` and the root HTML guides.** Flagged to the author
- GitHub repo is PRIVATE and excludes `CLAUDE.md`, `.claude/`, `thesis/`, root
  `*.html`, clones and datasets. Keep it that way
- TypeScript is CANCELLED. Do not work on it, do not spend effort deleting it


---

# 15. EXECUTION LOG, decisions and corrections made while running V2

Appended as the run proceeds, per the section 7.4 rule ("record the decision in
this file so the next session does not redo the analysis"). Everything here is
measured, not assumed; each entry names what was checked and against what.

## 15.1 Section 2 re-verified before use (2026-08-25)

Re-derived once, against `all_pull_request.parquet` / `all_repository.parquet`,
because a plan that is wrong at the base makes every later number wrong:

- outcome split 6,216,792 / 733,321 / 735,168 -> **exact match** to 2.1
- eligible pool per agent x outcome (repo-primary-language route) -> **exact
  match** to 2.2, all 18 cells
- review coverage Java merged 25.3% / rejected 10.8%, Python 12.1% / 6.2% ->
  **exact match** to 2.4

Section 2 stands. It is not re-derived again.

## 15.2 The Java eligible pool under the REAL curation path

2.2 quotes the repo-primary-language route. The pipeline actually uses that
route for `git_diff` rows plus an extension route for `dataset` rows, which
reaches Java living in non-Java-primary repositories. Measured:

| agent | merged | rejected | open |
|---|---|---|---|
| Claude_Code | 1,964 | 485 | 283 |
| Copilot | 2,676 | 1,272 | 364 |
| OpenAI_Codex | 1,989 | **315** | 93 |

The binding cell is OpenAI_Codex rejected at **315**, slightly above 2.2's 284.
`CELL_PR_CAP = 300` sits just under it, so no cell is capped by policy below
what the data allows.

## 15.3 The 25x file-attribution bias, re-measured on the Java pool

`pr_commit_details` coverage AFTER the language gate, which is the population
the sampler actually draws from:

| agent | merged | rejected | open |
|---|---|---|---|
| Claude_Code | 2.3% | 5.8% | 1.8% |
| Copilot | 42.3% | 38.4% | 53.6% |
| OpenAI_Codex | 60.4% | 53.7% | 55.9% |

Confirms 2.6 on this exact pool. The git-diff fallback is load-bearing, not a
convenience, and parity is verified after extraction on the clone manifest
(`clone_and_extract.py` now prints usable-PR rate per agent x outcome and warns
above a 15pp spread).

## 15.4 DECISION: reconstruction tier is recorded and reported

**Problem found before the run, not after.** `gitsnap.delta_pair` has three
tiers, and they do not build the same "before":

- tier 1, the merge commit's first parent = the base branch AT MERGE TIME
- tiers 2 and 3, the PR's own branch point

A merged PR can use tier 1; a not-merged PR never can. Had the tier mix differed
sharply by outcome, the whole merged-vs-rejected contrast would have been partly
a comparison of RECONSTRUCTION METHOD rather than of code, and nothing in the
pipeline would have said so.

Fix: `delta_pair(d, pr, info)` now records the tier, `run_java.py` writes it as
`pair_tier`, and `report_java.py` prints tier x outcome and warns when the
tier-1 share differs by more than 25 points across outcomes. The dataset makes
this less severe than it sounds, `pr_timeline` covers 2.6% of AIDev-7.6M so most
MERGED PRs also fall through to tier 3, but that has to be read off the report,
not assumed.

## 15.5 DECISION: the close reason comes from the GitHub API, not the dataset

`pr_timeline`'s `closed` event is not merely sparse on rejected PRs, it is
**agent-biased 20x**: Claude_Code 2.8% coverage against Copilot 58.5% and
OpenAI_Codex 64.4% (Java); Python 3.5% / 54.8% / 26.8%. Using it as-is would
have decided the rejection strata by metadata coverage rather than by what
happened to the PR.

`06_enrich/fetch_pr_outcomes.py` fetches state, every review state, the
CLOSED_EVENT actor and comment counts for the SELECTED corpus only, over
GraphQL (40 PRs per request; REST would need ~3 calls per PR and approach the
5,000/hour ceiling). First 40 PRs: **100% resolved in 6 seconds**, and the API
found external reviews on **57.5%** of merged PRs where the dataset showed
25.3%. `apply_enrichment.py` folds it back and prints per-agent coverage before
and after, refusing to call the gap closed above a 15pp per-cell spread.

Self-reviews are excluded from the review evidence: an agent reviewing its own
PR is not external quality evidence, and counting it would inflate the reviewed
strata for whichever agent does it most.

## 15.6 DECISION: two outcome columns, and which one each number uses

- `outcome`, the dataset's label at its 2026-03-31 cutoff, DEFINES THE SAMPLING
  FRAME and is never rewritten, or the corpus stops being reproducible.
- `outcome_final`, the state when the API was queried, is the true label and is
  what the merged-vs-not-merged analysis uses.

Roughly five months separate the two, so the `open` cells will largely have
resolved. Drift is printed by `apply_enrichment.py`; above 5% inside the
balanced cells, every reported rate must name the column it used.

## 15.7 `pr_task_type.parquet` examined (closes the 2.11 to-do)

32,702 rows, `type` in {feat, fix, docs, refactor, test, chore, build, ci, perf,
style, other, revert}. It covers **6,048 of 79,224** eligible Java+Python PRs
(7.6%) and is agent-skewed 47x (OpenAI_Codex 21,519 rows, Claude_Code 454), so
under the section 7 rule **it cannot be used as supplied**.

It is recoverable, though: its own `reason` column says "title provides
conventional commit label", i.e. it is a classifier over the PR TITLE, and
titles are present for 100% of PRs in `all_pull_request`. Re-deriving the same
classification locally gives even coverage by construction. Recorded as
available, scheduled with the section 5 metrics (step 9) so it cannot
destabilise the core result.

## 15.8 Corpus drawn (Java V2)

2,740 PRs / 730 repositories. 900 merged PRs are carried over from V1 and were
already extracted, so only ~1,840 needed new extraction.

| agent | merged | rejected | open |
|---|---|---|---|
| Claude_Code | 300 | 485 | 283 |
| Copilot | 300 | 300 | 364 |
| OpenAI_Codex | 300 | 315 | 93 |

Rejected cells above 300 are the deliberate `GIT_DIFF_OVERDRAW` (2.3x on the
git_diff portion only), because Step 2 discovers and drops PRs that touch no
Java. Final cell sizes are read off the report after extraction.

Concentration per cell, top-1 / top-3: Claude_Code merged 1.3/3.7, rejected
15.1/23.9; Copilot merged 1.0/3.0, rejected 1.3/4.0; OpenAI_Codex merged
6.0/18.0, rejected 13.3/36.5. Codex rejected is the most concentrated cell, so
macro-per-repo leads there especially.

## 15.9 Doc rules found FALSE and corrected in code

The author authorised removing rules that rest on superseded measurements. These
were verified wrong against the current dataset before being changed:

1. `config.py::SOURCE_EXTENSIONS` documented a "Copilot exception" for missing
   filenames. FALSE on AIDev-7.6M: Copilot is 97.3% non-null and Claude_Code is
   the agent at 2.3%. The routing has been data-driven since 2026-08-19; the
   comment now records the general rule (which agent has the gap CHANGES between
   releases, so never branch on agent name).
2. `clone_and_extract.py`'s module docstring described the git_diff path as
   "the Copilot path" and quoted "48 of 150 Copilot PRs" from AIDev-full.
   Rewritten to describe the three routes with no agent named.

Still outstanding, to be done at step 14 once the numbers are final (they are
listed in RERUN_LOG.md's checklist and are not silently inherited meanwhile):
CLAUDE.md still describes the Java corpus as "867 PRs / 27 repos, OpenAI_Codex
ONLY", still quotes symbol resolution as "93-100%" when it measured 25-95%
(median 66-78%), still gives Python as "546 merged PRs / 147 repos", and still
contains both "TypeScript is the 2nd language" and "TypeScript is CANCELLED".

## 15.10 DEFECT FOUND: the Java module graph cannot see intra-package use

Found 2026-08-25 while unit-testing the new coupling columns, i.e. by a test
that was written for a different purpose. It affects V1's Java DIP numbers too,
and it is not recorded anywhere in the existing docs.

**What the Java module graph actually contains.** `Facts.java` builds a module's
`imports` from exactly two sources: `import` declarations whose target package is
internal, and the type's supertypes. Java requires NO import statement for a
same-package reference, so:

| edge | captured? |
|---|---|
| cross-package use (field, param, call) | yes, the import is mandatory |
| cross-package inheritance | yes |
| intra-package inheritance | yes, via supertypes |
| **intra-package use** | **no** |

Verified both directions in `run_java.py --self-check`, which now pins the
semantics permanently.

**Why it matters, and it is a cross-language problem, not a Java one.** Python
requires an import statement even between two modules of the same package, so
`dip/graph.py::build_module_graph` sees intra-package edges that the Java
extractor structurally cannot. Java's compilation-unit cycles are therefore in
effect CROSS-PACKAGE cycles, while Python's include intra-package ones. The raw
`new_cycle` counts of the two languages are consequently NOT directly
comparable, and V1 compared them (JAVA_STATUS 12.1 quotes Java 0/40/4/2 against
Python 10/20 as if the only difference were the operationalisation of "new").

**Judgement, stated on the merits.** This is not a bug in the sense of a wrong
computation. For package-level Martin A/I/D the exclusion is correct by
definition, since an intra-package edge crosses no package boundary. For the
Acyclic Dependencies Principle the cross-package scope is the architecturally
meaningful one, and Martin's DIP is a statement about components, not about two
classes in one folder. It IS a defect in how the two languages were compared.

**Action taken.** (1) The semantics are asserted in a self-check so they cannot
drift. (2) The cross-language DIP comparison (step 8) is computed at a MATCHED
scope, restricting Python's cycle detection to cross-package edges, rather than
comparing the raw counts. (3) Java DIP is reported as cross-package cycles
explicitly. Nothing is hidden and no number is discarded.

**Not fixed by extending the extractor**, and the reason is measured, not
preferred: capturing intra-package use would need resolved field/param/call
references from SymbolSolver, whose resolution rate on this corpus is 25-95%
(median 66-78%). A graph that is complete in some repositories and half-blind in
others is worse than one that is uniformly scoped, because the incompleteness
would correlate with repository kind and therefore with agent.

## 15.11 Enrichment results, and a second trap caught (2026-08-25)

GraphQL pass over all 2,740 Java PRs: **10 minutes, 2,736 resolved (99.9%)**,
4 unavailable (deleted or private repos). Cost was a few hundred rate-limit
points of the 5,000/hour budget.

**Gap closed, and verified per agent.** Close-actor coverage over CLOSED PRs:
Claude_Code 99.4%, Copilot 99.7%, OpenAI_Codex 100.0%. Per-cell spread **0.8pp**,
against the dataset's own 2.8% / 58.5% / 64.4%. The column is now usable.

**Review evidence roughly tripled.** `changes_requested` in the drawn corpus
went **25 -> 85 PRs**. Still small, so it is reported descriptively with its
count printed beside every rate, never as a powered comparison.

**Outcome drift is negligible where it matters.** Inside the balanced
merged/rejected cells only **2 of 2,000 PRs (0.1%)** changed label between the
dataset cutoff and today, so `outcome` and `outcome_final` agree there and the
sampling frame is sound. The `open` cells behaved as expected for a five-month
gap: of 740, **424 are still open, 194 closed unmerged and 122 merged**, so 316
supplementary PRs join the real outcomes.

### A false alarm in my own coverage check, fixed

The first coverage run reported a **59.1pp** per-cell spread on `api_closed_by`
and told the reader to drop the column. That was wrong: an OPEN PR has no close
event, so its null is NOT-APPLICABLE, not missing, and pooling the open cells
with the closed ones manufactured a bias finding on a column that is 99.4-100%
complete wherever it applies. `coverage()` now takes an `applicable` mask. A
coverage check that cannot distinguish "missing" from "not applicable" is worse
than no check.

### DEFECT: "author-closed" is not an abandonment measure

The plan (2.4) proposes separating author-closed (abandonment) from
maintainer-closed. Measured, it reads:

| agent | self-closed share of not-merged |
|---|---|
| Claude_Code | 78.2% |
| OpenAI_Codex | 80.6% |
| Copilot | **0.5%** |

A 160x spread that looks like a spectacular behavioural result. It is an
artefact of **who owns the PR account**:

| agent | distinct PR authors | top author |
|---|---|---|
| Claude_Code | 426 | dongjoon-hyun, 94 of 1,068 |
| OpenAI_Codex | 167 | peter-lawrey, 126 of 708 |
| Copilot | **8** | **`copilot-swe-agent`, 941 of 964** |

Copilot opens its PRs from a BOT account, so a human closing one can never be
"the author"; Claude_Code and Codex PRs are opened from the developer's own
account, so the developer closing their own PR always is. The column measures
each agent's PR-authorship model, not whether anyone gave up.

**Action.** `api_closed_by_author` is printed as a corpus property with that
warning attached and is NOT available as an analysis variable, under the V2 rule
that an unevenly-closable gap is dropped rather than reported biased. It is
replaced by `closed_by_automation` (bot / policy service / stale bot), which
does not depend on PR-author identity and is therefore comparable: Claude_Code
2.7%, Copilot 6.4%, OpenAI_Codex 2.0%. Bulk automated closure is a small part of
the not-merged population in every cell.

**Worth stating in the thesis in its own right:** agent attribution in AIDev is
not uniform. One of the three agents opens PRs as a bot and the other two open
them as the developer, which means any per-agent statistic derived from PR
account identity needs this check first.

## 15.12 Extraction health, checked mid-flight (501 of 2,740 PRs)

Phase verification, not a post-hoc excuse. Three things were checked and one
apparent problem turned out to be benign.

**Usable-PR rate, conditioned on the file-list route.** The raw per-cell rate
looks alarming (Claude_Code rejected 72.4% against Copilot rejected 100%) and
the conditioning explains it entirely:

| route | usable |
|---|---|
| `dataset` (pre-filtered to touch >=1 Java file) | 98-100%, every agent, every outcome |
| `git_diff` (not pre-filtered, deliberately over-drawn 2.3x) | attrits, as designed |

The gap is the ROUTE MIX, not an agent effect: Copilot's rejected cell is 300
dataset rows with a guaranteed Java file, Claude_Code's is 28 dataset rows plus
457 unfiltered git_diff rows. That is exactly what `GIT_DIFF_OVERDRAW` exists to
absorb. The per-agent git_diff comparison is not yet meaningful (Copilot n=3,
Codex n=6 at this point of an alphabetically ordered run) and is re-checked on
the complete manifest before any number is quoted.

**`file_missing` is 74.5% mid-PR renames, and harmless.** 247 file rows, heavily
concentrated in two repositories. Diagnosed on conductor-oss/conductor#767: the
PR renamed a package from `com/netflix/conductor/...` to
`org/conductoross/conductor/...` partway through, and `pr_commit_details` lists
the filename PER COMMIT, so the intermediate path appears in the file list and
no longer exists at the PR head. The final path extracts fine. Measured across
all such rows, 184 of 247 (74.5%) have the same basename recovered elsewhere in
the same PR; the residue is 63 rows, **0.4% of 14,547**.

It affects nothing analytical either way: the delta harness reads its trees
through `gitsnap.src_tree` at the before/after SHAs, and a touched path absent
from the after tree simply contributes no rows. The manifest status is a
bookkeeping artefact, not data loss.

**Extraction of not-merged PRs works.** A 12-PR smoke test on rejected Java PRs
resolved 10, extracting 143 files through `refs/pull/N/head`, confirming 2.3 on
this corpus rather than on the four PRs the plan tested by hand.

## 15.13 BLINDING DEFECTS FOUND AND CLOSED (2026-08-25)

Three leaks, found by auditing the label pipeline against the V2 question rather
than by anything failing. All three would have inflated a headline.

**1. The Java DIP batch header printed `new_cycle=` unconditionally.** Since
`DIP_FLAG_INCLUDES_MEAN_D=False`, `new_cycle` IS the DIP flag, so the judge was
shown the verdict while every other principle's verdict sat behind
`--show-verdict`. Now gated. Cycle COUNTS stay visible because they are evidence
the row cannot be judged without.

**2. The Python DIP batch printed `NEW CYCLE: A -> B -> A` unconditionally.**
Same defect, same file family. Now the after-state cycles are shown WITHOUT the
novelty label, and the judge decides novelty from the new-edge list, which is the
independent check the label round exists to provide.

> **Consequence for existing numbers, stated plainly:** any DIP precision figure
> produced before 2026-08-25 is an **UPPER BOUND**. That includes the Python
> `dip_census.csv` result of **P=0.702 / R=1.000** and the earlier P=0.786 on
> new_cycle-only flags. Those remain the best available figures and are not
> withdrawn, but they must be quoted with this caveat until a blind round
> replaces them. The V2 Java and Python DIP rounds are blind from the start.

**3. The judged CSV carried the PR's OUTCOME.** This is the V2-specific one and
it is the most dangerous of the three. The batch `.txt` files were already blind,
but the judge edits the CSV, and `run_java.py` now writes `outcome` and
`rejection_stratum` onto every row. A judge who can see "rejected" beside a class
can produce the merged-vs-rejected result out of nothing, which is precisely the
finding V2 exists to test.

Fixed by withholding those columns from the judged file into a sidecar keyed by
`row_idx`; `score_labels.py` rejoins them AFTER judging. Withholding a column
from the JUDGE and withholding it from the ANALYSIS are different things, and
only the first is wanted.

**The check that catches a breach of this** is now in `score_labels.py`:
precision is reported PER OUTCOME and per rejection stratum, and a spread above
15 points means the merged-vs-rejected contrast is partly a property of the
judging rather than of the code. Rubric rules 7 and 8 record all of this, and the
rubric is frozen before labelling starts.

Regression check after every change: `score_labels.py` on the existing
`dip_census.csv` still reproduces P=0.702 / R=1.000 and the per-agent
0.654 / 0.714 / 0.857 exactly.

## 15.14 Verification infrastructure built (2026-08-25)

Because every defect this project has found was a SILENT one, the looking is now
written down instead of being done ad hoc:

- **`07_verify/verify_phase.py --phase {curate,extract,enrich,analyse}`.** Each
  check answers one question that has previously had a wrong answer, prints the
  evidence, and returns PASS / WARN / FAIL. FAIL means a number computed now
  would be wrong; WARN means something to DISCLOSE, not necessarily to fix.
  Results so far: curate 4 PASS 1 WARN, enrich 4 PASS 1 WARN, no FAILs.
- **`run_java.py --pilot --cells N`** takes N PRs from EVERY (agent x outcome)
  cell and writes to `*.PILOT.parquet`; `report_java.py --pilot` reads them.
  `--limit` was not enough: it takes the head of a frame sorted by agent, so it
  would rehearse one agent's merged cell and nothing else, which is precisely
  the coverage a rehearsal must not have. Pilot outputs are separate because
  `run_java.py` resumes on permanent statuses, so a pilot writing into the real
  files would freeze a few dozen PRs and the full run would skip them.
- **`run_java.py --self-check`** pins the semantics of the new coupling columns
  and asserts that no flag reads them.
- **`08_findings/answer_hypotheses.py`** answers all seven section-10 hypotheses
  from the parquets, with the CONFIRMED / REFUTED / UNRESOLVED rule encoded
  rather than left to judgement. A hand-written answer goes stale the moment a
  threshold moves.
- **`check_label_file.py`** now FAILS on a judged file that exposes `outcome`.

### Two further confounds added to the report, neither in the original plan

**PR SIZE.** A PR touching more files has more chances to introduce a violation,
mechanically. If rejected PRs are larger, a higher rejected violation rate is
partly an artefact of size. The size distribution is printed beside every
outcome contrast and warns above a 1.5x median gap.

**PR AGE.** A PR's before-tree is the repository as it stood when that PR
branched, so systematically older PRs sit in a systematically different
codebase. Measured on the drawn Java corpus the medians are 11 days apart
(merged 2026-01-17, rejected 2026-01-28), so there is no age confound at corpus
level, and the delta principles are immune anyway because each PR is compared
against its own base. Worth disclosing separately: THE AGENTS WORKED IN
DIFFERENT ERAS, median PR date OpenAI_Codex mid-2025 against Claude_Code early
2026, so a cross-agent comparison is partly a comparison of repository eras.

**WITHIN-REPO PAIRED CONTRAST.** The strongest available form of hypothesis 5.
Comparing all merged PRs to all rejected PRs mixes a repository effect into the
outcome effect, because the two sets are not drawn from the same projects.
Restricting to repositories that contributed BOTH outcomes removes the
repository as a variable, and the paired per-repo difference is what gets quoted
if the two numbers disagree.

### V1 result parquets moved aside

`java_pr_results.parquet` and the five `*_results_java.parquet` are renamed
`*.V1_merged_only.parquet`. `run_java.py` resumes by keeping rows with a
permanent status, so leaving them in place would have merged the V1 merged-only
corpus into V2 silently, and V1 rows carry no `outcome` column at all.

## 15.15 More doc rules verified FALSE (2026-08-25)

Checked against the code and the parquets, not against other documents.

**"Python SRP has NO authored-vs-edited flag."** FALSE. `srp_results.parquet`
carries both `is_new` (per class) and `file_is_new`, and `run_srp.py` computes
them, explicitly mirroring `run_java.py`. RERUN_LOG's phase 0.5 is listed as
open and is in fact done. This matters: hypothesis 4 (authored vs edited, the
fidelity result) is computable in BOTH languages, and the claim that it was
Java-only would have cost the cross-language half of the strongest fidelity
statement the study has.

**CLAUDE.md contradicts itself on TypeScript**, describing it as "the 2nd
language: ALL FIVE principles now run on TS" in one section and "CANCELLED, do
not report" in another. The cancellation is current.

Rather than rewrite CLAUDE.md now against provisional numbers, a banner at the
top lists every claim measured false with its correction, and points to this
file as authoritative. The full rewrite happens at step 14 against final
figures, once, instead of twice.

## 15.16 Corrected timing estimate

An earlier estimate in this session put Java extraction at 9-12 hours. That was
wrong, from misreading elapsed time. Measured: extraction started 18:16 and
reached 775 PRs by 18:56, **about 19 PRs per minute**, so the full 2,740 lands
near 20:40. The rate is lumpy because the progress counter advances per
REPOSITORY, and the large Apache repositories need depth-500 fetches that take
minutes each.

Bottleneck is CPU, not bandwidth: 90% processor against 3.7 MB/s network, which
is git pack resolution. Raising the worker count would thrash rather than help,
so it stays at 8. That also matches the measured warning in RERUN_LOG that 16
workers manufactured 1,931 `fetch_failed` rows which a quiet 8-worker pass
reduced to 15.

## 15.17 Report layer built and exercised against fixtures (2026-08-25)

`report_java.py` was extended and then RUN against a synthetic two-outcome
fixture built from the V1 parquets, so every new code path executed before the
real data arrives. Blocks added:

- agent x outcome cells with macro-per-repo, pooled and Wilson CIs
- outcome contrast, then the not-merged REJECTION STRATA separately
- `status x outcome` cross-tab, warning above a 10pp gap in analysable rate
- reconstruction `tier x outcome`, warning above a 25pp gap in tier-1 share
- PR SIZE by outcome, warning at >=1.5x median difference
- PR AGE by outcome, warning above 60 days median difference
- CLASS SIZE by outcome inside the SRP section, because the SRP rubric IS a
  size rubric and a size difference between outcomes is most of any SRP
  difference between them
- WITHIN-REPO PAIRED CONTRAST, with the paired per-repo difference
- a V2 rules block covering balanced-vs-representative reweighting, the
  rejection confound, and the precision-evenness requirement

### Two further defects found by running it

**`created_at` was never joined, so the PR-age check could not fire.** It lives
only in the gold set, never in the result parquets, so the block would have sat
in the report looking like a control that had passed when it had never run. A
silently inert check is worse than an absent one. `ensure_outcome` now joins it.

**`run_arch.py` resumed across corpora.** It kept permanent-status rows without
checking the PR is still in the current gold set, which is exactly how a
re-curated corpus silently inherits the previous one. Fixed, with the liveness
set taken from the FULL gold set rather than the `--limit` subset, because
otherwise a five-PR smoke run would have evicted every other row in the parquet.

### Encouraging measurement from the V1 fixture

Symbol resolution over 689 analysable Java PRs: **median 84.7%, p10 30.8%, p90
98.0%**, and per agent 85.9% / 81.1% / 83.5%, a spread of only **4.8 points**.
The 12-PR smoke sample in RERUN_LOG suggested 95.1 / 77.7 / 25.0, which would
have meant LSP and ISP recall was agent-dependent and therefore an artefact
masquerading as an agent difference. At corpus scale it is not. The corpus-wide
figure must still be read off the V2 report rather than carried over from here.

**Also note: the V1 Java rerun never got past calibration.** Its SRP, OCP and
ISP flags are all null in the archived parquets, consistent with RERUN_LOG's
checklist items 4-6 being unticked. There is nothing to salvage from it and
nothing to compare against; V2 is the first flagged 3-agent Java run.

## 15.18 The within-repo paired contrast is properly powered

Measured on the drawn Java corpus before any analysis, because a paired design
is worthless if the pairs do not exist:

| | count |
|---|---|
| repos contributing merged PRs | 370 |
| repos contributing rejected PRs | 369 |
| **repos contributing BOTH** | **174** |
| PRs inside those repos | **1,249** (572 merged, 677 rejected) |

Per agent, repositories contributing both outcomes: Claude_Code 59 (342 PRs),
Copilot 86 (383 PRs), OpenAI_Codex 33 (402 PRs).

This matters because the pooled merged-vs-rejected contrast confounds outcome
with REPOSITORY: the two sides are not drawn from the same projects, and
projects differ enormously in baseline design discipline. Restricting to
repositories that contributed both outcomes removes the repository as a variable
entirely. At 174 repositories and 1,249 PRs, and roughly balanced within the
subset (572 vs 677), that control costs little power and is available per agent
as well as pooled.

**Reporting rule: where the pooled and paired contrasts disagree, quote the
paired one and show both.** The report prints the paired per-repo difference
(mean and median, plus how many repositories point each way) so a result driven
by two or three repositories cannot pass as a corpus-wide effect.

## 15.19 CHURN WOULD HAVE BEEN A PURE ARTEFACT. Measured, before building it.

Section 5's headline new metric is the CHANGE-MAGNET: high afferent coupling
combined with high change frequency, which is far closer to Martin's actual
"one reason to change" than any size proxy, and which also supplies the
width-independent OCP signal that Java's validation asked for.

Churn has to come from the repository's own history, i.e. from the local clones.
**The clones are shallow at different depths, and the depth is agent-correlated.**
Sampled 12 repositories per agent from the V2 Java corpus, 2026-08-25:

| agent | median commits in the clone | repos with >=100 commits |
|---|---|---|
| Claude_Code | 2,144 | **66.7%** |
| Copilot | 15 | **8.3%** |
| OpenAI_Codex | 2 | **0.0%** |

A **67-point** coverage spread. The cause is mechanical and was predictable from
the pipeline rather than from the agents: a PR with a merge SHA resolves through
`gitsnap` tier 1, which fetches at `--depth 2`; a PR without one falls to tier 3,
which deepens to 500. OpenAI_Codex has a merge SHA for 286 of its 300 merged PRs;
Claude_Code has one for 27.

**Had the change-magnet metric been built on the clones as they are, it would
have measured Claude_Code's repositories in depth and Codex's not at all, and
reported the difference as a quality finding.** It would have looked like a
strong result. This is the same failure family as the 40x file-attribution
suppression and the 20x close-actor gap, and it is the third time in this run
that a per-agent coverage check has caught a metric before it produced a number.

**Required before any churn-based metric ships**, and recorded here so step 9
does not skip it:

1. `churn.ensure_history` runs over EVERY repository in the corpus, deepening the
   default branch to the same `CHURN_WINDOW` regardless of how that repo's PRs
   were reconstructed. Repos already deep are a no-op.
2. `churn.coverage_by_agent` is printed BEFORE and AFTER. Above a 15pp residual
   spread the metric is dropped, not reported.
3. Unmeasurable history returns `None`, never `0`. Zero churn and unknown churn
   are opposite claims and must never share an encoding.
4. Churn is counted over a FIXED WINDOW of commits, identical for every
   repository, so a 15-year-old project and a 6-month-old one are compared on
   equal terms. A raw lifetime commit count would just measure project age.
5. Merge commits are excluded: a merge touches every file of the branch it
   integrates and would credit a whole release to one "change".

`03_principles/churn.py` implements all five and self-checks offline against a
throwaway git repository. Nothing consumes it yet, by design: section 9 puts the
new metrics last so they cannot destabilise the core result, and any metric
derived from churn still needs its own tuning plus holdout round before it is
allowed to accuse anything.

**Nothing else in the pipeline depends on clone depth.** `src_tree` uses
`git archive <sha>`, which needs only that one commit's tree, and every detector
works from trees. Checked rather than assumed.

## 15.20 DEFECT: calibration printed the WRONG POPULATION's percentiles

Found 2026-08-25 while preparing the threshold recalibration.

`config.SRP_JAVA_THRESHOLDS` documents itself as calibrated on "PRODUCTION,
ENTITY-DEDUPLICATED types (n=976), not on all 6,453 rows", and gives two correct
reasons: most Java types in this corpus are TESTS and have a different size
distribution, and a class is repeated once per PR that touched it (duplication
1.86x, the worst of the three languages).

`run_java.py::_distributions` printed the percentiles over the RAW ROW LIST.

So an operator following the documented procedure -- run `--calibrate`, read
p90, paste it into config -- would set a bar calibrated on a population the
config does not describe. And the error has a direction: test classes are
smaller, so the raw p90 sits LOW, and a threshold anchored to it fires on far
more than the ~10% a p90 anchor is meant to. The bar would have been too loose,
inflating every SRP rate in the study, uniformly enough to look normal.

Fixed. `_distributions` now prints BOTH populations, labels the raw one
"DO NOT CALIBRATE ON THIS", and marks the production/deduplicated one as the
source. OCP chains and ISP contracts are deduplicated to the same keys the
report and the label census already use, so the calibration population, the
reported population and the judged population are one population rather than
three.

**The general lesson, worth keeping:** a documented procedure and the tool that
implements it drifted apart, and nothing failed. Every threshold this project
sets should state its population in the same place it states its value.

## 15.21 Label pipeline rehearsed end to end on a fixture (2026-08-25)

Run before the real data exists, so the labelling round does not discover a
broken tool after the expensive part:

- `make_java_census.py --principle srp` on a 4,000-row fixture: 3,576 distinct
  entities after deduplication, 291 flagged enumerated at 100%, 328 unflagged
  sampled at 10%.
- **Blinding verified twice.** The judged CSV contains no `outcome` or
  `rejection_stratum` column at all; both went to
  `java_srp_census.outcome.csv` keyed by `row_idx`.
  `check_label_file.py` prints `blind  no outcome/merge columns exposed to the
  judge`, and FAILS if they ever appear.
- `score_labels.py` rejoined the sidecar automatically and produced the new
  PER OUTCOME evenness block alongside the per-agent one.
- `check_label_file.py` correctly FAILED the unlabelled file ("ZERO rows
  labeled -- scoring would be vacuous") rather than reporting a vacuous 0.

Fixtures were deleted afterwards; no fixture data remains in `corpus/` or
`labels/`.

## 15.22 SHA provenance added

`run_java.py` now records `before_sha` and `after_sha` on every analysed PR, and
`verify_phase.py --phase analyse` reconciles `after_sha` against the clone
manifest's `head_sha`.

Reason: Step 2 extracts files at one SHA and Step 3 analyses the tree at
another, and for a still-OPEN PR the author can push between the two. The
failure would be entirely silent, the cached touched-file list naming paths
absent from the analysed tree, which simply contribute no rows. 424 of the drawn
PRs were still open at the dataset cutoff, so this is a real population. A
CLOSED PR's head must not move at all, so drift there is checked separately and
warns above 2%.

## 15.23 DEFECT: the cross-check would have run on never-integrated branches

`run_crosscheck_java.py` selected each repository's head-state tree with
`gs.sort_values("merged_at").groupby("full_name").tail(1)`. That is correct for
a merged-only corpus and WRONG for V2: a not-merged PR has `merged_at` NaN,
pandas sorts NaN LAST, so `tail(1)` picked a REJECTED PR wherever one existed.

Measured on the drawn Java corpus:

| selection | merged | rejected | open |
|---|---|---|---|
| old (`sort_values("merged_at")`) | 165 | **356** | 209 |
| corrected | **370** | 166 | 194 |

**356 of 730 repositories, 49%, would have had their "head state" taken from a
branch that was never integrated into the project.** CK, PMD and DesigniteJava
would then have been run against code that is not in the project, and the
quality profile would have described it as if it were.

Corrected to prefer a MERGED PR, latest first, falling back to the most recent
PR of any outcome only for the 360 repositories that contributed no merged PR at
all. `head_outcome` is now recorded per repository in
`java_crosscheck_status.csv` so the profile can be split on it.

**Note the asymmetry in how much this matters**, because it decides what has to
be disclosed: for the AGREEMENT figures (kappa against PMD/Designite, Spearman
against CK) all that is required is that our detector and the external tools see
THE SAME tree, which they did either way. For the QUALITY PROFILE, which is
meant to describe real projects, a never-integrated branch tip is not the
project. So the kappas were never at risk and the profile was.

**Scale warning for this run:** the previous cross-check covered 27 repositories
in about 25 minutes. V2 has 730. Budget accordingly, or run it on a documented
random subsample with the sample size stated, rather than silently on `--limit`.

## 15.24 DEFECT: SRP deduplication assigned shared classes to an outcome by tiebreak

`report_java.py` deduplicates SRP rows to one row per DISTINCT CLASS, keeping
the largest observation. That is correct for the overall and per-agent rates and
was correct in V1, where every PR had the same outcome.

In V2 it is wrong for the outcome split. A class touched by BOTH a merged and a
rejected PR collapses to a single row whose `outcome` is then whichever of the
two happened to carry the larger `class_loc` -- an irrelevant tiebreak deciding
which side of the study's new headline comparison the class lands on.

Not a rare case: 174 repositories contribute both outcomes, and on a fixture
built from the real corpus shape, **2,144 of 15,441 distinct production classes
(14%) appear under more than one outcome.**

Fixed by giving the outcome split its own deduplication key,
`(outcome, full_name, type_id)`. The question the split asks is "among classes
touched by MERGED PRs, what fraction is flagged", and separately for not-merged,
so a class touched under both outcomes legitimately contributes one observation
to EACH. The corpus-wide `uniq` frame is retained unchanged for the overall,
per-agent and authored-vs-edited numbers, where collapsing to one row per class
is the right unit.

The report now prints both counts, so the size of the overlap is visible rather
than implicit.

## 15.25 Calibration population corrected in the REPORT too, and a stale figure

`report_java.py`'s calibration-mode branch had the same defect as
`run_java.py::_distributions`: it printed percentiles over every row while
`config.SRP_JAVA_THRESHOLDS` documents itself as calibrated on production,
entity-deduplicated types. Both now print BOTH populations, with the raw one
labelled "do NOT calibrate on this" and the correct one marked as the source.

On the V1 fixture the two differ modestly (class_loc p90 410 vs 407, wmc 44 vs
49) but the gap scales with the test share, which is corpus-dependent.

**Stale figure noticed while doing it.** CLAUDE.md and `config.TEST_PATH_PATTERNS`
both state "72.2% of Java types in this corpus are tests (vs 34.7% Python)".
On the V1 AIDev-7.6M Java data the measured share is **26.6%**. The 72% figure
describes the old AIDev-full single-agent corpus, where the 27 repositories were
Apache-style server projects with very heavy test suites. The V2 figure must be
read off the report, and the reporting decision that depends on it -- lead with
production-only rates -- is unaffected either way.

## 15.26 V2.1 REDESIGN, after attacking the corpus as a jury (2026-08-26)

The V2 corpus (2,740 PRs / 730 repos, equal 300-PR cells) was attacked
deliberately, on measured evidence rather than intuition. Four attacks landed;
three are fixed, one is disclosed.

### ATTACK 1, the strongest: "you compared projects, not agents"

Measured on the V2 draw: only **13 of 730 repositories** contained all three
agents, holding **6.7% of PRs**. Pairwise overlap was 48 / 39 / 22 repositories.
So a cross-agent claim was almost entirely a BETWEEN-PROJECT claim. Macro-per-repo
controls how much each repository WEIGHS; it cannot control which repositories an
agent happened to work in.

**Fixed by preferring multi-agent repositories in the draw** (`V2_DRAW_PRIORITY`
contains `multi_agent`), which grows the subset on which a genuine
within-repository comparison exists rather than subsetting for it:

| | V2 | V2.1 |
|---|---|---|
| repos with all 3 agents | 13 (6.7% of PRs) | **21 (7.8%)** |
| Claude_Code / Copilot shared | 48 repos | **94 repos, 1,018 PRs** |
| Claude_Code / OpenAI_Codex shared | 39 repos | **48 repos, 633 PRs** |
| Copilot / OpenAI_Codex shared | 22 repos | **44 repos, 553 PRs** |
| PRs in multi-agent repos | ~7% | **29.4%** |

The ceiling is the dataset's: only 111 eligible Java repositories contain >= 2
agents at all, and 16 contain all three.

### ATTACK 2: "your agents are from different years"

Median PR date was OpenAI_Codex 2025-07-10 against Claude_Code 2026-02-21, a
**7.5-month** gap. A cross-agent comparison was partly a comparison of
repository eras and of older agent versions.

**Narrowed by preferring recent PRs in the draw** (`recent` in
`V2_DRAW_PRIORITY`): the gap is now **4.6 months** (Codex 2025-10-15 against
Claude_Code 2026-03-03). Not closed, and it cannot be: OpenAI_Codex has only 412
eligible Java PRs after 2025-10-01 against Claude_Code's 2,223. The residue is
DISCLOSED and gets a common-window sensitivity check.

Recency is also better science independently of the attack: it measures the
agents as they are now rather than as they were.

### ATTACK 3: "changes_requested cannot support a claim"

NOT FIXABLE, and now known precisely. All 1,927 eligible rejected Java PRs were
enriched from the GitHub API rather than only the drawn ones, and the stratum is
simply that rare in the population. Taking EVERY eligible rejected PR captures
every one that exists, which is the most the dataset allows. Reported
descriptively with its count beside every rate, never as a powered comparison.

### ATTACK 4: "Codex rests on few repos, and 72% of the corpus is Apache-2.0"

NOT FIXABLE. OpenAI_Codex has only 134 eligible Java repositories against
Claude_Code's 340 and Copilot's 466. Disclosed, and it is why macro-per-repo
leads for that agent especially.

### THE DESIGN CHANGE: within-agent outcome balance

Equal 300-PR cells capped every cell at the SMALLEST cell in the corpus
(OpenAI_Codex rejected = 315), discarding most of the eligible pool to buy equal
n ACROSS agents -- a control the reporting already provides, since cross-agent
comparison is made on macro-per-repo RATES and a rate is not biased by its
denominator. Equal n is genuinely needed only for merged vs rejected WITHIN one
agent, which is the V2 contrast.

So: **take every eligible rejected PR** (the scarce side in every agent) and
match merged to it, over-drawn 2x because the rejected pool is exhausted and
cannot absorb Step-2 attrition while merged can.

| agent | merged (2x) | rejected (all) | open |
|---|---|---|---|
| Claude_Code | 970 | 485 | 283 |
| Copilot | 2,544 | 1,272 | 364 |
| OpenAI_Codex | 630 | 315 | 93 |
| **total** | **4,144** | **2,072** | **740** |

**6,956 PRs across 1,010 repositories**, against 2,740 / 730. Concentration
improved too: worst merged cell top-1 is 1.9% (was 6.0%), and Claude_Code's
merged cell now spans 271 repositories instead of 165.

Balance is restored on ANALYSABLE PRs after extraction, which is the population
every rate is computed over; the merged surplus is not waste, it raises
precision on the merged side and enlarges the head-state inventory that forms
the denominator of the present-vs-introduced headline.

### A trap found while attacking

`gold_set.n_touched_files` is **0 for 100% of git_diff rows**, and the git_diff
share is agent-correlated (Claude_Code 94%, Copilot 18%). Anyone reading it as
PR size would conclude Claude_Code writes empty PRs. The truth from the resolved
manifest is the opposite: Claude_Code's rejected PRs are the LARGEST (median 5
files, mean 43.8). Nothing in the pipeline uses the gold column, but it is a
loaded gun and is now named as one.

### 15.27 Path exclusion matched SUBSTRINGS, not path components (found 2026-08-27)

**Symptom that led here.** Extraction closed with a `per-cell usable-rate spread:
22.8 pp` warning. Chasing it showed the spread itself was benign: it is explained
by which ATTRIBUTION ROUTE an agent's PRs travel, not by biased failure.

| route | meaning | usable |
|---|---|---|
| `dataset` | AIDev supplied the file list | 88.5-100%, spread 11.5 pp |
| `git_diff` | no attribution; PR selected by repo-language proxy | 42-75% touch zero `.java` |

Claude_Code is 95.5% `git_diff`, OpenAI_Codex 55.6% `dataset`, so the two agents
lose different fractions for a reason that has nothing to do with their code. A
PR in a Java repository that touches no Java file is OUT OF SCOPE, not lost.

**The real defect, found while verifying that claim.** Sampling `no_source_files`
PRs and re-diffing them independently with git turned up `vavr-io/vavr#3200`,
which contains 19 `.java` files and was still recorded as having none.

`is_source_file` tested `any(part in path for part in excluded_parts)`. That is a
SUBSTRING test against the whole path, so:

* `gradlebuild/` contains `build/`
* `riksdagen/` contains `gen/`
* `oxygen/`, `nitrogen/`, `imagen/` all contain `gen/`
* `mytarget/` contains `target/`

**Measured over the whole corpus** (983 repositories, `git ls-tree` at a real
SHA): **91 repositories affected, 8,994 `.java` files wrongly excluded**, and the
exposure is AGENT-BIASED:

| agent | PRs in an affected repo |
|---|---|
| Claude_Code | 17.0% |
| Copilot | 12.4% |
| OpenAI_Codex | 6.8% |

A 10 pp spread, so this would have moved a per-agent rate rather than merely
shrinking the corpus. It also corrupts HEAD-STATE trees, not just touched-file
lists: `src_tree` applies the same filter, so 91 repositories would have been
analysed with missing classes, giving broken supertype resolution and corrupt
hierarchies. That is precisely the failure mode that voided the first Java run.

**Fix.** `gitsnap.path_is_excluded`: entries ending in `/` are DIRECTORY
COMPONENTS matched against whole components; everything else matches inside the
FILE NAME only. A separate, deliberate `-gen`/`_gen` component-suffix rule keeps
genuinely generated trees out (`vavr/src-gen/`) while keeping the hand-written
generator in (`codegen/`). `clone_and_extract.is_source_file` now DELEGATES to
gitsnap, so extraction and the five delta harnesses cannot drift apart. Ten
assertions pin the regression in the gitsnap self-check.

### 15.28 The tree cache key omitted the exclusion rule (found while fixing 15.27)

`_cache_key(full_name, sha, extensions)` did not include `excluded_parts`, so a
tree cached under the OLD rule is served unchanged after the rule changes. The
repair in 15.27 would therefore have been a NO-OP for every repository already
cached: the code looks fixed, the self-check passes, and the data never changes.

`excluded_parts` is now part of the key, which makes every stale entry miss and
rebuild. The existing 3.9 GB `tree_cache/` was unreachable under the new key and
was deleted. Pinned by an assertion in the gitsnap self-check.

**Rework performed.** 885 PRs (12.7%) sit in an affected repository. Their
manifest rows (14,622) and extracted trees were dropped and re-extracted with the
corrected filter; `clone_manifest_java.preFilterFix.parquet` is the backup.

### 15.29 JVM tuning, measured not guessed (2026-08-27)

`ANALYZE_WORKERS` was 8 with a 3 GB heap each, i.e. 24 GB on a 15.4 GB machine.
Measured with 4 workers: peak RSS 2,953 MB per JVM and system available RAM
falling to **1.53 GB**. Three flags added in `java_bridge.JVM_TUNING`:

* `MaxHeapFreeRatio=30` / `MinHeapFreeRatio=10` -- G1 does not return pages to
  the OS, so a worker that parsed one big tree stayed at ~3 GB forever. After:
  peak per JVM **2,042 MB**, mean **1,035 MB**.
* `ActiveProcessorCount=2` -- each JVM otherwise sizes its GC thread pool from
  all 12 logical cores, so N workers spawn ~12N GC threads contending.
* `ExitOnOutOfMemoryError` -- without it a JVM at its ceiling spends minutes in
  back-to-back full GCs, freezing the machine; with it the worker dies at once
  and `analyze_tree` records the failure with its reason.

### 15.30 Corpus as built, and the honest jury reading

Drawn 6,956 PRs / 1,010 repos. **Usable 3,880 PRs / 716 repos.**

| agent | merged | rejected | open (supplementary) |
|---|---|---|---|
| Claude_Code | 445 | 279 | 188 |
| Copilot | 1,371 | 673 | 250 |
| OpenAI_Codex | 407 | 214 | 53 |

* Concentration is GOOD: top-1 repo <= 8.9%, top-3 <= 21.2%. The MontrealAI
  attack that broke V1 Python does not apply.
* **263 repositories contribute BOTH merged and rejected**, which is what makes
  the paired within-repo contrast possible.
* Worst MAIN-arm imbalance is 6.4x (Copilot merged 1,371 vs Codex rejected 214),
  handled by macro-per-repo reporting plus Wilson intervals.
* `OpenAI_Codex/open` is n=53. `open` is a SUPPLEMENTARY outcome by design
  (`V2_SUPPLEMENTARY_OUTCOMES`) and must be reported as such; no per-cell claim
  is available at that n.

### 15.31 The verifier could not have caught either defect in 15.27 / 15.28

Both defects passed every check in `verify_phase.py`, and the reason generalises:
**every existing check tests the DATA for bias, and both defects were applied
UNIFORMLY.** A consistently wrong in-scope rule shifts every agent's numbers
together, so a spread check sees an even distribution and reports PASS. Bias
tests cannot find a mistake that is uniform by construction.

New `--phase scope`, run automatically before `extract` and `analyse`, asserts
properties of the RULE instead of the data:

1. **Over-matching.** Seven adversarial paths that CONTAIN an excluded fragment
   but are real source (`gradlebuild/`, `riksdagen/`, `codegen/`, `mytarget/`,
   `oxygen/`, `prebuild/`, `generatedly/`) must survive; six genuinely generated
   paths must not. `part in path` fails all seven.
2. **Cache-key completeness.** Every input that changes a cached function's
   OUTPUT must change its KEY -- repo, sha, extensions AND the exclusion rule.
   This is the property whose absence made the 15.27 repair a no-op.
3. **Stale git locks**, age-filtered and SKIPPED while any git process is live.

**A note on check design, learned by getting it wrong here.** The first version
of check 3 counted every lock file and therefore fired during any healthy run
that was still going, reporting held locks as damage. A safety check that cries
wolf on a normal run is worse than no check at all, because it trains the reader
to ignore the next, real warning. It now skips when git is live and only counts
locks older than ten minutes.

### 15.32 Known weakness: checkpoint granularity is PER REPO, not per PR

`process_repo` runs all of a repository's PRs on one thread and returns rows only
when the whole repository finishes, so nothing is recorded for a large repository
until it completes. `Hack23/cia` (229 PRs) showed 0 manifest rows while visibly
processing. This is NOT corruption and NOT data loss: the extracted files are on
disk and a redo runs at `[cached]` speed. It does mean a kill mid-repository
loses that repository's bookkeeping. Left as-is deliberately -- restarting the
run to change it would cause exactly the loss it prevents. Worth fixing in the
next idle window by flushing rows per PR rather than per repo.

### 15.33 Extraction verified; the route-conditioned spread is a FINDING, not a defect

`verify_phase --phase extract`: 7 PASS, 3 WARN, no FAIL. Final corpus
**3,936 analysable PRs / 728 repositories** from 6,956 drawn.

**The clean result that validates the corpus.** On the `dataset` route -- where
AIDev supplies the file list -- the usable rate is even across agents to within
**2.2 pp** (Claude_Code 98.7%, Copilot 96.8%, OpenAI_Codex 99.0%, n=2,470).
Extraction itself is unbiased.

**The spread is on the `git_diff` route, and it is real.** Where AIDev gives no
file list, the PR is drawn by repository language and may touch no Java at all.
Usable rates there are Claude_Code 51.9% / Copilot 23.3% / Codex 25.4%
(28.6 pp). The obvious explanation is repository composition, and it is WRONG:

| | pooled | within repos where >=2 agents both worked | macro per-repo |
|---|---|---|---|
| Claude_Code | 51.9 | **52.2** | 53.2 |
| Copilot | 23.3 | **16.9** | 23.9 |
| OpenAI_Codex | 25.4 | **24.3** | 18.0 |
| spread | 28.6 pp | **35.3 pp** | 35.2 pp |

Holding the repository constant (108 repos, 1,247 PRs) the gap WIDENS. So it is
a behavioural difference between agents, not a sampling artefact: **in the same
repositories, Copilot's PRs are far more often documentation, build config and
CI, while Claude_Code's are far more often source code.** An independent
extension check on the non-Java PRs agrees -- they are dominated by `.csproj`
(73), `.md` (69), `.yaml`/`.yml` (35), `.xml` (15), with barely any real code.

**Report it as a corpus-characterisation finding, and disclose the selection
caveat it creates.** If only ~17% of Copilot's unattributed PRs touch Java, the
Copilot PRs that reach the SOLID analysis are a NARROWER and probably more
substantial slice of its output than Claude_Code's are of theirs. That is a
limitation on the per-agent comparison and must be stated; it is not removable
by any filter, because the PRs it excludes contain no Java to measure.

**Also WARNed, for disclosure:** `file_missing` residue is 6.91% of file rows
(6,859), of which only 10.5% are recoverable under a final renamed path -- higher
than the 2% the check wants. Mostly PRs whose head no longer resolves the listed
path. Quantified, not silent.

### 15.34 Mid-run audit of the calibration pass (clean), and the tier x outcome confound

Audited at 1,604 / 6,956 PRs. **18 checks, 0 failures.** Structural: no duplicate
PR rows, no orphaned entity rows in any of the four principle parquets, no nulls
in `agent`/`outcome` on any row, no negative sizes, LCOM and C3 inside [0,1], and
`agent`/`outcome` matching the gold set exactly.

Contract checks that matter for this pass specifically: SRP/OCP/ISP flags are
100% NULL (calibration must not flag), while LSP `violation` is 100% populated
(its signals are categorical and were never threshold-tuned, so it is not part of
calibration). Both behaved correctly.

`is_new` is **21.0%** of types. This is the cheap detector for the "empty before
tree" defect repaired in 15.x: a failed parent tree scored as an empty one makes
every type look newly authored and inflates the headline "violations the PR
INTRODUCED" counts. Near-100% would mean the bug is back; 21% is plausible.

**THE CONFOUND, and it is structural rather than a defect.** The before/after
pair is resolved in three tiers, and the tier mix DIFFERS BY OUTCOME by
construction, because a rejected PR has no merge commit:

| outcome | tier1 merge commit | tier2 commit set | tier3 pull ref |
|---|---|---|---|
| merged | **46.6%** | 19.1% | 34.3% |
| open | 1.9% | 44.9% | 53.3% |
| rejected | **0.0%** | 57.8% | 42.2% |

A bare merged-vs-rejected contrast is therefore PARTLY a contrast of how the
before-tree was reconstructed. Recording `pair_tier` on every row makes this
visible but does not settle it.

**The test that settles it, to run when the pass completes.** Merged PRs appear
in ALL THREE tiers, so the tier effect can be measured WITHIN the merged group,
where outcome is held constant:

* if violation rates do not differ by tier within merged, tier is not biasing the
  measurement and the full outcome contrast stands;
* if they do differ, the headline contrast must be restricted to tiers 2 and 3,
  which are the tiers both outcomes actually share.

Either way the answer is decidable from data already being collected, so this is
a limitation that gets RESOLVED rather than merely disclosed.

### 15.35 Memory grows with the run: the analysis process, not the JVMs

Measured at 1,268 / 3,936 analysable PRs with 4 workers: the **python process
held 2.68 GB**, more than every Java sidecar combined (882 MB at that instant).

`run_java.main` accumulates `prs` and the five `buckets` lists in memory for the
whole run and rewrites the complete parquet set at every checkpoint. That is what
makes each checkpoint a valid COMPLETE snapshot -- the property that made a
reboot cost nothing -- but it means resident memory grows LINEARLY with progress,
and the growth is in the coordinator, not in the workers everyone watches.

Consequence for tuning: a worker count validated early in a run is not valid late
in it. The right setting must leave headroom for the coordinator to roughly
double. Concretely, 5 workers measured safe at 1,000 PRs drove free RAM to 0.2 GB
once JVM heaps grew with larger trees and desktop applications loaded; 4 workers
with a 2.7 GB floor is the setting that survives to the end.

**Not fixed, deliberately.** Streaming rows to disk instead of accumulating them
would cap memory, but it would also break the "every checkpoint is a complete
snapshot" guarantee that crash-safety rests on. The trade is correct as it
stands; the growth just has to be BUDGETED FOR rather than discovered at 90%.

### 15.36 The flagged pass is GIL-bound, not network-bound (measured 2026-09-01)

The bottleneck MOVED THREE TIMES during this phase, and each time the previous
worker setting stopped being right. Measured, not assumed:

| stage of the run | bottleneck | evidence |
|---|---|---|
| tree cache cold | NETWORK | 14 git procs, ~1 live JVM, CPU 40%, link saturated at ~28 Mbps |
| tree cache warming | mixed | JVM bursts to 200-266% then idle |
| **tree cache warm** | **PYTHON, one core** | **git 0, java 0, python 91-100% of a core, sustained** |

**Why more workers stopped helping.** `run_java` uses a ThreadPoolExecutor, and
the detector stage (`build_index`, `snapshot`, the five principles) is pure
Python bytecode, so the GIL serialises it. Measured directly: 4 workers gave
35.7 PRs/min and 10 gave 40 -- an 11% difference for 2.5x the workers, because
the interpreter, not the machine, is the limit.

Checkpointing was RULED OUT as the cause: rewriting the full parquet set costs
0.20 s against a 98 s cycle, i.e. **0.2%**. The remaining ~100% is detector work.

**The fix is PROCESSES, not threads**, and it is deferred to Python V2.1 rather
than applied mid-run:

* `gitsnap.repo_lock` is a `threading.Lock`; under processes it silently becomes
  a no-op, so two processes could run git in one repository at once. Those
  failures would be recorded as ordinary fetch failures -- silent,
  repository-correlated, therefore agent-correlated loss.
* The clean form is to PARTITION `todo` BY REPOSITORY so each process owns whole
  repositories and no lock is needed at all.
* Note the irony to watch for: four separate Python heaps could recreate the
  MEMORY pressure this phase just escaped. The process count must be re-measured,
  not inherited from the thread count.

**Ranked improvement list for Python V2.1**, now that the measurements exist:

| change | expected gain |
|---|---|
| ProcessPool + repo partitioning | ~4x on the detector stage -- THE BIG ONE |
| GitHub API file lists before extraction | skips ~2,800 per-PR fetches that yield no Java |
| SQLite/WAL checkpointing | flat memory instead of linear growth |

Python V2.1 also still runs FIVE SEPARATE HARNESSES (run_srp/dip/isp/ocp/lsp),
each re-fetching and re-parsing the same trees. Porting it to Java's single-pass
design is probably a larger win than all three of the above combined, and should
be measured before that run starts.

### 15.37 The `sidecar_failed` PRs are HEAP EXHAUSTION, not timeouts (2026-09-01)

The flagged pass carries 9 `sidecar_failed` PRs and the error is `exit 3`, i.e.
the JVM terminating under `-XX:+ExitOnOutOfMemoryError`. They are **not**
timeouts, which means the timeout raise from 900s to 2400s made no difference to
them and never could have. Diagnosing the status by name rather than by its
error string would have hidden that.

**All 9 are one repository -- `kubernetes-client/java` -- and all 9 are
Copilot's.** The cause is repository size (it is dominated by generated
Kubernetes API model classes) and the tree does not fit a 3 GB heap. But the
EFFECT is agent-shaped: leaving them out costs Copilot 9 PRs and the other two
agents none. Size correlates with repository, repository correlates with agent,
and that is the same silent bias family as the no-merge-SHA drop and the
`pr_commit_details` inner join.

**Fix, deliberately deferred to AFTER the main pass:** retry just these PRs with
ONE worker and a 6-8 GB heap. A larger heap is safe when nothing competes for
memory, and unsafe during the main run where three concurrent JVMs already push
free RAM to 0.39 GB on the largest trees. Cost is minutes -- the trees are
already fetched and cached.

**Method note worth keeping:** the three failure statuses have three different
causes and three different fixes, and only the error string distinguishes them:

| status | cause | fix |
|---|---|---|
| `sidecar_failed` (exit 3) | JVM heap exhausted | bigger heap, run alone |
| `sidecar_failed` (timeout) | tree too slow to parse | longer timeout |
| `archive_failed` / `no_delta_pair` | transient git | retry (automatic) |
