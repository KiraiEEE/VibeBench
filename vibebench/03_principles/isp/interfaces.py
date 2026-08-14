"""
ISP (Interface Segregation Principle) analysis over a Python module tree.

Pure functions over {repo-relative path: source} dicts -- no dataset or git
dependency, unit-testable standalone (run this file directly for a self-check).

What ISP says: no client should be forced to depend on methods it does not
use. The canonical static proxies (all deterministic, stdlib-ast only):

  1. Interface inventory -- three tiers, tagged `kind`:
       abc      -- inherits abc.ABC / metaclass=ABCMeta (alias/attribute forms
                   handled) or defines >=1 @abstractmethod-family method
       protocol -- inherits typing/typing_extensions Protocol (incl.
                   Protocol[T] subscripts and aliased imports)
       implicit -- no marker, but >= N stub-body methods AND an internal
                   subclass (reported for inventory; excluded from the risk
                   flag in v1 -- template-method hooks are a known FP source)
  2. Abstract contract of an interface -- own abstract methods, plus stub-body
     methods for protocol/implicit kinds (Protocol methods are implicitly
     abstract), plus inherited abstracts from internally-resolvable ancestor
     interfaces, minus methods concretely implemented by intervening classes.
     Ancestor resolution uses a per-module import-alias table + repo-wide
     class symbol table (union semantics, not true C3 MRO -- documented
     approximation, deterministic).
  3. Implementer stats -- for each (implementer, interface) pair: how much of
     the contract is concretely implemented (`coverage`) vs overridden with a
     stub body (`stub_ratio`). A high stub_ratio is the textbook ISP smell:
     the class was forced to inherit methods it does not use.
  4. Client usage (secondary, reported raw) -- for functions whose parameter
     is *annotated* with an internal interface type, the share of the
     contract actually accessed on that parameter. Annotation-based on
     purpose: no type inference, stays deterministic.

Known limitations (documented, accepted for v1):
  - Structural (non-inherited) Protocol conformance is invisible statically;
    protocols are scored on width + client usage.
  - Re-exports (`from pkg import Store` where Store lives in pkg.base) are not
    chased through __init__ assignments; such bases count as external.
  - Stub predicate is conservative: only pass / ... / raise NotImplementedError
    bodies (after stripping a docstring) count. `return None` does not.
"""
from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path
from statistics import fmean

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dip"))
from graph import _resolve_relative, path_to_module  # noqa: E402

_ABSTRACT_DECORATORS = {
    "abstractmethod", "abstractproperty", "abstractclassmethod",
    "abstractstaticmethod",
}
_ABC_CANON = {"abc.ABC", "abc.ABCMeta", "ABC", "ABCMeta"}
_PROTOCOL_CANON = {"typing.Protocol", "typing_extensions.Protocol", "Protocol"}
# Annotations never worth tracking as interface clients.
_BUILTIN_SKIP = {
    "str", "int", "float", "bool", "bytes", "dict", "list", "set", "tuple",
    "object", "None", "Any", "typing.Any", "Optional", "typing.Optional",
    "Callable", "typing.Callable",
}


# --------------------------------------------------------------------------
# Dataclasses
# --------------------------------------------------------------------------
@dataclass
class MethodInfo:
    name: str
    lineno: int
    is_abstract: bool     # @abstractmethod-family decorator
    is_stub: bool         # body is pass / ... / raise NotImplementedError


@dataclass
class ClassInfo:
    module: str
    name: str
    lineno: int
    bases_canon: list[str] = field(default_factory=list)  # alias-resolved dotted
    resolved_bases: list[str] = field(default_factory=list)   # internal class ids
    external_bases: list[str] = field(default_factory=list)
    methods: dict[str, MethodInfo] = field(default_factory=dict)
    is_protocol: bool = False
    is_abc: bool = False

    @property
    def class_id(self) -> str:
        return f"{self.module}:{self.name}"


@dataclass
class ClientParam:
    """One interface-annotated parameter of one function (a 'client')."""
    module: str
    func: str
    lineno: int
    annotation_canon: str
    accessed: frozenset[str]
    iface_id: str | None = None   # resolved internal class id, if any


@dataclass
class ClassIndex:
    classes: dict[str, ClassInfo]
    clients: list[ClientParam]
    n_parse_errors: int = 0


@dataclass
class InterfaceInfo:
    class_id: str
    module: str
    name: str
    lineno: int
    kind: str             # "abc" | "protocol" | "implicit"
    abstract: set[str]    # the contract (may be empty for marker-only ABCs)


@dataclass
class ImplStats:
    impl_id: str
    iface_id: str
    n_iface_methods: int
    n_implemented: int
    n_stubbed: int
    coverage: float       # n_implemented / n_iface_methods
    stub_ratio: float     # n_stubbed / n_iface_methods


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------
def _dotted(expr: ast.expr) -> str | None:
    """Flatten Name/Attribute/Subscript/str-annotation to a dotted name."""
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


def _canon(expr: ast.expr, aliases: dict[str, str]) -> str | None:
    """Alias-resolved dotted name: with `import typing as t`, t.Protocol ->
    typing.Protocol; with `from abc import ABC as Base`, Base -> abc.ABC."""
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
                # plain `import a.b` binds only `a`, which is already canonical
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


def _raises_not_implemented(stmt: ast.Raise) -> bool:
    exc = stmt.exc
    if isinstance(exc, ast.Call):
        exc = exc.func
    name = exc.attr if isinstance(exc, ast.Attribute) else getattr(exc, "id", None)
    return name == "NotImplementedError"


def _is_stub_body(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True iff, after stripping a leading docstring, every statement is
    pass / `...` / raise NotImplementedError. Conservative on purpose."""
    body = fn.body
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    if not body:
        return True   # docstring-only body
    for stmt in body:
        if isinstance(stmt, ast.Pass):
            continue
        if (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)
                and stmt.value.value is Ellipsis):
            continue
        if isinstance(stmt, ast.Raise) and _raises_not_implemented(stmt):
            continue
        return False
    return True


def _is_abstract_method(fn: ast.FunctionDef | ast.AsyncFunctionDef,
                        aliases: dict[str, str]) -> bool:
    for dec in fn.decorator_list:
        c = _canon(dec, aliases)
        if c and c.split(".")[-1] in _ABSTRACT_DECORATORS:
            return True
    return False


# --------------------------------------------------------------------------
# Index construction (pass 1: parse, pass 2: resolve internal references)
# --------------------------------------------------------------------------
def build_class_index(files: dict[str, str]) -> ClassIndex:
    """files: {repo-relative path: source} -> repo-wide class/client index."""
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
    clients: list[ClientParam] = []
    n_parse_errors = 0

    for path, m in mod_of_path.items():
        try:
            tree = ast.parse(files[path])
        except (SyntaxError, ValueError):
            n_parse_errors += 1
            continue
        aliases = _alias_table(tree, m, m in packages)

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                info = ClassInfo(module=m, name=node.name, lineno=node.lineno)
                for b in node.bases:
                    c = _canon(b, aliases)
                    if c:
                        info.bases_canon.append(c)
                        if c in _PROTOCOL_CANON:
                            info.is_protocol = True
                        if c in _ABC_CANON:
                            info.is_abc = True
                for kw in node.keywords:
                    if kw.arg == "metaclass":
                        c = _canon(kw.value, aliases)
                        if c in {"abc.ABCMeta", "ABCMeta"}:
                            info.is_abc = True
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        mi = MethodInfo(
                            name=item.name, lineno=item.lineno,
                            is_abstract=_is_abstract_method(item, aliases),
                            is_stub=_is_stub_body(item),
                        )
                        if mi.is_abstract:
                            info.is_abc = info.is_abc or not info.is_protocol
                        info.methods[item.name] = mi
                classes[info.class_id] = info

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args
                ann = [(a.arg, _canon(a.annotation, aliases))
                       for a in (args.posonlyargs + args.args + args.kwonlyargs)
                       if a.annotation is not None]
                ann = [(p, c) for p, c in ann if c and c not in _BUILTIN_SKIP]
                if not ann:
                    continue
                accessed: dict[str, set[str]] = {p: set() for p, _ in ann}
                for sub in ast.walk(node):
                    if (isinstance(sub, ast.Attribute)
                            and isinstance(sub.value, ast.Name)
                            and sub.value.id in accessed):
                        accessed[sub.value.id].add(sub.attr)
                for p, c in ann:
                    clients.append(ClientParam(
                        module=m, func=node.name, lineno=node.lineno,
                        annotation_canon=c, accessed=frozenset(accessed[p]),
                    ))

    # Pass 2: resolve base/annotation names to internal class ids.
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
                continue   # markers, not architecture
            cid = resolve_class(c, info.module)
            if cid and cid != info.class_id:
                info.resolved_bases.append(cid)
            elif cid is None:
                info.external_bases.append(c)

    resolved_clients = []
    for cl in clients:
        cl.iface_id = resolve_class(cl.annotation_canon, cl.module)
        if cl.iface_id:
            resolved_clients.append(cl)

    return ClassIndex(classes=classes, clients=resolved_clients,
                      n_parse_errors=n_parse_errors)


# --------------------------------------------------------------------------
# Interfaces and contracts
# --------------------------------------------------------------------------
def _own_contract(cls: ClassInfo) -> tuple[set[str], set[str]]:
    """(names this class adds to the abstract contract, names it removes).
    abc: decorated methods only (an undecorated stub in an ABC is treated as
    an intentional no-op hook, neither abstract nor an implementation).
    protocol/unmarked: stub bodies also count as abstract declarations."""
    add: set[str] = set()
    remove: set[str] = set()
    for name, m in cls.methods.items():
        if m.is_abstract:
            add.add(name)
        elif m.is_stub:
            if cls.is_protocol or not cls.is_abc:
                add.add(name)
        else:
            remove.add(name)
    return add, remove


def abstract_contract(index: ClassIndex, class_id: str,
                      _memo: dict[str, set[str]] | None = None,
                      _visiting: set[str] | None = None) -> set[str]:
    """Method names still abstract at `class_id` (inherited + own - implemented).
    Cycle-safe; internal ancestors only (union semantics, not true MRO)."""
    memo = _memo if _memo is not None else {}
    visiting = _visiting if _visiting is not None else set()
    if class_id in memo:
        return memo[class_id]
    if class_id in visiting:
        return set()   # inheritance cycle in broken code; break it
    visiting.add(class_id)
    cls = index.classes[class_id]
    inherited: set[str] = set()
    for b in cls.resolved_bases:
        inherited |= abstract_contract(index, b, memo, visiting)
    add, remove = _own_contract(cls)
    out = (inherited | add) - remove
    visiting.discard(class_id)
    memo[class_id] = out
    return out


def find_interfaces(index: ClassIndex,
                    implicit_min_stubs: int = 2) -> dict[str, InterfaceInfo]:
    """All abc/protocol interfaces, plus implicit ones (stub-heavy bases with
    at least one internal subclass)."""
    has_internal_child: set[str] = set()
    for info in index.classes.values():
        has_internal_child.update(info.resolved_bases)

    memo: dict[str, set[str]] = {}
    out: dict[str, InterfaceInfo] = {}
    for cid, info in index.classes.items():
        if info.is_protocol:
            kind = "protocol"
        elif info.is_abc:
            kind = "abc"
        else:
            n_stubs = sum(1 for m in info.methods.values()
                          if m.is_stub and not m.is_abstract)
            if n_stubs >= implicit_min_stubs and cid in has_internal_child:
                kind = "implicit"
            else:
                continue
        out[cid] = InterfaceInfo(
            class_id=cid, module=info.module, name=info.name,
            lineno=info.lineno, kind=kind,
            abstract=abstract_contract(index, cid, memo),
        )
    return out


# --------------------------------------------------------------------------
# Implementer stats
# --------------------------------------------------------------------------
def _ancestors(index: ClassIndex, class_id: str) -> set[str]:
    seen: set[str] = set()
    stack = list(index.classes[class_id].resolved_bases)
    while stack:
        b = stack.pop()
        if b in seen or b not in index.classes:
            continue
        seen.add(b)
        stack.extend(index.classes[b].resolved_bases)
    return seen


def _chain_below(index: ClassIndex, impl_id: str, iface_id: str) -> list[str]:
    """BFS chain from implementer up to (exclusive) the interface -- nearest
    definition wins, approximating MRO deterministically."""
    order: list[str] = []
    seen = {iface_id}
    queue = [impl_id]
    while queue:
        cid = queue.pop(0)
        if cid in seen or cid not in index.classes:
            continue
        seen.add(cid)
        order.append(cid)
        queue.extend(index.classes[cid].resolved_bases)
    return order


def implementer_stats(index: ClassIndex,
                      interfaces: dict[str, InterfaceInfo]) -> list[ImplStats]:
    """One row per (implementer, interface) pair. Implementers are classes
    with no abstract marker of their own (abc/protocol classes extending
    another interface are contract *extensions*, not implementations)."""
    out: list[ImplStats] = []
    for cid, info in index.classes.items():
        if info.is_abc or info.is_protocol:
            continue
        for iface_id in _ancestors(index, cid):
            iface = interfaces.get(iface_id)
            if iface is None or not iface.abstract:
                continue
            state: dict[str, str] = {}
            for node_id in _chain_below(index, cid, iface_id):
                for name, m in index.classes[node_id].methods.items():
                    if name in state or name not in iface.abstract:
                        continue
                    state[name] = ("abstract" if m.is_abstract
                                   else "stub" if m.is_stub else "concrete")
            n = len(iface.abstract)
            n_impl = sum(1 for v in state.values() if v == "concrete")
            n_stub = sum(1 for v in state.values() if v == "stub")
            out.append(ImplStats(
                impl_id=cid, iface_id=iface_id, n_iface_methods=n,
                n_implemented=n_impl, n_stubbed=n_stub,
                coverage=n_impl / n, stub_ratio=n_stub / n,
            ))
    return out


def client_usage(index: ClassIndex, iface_id: str,
                 abstract: set[str]) -> list[float]:
    """Per annotated client of `iface_id`: share of the contract it accesses."""
    if not abstract:
        return []
    return [len(cl.accessed & abstract) / len(abstract)
            for cl in index.clients if cl.iface_id == iface_id]


# --------------------------------------------------------------------------
# Rubric (thresholds passed in from config.py -- this module stays pure)
# --------------------------------------------------------------------------
def forced_stub(stats: ImplStats, thresholds: dict) -> bool:
    """The textbook ISP violation: forced to inherit methods it stubs out."""
    return (stats.stub_ratio > thresholds["impl_stub_ratio"]
            and stats.n_stubbed >= thresholds["impl_stub_min"])


def interface_hits(iface: InterfaceInfo, impl_stats: list[ImplStats],
                   thresholds: dict) -> list[str]:
    hits: list[str] = []
    if len(iface.abstract) > thresholds["iface_abstract_methods"]:
        hits.append("fat_iface")
    covs = [s.coverage for s in impl_stats]
    if (len(covs) >= thresholds["iface_min_implementers"]
            and fmean(covs) < thresholds["iface_mean_coverage"]):
        hits.append("low_coverage")
    if any(forced_stub(s, thresholds) for s in impl_stats):
        hits.append("stubbed_impl")
    return hits


# --------------------------------------------------------------------------
# Self-check (run `python interfaces.py`)
# --------------------------------------------------------------------------
if __name__ == "__main__":
    T = {  # mirrors config.ISP_THRESHOLDS defaults
        "iface_abstract_methods": 10, "impl_stub_ratio": 0.25,
        "impl_stub_min": 2, "iface_mean_coverage": 0.75,
        "iface_min_implementers": 2,
    }

    fat_methods = "\n".join(
        f"    @abstractmethod\n    def m{i}(self): ..." for i in range(12))
    impl_a = ("\n".join(f"    def m{i}(self): return {i}" for i in range(6))
              + "\n"
              + "\n".join(f"    def m{i}(self): raise NotImplementedError"
                          for i in range(6, 12)))
    impl_b = ("\n".join(f"    def m{i}(self): return {i}" for i in range(6))
              + "\n"
              + "\n".join(f"    def m{i}(self): pass" for i in range(6, 12)))
    rel_impl = "\n".join(f"    def m{i}(self): pass" for i in range(12))

    files = {
        "app/__init__.py": "",
        "app/stores.py": (
            "from abc import ABC, abstractmethod\n"
            f"class FatStore(ABC):\n{fat_methods}\n"
        ),
        "app/impl.py": (
            "from app.stores import FatStore\n"
            f"class ImplA(FatStore):\n{impl_a}\n"
            f"class ImplB(FatStore):\n{impl_b}\n"
        ),
        # relative import + full stubbing
        "app/svc.py": (
            "from .stores import FatStore\n"
            f"class RelImpl(FatStore):\n{rel_impl}\n"
            "class Ext(external.Base):\n    def go(self): return 1\n"
        ),
        # segregated protocols: aliased import + Protocol[T] subscript
        "app/proto.py": (
            "from typing import Protocol as P, TypeVar, Generic\n"
            "T = TypeVar('T')\n"
            "class Reader(P):\n"
            "    def read(self): ...\n    def close(self): ...\n"
            "    def seek(self): ...\n"
            "class Writer(P[T]):\n"
            "    def write(self): ...\n"
            "class FileReader(Reader):\n"
            "    def read(self): return 1\n    def close(self): return 1\n"
            "    def seek(self): return 1\n"
        ),
        # implicit interface: stub-body base with an internal subclass
        "app/base.py": (
            "class Hook:\n"
            "    def before(self): raise NotImplementedError\n"
            "    def after(self): raise NotImplementedError\n"
            "class SubHook(Hook):\n"
            "    def before(self): return 1\n    def after(self): return 2\n"
        ),
        # inheritance chain: B implements half of A, C finishes the job
        "app/chain.py": (
            "from abc import ABC, abstractmethod\n"
            "class A(ABC):\n"
            + "\n".join(f"    @abstractmethod\n    def f{i}(self): ..."
                        for i in range(4)) + "\n"
            "class B(A):\n    def f0(self): return 0\n    def f1(self): return 1\n"
            "class C(B):\n    def f2(self): return 2\n    def f3(self): return 3\n"
        ),
        "app/client.py": (
            "from app.stores import FatStore\n"
            "def use(s: FatStore):\n    s.m0()\n    return s.m1()\n"
        ),
    }

    idx = build_class_index(files)
    ifaces = find_interfaces(idx, implicit_min_stubs=2)
    stats = implementer_stats(idx, ifaces)
    by_pair = {(s.impl_id, s.iface_id): s for s in stats}

    fat = ifaces["app.stores:FatStore"]
    assert fat.kind == "abc" and len(fat.abstract) == 12, fat
    assert ifaces["app.proto:Reader"].kind == "protocol"
    assert ifaces["app.proto:Reader"].abstract == {"read", "close", "seek"}
    assert ifaces["app.proto:Writer"].kind == "protocol"     # Protocol[T]
    assert ifaces["app.base:Hook"].kind == "implicit"
    assert "app.base:SubHook" not in ifaces

    sa = by_pair[("app.impl:ImplA", "app.stores:FatStore")]
    assert sa.coverage == 0.5 and sa.stub_ratio == 0.5, sa
    assert forced_stub(sa, T)
    sr = by_pair[("app.svc:RelImpl", "app.stores:FatStore")]   # relative import
    assert sr.coverage == 0.0 and sr.stub_ratio == 1.0, sr
    assert idx.classes["app.svc:Ext"].external_bases == ["external.Base"]
    assert ("app.svc:Ext", "app.stores:FatStore") not in by_pair

    fat_stats = [s for s in stats if s.iface_id == fat.class_id]
    assert sorted(interface_hits(fat, fat_stats, T)) == \
        ["fat_iface", "low_coverage", "stubbed_impl"]
    reader_stats = [s for s in stats if s.iface_id == "app.proto:Reader"]
    assert by_pair[("app.proto:FileReader", "app.proto:Reader")].coverage == 1.0
    assert interface_hits(ifaces["app.proto:Reader"], reader_stats, T) == []

    assert by_pair[("app.chain:B", "app.chain:A")].coverage == 0.5
    assert by_pair[("app.chain:C", "app.chain:A")].coverage == 1.0

    assert client_usage(idx, fat.class_id, fat.abstract) == [2 / 12]

    # _is_stub_body truth table
    def _fn(src: str):
        return ast.parse(src).body[0]
    assert _is_stub_body(_fn("def f():\n    '''doc'''"))
    assert _is_stub_body(_fn("def f(): ..."))
    assert _is_stub_body(_fn("def f(): pass"))
    assert _is_stub_body(_fn("def f(): raise NotImplementedError('msg')"))
    assert not _is_stub_body(_fn("def f(): return None"))
    assert not _is_stub_body(_fn("def f():\n    x = 1\n    raise NotImplementedError"))

    print("interfaces.py self-check OK:",
          f"{len(idx.classes)} classes, {len(ifaces)} interfaces "
          f"({', '.join(sorted(i.kind for i in ifaces.values()))}), "
          f"{len(stats)} implementer pairs, {idx.n_parse_errors} parse errors")
