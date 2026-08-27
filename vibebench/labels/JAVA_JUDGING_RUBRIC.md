# Java judging rubric , freeze this BEFORE labelling starts

## Why this file exists

In the Python round the SRP rubric was never written down, and it drifted: the
first 480 rows were judged ~3x more leniently than the remaining 3,945, on
populations that were statistically identical (same agent mix, same flagged
share, same class-size profile). It took a full re-judge of those 480 rows to
repair, and a residual gap of p=0.033 still remains.

**Read this file first. Do not let the standard evolve mid-run.** If you find
yourself wanting to change a rule at row 800, stop, change it here, and re-judge
what came before under the new rule.

## Rules that apply to every principle

1. **Judge the entity, not the metrics.** The batch prints size/complexity
 numbers because they are facts about the class, but a class is not a
 violation because it is large. Read the code.
2. **Batches are blind by default** , the detector's verdict is not shown. Do
 not go looking for it. If you need it to decide, the answer is "no".
3. **Test code is NEGATIVE** for every principle. Test doubles, mocks, fakes,
 fixtures and `src/test/**` classes are not designed for substitution or
 extension. Java's corpus is ~60-72% test code, so this matters.
4. **Generated code is NEGATIVE** , anything under `target/`, `build/`,
 `generated/`, or carrying `@Generated`. Nobody authored its design.
5. **Insufficient evidence ⇒ NEGATIVE.** If the source did not extract, or the
 entity cannot be located, do not guess a positive.
6. **Precedent binds.** The corpus repeats near-identical code across PRs. Once
 a pattern is judged, judge every later instance of it the same way. Keep a
 note of pattern decisions as you go.

7. **NEVER judge with the PR's OUTCOME in view.** V2 asks whether SOLID
 conformance relates to whether a PR was accepted, so a judge who knows a PR
 was rejected can produce that answer out of nothing. The batch files carry
 `owner/repo#number` and code, and no outcome, no merge state and no
 rejection stratum, deliberately. Do not look one up, do not sort the CSV by
 `outcome` before judging, and do not judge merged and rejected rows in
 separate sittings. The label sets are SHUFFLED at build time precisely so
 the two are interleaved.

 The check that catches a violation of this rule is in `score_labels.py`:
 it reports precision PER OUTCOME, and a spread above 15 points means the
 merged-vs-rejected contrast is partly a property of the judging rather than
 of the code. If that fires, the round is re-judged, not explained.

8. **The detector's verdict is not shown, for ANY principle.** Every batch
 header is blind by default and `--show-verdict` exists only for debugging.
 Two leaks were found and closed on 2026-08-25: the Java DIP header printed
 `new_cycle=` and the Python DIP header printed `NEW CYCLE: ...`, both
 unconditionally, and both ARE the flag. Any DIP precision figure produced
 before that date is an UPPER BOUND. Cycle membership is still shown, without
 the novelty label, because the row cannot be judged without it; novelty is
 decided from the new-edge list.

---

## SRP , does this type have more than one reason to change?

**POSITIVE** when the type contains **two or more method clusters serving
different DOMAINS**, each of which could be extracted into a coherent standalone
class of >=4 methods. Examples that qualified in Python: a filesystem API that
also owns agent registration, ML trajectory learning and sandbox orchestration;
a PDF parser that also does programming-language detection and code-quality
scoring; a client exposing tracking, model-registry, prompt-registry and tracing
as one surface.

**NEGATIVE** for large-but-cohesive. Specifically:
- One coherent API surface (a client wrapping one service's endpoints; a DB-API
 cursor; a cloud-provider adapter) , many methods, one responsibility
- A parser/serializer for ONE format
- A data/model/DTO/entity class, however many fields
- Framework-imposed shape (JPA entity, Spring `@Configuration`, Android
 `Activity`, builder/DSL with chainable methods)
- A facade that DELEGATES to collaborators rather than implementing them
- Infrastructure serving the class's own function , its own cache, its own
 metrics, its own logging. That is support, not a second domain.
- **Size alone. Never positive on size alone.**

Java note: Java classes run far larger at the tails than Python or TypeScript
(Python's 200-LOC bar sits near Java's p57). Calibrate on the code, not on a
number carried over from another language.

---

## OCP , is this dispatch open to extension, or closed by construction?

**POSITIVE** when the branch set is a **genuinely open-ended family the project's
own domain model would realistically extend**: a `switch` over a type tag where
new variants arrive with new features; an `instanceof` ladder over a hierarchy
the project owns and keeps growing.

**NEGATIVE** when the set is closed or exhaustive:
- `switch` over an **enum** , fixed by the language (the detector already
 suppresses these; if one reaches you, it is still NEGATIVE)
- A fixed grammar or protocol with a specification-defined variant set (HTTP
 methods, JSON token types, SQL node kinds)
- Input normalisation: `String`-or-`CharSequence`-or-`char[]` handling
- Platform/OS/architecture switches , the set is fixed by the world
- Ladders over Java built-ins (`Integer`/`Long`/`Double`/`String`)

The question is not "is this a switch" but **"will this switch need a new arm
when the domain grows?"**

---

## LSP , does the override break a caller relying on the base contract?

Java is stricter than Python here: **`javac` already enforces** parameter lists
(a changed list is an overload, not an override), parameter types (invariant),
visibility (cannot narrow) and checked exceptions (cannot broaden). So those
cannot occur in compiling code and are not signals. Only three remain.

**POSITIVE:**
- **`refused_bequest`** , the override throws (`UnsupportedOperationException`,
 `NotImplementedError`-equivalent) or silently no-ops **in place of real,
 working base behaviour**. This is the strongest and most reliable signal.
- **`precondition_strengthened`** , the override adds a validation/guard the
 base did not impose, so input the base accepts is now rejected.
- **`postcondition_weakened`** , the override returns null/empty where the base
 guarantees a value, or stops honouring a documented invariant.

**NEGATIVE:**
- Implementing an `abstract` method or an `interface` method , there is no
 bequest to refuse
- Overriding a base whose body is itself `throw new UnsupportedOperationException()`
 or empty , both are non-functional, so nothing was taken away
- Template-method hooks: a base returning `false`/`null`/empty **documented as
 an override point**
- Calling `super.method(...)` and then extending
- Covariant return narrowing , legal and safe in Java
- A documented, provider-specific no-op with a stated reason

---

## ISP , are implementers forced to take methods they cannot use?

**POSITIVE** when a wide contract forces implementers into **stub methods** ,
empty bodies or throws , for members irrelevant to them, and the contract could
be split along the lines its implementers already reveal.

**NEGATIVE:**
- A wide interface whose implementers genuinely implement all of it (wide but
 cohesive)
- A marker or data-shape interface
- Framework/SPI contracts whose shape is imposed externally (`Serializable`,
 JDBC, servlet APIs)
- Default methods providing real behaviour , an implementer not overriding a
 `default` is NOT a forced stub
- Fewer than 2 stubs, or stubs below half the contract

---

## DIP , did this PR introduce a dependency cycle?

**POSITIVE** when the PR creates a **new mutually-reachable pair** of
compilation units that did not exist before, and at least one side was touched
by the PR. The canonical shape from the Python round: a new leaf module imports
the package root, which re-exports it.

**NEGATIVE:**
- The cycle already existed before the PR
- The cycle involves only untouched modules , not this PR's doing
- The PR **removed** a module from a cycle
- `mean_d_worse` fired but no new cycle appeared. Martin distance moving is a
 graph-recomposition artefact; it scored **0/7** in the Python census and
 **0/10** in TypeScript. It is excluded from the flag and must not drive a
 positive judgement.

---

## When you finish each principle

```
python vibebench/labels/check_label_file.py --file java_<p>_census.csv \
 --pred-col <pred> --label-col llm_<p>_risk
python vibebench/labels/score_labels.py --file java_<p>_census.csv \
 --pred-col <pred> --label-col llm_<p>_risk
```

pred columns: srp=`srp_risk` · ocp=`smell` · lsp=`violation` · isp=`flagged` ·
dip=`dip_risk`

The guard must print **PASS** before any number is quoted.

## Consistency self-check (do this, it is what was missed in Python)

After finishing, split your judgements in half **by position** and compare the
positive rate and precision of the two halves. Populations are randomised, so
the halves should agree. If they differ by more than ~1.5x, you drifted , go
back and re-judge the earlier half under the final standard.
