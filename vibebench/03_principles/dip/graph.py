"""
DIP (Dependency Inversion Principle) analysis over a Python module tree.

Pure functions over {module_name: source} dicts -- no dataset or git
dependency, unit-testable standalone (run this file directly for a self-check).

What DIP says: high-level code should depend on abstractions, not concretions,
and abstractions should not depend on details. No static tool can read intent,
so -- as with SRP -- we compute the standard, literature-backed structural
proxies and report raw values alongside any flag:

  1. Internal import graph      -- who depends on whom inside the repo.
     (grimp-inspired; custom AST builder because grimp requires importable
     packages on sys.path per repo, which is fragile across hundreds of
     arbitrary repo layouts. Handles absolute + relative imports, packages
     (__init__) and modules, resolved by longest-prefix match.)
  2. Abstraction detection      -- a class is "abstract" if it inherits from
     abc.ABC / uses ABCMeta / typing.Protocol, or defines @abstractmethod
     methods. A module's abstractness A = abstract classes / all classes.
  3. Abstract-dependency ratio  -- of a module's internal imports, the share
     that land on modules defining >=1 abstraction ("depend on abstractions").
  4. Cycles                     -- strongly-connected components with >1 module.
     Import cycles are mutual concrete coupling, the canonical DIP breakage.
  5. Martin metrics per package -- Ce (efferent), Ca (afferent),
     I = Ce/(Ce+Ca), A = abstractness, D = |A + I - 1| (distance from the
     main sequence, Robert Martin, "Agile Software Development").
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field


# --------------------------------------------------------------------------
# Module-tree construction
# --------------------------------------------------------------------------
def path_to_module(path: str) -> str | None:
    """'pkg/sub/mod.py' -> 'pkg.sub.mod'; 'pkg/__init__.py' -> 'pkg'.

    Returns None for non-.py paths. A leading 'src/' layout directory is
    stripped (the usual "src layout" convention).
    """
    p = path.replace("\\", "/")
    if not p.endswith(".py"):
        return None
    parts = [x for x in p[:-3].split("/") if x]
    if parts and parts[0] == "src":
        parts = parts[1:]
    if not parts:
        return None
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or not all(x.isidentifier() for x in parts):
        return None
    return ".".join(parts)


def _resolve_relative(module: str, is_package: bool, level: int,
                      target: str | None) -> str | None:
    """Resolve `from ..x import y` relative to `module`."""
    parts = module.split(".")
    if not is_package:
        parts = parts[:-1]
    drop = level - 1
    if drop > len(parts):
        return None
    base = parts[:len(parts) - drop] if drop else parts
    if target:
        base = base + target.split(".")
    return ".".join(base) if base else None


@dataclass
class ModuleInfo:
    name: str
    n_classes: int = 0
    n_abstract_classes: int = 0
    imports: set[str] = field(default_factory=set)   # internal modules only
    parse_ok: bool = True


_ABSTRACT_BASES = {"ABC", "ABCMeta", "Protocol"}


def _is_abstract_class(node: ast.ClassDef) -> bool:
    for b in node.bases + [kw.value for kw in node.keywords]:
        name = b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", None)
        # Handles Generic subscripts like Protocol[T] too.
        if isinstance(b, ast.Subscript):
            v = b.value
            name = v.attr if isinstance(v, ast.Attribute) else getattr(v, "id", None)
        if name in _ABSTRACT_BASES:
            return True
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in item.decorator_list:
                d = dec.attr if isinstance(dec, ast.Attribute) else getattr(dec, "id", None)
                if d in {"abstractmethod", "abstractproperty"}:
                    return True
    return False


def build_module_graph(files: dict[str, str]) -> dict[str, ModuleInfo]:
    """files: {repo-relative path: source} -> {module_name: ModuleInfo}.

    Import edges are kept only when they resolve to another internal module
    (longest dotted-prefix match), so external libraries never appear.
    """
    modules: dict[str, ModuleInfo] = {}
    packages: set[str] = set()
    path_by_module: dict[str, str] = {}
    for path, _ in files.items():
        m = path_to_module(path)
        if m is None:
            continue
        modules[m] = ModuleInfo(name=m)
        path_by_module[m] = path
        if path.replace("\\", "/").endswith("__init__.py"):
            packages.add(m)

    internal = set(modules)

    def to_internal(dotted: str | None) -> str | None:
        """Longest internal prefix of a dotted import target, if any."""
        if not dotted:
            return None
        parts = dotted.split(".")
        for k in range(len(parts), 0, -1):
            cand = ".".join(parts[:k])
            if cand in internal:
                return cand
        return None

    for m, info in modules.items():
        src = files[path_by_module[m]]
        try:
            tree = ast.parse(src)
        except (SyntaxError, ValueError):
            info.parse_ok = False
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                info.n_classes += 1
                if _is_abstract_class(node):
                    info.n_abstract_classes += 1
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    t = to_internal(alias.name)
                    if t and t != m:
                        info.imports.add(t)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = _resolve_relative(m, m in packages, node.level, node.module)
                else:
                    base = node.module
                # `from pkg import name`: when pkg.name is itself an internal
                # module, the real dependency is on that submodule; the edge to
                # the package is only added for names that are NOT submodules
                # (constants/classes re-exported from pkg/__init__.py).
                non_module_names = False
                if base:
                    for alias in node.names:
                        sub = to_internal(f"{base}.{alias.name}")
                        if sub == f"{base}.{alias.name}":
                            if sub != m:
                                info.imports.add(sub)
                        else:
                            non_module_names = True
                t = to_internal(base)
                if t and t != m and (non_module_names or not node.names):
                    info.imports.add(t)
    return modules


# --------------------------------------------------------------------------
# Graph metrics
# --------------------------------------------------------------------------
def find_cycles(modules: dict[str, ModuleInfo]) -> list[frozenset[str]]:
    """Strongly-connected components with >1 node (Tarjan, iterative)."""
    graph = {m: sorted(i.imports) for m, i in modules.items()}
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    sccs: list[frozenset[str]] = []
    counter = [0]

    for root in graph:
        if root in index:
            continue
        work: list[tuple[str, int]] = [(root, 0)]
        while work:
            node, pi = work[-1]
            if pi == 0:
                index[node] = low[node] = counter[0]
                counter[0] += 1
                stack.append(node)
                on_stack.add(node)
            advanced = False
            children = graph.get(node, [])
            while pi < len(children):
                child = children[pi]
                pi += 1
                work[-1] = (node, pi)
                if child not in index:
                    work.append((child, 0))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if advanced:
                continue
            if low[node] == index[node]:
                comp = set()
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.add(w)
                    if w == node:
                        break
                if len(comp) > 1:
                    sccs.append(frozenset(comp))
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
    return sccs


def module_abstractness(info: ModuleInfo) -> float | None:
    if info.n_classes == 0:
        return None
    return info.n_abstract_classes / info.n_classes


def abstract_dep_ratio(modules: dict[str, ModuleInfo],
                       scope: set[str]) -> float | None:
    """Of all internal imports made by `scope` modules, the fraction landing
    on modules that define >= 1 abstraction. None if scope makes no imports."""
    defines_abstraction = {m for m, i in modules.items() if i.n_abstract_classes > 0}
    total = 0
    abstract = 0
    for m in scope:
        info = modules.get(m)
        if not info:
            continue
        for t in info.imports:
            total += 1
            if t in defines_abstraction:
                abstract += 1
    return (abstract / total) if total else None


def top_package(module: str) -> str:
    return module.split(".")[0]


def martin_metrics(modules: dict[str, ModuleInfo],
                   package_of=None) -> dict[str, dict[str, float]]:
    """Per package: Ce, Ca, I, A, D (distance from main sequence).

    `package_of` maps a module name to the package it is aggregated into. It
    DEFAULTS to `top_package` -- the first dotted segment -- which is correct for
    Python and TypeScript, where a module is `pkg.mod` and the first segment IS
    the top-level package.

    It is WRONG for Java. Java packages are reverse-DNS, so `top_package` on
    `net.openhft.chronicle.core.Maths` returns `"net"` and every module in the
    corpus collapses into three buckets (`net`, `com`, `org`). That silently
    voided DIP-Java: the touched-package filter compared `"net"` against real
    package names and never matched, so `new_cycle` was False on all 867 PRs and
    `mean_d` was null on 866 of them. The default is unchanged so the validated
    Python and TypeScript numbers are untouched.
    """
    key = package_of or top_package
    pkgs = {key(m) for m in modules}
    ce: dict[str, set[str]] = {p: set() for p in pkgs}
    ca: dict[str, set[str]] = {p: set() for p in pkgs}
    for m, info in modules.items():
        pm = key(m)
        for t in info.imports:
            pt = key(t)
            if pt != pm:
                ce[pm].add(pt)
                ca[pt].add(pm)

    out: dict[str, dict[str, float]] = {}
    for p in pkgs:
        n_cls = sum(i.n_classes for m, i in modules.items() if key(m) == p)
        n_abs = sum(i.n_abstract_classes for m, i in modules.items() if key(m) == p)
        e, a = len(ce[p]), len(ca[p])
        instability = e / (e + a) if (e + a) else None
        abstractness = (n_abs / n_cls) if n_cls else None
        dist = (abs(abstractness + instability - 1)
                if (abstractness is not None and instability is not None) else None)
        out[p] = {"Ce": e, "Ca": a, "I": instability, "A": abstractness, "D": dist}
    return out


# --------------------------------------------------------------------------
# Self-check (run `python graph.py`)
# --------------------------------------------------------------------------
if __name__ == "__main__":
    files = {
        "app/__init__.py": "",
        "app/interfaces.py": (
            "from abc import ABC, abstractmethod\n"
            "class Store(ABC):\n"
            "    @abstractmethod\n"
            "    def save(self): ...\n"
        ),
        "app/db.py": (
            "from app.interfaces import Store\n"
            "class PgStore(Store):\n"
            "    def save(self): pass\n"
        ),
        "app/service.py": "from .interfaces import Store\n",
        # a 2-cycle of concrete modules
        "app/a.py": "from app import b\nclass A: pass\n",
        "app/b.py": "import app.a\nclass B: pass\n",
    }
    g = build_module_graph(files)
    assert g["app.db"].imports == {"app.interfaces"}, g["app.db"].imports
    assert g["app.service"].imports == {"app.interfaces"}, g["app.service"].imports
    assert g["app.interfaces"].n_abstract_classes == 1
    cycles = find_cycles(g)
    assert cycles == [frozenset({"app.a", "app.b"})], cycles
    r = abstract_dep_ratio(g, {"app.db", "app.a"})
    assert r == 0.5, r   # db->interfaces (abstract), a->b (concrete)
    mm = martin_metrics(g)
    assert mm["app"]["Ce"] == 0 and mm["app"]["Ca"] == 0
    print("graph.py self-check OK:",
          {m: sorted(i.imports) for m, i in g.items()},
          "| cycles:", [sorted(c) for c in cycles],
          "| abstract_dep_ratio:", r)

def cyclic_pairs(cycles) -> set[tuple[str, str]]:
    """Every unordered pair of modules that share a strongly-connected component.

    THE UNIT OF "A NEW CYCLE". Comparing SCCs as whole sets is wrong in both
    directions, and this project hit both:
      * comparing only the TOUCHED subset lets a pre-existing cycle count as new
        the moment a PR adds a file near it (the Java defect, 40 false flags);
      * comparing whole SCCs by identity makes ANY membership change in a large
        pre-existing component look brand new -- including a PR that REMOVES a
        module from the cycle, which is an improvement.
    A pair is the stable unit: (u, v) is newly cyclic iff u and v are mutually
    reachable after and were not before. Shrinking an SCC produces no new pair;
    adding a module to one produces pairs only for that module.
    """
    out: set[tuple[str, str]] = set()
    for c in cycles:
        cl = sorted(c)
        for i in range(len(cl)):
            for j in range(i + 1, len(cl)):
                out.add((cl[i], cl[j]))
    return out
