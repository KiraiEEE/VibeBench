# Python V2 judging precedents

Rule 6 of PY2_JUDGING_RUBRIC.md: once a pattern is judged, every later instance
is judged the same way. One line per decision, written when the decision is made.

## LSP
- L1 (row 3, 7) Concrete base method typed with a base config/param class; override narrows the annotation to its own subclass AND reads subclass-only attributes: POSITIVE (real covariant parameter narrowing).
- L2 (row 4) Override adds a REQUIRED parameter a base-signature call does not supply, even when the base is abstract with *args/**kwargs: POSITIVE.
- L3 (row 5) Base forwards its *args/**kwargs onward; override removes the catch-all: POSITIVE.
- L4 (row 6) Implements an abstract method, widening parameter types and/or adding **kwargs: NEGATIVE.
- L5 (row 8) Base's own fallback raises NotImplementedError for layouts it cannot handle; override raises explicitly with a stated reason: NEGATIVE.
- L6 (row 11) Base docstring explicitly permits overrides to accept fewer parameters and the caller adapts: NEGATIVE.
- L7 (row 12) Base returns a success value (e.g. True) callers rely on; override raises NotImplementedError instead: POSITIVE (refused bequest).
- L8 (rows 21, 25, 26) Base raises NotImplementedError as "not supported"; override implements real behaviour: NEGATIVE (adds capability).
- L9 (row 30) Implements an abstract method compatibly but with weak/partial behaviour: NEGATIVE (postcondition weakening is not a Python signal; no bequest).
- L10 (row 34) Override replaces the base's *args with OPTIONAL named positionals, KEEPS **kwargs, and forwards to super(): NEGATIVE (the specialisation the catch-all exists for). Contrast L3: keyword catch-all removed entirely.
- L11 (row 35) Base contract promises a working capability (not raising); subclass raises NotImplementedError with a reason: POSITIVE refused bequest. Contrast L5, where raising is part of the base's own documented contract.
- L12 (row 45) Override drops OPTIONAL parameters the base accepts, with no **kwargs to absorb them: POSITIVE (parameter removed).
- L13 (row 49) UI/event action adds a state guard (early return) and otherwise delegates to super(): NEGATIVE.
- L14 (row 54) transformers `modular_*.py` override whose signature is `**super_kwargs` delegating to super(): NEGATIVE. The converter expands it to the parent signature and modular files are not imported at runtime. (A modular override that WRITES an explicit incompatible signature is still judged on that signature.)
- L15 (row 58) Protocol method accepts a protocol type; concrete override narrows the parameter to its own concrete class whose internals it relies on: POSITIVE.
- L16 (row 62) Validation method that raises on invalid input keeps its signature: NEGATIVE (raising is its contract).
- L17 (row 65) Base *args is a subclass-specific POSITIVE-input slot forwarded to an internal hook; override with no extra positional inputs drops *args, KEEPS **kwargs and forwards to super(): NEGATIVE. (Removing the general keyword options is still POSITIVE, L3/L12.)
- L18 (row 70) Abstract base signature is ONLY (*args, **kwargs) and documented as format/subclass-specific: any concrete override signature is NEGATIVE (no fixed contract). Contrast L2, where the base fixes at least one parameter.
- L19 (row 71) Base is a raising placeholder whose declared signature omits the arguments the framework actually delivers as keywords (graphene perform_mutation); override adds them keyword-only: NEGATIVE.
- L20 (row 79) Base signature lists *args/**kwargs but the base body neither uses nor forwards them and no caller passing extras is shown; override drops them: NEGATIVE (rule 5, no evidence extras are passed). L3 needs forwarding evidence.
- L21 (row 96) Base *args/**kwargs is DOCUMENTED as specific parameters; override accepts all of them (plus optionals) and forwards to super(): NEGATIVE (documented contract intact). Refines L3.
- L22 (row 104) Base always returns a value callers use (e.g. a tuple they unpack); override returns None: POSITIVE (return contract broken).
- L23 (row 105) Protocol return annotation contradicted by its own docstring; override matches the documented behaviour: NEGATIVE (stylistic annotation, not a real widening).
- L24 (row 122) Override changes async to sync (or sync to async) against a base contract callers await: POSITIVE.
- L25 (row 135) Override drops options that **kwargs still absorbs as keywords and adds only DEFAULTED params: NEGATIVE (no required param added). Contrast L2.
- L26 (row 158) Override changes a parameter to an INCOMPATIBLE type (not a subtype, e.g. str to list[Fragment]) against an abstract base: POSITIVE.
- L27 (row 169) Base method is an empty 'write logic here' template in a type-registry pattern (each subclass only receives its registered types); override narrows the data annotation to those types: NEGATIVE. Contrast L1, where the base has WORKING behaviour for the wider type.
- L28 (row 191) Override makes a defaulted parameter required ONLY where a call omitting it could never have worked for this subclass (the base forwards the default into a computation that needs a value): NEGATIVE. L2 applies when the omitted call works on the base.

## OCP
- O1 (rows 18, 76, 96, 122, 147, 152) Dispatch over an Enum: NEGATIVE, even when the enum names a family that plausibly grows (search types, decoder blocks). The rubric's Enum rule is applied literally so the standard does not depend on guessing an enum's future.
- O2 (rows 8, 20, 21, 29, 32, 35, 45, 88, 105, 128, 134, 154, 166, 198) Values fixed by an external specification, API, wire protocol, file format or grammar (HTTP methods, SSE event types of a vendor API, MCP methods and transports, tree-sitter node kinds, SAM CIGAR codes): NEGATIVE. The project cannot add a variant; the specification does.
- O3 (rows 0, 6, 22, 26, 33, 37, 43, 48, 115, 140, 183) isinstance or None checks over the accepted SHAPES of one argument (str/bytes/Path, list/scalar, dict/model, numpy/pandas): NEGATIVE, input normalisation.
- O4 (rows 14, 28, 60, 65, 82, 86, 95, 104, 109, 110, 130, 137) isinstance or match ladder over a class hierarchy the project OWNS (content classes, event classes, geometry shapes, session types, task classes): POSITIVE. A new subclass forces a new arm instead of a method on the class.
- O5 (rows 31, 34, 41, 50, 155, 177, 193) String factory over a named family (model architectures, reward functions, loss functions, codecs, regressors): POSITIVE even with two arms. The rubric's two-way rule is read as boolean-like or exhaustive partitions (rows 49, 116, 145, 164, 188: NEGATIVE).
- O6 (rows 62, 69, 168) Framework request or event handler over one page's or screen's own buttons and form actions: NEGATIVE, the set is the screen's controls. Contrast (rows 17, 73, 123, 163, 178): a hand-written command or tool-action dispatcher for a bot, REPL or MCP tool is POSITIVE, new commands are routine.
- O7 (rows 102, 103, 138, 153, 162) A few special cases layered over a generic default that already handles any new value: NEGATIVE. Contrast row 179, a recogniser whose default means "not recognised": POSITIVE.
- O8 (row 61) A renderer that deliberately shows only some kinds and skips the rest by stated intent: NEGATIVE. Row 120, same hierarchy silently dropped without stated intent: POSITIVE.
- O9 (row 129) The switch subject is a classification computed a few lines above in the same function: NEGATIVE.
- O10 (rows 49, 83, 92, 180, 187, 197) A switch scoped to one model file, one dataset or one dedicated script, whose values that artefact fixes: NEGATIVE.
- O11 (rows 15, 74, 142, 157) A rule table ported from a fixed external ruleset or physical model (a game generation, llama.cpp quant rules, a meteor fragmentation model): NEGATIVE.

## SRP
- S1 (rows 5, 45, 92, 103) Several operations on ONE store or ONE algorithm (CRUD plus trackers plus persistence of schedules; dedup, contradiction, aging and merging of one pattern bank; stages of one phylogenetic step): NEGATIVE. A docstring listing "Responsibilities" that are sub-operations of one domain does not make it positive.
- S2 (rows 14, 35, 72, 104, 110, 141, 156, 163) A client or driver for ONE external service or device, however many commands (robot arm, mail account over IMAP and SMTP, stream protocol, OS calendar, capture session): NEGATIVE.
- S3 (rows 4, 7, 34, 44, 54, 105, 127, 139, 182) Framework-imposed shape (transformers model heads, pip commands, evaluator interface, PDF device callbacks, Home Assistant entities and flows, Open WebUI pipes, Lightning callbacks and strategies): NEGATIVE.
- S4 (rows 50, 113, 118, 168) Very large configuration or value-type API classes with no second domain visible in the capped batch: NEGATIVE, never on size alone.
- S5 (rows 60, 121, 178, 185) A second cluster that belongs to a different technical domain than the class's purpose (host hardware fingerprinting inside a miner, virtualenv selection inside file operations, a 3D visualisation server inside a retargeting solver, exchange market-data fetching inside a signal generator): POSITIVE.
- S6 (rows 28, 99, 161, 193) God classes whose visible methods span persistence, orchestration and unrelated subsystems (Instaloader, mlonmcu Run, a storage backend for nine subsystems, nearai Environment with blockchain, vector store and inference): POSITIVE.
- S7 (rows 29, 191) Proxying plus billing/usage settlement plus rate-limit reporting; an interactive console plus the C2 operations it drives: POSITIVE (the rubric's "CLI command class that also implements the business logic").

## ISP
- I1 (rows 2, 9, 53, 56, 81, 106, 117, 130, 145, 157, 161, 166, 176) Stubs that exist only in test doubles, mocks and fixtures (including classes defined inside tests): NEGATIVE. A test double implementing a contract minimally is not a production implementer forced into irrelevant members.
- I2 (rows 29, 51, 165) An intentional null object (NoOp, Silent, Mock-when-disabled) that no-ops the whole contract by design: NEGATIVE.
- I3 (rows 21, 45, 94, 134, 169) Members raising "not yet implemented", "pending wiring" or scaffold templates meant to be filled in: NEGATIVE. The member is relevant to the implementer; the work is unfinished.
- I4 (rows 4, 8, 22, 25, 30, 33, 42, 43, 44, 58, 62, 67, 83, 90, 107, 118, 121, 140, 149, 158, 167, 173, 179) At least two members an implementer declares it CANNOT support ("does not support", "read-only", "not relevant", "should not be called", device-managed), in production code: POSITIVE. Device and provider capability contracts dominate.
- I5 (rows 36, 50, 101, 172) Fat listener or callback contracts whose production implementers leave irrelevant events as docstring-only or pass bodies (web UI ignoring console events, a messaging sink ignoring three of four events, sample handlers passing 11 to 13 of 22 events, a simulator ignoring multi-process coordination): POSITIVE.
- I6 (rows 32, 139, 143) An abstract class used as the self-type of mixins, where each mixin uses members the composed class supplies: NEGATIVE.
- I7 (rows 11, 54, 57, 73, 77, 91, 113, 131, 147) A single stub per implementer (one unsupported member, an empty downgrade, a no-op hook): NEGATIVE under the two-stub rule.
- I8 (rows 19, 26, 74, 150) Template-method or implicit bases whose members have no-op defaults, so implementers override only what they need: NEGATIVE.
- I9 (rows 102 vs 179) The same legacy contract judged on what each batch shows: NEGATIVE where only single stubs are visible among the shown implementers, POSITIVE where a shown implementer stubs seven.

## DIP
- D1 (all 100 charged rows) The Java census rule applied unchanged: TRUE when the batch lists at least one newly cyclic pair involving a module whose text differs between the trees. Every charged row checked had a visible import closing the cycle; rows 103, 104 and 126, where the 60-edge listing was truncated, were confirmed from the changed modules' own import lines (credentials <-> storage, app <-> event_handler, script <-> orchestration).
- D2 (the canonical Python shape, rows 4, 5, 7, 12, 17, 36, 102, 130) A new or changed leaf module imports its package root (`from pkg import X`) while the root re-exports the leaf: TRUE, as the rubric states. These rows produce thousands of charged pairs because one edge merges a leaf into the root's strongly connected component.
- D3 (rows 1, 16, 43, 68, 89, 90, 94 and others) Large pre-existing cyclic structure but no new edge through a changed module: FALSE. Pre-existing pairs are never counted.
