"""
LSP (Liskov Substitution Principle) analysis over a Python module tree.

Pure functions over {repo-relative path: source} dicts -- no dataset or git
dependency, unit-testable standalone (run this file directly for a self-check).

What LSP says (Barbara Liskov, 1987): objects of a supertype shall be
replaceable with objects of a subtype without altering the correctness of the
program. Operationally, for a method override to be substitutable it must:
  * NOT strengthen preconditions  (accept everything the base accepts:
    parameter types are CONTRAVARIANT, and no new required argument),
  * NOT weaken postconditions      (promise everything the base promises:
    return type is COVARIANT, and it must not refuse to do the work at all).

LSP is -- with SRP -- the hardest SOLID principle to automate (see TOOLS.md).
The literature's canonical static signal is a TYPE CHECKER's override rule:
mypy's `[override]` error and Pyright's reportIncompatibleMethodOverride flag
exactly the substitutability breaks above. Running mypy/Pyright per repo needs
an importable environment resolved across 147 arbitrary repo layouts -- the same
fragility that made DIP reject grimp in favour of a custom AST graph. So this
module REPLICATES the type checker's override-compatibility rule
DETERMINISTICALLY on the stdlib `ast`, for every internal subclass->ancestor
override pair, with no environment and no LLM. The trade-off is documented under
"Known limitations": we see annotations as WRITTEN, not inferred types.

Signals (all deterministic, stdlib-ast only), grouped by contract dimension:

  STATIC  -- structural signature incompatibility (the mypy [override] core):
    param_required_added  a base-contract caller would omit a parameter the
                          override REQUIRES -> TypeError (strengthened
                          precondition).
    param_removed         the override cannot accept a parameter the base
                          accepts (dropped positional, or a base keyword the
                          override neither names nor absorbs via **kwargs).
  SEMANTIC -- type-annotation variance over the internal class hierarchy:
    param_type_narrowed   a parameter is annotated with a strict SUBtype of the
                          base's declared type (contravariance violation).
    return_type_widened   the return is annotated with a strict SUPERtype of the
                          base's, or adds None/Optional/object (covariance
                          violation / weakened postcondition).
  BEHAVIORAL -- contract refusal (the textbook "refused bequest" smell):
    refused_bequest       the base method has a real working body and the
                          override's body only raises NotImplementedError /
                          TypeError / ... -> the subtype refuses an inherited
                          contract it is supposed to honour (Square.set_width,
                          ReadOnlyList.append, Ostrich.fly).

`is_lsp_violation` flags an override when it fires any of `strong_signals`
(config.LSP_THRESHOLDS). Each signal is individually a recognized break, so --
unlike OCP's branch count -- there is no continuous threshold to calibrate; the
calibration question is per-signal precision, answered by the label loop. The
harness emits EVERY in-scope override pair (violating or not) for that.

Known limitations (documented, accepted for v1):
  - Annotations are read as WRITTEN, never inferred; an unannotated parameter or
    return contributes no type-variance signal (conservative -- fewer false
    positives, some false negatives vs a full type checker).
  - Type variance is only asserted when it is PROVABLE from the internal class
    hierarchy (base type is an in-repo ancestor of the override type, or is
    object/Any) -- unrelated annotations are never guessed to be sub/supertypes.
  - Positional-or-keyword parameter RENAMES are not flagged (idiomatic in Python
    and a large false-positive source); arity, keyword-only names, and the
    presence of *args/**kwargs catch-alls carry the structural signal.
  - `__init__`/`__new__`/`__post_init__`/`__init_subclass__`/`__class_getitem__`
    /`__set_name__` are excluded: constructors and hooks are not substitutable
    call sites, so their signature drift is idiomatic, not an LSP break (same
    "exclude constructors" convention as SRP's LCOM).
  - Only INTERNAL (in-repo) base classes are resolvable; overrides of external
    library base methods are invisible (same scope limit as ISP/DIP).
"""
from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dip"))
from graph import _resolve_relative, path_to_module  # noqa: E402

_ABSTRACT_DECORATORS = {
    "abstractmethod", "abstractproperty", "abstractclassmethod",
    "abstractstaticmethod",
}
_ABC_CANON = {"abc.ABC", "abc.ABCMeta", "ABC", "ABCMeta"}
_PROTOCOL_CANON = {"typing.Protocol", "typing_extensions.Protocol", "Protocol"}
# Exceptions whose sole-statement raise means "I refuse to honour this contract".
_REFUSAL_EXCEPTIONS = {
    "NotImplementedError", "NotImplemented", "TypeError", "NotSupportedError",
    "RuntimeError", "PermissionError", "AttributeError",
    "UnsupportedOperation", "ReadOnlyError",
}
# Not substitutable call sites -> signature drift here is idiomatic, not LSP.
_SKIP_METHODS = {
    "__init__", "__new__", "__post_init__", "__init_subclass__",
    "__class_getitem__", "__set_name__", "__init_subclass__",
}
# Annotations that are the "top" type -- narrowing away from them is a violation.
_TOP_TYPES = {"object", "Any", "typing.Any"}
_NONEISH = {"None", "Optional", "typing.Optional"}
# --- Precision-suppressor support (used only when suppress_fps=True; see
# config.LSP_SUPPRESS_FPS). The 4 false-positive families the label loop found
# are all structurally detectable, so this is a pure-rule fix, no ML needed.
_ANY_TYPES = {"Any", "typing.Any"}          # mypy treats Any as compatible both ways
_TYPEVAR_NAMES = {"T", "S", "U", "K", "V", "R", "KT", "VT", "T_co", "T_contra",
                  "U_co", "V_co", "_T", "_S", "_KT", "_VT", "AnyStr"}
_FACTORY_NAMES = {"create", "build", "new", "of", "make", "construct", "from_dict"}


def _looks_typevar(ann: str | None) -> bool:
    """Heuristic: an unbound TypeVar (T, S, KT, T_co, ...) , variance against one
    is meaningless (the subclass may bind it to anything)."""
    if not ann:
        return False
    return (ann in _TYPEVAR_NAMES
            or ann.endswith(("_co", "_contra"))
            or (len(ann) <= 2 and ann[:1].isupper() and ann.isalnum()))


# --------------------------------------------------------------------------
# Dataclasses
# --------------------------------------------------------------------------
@dataclass
class Sig:
    """Caller-visible parameter model (implicit self/cls already dropped)."""
    n_pos_required: int              # positional params with no default
    n_pos_max: int                   # posonly + positional-or-keyword params
    has_vararg: bool                 # *args present
    has_kwarg: bool                  # **kwargs present
    kwonly_all: frozenset[str]       # keyword-only names (required + optional)
    kwonly_required: frozenset[str]  # keyword-only names with no default
    pos_annotations: tuple           # canon annotation per positional slot (or None)
    return_annotation: str | None


@dataclass
class MethodDef:
    name: str
    lineno: int
    is_abstract: bool
    is_stub: bool                    # body is pass / ... / raise NotImplementedError
    is_static: bool
    is_classmethod: bool
    is_property: bool
    is_overload: bool
    refusal: str | None              # refusal-exception name if body is SOLELY a raise
    refusal_liberal: str | None      # last stmt is a refusal raise, no value returned
    sig: Sig


@dataclass
class ClassInfo:
    module: str
    name: str
    lineno: int
    bases_canon: list[str] = field(default_factory=list)
    resolved_bases: list[str] = field(default_factory=list)
    is_abc: bool = False
    is_protocol: bool = False
    methods: dict[str, MethodDef] = field(default_factory=dict)

    @property
    def class_id(self) -> str:
        return f"{self.module}:{self.name}"


@dataclass
class OverridePair:
    module: str          # subclass module (attribution scope)
    subclass: str        # subclass name
    base_id: str         # class_id of the ancestor whose method is overridden
    method: str
    lineno: int          # override's def line
    signals: list[str] = field(default_factory=list)
    in_scope: bool = False

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.module, self.subclass, self.method)

    @property
    def qualname(self) -> str:
        return f"{self.module}:{self.subclass}"


@dataclass
class Index:
    classes: dict[str, ClassInfo]
    n_parse_errors: int = 0

    def resolve(self, canon: str | None, module: str) -> str | None:
        """Resolve a canon dotted name to an internal class_id, else None."""
        if not canon:
            return None
        parts = canon.split(".")
        if len(parts) == 1:
            cid = f"{module}:{canon}"
            return cid if cid in self.classes else None
        mod, cls = ".".join(parts[:-1]), parts[-1]
        cid = f"{mod}:{cls}"
        return cid if cid in self.classes else None

    def ancestors(self, class_id: str) -> set[str]:
        seen: set[str] = set()
        info = self.classes.get(class_id)
        if info is None:
            return seen
        stack = list(info.resolved_bases)
        while stack:
            b = stack.pop()
            if b in seen or b not in self.classes:
                continue
            seen.add(b)
            stack.extend(self.classes[b].resolved_bases)
        return seen


# --------------------------------------------------------------------------
# Parsing helpers (self-contained; mirror ISP's alias/canon logic)
# --------------------------------------------------------------------------
def _dotted(expr: ast.expr | None) -> str | None:
    if expr is None:
        return None
    if isinstance(expr, ast.Subscript):
        return _dotted(expr.value)
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        base = _dotted(expr.value)
        return f"{base}.{expr.attr}" if base else None
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        s = expr.value.strip()
        parts = s.split(".")
        return s if parts and all(p.isidentifier() for p in parts) else None
    return None


def _canon(expr: ast.expr | None, aliases: dict[str, str]) -> str | None:
    d = _dotted(expr)
    if not d:
        return None
    head, _, rest = d.partition(".")
    head = aliases.get(head, head)
    return f"{head}.{rest}" if rest else head


def _alias_table(tree: ast.Module, module: str, is_package: bool) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    aliases[a.asname] = a.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = _resolve_relative(module, is_package, node.level, node.module)
            else:
                base = node.module
            for a in node.names:
                if a.name == "*":
                    continue
                target = f"{base}.{a.name}" if base else a.name
                aliases[a.asname or a.name] = target
    return aliases


def _strip_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        return body[1:]
    return body


def _is_stub_body(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    body = _strip_docstring(fn.body)
    if not body:
        return True
    for stmt in body:
        if isinstance(stmt, ast.Pass):
            continue
        if (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)
                and stmt.value.value is Ellipsis):
            continue
        if isinstance(stmt, ast.Raise) and _refusal_name(stmt) is not None:
            continue
        return False
    return True


def _refusal_name(stmt: ast.Raise) -> str | None:
    exc = stmt.exc
    if exc is None:
        return None
    if isinstance(exc, ast.Call):
        exc = exc.func
    name = exc.attr if isinstance(exc, ast.Attribute) else getattr(exc, "id", None)
    return name if name in _REFUSAL_EXCEPTIONS else None


def _refusal(fn: ast.FunctionDef | ast.AsyncFunctionDef,
             liberal: bool = False) -> str | None:
    """The refusal exception if the method categorically refuses, else None.
    strict (default): the body is SOLELY `raise <RefusalExc>` -- one statement.
    liberal: the LAST top-level statement is a `raise <RefusalExc>` and the body
    contains no value `return`/`yield` and no earlier top-level raise (so a leading
    `del`/log line before an unconditional refusal still counts, e.g.
    `del args, kwargs; raise TypeError(...)`) -- catches refused bequests the
    strict rule misses. Used only when suppress_fps is on."""
    body = _strip_docstring(fn.body)
    if not body:
        return None
    if not liberal:
        if len(body) == 1 and isinstance(body[0], ast.Raise):
            return _refusal_name(body[0])
        return None
    last = body[-1]
    if not isinstance(last, ast.Raise):
        return None
    name = _refusal_name(last)
    if name is None:
        return None
    for stmt in body[:-1]:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Return) and sub.value is not None:
                return None
            if isinstance(sub, (ast.Yield, ast.YieldFrom, ast.Raise)):
                return None
    return name


def _decorator_names(fn) -> set[str]:
    out: set[str] = set()
    for dec in fn.decorator_list:
        d = _dotted(dec)
        if d:
            out.add(d.split(".")[-1])
    return out


def _extract_sig(fn: ast.FunctionDef | ast.AsyncFunctionDef,
                 aliases: dict[str, str], drop_first: bool) -> Sig:
    a = fn.args
    positional = list(a.posonlyargs) + list(a.args)
    if drop_first and positional:
        positional = positional[1:]            # drop self / cls
    n_pos_max = len(positional)
    n_pos_required = n_pos_max - len(a.defaults)
    if n_pos_required < 0:                      # defaults >= positionals after self
        n_pos_required = 0
    pos_annotations = tuple(_canon(p.annotation, aliases) for p in positional)

    kwonly_all = frozenset(p.arg for p in a.kwonlyargs)
    kwonly_required = frozenset(
        p.arg for p, d in zip(a.kwonlyargs, a.kw_defaults) if d is None)
    ret = _canon(fn.returns, aliases)
    return Sig(
        n_pos_required=n_pos_required, n_pos_max=n_pos_max,
        has_vararg=a.vararg is not None, has_kwarg=a.kwarg is not None,
        kwonly_all=kwonly_all, kwonly_required=kwonly_required,
        pos_annotations=pos_annotations, return_annotation=ret,
    )


# --------------------------------------------------------------------------
# Index construction
# --------------------------------------------------------------------------
def build_index(files: dict[str, str]) -> Index:
    mod_of_path: dict[str, str] = {}
    packages: set[str] = set()
    for path in files:
        m = path_to_module(path)
        if m is None:
            continue
        mod_of_path[path] = m
        if path.replace("\\", "/").endswith("__init__.py"):
            packages.add(m)

    classes: dict[str, ClassInfo] = {}
    n_parse_errors = 0
    for path, m in mod_of_path.items():
        try:
            tree = ast.parse(files[path])
        except (SyntaxError, ValueError):
            n_parse_errors += 1
            continue
        aliases = _alias_table(tree, m, m in packages)

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            info = ClassInfo(module=m, name=node.name, lineno=node.lineno)
            for b in node.bases:
                c = _canon(b, aliases)
                if not c:
                    continue
                info.bases_canon.append(c)
                if c in _PROTOCOL_CANON:
                    info.is_protocol = True
                if c in _ABC_CANON:
                    info.is_abc = True
            for kw in node.keywords:
                if kw.arg == "metaclass" and _canon(kw.value, aliases) in {
                        "abc.ABCMeta", "ABCMeta"}:
                    info.is_abc = True
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                decs = _decorator_names(item)
                is_abstract = bool(decs & _ABSTRACT_DECORATORS)
                is_static = "staticmethod" in decs
                info.methods[item.name] = MethodDef(
                    name=item.name, lineno=item.lineno,
                    is_abstract=is_abstract, is_stub=_is_stub_body(item),
                    is_static=is_static,
                    is_classmethod="classmethod" in decs,
                    is_property="property" in decs or "cached_property" in decs,
                    is_overload="overload" in decs,
                    refusal=_refusal(item),
                    refusal_liberal=_refusal(item, liberal=True),
                    sig=_extract_sig(item, aliases, drop_first=not is_static),
                )
                if is_abstract:
                    info.is_abc = info.is_abc or not info.is_protocol
            classes[info.class_id] = info

    # Resolve bases to internal class ids.
    by_module: dict[str, dict[str, str]] = {}
    for cid, info in classes.items():
        by_module.setdefault(info.module, {})[info.name] = cid

    def resolve_class(canon: str, module: str) -> str | None:
        parts = canon.split(".")
        if len(parts) == 1:
            return by_module.get(module, {}).get(canon)
        mod, cls = ".".join(parts[:-1]), parts[-1]
        return by_module.get(mod, {}).get(cls)

    for info in classes.values():
        for c in info.bases_canon:
            if c in _ABC_CANON or c in _PROTOCOL_CANON:
                continue
            cid = resolve_class(c, info.module)
            if cid and cid != info.class_id:
                info.resolved_bases.append(cid)

    return Index(classes=classes, n_parse_errors=n_parse_errors)


# --------------------------------------------------------------------------
# Override discovery + comparison
# --------------------------------------------------------------------------
def _nearest_ancestor_defining(index: Index, class_id: str,
                               method: str) -> str | None:
    """BFS up the resolved-base graph for the nearest ancestor that defines
    `method` (nearest wins -> approximates MRO deterministically)."""
    seen = {class_id}
    queue = list(index.classes[class_id].resolved_bases)
    while queue:
        cid = queue.pop(0)
        if cid in seen or cid not in index.classes:
            continue
        seen.add(cid)
        if method in index.classes[cid].methods:
            return cid
        queue.extend(index.classes[cid].resolved_bases)
    return None


def _signature_signals(sub: Sig, base: Sig, suppress_fps: bool = False) -> list[str]:
    """Structural incompatibilities -- the mypy [override] rule.
    A valid base-contract call passes between base.n_pos_required and
    base.n_pos_max positional args (unbounded if base has *args); the override
    must accept all of them."""
    out: list[str] = []
    # Override requires MORE than the minimum a base call provides.
    if sub.n_pos_required > base.n_pos_required:
        out.append("param_required_added")
    # Override cannot accept as MANY as a base call may pass.
    if not sub.has_vararg:
        if base.has_vararg or sub.n_pos_max < base.n_pos_max:
            # FP suppressor: the override's **kwargs absorbs a dropped
            # positional-or-keyword param IFF the dropped slots are all OPTIONAL
            # (base caller can omit or pass by keyword) and base has no *args
            # (unbounded positional cannot go through **kwargs).
            absorbed = (suppress_fps and sub.has_kwarg and not base.has_vararg
                        and sub.n_pos_max >= base.n_pos_required)
            if not absorbed:
                out.append("param_removed")
    # Base keyword-only names a base caller may pass must be acceptable
    # (a **kwargs on the override already absorbs them -- only flag when absent).
    if not sub.has_kwarg:
        if base.kwonly_all - sub.kwonly_all:
            if "param_removed" not in out:
                out.append("param_removed")
    # Override requires a keyword-only a base caller would not know to pass.
    if sub.kwonly_required - base.kwonly_required:
        if "param_required_added" not in out:
            out.append("param_required_added")
    return out


def _variance_signals(index: Index, sub_mod: str, base_mod: str,
                      sub: Sig, base: Sig, suppress_fps: bool = False) -> list[str]:
    """Semantic type-variance over the internal class hierarchy. Only asserted
    when PROVABLE (base type is an in-repo ancestor of the override type, or is
    object/Any / None). Unrelated annotations are never guessed.
    FP suppressor: variance against `Any` or an unbound TypeVar is never a real
    violation (mypy treats Any as compatible both ways; a TypeVar binds freely)."""
    out: list[str] = []

    def is_strict_subtype(sub_ann: str | None, sup_ann: str | None,
                          a_mod: str, b_mod: str) -> bool:
        if not sub_ann or not sup_ann or sub_ann == sup_ann:
            return False
        if suppress_fps and (sup_ann in _ANY_TYPES
                             or _looks_typevar(sub_ann) or _looks_typevar(sup_ann)):
            return False
        if sup_ann in _TOP_TYPES:                       # object/Any = top
            return sub_ann not in _TOP_TYPES
        sub_id = index.resolve(sub_ann, a_mod)
        sup_id = index.resolve(sup_ann, b_mod)
        return bool(sub_id and sup_id and sup_id in index.ancestors(sub_id))

    # Contravariance: a parameter narrowed to a subtype of the base's type.
    for i in range(min(len(sub.pos_annotations), len(base.pos_annotations))):
        s_ann, b_ann = sub.pos_annotations[i], base.pos_annotations[i]
        if is_strict_subtype(s_ann, b_ann, sub_mod, base_mod):
            out.append("param_type_narrowed")
            break

    # Covariance: a return widened to a supertype (or None/Optional/object).
    s_ret, b_ret = sub.return_annotation, base.return_annotation
    if s_ret and b_ret and s_ret != b_ret and b_ret not in _NONEISH:
        if suppress_fps and (s_ret in _ANY_TYPES
                             or _looks_typevar(s_ret) or _looks_typevar(b_ret)):
            widened = False                             # Any/typevar return = not a break
        else:
            widened = (s_ret in _TOP_TYPES
                       or (s_ret in _NONEISH and b_ret not in _NONEISH)
                       or is_strict_subtype(b_ret, s_ret, base_mod, sub_mod))
        if widened:
            out.append("return_type_widened")
    return out


def _is_plugin_placeholder(m: MethodDef) -> bool:
    """A base method whose signature is a pure *args/**kwargs hook and whose body
    is a stub / sole-raise -- the 'plugin' pattern where each subclass defines its
    own real signature. It carries no substitutable contract to violate."""
    s = m.sig
    return (s.n_pos_max == 0 and not s.kwonly_all
            and (s.has_vararg or s.has_kwarg)
            and (m.is_abstract or m.is_stub or m.refusal is not None))


def _is_factory_classmethod(m: MethodDef) -> bool:
    """A constructor-like factory classmethod (create / from_* / build / ...).
    Like __init__, these are not substitutable call sites, so their signature is
    free to differ per subclass."""
    return m.is_classmethod and (m.name in _FACTORY_NAMES
                                 or m.name.startswith("from_"))


def compare_override(index: Index, sub_cls: ClassInfo, base_cls: ClassInfo,
                     method: str, suppress_fps: bool = False) -> list[str]:
    sub_m = sub_cls.methods[method]
    base_m = base_cls.methods[method]

    # FP suppressor (config.LSP_SUPPRESS_FPS): a plugin-placeholder base or a
    # constructor-like factory classmethod carries no substitutable contract.
    if suppress_fps and (_is_plugin_placeholder(base_m)
                         or _is_factory_classmethod(sub_m)):
        return []

    signals: list[str] = []

    # BEHAVIORAL: refused bequest -- concrete working base, override only raises.
    # The liberal refusal test (suppress mode) also catches a leading del/log line
    # before an unconditional raise (e.g. marimo sidebar.batch).
    refusal = sub_m.refusal_liberal if suppress_fps else sub_m.refusal
    if (refusal is not None and not sub_m.is_abstract
            and not base_m.is_abstract and not base_m.is_stub
            and base_m.refusal is None):
        signals.append("refused_bequest")

    # A property vs non-property (or static vs instance) mismatch makes the
    # parameter models incomparable -- skip structural/variance for that pair
    # (still counts refused_bequest above). Otherwise compare signatures.
    kind_mismatch = (sub_m.is_property != base_m.is_property
                     or sub_m.is_static != base_m.is_static)
    if not kind_mismatch and not sub_m.is_overload and not base_m.is_overload:
        signals.extend(_signature_signals(sub_m.sig, base_m.sig, suppress_fps))
        signals.extend(_variance_signals(
            index, sub_cls.module, base_cls.module, sub_m.sig, base_m.sig,
            suppress_fps))
    return signals


def find_overrides(index: Index, suppress_fps: bool = False) -> list[OverridePair]:
    """Every (internal subclass, nearest internal ancestor, method) override,
    with its LSP signals computed. suppress_fps toggles the deterministic
    false-positive suppressor (config.LSP_SUPPRESS_FPS)."""
    out: list[OverridePair] = []
    for cid, info in index.classes.items():
        if not info.resolved_bases:
            continue
        for method, m in info.methods.items():
            if method in _SKIP_METHODS:
                continue
            base_id = _nearest_ancestor_defining(index, cid, method)
            if base_id is None:
                continue
            signals = compare_override(index, info, index.classes[base_id],
                                       method, suppress_fps)
            out.append(OverridePair(
                module=info.module, subclass=info.name, base_id=base_id,
                method=method, lineno=m.lineno, signals=signals))
    return out


# --------------------------------------------------------------------------
# Rubric (thresholds passed in from config.py -- this module stays pure)
# --------------------------------------------------------------------------
def is_lsp_violation(pair: OverridePair, thresholds: dict) -> bool:
    strong = set(thresholds["strong_signals"])
    return bool(strong & set(pair.signals))


# --------------------------------------------------------------------------
# Self-check (run `python overrides.py`)
# --------------------------------------------------------------------------
if __name__ == "__main__":
    T = {"strong_signals": [
        "refused_bequest", "param_required_added", "param_removed",
        "param_type_narrowed", "return_type_widened"]}

    files = {
        "app/__init__.py": "",
        "app/shapes.py": (
            "from abc import ABC, abstractmethod\n"
            "class Animal:\n"
            "    def eat(self, food): return 1\n"
            "class Rectangle:\n"
            "    def set_size(self, w, h): self.w = w; self.h = h\n"
            "    def area(self) -> int: return self.w * self.h\n"
            "class Base:\n"
            "    def handle(self, x, y=0): return x\n"
            "    def read(self) -> 'Dog': ...\n"
            "class Dog(Animal):\n"
            "    def eat(self, food): return 2\n"          # clean override
        ),
        # refused bequest: concrete base append -> override raises
        "app/coll.py": (
            "class MyList:\n"
            "    def append(self, x): self._data.append(x)\n"
            "class ReadOnlyList(MyList):\n"
            "    def append(self, x): raise NotImplementedError\n"
        ),
        # strengthened precondition: override adds a required param
        "app/pre.py": (
            "from app.shapes import Base\n"
            "class Strict(Base):\n"
            "    def handle(self, x, y, z): return x + y + z\n"   # z required-added
        ),
        # param_removed: base accepts 2 positional, override accepts 1, no *args
        "app/rem.py": (
            "from app.shapes import Base\n"
            "class Narrow(Base):\n"
            "    def handle(self, x): return x\n"
        ),
        # contravariance violation: param narrowed Animal -> Dog
        "app/contra.py": (
            "from app.shapes import Animal, Dog\n"
            "class Feeder:\n"
            "    def feed(self, a: Animal): return 1\n"
            "class DogFeeder(Feeder):\n"
            "    def feed(self, a: Dog): return 2\n"
        ),
        # covariance violation: return widened Dog -> Animal
        "app/cov.py": (
            "from app.shapes import Animal, Dog\n"
            "class Shelter:\n"
            "    def adopt(self) -> Dog: ...\n"
            "class BadShelter(Shelter):\n"
            "    def adopt(self) -> Animal: ...\n"
        ),
        # clean: widening a param (Dog -> Animal) is ALLOWED (contravariant ok)
        "app/ok.py": (
            "from app.shapes import Animal, Dog\n"
            "class DogWalker:\n"
            "    def walk(self, d: Dog): return 1\n"
            "class AnyWalker(DogWalker):\n"
            "    def walk(self, d: Animal): return 2\n"
        ),
        # clean: override abstract method with a raise is NOT refused bequest
        "app/abs.py": (
            "from abc import ABC, abstractmethod\n"
            "class Store(ABC):\n"
            "    @abstractmethod\n    def get(self, k): ...\n"
            "class Stub(Store):\n"
            "    def get(self, k): raise NotImplementedError\n"
        ),
        # clean: adding *args/**kwargs is fine; optional param is fine
        "app/wide.py": (
            "from app.shapes import Base\n"
            "class Wide(Base):\n"
            "    def handle(self, x, y=0, *args, **kwargs): return x\n"
        ),
        # --- FP cases: flagged by DEFAULT, clean ONLY under suppress_fps=True ---
        # (a) variance against Any -> not a real violation (mypy allows)
        "app/anyv.py": (
            "from typing import Any\n"
            "from app.shapes import Dog\n"
            "class AnyBase:\n"
            "    def h(self, x: Any): return x\n"
            "class AnyNarrow(AnyBase):\n"
            "    def h(self, x: Dog): return x\n"
        ),
        # (b) return widened to an unbound typevar -> not a real violation
        "app/tvret.py": (
            "class TVR:\n"
            "    def g(self) -> T: ...\n"
            "class TVRSub(TVR):\n"
            "    def g(self) -> Optional[T]: ...\n"
        ),
        # (c) **kwargs absorbs a dropped OPTIONAL param -> no real break
        "app/kw.py": (
            "class KwBase:\n"
            "    def m(self, a, b=1, c=2): return a\n"
            "class KwSub(KwBase):\n"
            "    def m(self, a, **kwargs): return a\n"
        ),
        # (d) base is a pure *args/**kwargs plugin placeholder -> no contract
        "app/plugin.py": (
            "class Tool:\n"
            "    def run(self, *args, **kwargs): raise NotImplementedError\n"
            "class MyTool(Tool):\n"
            "    def run(self, url, action=None): return url\n"
        ),
        # (e) constructor-like factory classmethod -> not a substitutable site
        "app/factory.py": (
            "class Widget:\n"
            "    @classmethod\n    def create(cls, a, b): return cls()\n"
            "class Button(Widget):\n"
            "    @classmethod\n    def create(cls, label): return cls()\n"
        ),
        # --- suppress_fps should ALSO catch these genuine cases ---
        # (f) liberal refused bequest: leading `del` before an unconditional raise
        "app/refuse2.py": (
            "class Widget2:\n"
            "    def batch(self, x): return x + 1\n"
            "class NoBatch(Widget2):\n"
            "    def batch(self, *a, **k):\n        del a, k\n"
            "        raise TypeError('nope')\n"
        ),
        # (g) a REQUIRED param drop must still flag even with **kwargs present
        "app/kwreq.py": (
            "class KwReqBase:\n"
            "    def m(self, a, b, c=2): return a\n"
            "class KwReqSub(KwReqBase):\n"
            "    def m(self, a, **kwargs): return a\n"
        ),
    }

    idx = build_index(files)
    overrides = find_overrides(idx)
    by_key = {(o.module, o.subclass, o.method): o for o in overrides}

    def sig_of(mod, cls, meth):
        return set(by_key[(mod, cls, meth)].signals)

    # refused bequest
    assert sig_of("app.coll", "ReadOnlyList", "append") == {"refused_bequest"}, \
        sig_of("app.coll", "ReadOnlyList", "append")
    # strengthened precondition (required param added)
    assert "param_required_added" in sig_of("app.pre", "Strict", "handle")
    # param removed
    assert "param_removed" in sig_of("app.rem", "Narrow", "handle")
    # contravariance violation (param narrowed)
    assert sig_of("app.contra", "DogFeeder", "feed") == {"param_type_narrowed"}, \
        sig_of("app.contra", "DogFeeder", "feed")
    # covariance violation (return widened)
    assert sig_of("app.cov", "BadShelter", "adopt") == {"return_type_widened"}, \
        sig_of("app.cov", "BadShelter", "adopt")

    # NEGATIVES (must be clean)
    assert sig_of("app.shapes", "Dog", "eat") == set()
    assert sig_of("app.ok", "AnyWalker", "walk") == set(), \
        sig_of("app.ok", "AnyWalker", "walk")          # widening a param is OK
    assert sig_of("app.abs", "Stub", "get") == set(), \
        sig_of("app.abs", "Stub", "get")               # abstract base, not refused
    assert sig_of("app.wide", "Wide", "handle") == set(), \
        sig_of("app.wide", "Wide", "handle")           # *args/**kwargs + optional

    # DEFAULT mode: the 5 FP cases (a-e) fire, the genuine liberal-refusal (f)
    # does NOT (strict refusal misses it), the required-drop (g) fires.
    assert sig_of("app.anyv", "AnyNarrow", "h") == {"param_type_narrowed"}
    assert sig_of("app.tvret", "TVRSub", "g") == {"return_type_widened"}
    assert sig_of("app.kw", "KwSub", "m") == {"param_removed"}
    assert sig_of("app.plugin", "MyTool", "run")          # flagged (some signals)
    assert sig_of("app.factory", "Button", "create") == {"param_removed"}
    assert sig_of("app.refuse2", "NoBatch", "batch") == set()   # strict misses it
    assert "param_removed" in sig_of("app.kwreq", "KwReqSub", "m")

    # --- SUPPRESS mode: re-run with the deterministic FP suppressor on ---
    sup = {(o.module, o.subclass, o.method): set(o.signals)
           for o in find_overrides(idx, suppress_fps=True)}

    def sup_of(mod, cls, meth):
        return sup[(mod, cls, meth)]

    # FP cases (a-e) are now CLEAN:
    for m, c, meth in [("app.anyv", "AnyNarrow", "h"),
                       ("app.tvret", "TVRSub", "g"),
                       ("app.kw", "KwSub", "m"),
                       ("app.plugin", "MyTool", "run"),
                       ("app.factory", "Button", "create")]:
        assert sup_of(m, c, meth) == set(), (m, c, meth, sup_of(m, c, meth))
    # (f) genuine refused bequest with a leading `del` is now CAUGHT:
    assert sup_of("app.refuse2", "NoBatch", "batch") == {"refused_bequest"}
    # (g) required-param drop still flags under suppress:
    assert "param_removed" in sup_of("app.kwreq", "KwReqSub", "m")
    # genuine TPs are UNCHANGED under suppress:
    assert sup_of("app.coll", "ReadOnlyList", "append") == {"refused_bequest"}
    assert "param_required_added" in sup_of("app.pre", "Strict", "handle")
    assert "param_removed" in sup_of("app.rem", "Narrow", "handle")
    assert sup_of("app.contra", "DogFeeder", "feed") == {"param_type_narrowed"}
    assert sup_of("app.cov", "BadShelter", "adopt") == {"return_type_widened"}

    violations = [o for o in overrides if is_lsp_violation(o, T)]
    sup_pairs = find_overrides(idx, suppress_fps=True)
    sup_viol = [o for o in sup_pairs if is_lsp_violation(o, T)]
    print("overrides.py self-check OK:",
          f"{len(idx.classes)} classes, {len(overrides)} override pairs, "
          f"{len(violations)} violations (default) -> {len(sup_viol)} (suppress_fps); "
          f"{idx.n_parse_errors} parse errors")
