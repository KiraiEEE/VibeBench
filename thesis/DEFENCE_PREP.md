# Defence preparation

Read this in order. Do not try to memorise the thesis. Memorise **Layer 1**, then
Layer 2, and let everything else hang off them. If you know Layers 1 and 2 cold,
you can answer almost anything by reasoning from them out loud.

---

## Layer 1: the spine (memorise this, nothing else at first)

Five sentences. Say them out loud until they are automatic.

1. Coding agents open real pull requests now, and they are judged on whether tests
   pass. Tests say nothing about design.
2. So: can SOLID adherence be measured on real agent pull requests at scale? Five
   structural detectors, run before and after each pull request, in Java, then the
   whole study repeated in Python.
3. A detector is only a proxy, so I measured the detector too. 2,475 entities were
   read and labelled by an independent judge. Two principles come out measurable,
   three only screen.
4. Agents rarely introduce violations. The code they touch already contains far
   more. And how much an agent changes matters more than which agent it is.
5. Only one agent difference holds in both languages: Claude Code on Open/Closed.

**If you freeze at any point in the defence, go back to these five.** Every slide
in the deck is one of these five in more detail.

---

## Layer 2: the eight numbers

These are the only figures you must know without looking.

| Number | What it is |
|---|---|
| **4,245 / 4,946** | analysable pull requests, Java / Python |
| **2,475** | entities judged, each with a written reason |
| **0.4% to 4.1%** | pull requests that introduce a violation, after precision correction |
| **183 against 16** | refused-bequest violations present in touched Java code, against pull requests that introduced one |
| **1.88 to 2.18** | odds ratio per doubling of files touched. The dominant effect |
| **2.22 / 2.09** | Claude Code's Open/Closed odds ratio, Java / Python |
| **0.892 and 0.110** | best and worst detector precision (Java DIP, Python SRP) |
| **0.574 vs 0.384** | our agreement with PMD, against PMD's agreement with DesigniteJava |

That last row is your shield. **Your detector agrees with published tools more
than those tools agree with each other.**

---

## Layer 3: the state of the art, in one page

You do not need to recite papers. You need to know **four claims other people made**
and what you did about each.

| They found | You measured | Your line |
|---|---|---|
| More code generated, more structural decay (**Zhu, Yuecai**, Volume-Quality Inverse Law) | Change size dominates: OR 1.88–1.95 Java, 2.01–2.18 Python | "Confirmed. It is my strongest result." |
| Agent differences vanish once you normalise for churn (**Cynthia et al.**) | Only Claude Code on Open/Closed survives adjustment | "Confirmed, with one exception that replicates." |
| Half of passing SWE-bench patches would not be merged (**Whitfill et al., METR**) | Merged Java code is indistinguishable from code closed without review | "Consistent. Merge status carries little structural information." |
| 91% functionally correct, thousands of design issues (**Kashif et al.**) | Present violations outnumber introduced ones about 11:1 | "Consistent, but they build new projects and I measure edits to existing ones." |

And two where you **differ**, which are the interesting ones to raise yourself:

* **Pehlivan et al.** found SRP easiest for an LLM to detect and DIP hardest.
  For structural detectors it is **reversed**: DIP is the most precise, SRP the
  least. Your line: *"A model reading an example can see a class doing several
  things, but it cannot see an import cycle across files it was not shown. A
  static analyser has the opposite profile. The two approaches fail on opposite
  principles, so they should be combined."* This is a genuinely good point. Use it.
* **NITR (Zhu, Haichao)** found agents succeed at only 4.3% of dependency-control
  tasks. That is **a different question**: it asks agents to *repair* structure,
  you ask whether they *damage* it. Also C++, not Java or Python. Your line:
  *"An agent can be both unlikely to fix a cycle and unlikely to create one."*

---

## Layer 4: your experiment, in one flow

Say it as a sequence. Each step answers "and then what?"

1. **Corpus.** AIDev, 7.69M agent pull requests. Filter to Java and Python, three
   agents, three outcomes. 4,245 and 4,946 analysable.
2. **Recover two trees.** For each pull request, the code *before* and *after*.
   Four fallbacks, because merge commits are not always available.
3. **Run five detectors on both trees.** Charge to the pull request only the
   *difference*. That is the delta. A violation that was already there is not
   the agent's.
4. **Calibrate thresholds at the 90th percentile of each corpus, before any label
   existed.** Then the result files were deleted and the pass re-run, so no flag
   predates its threshold.
5. **Validate.** 100 flagged + 100 unflagged per principle, seeded, audited, then
   read by an independent judge that records a reason for every verdict.
6. **Score.** Precision on the flagged side. Recall weighted by stratum size,
   because the sample is half flagged but the corpus is not.
7. **Model.** GEE, clustered by repository, adjusting for change size and time.
8. **Cross-check.** CK, PMD, DesigniteJava on 150 repositories.
9. **Replicate.** Everything again in Python, thresholds recalibrated first.

**The one-sentence version:** *"I measure what each pull request changed about the
structure of the code it touched, and I measure how much to trust each measurement."*

---

## Layer 5: the questions a jury will actually ask

Short answers. Say the first sentence, stop, and let them follow up.

**"Your precision is 0.33. Why should we believe anything?"**
> Because every rate I report is already corrected by it, and the headline result
> rests on the detectors that are precise. Refused bequest is 1.000 on 64 judged
> flags: 183 present against 16 introduced needs no correction at all.

**"Your judge is a Claude model and one of your agents is Claude Code. Bias?"**
> I tested for it. Per-agent precision shows no leniency towards Claude Code; it
> is in fact lowest on Dependency Inversion (0.792 against 0.959). Judge
> self-consistency is 89.7% on 174 entities judged twice.

**"These are LLM labels, not human labels."**
> Correct, and it is stated as a limitation. LLM judging of SOLID is a validated
> technique (Pehlivan et al., 2025). Every label records the reason and the source
> the judge was shown, and a mechanical audit gate refuses a label whose source is
> not on disk.

**"Why not just use existing static analysis tools?"**
> None of the three has a rule for Interface Segregation at all. For Open/Closed,
> DesigniteJava has the closest counterpart and we agree at only kappa 0.122. For
> God Class, where a counterpart does exist, my rubric agrees with PMD at 0.574 and
> DesigniteJava at 0.531, while those two agree with each other at 0.384.

**"Isn't SOLID too subjective to measure?"**
> Partly, and saying *which* parts is the answer to my second research question.
> Dependency Inversion and refused bequest are structural and measure cleanly.
> Responsibility and openness are judgements about intent, and there my detectors
> screen rather than measure. I report that instead of assuming all five work.

**"Why does your corpus over-sample rejected and open PRs when the real mix is 80/10/10?"**
> Deliberately, to have enough rejected and open pull requests to compare: per
> agent I took every rejected PR and twice as many merged ones (Python: fixed
> quotas of 1,400 / 700 / 250). It
> means my outcome rates are conditional, not population rates, and that is stated
> where they are reported.

**"Did you fix the weak detectors?"**
> One of them, yes: restricting Liskov to refused bequest took precision from
> 0.420 to 1.000, and it held on a holdout and transferred to Python. I tried a
> numeric rule for Interface Segregation and it failed its holdout, 0.862 down to
> 0.704 and then 0.200 in Python. For Open/Closed no threshold can work: precision
> is flat at 0.270 to 0.400 across every branch-count band.

**"Your recall is 0.386 for Liskov. You are missing violations."**
> Yes, and it is stated. Note which way that cuts: missing violations means more
> are present than I counted, which widens the gap between present and introduced
> rather than closing it. My main claim survives.

**"Why Bonferroni? Your Java effect fails it."**
> It does, on its own. That is why the claim rests on the replication: the Python
> coefficient passes the correction, has the same direction and a similar size.
> The Python import-cycle effect does not pass and I report it as unconfirmed.

**"Can you claim agents cause this?"**
> No. Agents chose their own tasks, so these are associations. I adjust for change
> size, repository and time, but not for task selection.

**"What is actually new here?"**
> Three things. A map of which SOLID principles a rule can measure and which it can
> only screen, with precision and recall for every detector. Evidence in two
> languages that most design problems in the code agents touch predate them. And a
> replication that separates what holds from what does not.

**"Why two languages?"**
> Because the type system changes what is detectable. 46 of the 51 confirmed
> Python Liskov violations rest on faults the Java compiler would reject outright.
> The set of detectable violations depends on the language, which you cannot see
> from one language alone.

**"What would you do next?"**
> A change-history signal for Open/Closed: a conditional edited across many pull
> requests to add cases is, by definition, not closed for modification. A
> framework-aware responsibility screen for Python. And sampling only unflagged
> entities to narrow the recall intervals.

---

## When you do not know the answer

Do not guess, and do not bluff a number. This project's whole credibility is that
every figure traces to a script. Say one of these:

* *"I did not measure that. What I did measure is ..."* then bridge to something
  you did.
* *"That is a limitation I state in Section 5.5."* (Say the number of the section.
  It shows you know your own weaknesses.)
* *"I would have to check the data rather than answer from memory."*

A jury respects "I did not measure that" far more than a confident wrong answer.
You have a verifier that re-checks 61 figures against the data precisely because
this project refuses to trust memory. Say so if it comes up.

---

## How to rehearse, in four sessions

1. **Session 1.** Say Layer 1 out loud, five times, without notes. Then the eight
   numbers. Nothing else.
2. **Session 2.** Walk the deck and give **one sentence per slide**. If a slide
   needs more than one sentence, you do not understand it yet; go read that part
   of the thesis.
3. **Session 3.** Have someone ask you the Layer 5 questions in random order.
   Answer in two sentences and stop talking. Overexplaining is what gets people
   into trouble.
4. **Session 4.** Explain the whole thesis in two minutes to someone who is not a
   software engineer. If you can do that, you are ready.

**The single most useful habit:** answer, then stop. Let them ask the follow-up.
Most bad defences are people talking themselves into a corner they were never
pushed towards.

---

## What is still open, so it does not surprise you

* Cover fields (jury president, defence date, topic code) are deliberately blank
  until the department sets them.
* Open/Closed is your weakest point on both flanks: precision 0.330 and kappa
  0.122 against DesigniteJava. **Raise it yourself** before they find it. It is in
  the limitations and in future work.
* Python has no judge self-consistency measurement. Java does, 89.7%.
* 7.0% of Python pull requests lost their source, unevenly: 11.0% of rejected,
  14.3% of Copilot's.
