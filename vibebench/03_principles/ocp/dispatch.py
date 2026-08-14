"""
OCP (Open/Closed Principle) analysis over a Python module tree.

Pure functions over {repo-relative path: source} dicts -- no dataset or git
dependency, unit-testable standalone (run this file directly for a self-check).

What OCP says: software entities should be open for extension but closed for
modification -- you should be able to add new behavior WITHOUT editing existing
code. Robert Martin's own canonical violation is the "switch statement": a
type-discriminating `if/elif` (or `match`) ladder that must be *edited* every
time a new variant appears. That is the primary static proxy here.

Signals (all deterministic, stdlib-ast only):

  1. Dispatch-chain inventory -- a run of `if/elif` tests (or a `match`) that
     discriminates on the runtime type or a type-tag of one common subject.
     Four kinds, tagged `kind`:
       isinstance    -- isinstance(x, T) chain (incl. `or` of isinstances)
       type_eq       -- type(x) is/== T chain
       match_type    -- match x: case ClassName(): ...  (class patterns)
       value_switch  -- x == "a" / x is Enum.A / x in {...} chain, same subject
       match_value   -- match x: case "a": / case 1: ...  (value patterns)
     A chain needs >= 2 branches on ONE subject to be a chain at all; it is a
     `dispatch_smell` at >= dispatch_min_branches (type-based) or
     >= value_switch_min_branches (value-based -- noisier, higher bar).

  2. Add-vs-modify def profile (secondary, reported RAW, never flagged in v1)
     -- across the AI-touched modules, how many function/method definitions the
     PR ADDED (extension) vs MODIFIED in place (edited an existing body) vs
     removed, plus classes added. High modify:add is OCP-suspect, but bug fixes
     legitimately modify existing code and static analysis cannot tell intent
     apart -- so this is descriptive context, not a flag. Same discipline as
     ISP's raw client_usage and its inventory-only `implicit` tier.

Delta reasons the harness derives from head-vs-parent (see run_ocp.py):
  new_dispatch_smell -- an AI-touched module gained a flagged chain absent (by
                        module+qualname+subject key) from the parent snapshot.
  dispatch_widened   -- a chain present in both grew its branch count: the
                        textbook "had to edit the conditional to add a case".

Known limitations (documented, accepted for v1):
  - A `value_switch` cannot statically distinguish genuine open-coded type
    dispatch from ordinary value comparison; hence the higher branch bar and
    the fact that value chains are inventoried but weighted conservatively.
  - The subject is matched by unparsed text, so renaming the discriminant
    variable between parent and head reads as a new chain (rare; conservative).
  - "Modified" is AST-structural (ast.dump ignores positions/comments); a pure
    reformat is not counted, but a local-variable rename is -- intentionally,
    it IS a modification of behavior-bearing code.
"""
from __future__ import annotations

import ast
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dip"))
from graph import path_to_module  # noqa: E402

_TYPE_KINDS = {"isinstance", "type_eq", "match_type"}


# --------------------------------------------------------------------------
# Dataclasses
# --------------------------------------------------------------------------
@dataclass
class DispatchChain:
    module: str
    qualname: str          # enclosing def/class dotted path, "" at module level
    subject: str           # unparsed discriminant expression
    kind: str              # isinstance|type_eq|match_type|value_switch|match_value
    n_branches: int
    lineno: int
    type_names: tuple[str, ...] = ()  # types tested (type-kinds only); closed-set signal

    @property
    def key(self) -> tuple[str, str, str]:
        """Location identity for before/after matching (position-independent)."""
        return (self.module, self.qualname, self.subject)


@dataclass
class DefProfile:
    n_added: int           # defs (func/method) in after-touched, absent in before
    n_removed: int
    n_modified: int        # same qualname, different AST body
    n_classes_added: int   # new classes in touched modules (extension proxy)


# --------------------------------------------------------------------------
# Test classification
# --------------------------------------------------------------------------
def _is_name(node: ast.expr, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _unparse(node: ast.expr) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001 -- unparse is best-effort for a key
        return ast.dump(node)


def _type_arg_names(node: ast.expr) -> list[str]:
    """Unparsed type name(s) in an isinstance 2nd arg / type() comparand.
    A tuple `(A, B)` yields both; a single name yields one."""
    if isinstance(node, ast.Tuple):
        return [_unparse(e) for e in node.elts]
    return [_unparse(node)]


def _classify_test(test: ast.expr) -> tuple[str, str, tuple[str, ...]] | None:
    """(kind, subject, type_names) for one branch test, or None if it does not
    discriminate on a single subject's type/tag. kind in {isinstance, type_eq,
    value}. type_names is the set of types tested (empty for value switches) --
    feeds the deterministic closed-set suppressor (config.OCP_CLOSED_SET_SUPPRESS)."""
    # `isinstance(x, A) or isinstance(x, B)` -> one subject if all agree.
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        parts = [_classify_test(v) for v in test.values]
        parts = [p for p in parts if p]
        if parts and all(p[1] == parts[0][1] for p in parts):
            kinds = {p[0] for p in parts}
            k = ("isinstance" if "isinstance" in kinds
                 else "type_eq" if "type_eq" in kinds else "value")
            types = tuple(t for p in parts for t in p[2])
            return (k, parts[0][1], types)
        return None

    if isinstance(test, ast.Call) and _is_name(test.func, "isinstance") and test.args:
        types = tuple(_type_arg_names(test.args[1])) if len(test.args) > 1 else ()
        return ("isinstance", _unparse(test.args[0]), types)

    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        op = test.ops[0]
        left, right = test.left, test.comparators[0]
        if isinstance(op, (ast.Is, ast.IsNot, ast.Eq, ast.NotEq)):
            # type(x) is/== T   (either operand order)
            for a, b in ((left, right), (right, left)):
                if isinstance(a, ast.Call) and _is_name(a.func, "type") and a.args:
                    return ("type_eq", _unparse(a.args[0]), tuple(_type_arg_names(b)))
            # SUBJ == const / SUBJ is Enum.MEMBER  (value switch)
            for a, b in ((left, right), (right, left)):
                if (isinstance(a, (ast.Name, ast.Attribute))
                        and isinstance(b, (ast.Constant, ast.Attribute))):
                    return ("value", _unparse(a), ())
            return None
        if isinstance(op, (ast.In, ast.NotIn)):
            if (isinstance(left, (ast.Name, ast.Attribute))
                    and isinstance(right, (ast.Set, ast.Tuple, ast.List))):
                return ("value", _unparse(left), ())
            return None
    return None


def _chain_from_ifs(tests: list[ast.expr], node: ast.If) -> DispatchChain | None:
    classes = [_classify_test(t) for t in tests]
    counts = Counter(c[1] for c in classes if c)
    if not counts:
        return None
    subject, cnt = counts.most_common(1)[0]
    if cnt < 2:
        return None
    kinds = {c[0] for c in classes if c and c[1] == subject}
    kind = ("isinstance" if "isinstance" in kinds
            else "type_eq" if "type_eq" in kinds else "value_switch")
    type_names = tuple(t for c in classes if c and c[1] == subject for t in c[2])
    return DispatchChain(module="", qualname="", subject=subject,
                         kind=kind, n_branches=cnt, lineno=node.lineno,
                         type_names=type_names)


def _chain_from_match(node: ast.Match) -> DispatchChain | None:
    n = 0
    has_class = False
    type_names: list[str] = []
    for case in node.cases:
        pat = case.pattern
        if (isinstance(pat, ast.MatchAs) and pat.pattern is None
                and pat.name is None):
            continue  # `case _:` wildcard -- not a discriminating branch
        n += 1
        if isinstance(pat, ast.MatchClass):
            has_class = True
            type_names.append(_unparse(pat.cls))
        elif isinstance(pat, ast.MatchOr):
            for p in pat.patterns:
                if isinstance(p, ast.MatchClass):
                    has_class = True
                    type_names.append(_unparse(p.cls))
    if n < 2:
        return None
    kind = "match_type" if has_class else "match_value"
    return DispatchChain(module="", qualname="", subject=_unparse(node.subject),
                         kind=kind, n_branches=n, lineno=node.lineno,
                         type_names=tuple(type_names))


# --------------------------------------------------------------------------
# Tree scan
# --------------------------------------------------------------------------
def _unroll_if(node: ast.If) -> tuple[list[ast.expr], list[list[ast.stmt]]]:
    """Flatten an if/elif/else ladder into (tests, branch_bodies incl. else)."""
    tests: list[ast.expr] = []
    bodies: list[list[ast.stmt]] = []
    cur = node
    while True:
        tests.append(cur.test)
        bodies.append(cur.body)
        if len(cur.orelse) == 1 and isinstance(cur.orelse[0], ast.If):
            cur = cur.orelse[0]
        else:
            bodies.append(cur.orelse)
            break
    return tests, bodies


def _scan(stmts: list[ast.stmt], module: str, qual: str,
          out: list[DispatchChain]) -> None:
    for stmt in stmts:
        if isinstance(stmt, ast.If):
            tests, bodies = _unroll_if(stmt)
            chain = _chain_from_ifs(tests, stmt)
            if chain is not None:
                chain.module, chain.qualname = module, qual
                out.append(chain)
            for body in bodies:
                _scan(body, module, qual, out)
        elif isinstance(stmt, ast.Match):
            chain = _chain_from_match(stmt)
            if chain is not None:
                chain.module, chain.qualname = module, qual
                out.append(chain)
            for case in stmt.cases:
                _scan(case.body, module, qual, out)
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            newqual = f"{qual}.{stmt.name}" if qual else stmt.name
            _scan(stmt.body, module, newqual, out)
        else:
            for field in ("body", "orelse", "finalbody"):
                body = getattr(stmt, field, None)
                if isinstance(body, list):
                    _scan(body, module, qual, out)
            for handler in getattr(stmt, "handlers", []):
                _scan(handler.body, module, qual, out)


def find_dispatch_chains(files: dict[str, str]) -> list[DispatchChain]:
    """Every type/tag dispatch chain across a {path: source} tree."""
    out: list[DispatchChain] = []
    for path, src in files.items():
        m = path_to_module(path)
        if m is None:
            continue
        try:
            tree = ast.parse(src)
        except (SyntaxError, ValueError):
            continue
        _scan(tree.body, m, "", out)
    return out


def _safe_unparse(stmt: ast.stmt) -> str:
    try:
        return ast.unparse(stmt)
    except Exception:  # noqa: BLE001
        return ""


def branch_bodies_at(source: str, lineno: int) -> list[str]:
    """Source text of each branch body of the if/elif (or match) at `lineno`
    (a dispatch chain's location). Feeds the OPTIONAL OCP branch-cohesion
    suppressor (config.OCP_BRANCH_COHESION_SUPPRESS): if the branch bodies are
    semantically homogeneous (embedding cohesion high) the chain handles a CLOSED
    set (JSON/AST kinds) -- the dominant OCP false positive -- not an open-ended
    type switch. Pure/deterministic; the embedding step lives in run_ocp."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    target: ast.stmt | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.Match)) and node.lineno == lineno:
            target = node
            break
    if target is None:
        return []
    if isinstance(target, ast.If):
        _, bodies = _unroll_if(target)
    else:
        bodies = [case.body for case in target.cases]
    out: list[str] = []
    for body in bodies:
        text = "\n".join(t for t in (_safe_unparse(s) for s in body) if t)
        if text.strip():
            out.append(text)
    return out


# --------------------------------------------------------------------------
# Add-vs-modify def profile (secondary, raw)
# --------------------------------------------------------------------------
def def_index(files: dict[str, str]) -> dict[str, tuple[str, str | None]]:
    """{f"{module}:{qualname}": (kind, body_hash)} for every def and class.
    kind in {"func", "class"}; body_hash is ast.dump (positions ignored) for
    funcs, None for classes (added/removed only)."""
    out: dict[str, tuple[str, str | None]] = {}
    for path, src in files.items():
        m = path_to_module(path)
        if m is None:
            continue
        try:
            tree = ast.parse(src)
        except (SyntaxError, ValueError):
            continue

        def rec(stmts: list[ast.stmt], qual: str) -> None:
            for s in stmts:
                if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    q = f"{qual}.{s.name}" if qual else s.name
                    out[f"{m}:{q}"] = ("func", ast.dump(s))
                    rec(s.body, q)
                elif isinstance(s, ast.ClassDef):
                    q = f"{qual}.{s.name}" if qual else s.name
                    out[f"{m}:{q}"] = ("class", None)
                    rec(s.body, q)
                else:
                    for field in ("body", "orelse", "finalbody"):
                        body = getattr(s, field, None)
                        if isinstance(body, list):
                            rec(body, qual)
                    for handler in getattr(s, "handlers", []):
                        rec(handler.body, qual)

        rec(tree.body, "")
    return out


def def_delta(before: dict[str, tuple[str, str | None]],
              after: dict[str, tuple[str, str | None]],
              touched_modules: set[str]) -> DefProfile:
    """Add/modify/remove profile restricted to AI-touched modules."""
    def touched(key: str) -> bool:
        return key.split(":", 1)[0] in touched_modules

    a_funcs = {k: v for k, v in after.items() if v[0] == "func" and touched(k)}
    b_funcs = {k: v for k, v in before.items() if v[0] == "func"}
    added = sum(1 for k in a_funcs if k not in b_funcs)
    removed = sum(1 for k, v in before.items()
                  if v[0] == "func" and touched(k) and k not in after)
    modified = sum(1 for k, v in a_funcs.items()
                   if k in b_funcs and b_funcs[k][1] != v[1])
    classes_added = sum(1 for k, v in after.items()
                        if v[0] == "class" and touched(k) and k not in before)
    return DefProfile(added, removed, modified, classes_added)


# --------------------------------------------------------------------------
# Rubric (thresholds passed in from config.py -- this module stays pure)
# --------------------------------------------------------------------------
# Types over which a dispatch is CLOSED, not open-ended: primitives/containers
# (JSON-shaped `str`/`dict`/`list` recursion, input normalization) and fixed
# stdlib grammars (the `ast` node hierarchy). Dispatching over these is exhaustive
# by construction -- adding a "new type" is not the extension OCP guards against --
# so a wide chain over ONLY these is the dominant OCP false positive, not a smell.
_CLOSED_BUILTINS = frozenset({
    "str", "bytes", "bytearray", "memoryview", "int", "float", "complex",
    "bool", "list", "tuple", "dict", "set", "frozenset", "range",
    "NoneType", "None",
})
_CLOSED_PREFIXES = frozenset({"ast"})  # fixed stdlib node grammars


def is_closed_type_set(type_names: tuple[str, ...]) -> bool:
    """True if every tested type is a builtin primitive/container, or every type
    shares one fixed-grammar module prefix (e.g. `ast.*`). Deterministic; no ML.
    Requires >=2 named types (a genuine multi-type chain)."""
    names = [n for n in type_names if n]
    if len(names) < 2:
        return False
    lasts = [n.split(".")[-1] for n in names]
    if all(l in _CLOSED_BUILTINS for l in lasts):
        return True
    if all("." in n for n in names):
        prefixes = {n.rsplit(".", 1)[0] for n in names}
        if len(prefixes) == 1 and next(iter(prefixes)) in _CLOSED_PREFIXES:
            return True
    return False


def is_dispatch_smell(chain: DispatchChain, thresholds: dict,
                      suppress_closed: bool = False) -> bool:
    """A dispatch chain wide enough to be an OCP 'switch statement' smell.
    With suppress_closed (config.OCP_CLOSED_SET_SUPPRESS), a type-dispatch over a
    CLOSED type set (all builtins / one fixed stdlib grammar) is NOT flagged --
    the deterministic fix for the isinstance-over-closed-set false positive."""
    if chain.kind in _TYPE_KINDS:
        if suppress_closed and is_closed_type_set(chain.type_names):
            return False
        return chain.n_branches >= thresholds["dispatch_min_branches"]
    return chain.n_branches >= thresholds["value_switch_min_branches"]


# --------------------------------------------------------------------------
# Self-check (run `python dispatch.py`)
# --------------------------------------------------------------------------
if __name__ == "__main__":
    T = {"dispatch_min_branches": 3, "value_switch_min_branches": 4}

    before_files = {
        "app/__init__.py": "",
        # isinstance dispatch with 2 branches (a chain, not yet a smell)
        "app/render.py": (
            "def render(shape):\n"
            "    if isinstance(shape, Circle):\n        return 1\n"
            "    elif isinstance(shape, Square):\n        return 2\n"
            "    return 0\n"
        ),
        # value switch, 3 branches (below value bar of 4)
        "app/route.py": (
            "def route(req):\n"
            "    if req.method == 'GET':\n        return 1\n"
            "    elif req.method == 'POST':\n        return 2\n"
            "    elif req.method == 'PUT':\n        return 3\n"
        ),
    }
    after_files = {
        "app/__init__.py": "",
        # isinstance chain WIDENED to 3 branches -> now a smell + dispatch_widened
        "app/render.py": (
            "def render(shape):\n"
            "    if isinstance(shape, Circle):\n        return 1\n"
            "    elif isinstance(shape, Square):\n        return 2\n"
            "    elif isinstance(shape, Triangle):\n        return 3\n"
            "    return 0\n"
        ),
        # value switch widened to 4 -> smell; also this function body modified
        "app/route.py": (
            "def route(req):\n"
            "    if req.method == 'GET':\n        return 1\n"
            "    elif req.method == 'POST':\n        return 2\n"
            "    elif req.method == 'PUT':\n        return 3\n"
            "    elif req.method == 'DELETE':\n        return 4\n"
        ),
        # brand-new type_eq smell in a new module (extension by new class too)
        "app/kind.py": (
            "class Handler:\n    pass\n"
            "def dispatch(x):\n"
            "    if type(x) is A:\n        return 1\n"
            "    elif type(x) is B:\n        return 2\n"
            "    elif type(x) is C:\n        return 3\n"
        ),
        # match on class patterns (type dispatch, 3 cases)
        "app/visit.py": (
            "def visit(node):\n"
            "    match node:\n"
            "        case Add():\n            return 1\n"
            "        case Sub():\n            return 2\n"
            "        case Mul():\n            return 3\n"
            "        case _:\n            return 0\n"
        ),
    }

    before = find_dispatch_chains(before_files)
    after = find_dispatch_chains(after_files)
    by_key_after = {c.key: c for c in after}
    by_key_before = {c.key: c for c in before}

    # kinds detected
    kinds = {(c.module, c.qualname): c for c in after}
    render = kinds[("app.render", "render")]
    assert render.kind == "isinstance" and render.n_branches == 3, render
    assert is_dispatch_smell(render, T)
    route = kinds[("app.route", "route")]
    assert route.kind == "value_switch" and route.n_branches == 4, route
    assert is_dispatch_smell(route, T)
    kind_disp = kinds[("app.kind", "dispatch")]
    assert kind_disp.kind == "type_eq" and kind_disp.n_branches == 3, kind_disp
    visit = kinds[("app.visit", "visit")]
    assert visit.kind == "match_type" and visit.n_branches == 3, visit
    assert is_dispatch_smell(visit, T)

    # before-state: 2-branch isinstance is a chain but NOT a smell
    assert render.key in by_key_before
    assert not is_dispatch_smell(by_key_before[render.key], T)
    # 3-branch value switch below the value bar of 4 -> not a smell
    assert not is_dispatch_smell(by_key_before[route.key], T)

    # widened detection (same key, grew branches)
    widened = [k for k in by_key_after
               if k in by_key_before
               and by_key_after[k].n_branches > by_key_before[k].n_branches]
    assert set(widened) == {render.key, route.key}, widened
    # new smell absent from before
    new_smells = [c for c in after if is_dispatch_smell(c, T)
                  and c.key not in by_key_before]
    assert {c.key for c in new_smells} == {kind_disp.key, visit.key}, new_smells

    # add/modify profile over touched modules
    touched = {"app.render", "app.route", "app.kind", "app.visit"}
    prof = def_delta(def_index(before_files), def_index(after_files), touched)
    assert prof.n_added == 2, prof        # app.kind:dispatch + app.visit:visit
    assert prof.n_modified == 2, prof     # render + route bodies changed
    assert prof.n_classes_added == 1, prof  # app.kind:Handler

    # closed-set suppressor: JSON-shaped isinstance over builtins is suppressed,
    # but the Circle/Square/Triangle (user-type) chain is NOT.
    closed = find_dispatch_chains({"j.py": (
        "def enc(x):\n"
        "    if isinstance(x, str):\n        return 1\n"
        "    elif isinstance(x, dict):\n        return 2\n"
        "    elif isinstance(x, list):\n        return 3\n"
    )})[0]
    assert closed.type_names == ("str", "dict", "list"), closed.type_names
    assert is_closed_type_set(closed.type_names)
    assert is_dispatch_smell(closed, T)                       # flagged by default
    assert not is_dispatch_smell(closed, T, suppress_closed=True)  # suppressed
    assert render.type_names == ("Circle", "Square", "Triangle"), render.type_names
    assert not is_closed_type_set(render.type_names)          # user types = open
    assert is_dispatch_smell(render, T, suppress_closed=True)  # still a smell
    ast_chain = find_dispatch_chains({"a.py": (
        "def v(n):\n"
        "    if isinstance(n, ast.Add):\n        return 1\n"
        "    elif isinstance(n, ast.Sub):\n        return 2\n"
    )})[0]
    assert is_closed_type_set(ast_chain.type_names), ast_chain.type_names

    # negatives: two different subjects don't form a chain
    neg = find_dispatch_chains({"n.py": (
        "def f(a, b):\n"
        "    if a.x == 1:\n        return 1\n"
        "    elif b.y == 2:\n        return 2\n"
    )})
    assert neg == [], neg
    # comparing two variables (not a constant/enum) is not a value switch
    neg2 = find_dispatch_chains({"n.py": (
        "def f(a, b):\n"
        "    if a == b:\n        return 1\n"
        "    elif a == c:\n        return 2\n"
    )})
    assert all(c.kind != "value_switch" or c.n_branches < 2 for c in neg2), neg2

    print("dispatch.py self-check OK:",
          f"{len(after)} chains after / {len(before)} before, "
          f"{len(new_smells)} new smells, {len(widened)} widened, "
          f"profile added={prof.n_added} modified={prof.n_modified} "
          f"classes_added={prof.n_classes_added}")
