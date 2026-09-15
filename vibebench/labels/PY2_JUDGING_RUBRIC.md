# Python V2 judging rubric, frozen BEFORE labelling starts (2026-09-14)

Adapted from `JAVA_JUDGING_RUBRIC.md` so that the two languages' precision is
produced under the same standard. Only language-specific facts change. **Read
this first, and do not let the standard evolve mid-run.** If a rule needs to
change at row 400, change it here and re-judge everything before it.

## Rules for every principle (identical to Java)

1. **Judge the entity, not the metrics.** Size numbers in a header are facts,
   not verdicts. Read the code.
2. **Batches are blind.** No verdict, hit list, signal list or outcome is
   printed. Never open the sample CSV while judging: it carries the prediction.
   If you would need the verdict to decide, the answer is NEGATIVE.
3. **Test code is NEGATIVE** (test doubles, mocks, fakes, fixtures, `conftest`),
   even if one slipped past the production filter.
4. **Generated code is NEGATIVE** (protobuf `_pb2`, migrations, vendored copies).
5. **Insufficient evidence means NEGATIVE.** Never guess a positive.
6. **Precedent binds.** Once a pattern is judged, every later instance of it is
   judged the same way. Decisions are logged in `py2_precedents.md`.
7. **Never judge with the PR's outcome in view.** Batches carry no outcome, and
   the samples are shuffled so merged and rejected rows are interleaved.
8. **Every label carries a written reason** of at least 12 characters, and is
   recorded only through `record_labels.py` after `use_lang('py2')`.

***

## SRP: does this class have more than one reason to change?

**POSITIVE** when the class contains **two or more method clusters serving
different DOMAINS**, each of which could be extracted into a coherent standalone
class. Examples: a file-system API that also owns agent registration; a parser
that also scores code quality; a client exposing tracking, model registry and
tracing as one surface; a CLI command class that also implements the business
logic, persistence and rendering it drives.

**NEGATIVE** for large but cohesive:
- one coherent API surface (a client wrapping one service; a DB cursor; a cloud
  adapter), however many methods
- a parser or serializer for ONE format
- data classes, pydantic models, dataclasses, TypedDicts, enums, exceptions
- framework-imposed shape (Django model or admin, Flask/FastAPI view class,
  argparse/click command groups whose methods are the commands of one tool,
  `unittest`-style suites), a builder or fluent DSL
- a facade that DELEGATES to collaborators rather than implementing them
- infrastructure serving the class's own function (its own cache, retries,
  logging, metrics)
- **size alone. Never positive on size alone.**

## OCP: is this dispatch open to extension, or closed by construction?

**POSITIVE** when the branch set is a **genuinely open family the project's own
domain would realistically extend**, so adding a variant forces editing this
chain: an `isinstance` ladder over a hierarchy the project owns and keeps
growing; an `if kind == "..."` / `match` over a tag string naming providers,
backends, formats, message types or model families that grow with features.

**NEGATIVE** when closed or exhaustive:
- a dispatch over an `Enum`, or over literal values fixed by a specification
  (HTTP methods, JSON value types, AST node kinds of a fixed grammar)
- `isinstance` over built-ins or input normalisation (`str` / `bytes` / `Path`,
  `list` / `tuple` / scalar, `None` handling)
- platform, OS, architecture or Python-version switches
- boolean-like or two-way splits, error-handling branches, argument validation
- a value switch whose arms are unrelated conditions rather than variants of one
  family (a guard sequence, not a dispatch)

The question is not "is this an if-ladder" but **"will this chain need a new arm
when the domain grows?"**

## LSP: does the override break a caller relying on the base contract?

Python has no compiler, so the faults `javac` rejects are LIVE here. Five kinds
count, judged on whether **a caller written correctly against the BASE would
break** when handed the subclass:

**POSITIVE:**
- **refused bequest**: the override raises (`NotImplementedError`,
  `NotSupportedError`, a bare `raise`) or silently no-ops **in place of real,
  working base behaviour**
- **a required parameter added**: the override needs an argument the base does
  not take and gives it no default, so a call matching the base signature fails
- **a parameter removed**: the override drops a parameter the base accepts, with
  no `*args` / `**kwargs` to absorb it
- **a parameter type narrowed**: the base accepts a wider annotated type than
  the override (e.g. base `str | None`, override `str`), where the narrowing is
  real rather than a stylistic annotation change
- **a return type widened**: the override may return what the base promises not
  to (e.g. base `-> str`, override `-> str | None`)

**NEGATIVE:**
- implementing an abstract method or a `Protocol` member **with a compatible
  signature**: there is no bequest to refuse (an INCOMPATIBLE signature against
  an abstract base is still POSITIVE, because callers use the base signature)
- a base whose own body is `raise NotImplementedError` / `pass` / `...`, when the
  override also refuses: nothing working was taken away
- template-method hooks the base documents as override points
- calling `super().method(...)` and then extending
- an added parameter WITH a default, an added `*args` / `**kwargs`, a renamed
  positional parameter with no evidence of keyword callers
- a narrowed return type (covariant, safe), a widened parameter type
  (contravariant, safe)
- a documented, deliberate no-op with a stated reason (e.g. a null backend)

## ISP: are implementers forced to take members they cannot use?

**POSITIVE** when a wide contract forces implementers into **stub methods**
(`pass`, `...`, `raise NotImplementedError`, `return None` placeholders) for
members irrelevant to them, and the contract could be split along the lines its
implementers already reveal. Rule of thumb carried from Java: at least 2 such
stubs in at least one implementer, amounting to a meaningful part of the
contract.

**NEGATIVE:**
- a wide contract whose implementers genuinely implement all of it (wide but
  cohesive), or a single implementer that implements it fully
- a contract with **no internal implementer shown**: no forcing is observable
- a marker, mixin or data-shape class
- framework or plugin contracts whose shape is imposed externally
- concrete default behaviour in the base: an implementer not overriding it is
  NOT a forced stub
- a template-method base where the stub-bodied members are optional hooks

## DIP: did this PR introduce a dependency cycle?

The same mechanical rule as the Java census, so the two are comparable.

**POSITIVE** when the batch lists **at least one newly cyclic pair involving a
module this PR changed** ("changed" means the file text differs between the
before and after trees), and the pair is a genuine import dependency in the
shown code. The canonical Python shape: a new leaf module imports the package
root (`from pkg import X`), which re-exports it.

**NEGATIVE:**
- "NO newly cyclic pair involves a module this PR changed"
- the only new pairs are in modules the PR did not change
- the cycle existed before the PR, or the PR removed a module from a cycle
- no new import edge at all
- import-edge direction or `CONCRETE` markers ALONE, without a new cycle: Python
  modules rarely define abstract classes, so almost every edge reads CONCRETE,
  and that is not a violation by itself

***

## Scoring

Precision on the flagged stratum. Recall is ALWAYS stratum-weighted with the
population sizes in `py2_<p>_labeling_sample.population.json`.

## Consistency self-check (mandatory)

After each principle, split the judgements in half **by position** and compare
positive rates on each stratum. The populations are shuffled, so the halves
should agree. If they differ by more than ~1.5x, the standard drifted: re-judge
the earlier half under the final standard.
