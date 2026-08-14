"""
!! SUPERSEDED AND VOID -- DO NOT RUN, DO NOT QUOTE ITS OUTPUT (2026-08-03) !!

This module is part of the tree-sitter Java layer that produced the VOID Java
results. Kept only as a record of what was tried and why it failed; see
JAVA_STATUS.md sections 2 and 6 for the measured defects.

The replacement is:
  03_principles/java_sidecar/Facts.java   -- JavaParser + SymbolSolver extractor
  03_principles/java_bridge.py            -- Python bridge + facts cache
  03_principles/java_lang/detectors.py    -- all five principles, self-checked
  03_principles/java_lang/run_java.py     -- one-pass delta harness

Why this one had to go, in one line: tree-sitter parses Java but cannot RESOLVE
symbols, so two classes named `Node` in different packages collapsed into one
and every type hierarchy silently corrupted -- which is why ISP found nothing
and LSP found seven override pairs in 867 pull requests.
"""

"""
The four Java delta principles (OCP, LSP, ISP, DIP) as pure functions over
{path: source}. Facts come from javafacts.py; DIP reuses the ALREADY-VALIDATED
Python maths in dip/graph.py verbatim (Tarjan + Martin), so nothing is
re-implemented.

WHAT JAVA CHANGES, PER PRINCIPLE -- each of these was derived from the
language, not copied from Python or TypeScript:

OCP  A `switch` over an ENUM is CLOSED BY CONSTRUCTION -- you cannot add a
     variant without editing the enum declaration itself, so the switch is
     exhaustive by definition and is not an open/closed violation. Same for a
     switch over a `sealed` hierarchy. This is Java's analogue of Python's
     all-builtins rule and TypeScript's 8-fixed-`typeof`-results rule.

LSP  **The Java compiler already enforces the signature half.** An override
     must match its parent's arity and parameter types exactly -- change them
     and you have written an OVERLOAD, not an override, and `@Override` fails
     to compile. Return types may only NARROW (covariant returns). So the
     entire static signal set that Python needed (`param_removed`,
     `param_required_added`, `param_type_narrowed`) is *unrepresentable* here.
     What remains is BEHAVIOURAL: refused bequest -- overriding a working
     method with a bare `throw`, the `UnsupportedOperationException` idiom --
     plus widening the `throws` clause, which forces callers to handle new
     checked exceptions.

ISP  `interface` is unambiguous in Java (no TypeScript data-shape problem) and
     an implementer is explicit via `implements`. A stub is an empty body, a
     bare `throw`, or `return null`.

DIP  Imports are FULLY QUALIFIED, so the module graph needs no path-alias or
     workspace-map guessing -- the weakness that cost the TypeScript port a
     0-61% edge loss until it was fixed. Package = module.

Self-check: python vibebench/03_principles/java_lang/principles.py
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parents[1] / "dip"))
import javafacts  # noqa: E402
from graph import ModuleInfo  # noqa: E402

# ---------------------------------------------------------------------------
# shared index
# ---------------------------------------------------------------------------


def build_index(files: dict[str, str]):
    """{path: FileFacts} plus a {simple type name: TypeFacts} lookup."""
    per_file, by_name = {}, {}
    for path, src in files.items():
        f = javafacts.analyze_source(path, src)
        per_file[path] = f
        for t in f.types:
            by_name.setdefault(t.name, (f, t))
    return per_file, by_name


# ---------------------------------------------------------------------------
# OCP -- dispatch chains
# ---------------------------------------------------------------------------
_INSTANCEOF = re.compile(r"\binstanceof\s+([A-Z][A-Za-z0-9_.]*)")
_GETCLASS = re.compile(r"getClass\(\)\s*==\s*([A-Za-z0-9_.]+)\.class")


@dataclass
class DispatchChain:
    module: str
    qualname: str
    subject: str
    kind: str            # instanceof | getclass | switch_enum | switch_value
    n_branches: int
    lineno: int
    file_path: str
    type_names: str
    closed_set: bool     # enum/sealed switch: exhaustive by construction


def find_dispatch_chains(files: dict[str, str], enum_names: set[str] | None = None,
                         index=None) -> list[DispatchChain]:
    """Type/tag switch statements -- Martin's canonical OCP smell."""
    per_file, by_name = index if index is not None else build_index(files)
    enums = enum_names if enum_names is not None else {
        t.name for f in per_file.values() for t in f.types if t.kind == "enum"}
    sealed = {t.name for f in per_file.values() for t in f.types
              if t.kind == "interface" and t.is_abstract}

    # Chains are attributed per METHOD, using a source window bounded by the
    # method's own line span -- a file-wide regex would merge unrelated chains
    # from different methods into one oversized "smell".
    out: list[DispatchChain] = []
    for path, src in files.items():
        f = per_file[path]
        mod = f.package or path.rsplit("/", 1)[0].replace("/", ".")
        lines = src.splitlines()
        for t in f.types:
            for m in t.methods:
                if m.is_abstract or m.loc <= 0:
                    continue
                start = max(0, _method_line(src, t, m) - 1)
                window = "\n".join(lines[start:start + m.loc])
                for rx, kind in ((_INSTANCEOF, "instanceof"), (_GETCLASS, "getclass")):
                    names = rx.findall(window)
                    if len(names) >= 2:
                        uniq = sorted(set(names))
                        out.append(DispatchChain(
                            mod, f"{t.name}.{m.name}", "instanceof", kind,
                            len(uniq), start + 1, path, ";".join(uniq),
                            closed_set=all(n.split(".")[-1] in sealed for n in uniq)))
                for subj, labels, ln in _switches(window, start):
                    if len(labels) < 2:
                        continue
                    is_enum = any(l in enums for l in labels) or _looks_enum(labels)
                    out.append(DispatchChain(
                        mod, f"{t.name}.{m.name}", subj,
                        "switch_enum" if is_enum else "switch_value",
                        len(labels), ln, path, ";".join(sorted(labels)[:12]),
                        closed_set=is_enum))
    return out


def _method_line(src: str, t, m) -> int:
    """Best-effort 1-based line of a method declaration inside its type."""
    pat = re.compile(rf"\b{re.escape(m.name)}\s*\(")
    for i, line in enumerate(src.splitlines(), 1):
        if i >= t.lineno and pat.search(line):
            return i
    return t.lineno


_SWITCH = re.compile(r"\bswitch\s*\(([^)]*)\)\s*\{", re.S)
_CASE = re.compile(r"\bcase\s+([A-Za-z0-9_\"'.]+)")


def _switches(window: str, offset: int):
    for m in _SWITCH.finditer(window):
        subject = m.group(1).strip()
        rest = window[m.end():]
        depth, end = 1, len(rest)
        for i, ch in enumerate(rest):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        labels = sorted(set(_CASE.findall(rest[:end])))
        yield subject, labels, offset + window[:m.start()].count("\n") + 1


def _looks_enum(labels: list[str]) -> bool:
    """Bare SCREAMING_CASE labels are enum constants; quoted ones are strings."""
    bare = [l for l in labels if not l.startswith(('"', "'"))]
    return len(bare) == len(labels) and all(
        l.isupper() or "." in l for l in bare) and bool(bare)


def is_dispatch_smell(c: DispatchChain, thr: dict, suppress_closed: bool = True) -> bool:
    if suppress_closed and c.closed_set:
        return False
    bar = (thr["type_branches"] if c.kind in ("instanceof", "getclass")
           else thr["value_branches"])
    return c.n_branches >= bar


# ---------------------------------------------------------------------------
# LSP -- override pairs (behavioural only; see module docstring)
# ---------------------------------------------------------------------------
@dataclass
class OverridePair:
    module: str
    qualname: str
    base_id: str
    method: str
    lineno: int
    file_path: str
    signals: list[str]


def find_overrides(files: dict[str, str], index=None) -> list[OverridePair]:
    per_file, by_name = index if index is not None else build_index(files)
    out: list[OverridePair] = []
    for path, src in files.items():
        f = per_file[path]
        mod = f.package or ""
        for t in f.types:
            parents = ([t.extends] if t.extends else []) + list(t.implements)
            for m in t.methods:
                if m.is_constructor or m.is_abstract:
                    continue
                base = None
                for p in parents:
                    hit = by_name.get(p.split(".")[-1])
                    if hit and any(bm.name == m.name for bm in hit[1].methods):
                        base = (p, hit)
                        break
                if base is None:
                    continue
                pname, (bf, bt) = base
                bm = next(x for x in bt.methods if x.name == m.name)
                sig = []
                # Refused bequest: the parent has a WORKING body, the child
                # replaces it with nothing but `throw`. The classic Java form
                # is UnsupportedOperationException.
                if m.throws_only and not bm.is_abstract and not bm.throws_only \
                        and bm.body_stmts > 0:
                    sig.append("refused_bequest")
                # Covariant return is legal and good; WIDENING is what breaks
                # callers. Java rejects most of it at compile time, so this
                # only fires on generic/raw erasure cases.
                if (bm.return_type and m.return_type
                        and bm.return_type != m.return_type
                        and bm.return_type != "Object" and m.return_type == "Object"):
                    sig.append("return_type_widened")
                if sig:
                    out.append(OverridePair(
                        mod, t.name, f"{bf.package}:{bt.name}", m.name,
                        t.lineno, path, sig))
    return out


# ---------------------------------------------------------------------------
# ISP -- interface width + implementer stub ratio
# ---------------------------------------------------------------------------
@dataclass
class Contract:
    iface_id: str
    module: str
    name: str
    kind: str
    n_members: int
    lineno: int
    file_path: str


@dataclass
class Implementer:
    iface_id: str
    class_id: str
    module: str
    qualname: str
    iface_width: int
    n_implemented: int
    n_stubs: int
    coverage: float
    stub_ratio: float
    file_path: str


def _ancestor_contracts(t, by_name, contracts):
    """Every contract reachable from `t` through extends/implements, transitively.

    Yields (contract_id, contract_TypeFacts). Cycles are impossible in a legal
    Java hierarchy but the `seen` guard keeps a malformed tree from looping.
    """
    seen, stack, out = set(), list(t.implements) + ([t.extends] if t.extends else []), []
    while stack:
        name = stack.pop().split(".")[-1]
        if name in seen:
            continue
        seen.add(name)
        hit = by_name.get(name)
        if not hit:
            continue
        bf, bt = hit
        cid = f"{bf.package}:{bt.name}"
        if cid in contracts:
            out.append((cid, bt))
        stack += list(bt.implements) + ([bt.extends] if bt.extends else [])
    return out


def find_interfaces(files: dict[str, str], index=None):
    per_file, by_name = index if index is not None else build_index(files)
    contracts: dict[str, Contract] = {}
    for path, f in per_file.items():
        for t in f.types:
            if t.kind == "interface" or (t.kind == "class" and t.is_abstract):
                members = [m for m in t.methods
                           if m.is_abstract and not m.is_constructor]
                if not members:
                    continue
                cid = f"{f.package}:{t.name}"
                contracts[cid] = Contract(cid, f.package, t.name, t.kind,
                                          len(members), t.lineno, path)

    impls: list[Implementer] = []
    for path, f in per_file.items():
        for t in f.types:
            # An ABSTRACT class is not a forced client -- it defers to
            # subclasses. Same rule the TypeScript port needed.
            if t.is_abstract:
                continue
            # Walk ancestors TRANSITIVELY: `class Impl extends Base implements
            # Contract` is a client of Contract even though it names only Base.
            # Missing this dropped every multi-level hierarchy, which in Java
            # is the common shape.
            for cid, bt in _ancestor_contracts(t, by_name, contracts):
                need = {m.name for m in bt.methods if m.is_abstract}
                own = {m.name: m for m in t.methods if not m.is_abstract}
                impl = [own[n] for n in need if n in own]
                stubs = sum(1 for m in impl
                            if m.throws_only or m.body_stmts == 0
                            or (m.body_stmts == 1 and m.return_type in
                                ("void", "", "null")))
                impls.append(Implementer(
                    cid, f"{f.package}:{t.name}", f.package, t.name,
                    len(need), len(impl), stubs,
                    len(impl) / len(need) if need else 0.0,
                    stubs / len(impl) if impl else 0.0, path))
    return contracts, impls


# ---------------------------------------------------------------------------
# DIP -- package graph feeding the VALIDATED Python Tarjan + Martin code
# ---------------------------------------------------------------------------
def build_modules(files: dict[str, str], index=None) -> dict[str, ModuleInfo]:
    per_file, _ = index if index is not None else build_index(files)
    own_packages = {f.package for f in per_file.values() if f.package}
    mods: dict[str, ModuleInfo] = {}
    for path, f in per_file.items():
        pkg = f.package
        if not pkg:
            continue
        internal = set()
        for imp in f.imports:
            # Java imports are FULLY QUALIFIED: the package is everything up to
            # the last dot. No alias or workspace guessing needed.
            owner = imp.rsplit(".", 1)[0]
            if imp.endswith(".*"):
                owner = imp[:-2]
            if owner in own_packages and owner != pkg:
                internal.add(owner)
        m = mods.get(pkg)
        if m is None:
            mods[pkg] = ModuleInfo(name=pkg, n_classes=len(f.types),
                                   n_abstract_classes=sum(1 for t in f.types
                                                          if t.is_abstract),
                                   imports=internal, parse_ok=f.parse_ok)
        else:
            m.n_classes += len(f.types)
            m.n_abstract_classes += sum(1 for t in f.types if t.is_abstract)
            m.imports |= internal
    return mods


# ---------------------------------------------------------------------------
def _self_check() -> None:
    def die(m):
        print("SELF-CHECK FAIL:", m); raise SystemExit(1)

    files = {
        "com/x/Kind.java": "package com.x;\npublic enum Kind { A, B, C }\n",
        "com/x/Router.java": """
package com.x;
import com.x.model.Node;
public class Router {
    String openChain(Object o) {
        if (o instanceof Dog) return "woof";
        else if (o instanceof Cat) return "meow";
        else if (o instanceof Bird) return "tweet";
        return "?";
    }
    String closedEnum(Kind k) {
        switch (k) { case A: return "a"; case B: return "b"; case C: return "c"; }
        return "?";
    }
    String openTags(String s) {
        switch (s) { case "one": return "1"; case "two": return "2";
                     case "three": return "3"; case "four": return "4";
                     case "five": return "5"; case "six": return "6"; }
        return "?";
    }
}""",
        "com/x/model/Node.java": """
package com.x.model;
import com.x.Kind;
public interface Repo {
    void save(String k, String v);
    String load(String k);
    void remove(String k);
}
public abstract class BaseRepo implements Repo {
    public void save(String k, String v) { store(k, v); }
    public String load(String k) { return "x"; }
    public void remove(String k) { drop(k); }
    void store(String k, String v) { System.out.println(k); }
    void drop(String k) { System.out.println(k); }
}
class ReadOnlyRepo extends BaseRepo {
    @Override public void save(String k, String v) { throw new UnsupportedOperationException(); }
    @Override public void remove(String k) { throw new UnsupportedOperationException(); }
}
class Node {}""",
    }

    chains = find_dispatch_chains(files)
    kinds = {c.kind for c in chains}
    if "instanceof" not in kinds:
        die(f"instanceof chain missed; got {[(c.kind, c.n_branches) for c in chains]}")
    enum_c = [c for c in chains if c.kind == "switch_enum"]
    val_c = [c for c in chains if c.kind == "switch_value"]
    if not enum_c or not all(c.closed_set for c in enum_c):
        die("enum switch must be detected AND marked closed_set")
    if not val_c or any(c.closed_set for c in val_c):
        die("string switch must be detected and NOT closed")
    thr = {"type_branches": 3, "value_branches": 6}
    if any(is_dispatch_smell(c, thr) for c in enum_c):
        die("enum switch must be suppressed (closed by construction)")
    if not any(is_dispatch_smell(c, thr) for c in val_c):
        die("6-branch string switch should be a smell")

    pairs = find_overrides(files)
    rb = [p for p in pairs if "refused_bequest" in p.signals]
    if len(rb) != 2:
        die(f"expected 2 refused-bequest overrides, got {len(rb)}: "
            f"{[(p.qualname, p.method) for p in pairs]}")

    contracts, impls = find_interfaces(files)
    if not any(c.name == "Repo" and c.n_members == 3 for c in contracts.values()):
        die(f"Repo contract width 3, got {[(c.name, c.n_members) for c in contracts.values()]}")
    ro = [i for i in impls if i.qualname == "ReadOnlyRepo"]
    if not ro:
        die("ReadOnlyRepo should be an implementer")
    if ro[0].n_stubs < 2:
        die(f"ReadOnlyRepo stubs >=2, got {ro[0].n_stubs}")
    if any(i.qualname == "BaseRepo" for i in impls):
        die("abstract class must not count as a forced client")

    mods = build_modules(files)
    if "com.x" not in mods or "com.x.model" not in mods:
        die(f"packages, got {list(mods)}")
    if "com.x.model" not in mods["com.x"].imports:
        die(f"com.x should import com.x.model, got {mods['com.x'].imports}")
    if "com.x" not in mods["com.x.model"].imports:
        die("com.x.model should import com.x (the cycle edge)")

    print(f"javaprinciples self-check OK: {len(chains)} chains "
          f"(enum suppressed, string switch flagged), {len(rb)} refused-bequest "
          f"overrides, {len(contracts)} contracts / {len(impls)} implementers "
          f"(abstract excluded), {len(mods)} packages with the cycle edge present")


if __name__ == "__main__":
    _self_check()
