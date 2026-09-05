"""
Java SOLID detectors over the JavaParser fact stream -- the rewrite that
replaces the void tree-sitter layer (JAVA_STATUS.md sections 2 and 6).

DESIGN RULES, inherited from the validated Python and TypeScript evaluators:
  * pure functions over facts; no I/O, no git, no thresholds hardcoded
  * every metric formula that already exists is REUSED, not re-implemented --
    `lcom_from_access_sets` and `c3_from_token_bags` come straight from
    srp/metrics.py, and DIP calls dip/graph.py's Tarjan + Martin maths, so the
    three languages stay numerically commensurable
  * policy (thresholds, suppressors) is passed in from config, never baked in

SCOPE PER PRINCIPLE (the defect-#13 fix -- the void run used one scope for all
five, which made the numbers incommensurable):
  * SRP  -- per TYPE in a touched file, and we record `is_new` so "Codex wrote
            a god class" is separable from "Codex edited someone else's"
  * OCP  -- per DISPATCH CHAIN, keyed (type, method, subject); method-local
  * LSP  -- per OVERRIDE PAIR, needs the whole-repo resolved hierarchy
  * ISP  -- per CONTRACT + implementer, needs the whole-repo hierarchy
  * DIP  -- per PACKAGE over the whole-repo module graph

Self-check: python vibebench/03_principles/java_lang/detectors.py
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1] / "srp"))
sys.path.insert(0, str(_HERE.parents[1] / "dip"))
from metrics import c3_from_token_bags, lcom_from_access_sets  # noqa: E402
import graph as dipgraph  # noqa: E402

# Java's `Object` methods are declared by every class; treating them as
# overrides would swamp LSP with equals/hashCode/toString noise.
OBJECT_METHODS = {"equals", "hashCode", "toString", "clone", "finalize"}

# A throw-only body counts as a refusal only for these types. `throw new
# IllegalStateException` inside a lifecycle method is often legitimate.
REFUSAL_THROWS = {
    "UnsupportedOperationException", "NotImplementedException",
    "UnsupportedOperationError", "NotImplementedError",
}


# --------------------------------------------------------------------------
# index
# --------------------------------------------------------------------------

@dataclass
class Index:
    types: dict[str, dict] = field(default_factory=dict)     # fqn -> TypeF
    by_file: dict[str, list[str]] = field(default_factory=dict)
    modules: list[dict] = field(default_factory=list)
    files: dict[str, dict] = field(default_factory=dict)

    def ancestors(self, fqn: str) -> list[str]:
        """Transitive internal ancestors, preferring the resolved chain the
        sidecar computed and falling back to walking `supertypes` (which also
        covers the case where SymbolSolver resolved the direct edge but not the
        full chain)."""
        t = self.types.get(fqn)
        if not t:
            return []
        seen, out, stack = {fqn}, [], list(t.get("allAncestors") or []) + list(t.get("supertypes") or [])
        while stack:
            a = stack.pop()
            if a in seen:
                continue
            seen.add(a)
            if a in self.types:
                out.append(a)
                stack.extend(self.types[a].get("supertypes") or [])
        return out


def build_index(facts: dict) -> Index:
    ix = Index()
    for t in facts.get("types") or []:
        ix.types[t["id"]] = t
        ix.by_file.setdefault(t["file"], []).append(t["id"])
    ix.modules = list(facts.get("modules") or [])
    for f in facts.get("files") or []:
        ix.files[f["path"]] = f
    return ix


# --------------------------------------------------------------------------
# SRP
# --------------------------------------------------------------------------

def srp_metrics(t: dict) -> dict:
    """God-class metric vector for one type. Same 7 metrics as Python/TS."""
    methods = [m for m in t["methods"] if not m["isCtor"]]
    names = [m["signature"] for m in methods]
    attrs = {m["signature"]: set(m["props"]) for m in methods}
    # `lcom_from_access_sets` matches call edges against its own KEYS, so the
    # call sets must be expressed in the same vocabulary as `names`. The sidecar
    # reports bare call names (`foo`) while the keys are signatures
    # (`foo(String)`) -- Java needs signatures because overloading is pervasive
    # and two `foo`s are genuinely different methods. Map each called name onto
    # every signature that declares it: a call to an overloaded `foo` links to
    # all its overloads, which is the conservative, cohesion-preserving choice.
    by_name: dict[str, list[str]] = {}
    for m in methods:
        by_name.setdefault(m["name"], []).append(m["signature"])
    calls = {m["signature"]: {sig for c in m["calls"] for sig in by_name.get(c, ())}
             for m in methods}
    # LCOM4 IS UNDEFINED WHEN NO EDGE IS POSSIBLE (JAVA_STATUS.md 8.2).
    # LCOM4 counts connected components of the graph "methods, joined when they
    # touch a common field or call one another". A type whose methods touch no
    # field and call nothing has NO EDGES BY CONSTRUCTION, so every method is its
    # own component and the normalised value pins near 1.0 -- for reasons of
    # syntax, not design. That is every plain `interface` and every pure-abstract
    # class, and it made `class_lcom` fire as a second "cohesion" hit on types
    # whose only real signal was width, double-counting `class_methods`.
    # Measured: mean LCOM 0.857 for interfaces vs 0.425 for everything else.
    #
    # The test is on the GRAPH, not on `kind`. An interface with default methods
    # that read constants or call each other HAS edges, and there LCOM4 is
    # meaningful -- it keeps its value. `srp_hits` already skips the knob when
    # lcom is None, so no rubric change is needed.
    #
    # Confined to the Java detector ON PURPOSE: `lcom_from_access_sets` is shared
    # verbatim with the validated Python and TypeScript evaluators, and editing it
    # would silently invalidate their published numbers.
    has_edges = any(attrs.values()) or any(calls.values())
    lcom = lcom_from_access_sets(names, attrs, calls) if has_edges else None
    c3 = c3_from_token_bags([m["tokens"] for m in methods])
    return {
        "class_loc": t["loc"],
        "method_count": len(methods),
        "wmc": sum(m["ccn"] for m in methods),
        "max_method_ccn": max((m["ccn"] for m in methods), default=0),
        "max_params": max((m["nParams"] for m in t["methods"]), default=0),
        "lcom": lcom,
        "c3": c3,
    }


def srp_hits(mv: dict, th: dict) -> list[str]:
    """Rubric hits. CLASS-SCOPED complexity (the Python scope bug stays fixed:
    a file's worst method must not be charged to every class in the file)."""
    hits = []
    if mv["class_loc"] >= th["class_loc"]:
        hits.append("class_loc")
    if mv["wmc"] >= th["class_wmc"]:
        hits.append("class_wmc")
    if mv["method_count"] >= th["class_methods"]:
        hits.append("class_methods")
    if mv["max_method_ccn"] >= th["func_ccn"]:
        hits.append("func_ccn")
    if mv["max_params"] >= th["func_params"]:
        hits.append("func_params")
    if (mv["lcom"] is not None and mv["method_count"] >= th["lcom_min_methods"]
            and mv["lcom"] >= th["class_lcom"]):
        hits.append("class_lcom")
    return hits


def srp_risk(mv: dict, th: dict) -> tuple[bool, bool, list[str], bool]:
    """-> (risk, strict, hits, c3_suppressed). The C3 suppressor is the
    validated precision fix: a big class whose methods share a vocabulary is
    cohesive, not a god class."""
    hits = srp_hits(mv, th)
    suppressed = bool(mv["c3"] is not None and mv["c3"] >= th["c3_suppress"] and len(hits) < th["strict_hits"])
    risk = len(hits) >= th["min_hits"] and not suppressed
    return risk, len(hits) >= th["strict_hits"], hits, suppressed


# --------------------------------------------------------------------------
# OCP
# --------------------------------------------------------------------------

def dispatch_chains(ix: Index) -> list[dict]:
    """Every in-scope chain (>=2 branches), with a REAL subject so the delta key
    discriminates. `closed_set` means the subject resolved to an enum, i.e. the
    variant set is fixed by the language -- the Java analogue of Python's
    all-builtins rule and TypeScript's `typeof` rule."""
    out = []
    for t in ix.types.values():
        for m in t["methods"]:
            for c in m["chains"]:
                if c["nBranches"] < 2:
                    continue
                out.append({
                    "type_id": t["id"], "method": m["signature"], "file_path": t["file"],
                    "subject": c["subject"], "subject_type": c.get("subjectType"),
                    "kind": c["kind"], "n_branches": c["nBranches"], "lineno": c["lineno"],
                    "type_names": ";".join(c["typeNames"][:12]), "closed_set": bool(c["closedSet"]),
                    "key": (t["id"], m["signature"], c["subject"]),
                })
    return out


def is_dispatch_smell(c: dict, th: dict, suppress_closed: bool = True) -> bool:
    if suppress_closed and c["closed_set"]:
        return False
    bar = th["type_branches"] if c["kind"] in ("instanceof", "type_eq") else th["value_branches"]
    return c["n_branches"] >= bar


# --------------------------------------------------------------------------
# LSP
# --------------------------------------------------------------------------

def override_pairs(ix: Index) -> list[dict]:
    """Subclass method -> nearest ancestor declaring the same signature.

    WHY THE SIGNAL SET DIFFERS FROM PYTHON (see JAVA_STATUS.md): `javac` already
    enforces three of Python's five signals, so they cannot occur in code that
    compiles. A changed parameter list is an OVERLOAD, not an override;
    parameter types are invariant; visibility cannot narrow. What a compiler
    cannot check is what we detect: refused bequest, strengthened preconditions,
    weakened postconditions.
    """
    out = []
    for t in ix.types.values():
        ancs = ix.ancestors(t["id"])
        if not ancs:
            continue
        for m in t["methods"]:
            if m["isCtor"] or m["isStatic"] or m["name"] in OBJECT_METHODS:
                continue
            base_id = base = None
            for a in ancs:
                for bm in ix.types[a]["methods"]:
                    # The base must supply a REAL inherited implementation. A
                    # pair against an abstract/interface declaration is not an
                    # LSP relation -- there is no bequest to refuse and no
                    # precondition to strengthen -- and counting them would
                    # inflate the denominator with every interface method.
                    if (bm["signature"] == m["signature"] and not bm["isCtor"]
                            and bm["bodyKind"] == "normal"):
                        base_id, base = a, bm
                        break
                if base is not None:
                    break
            if base is None:
                continue
            signals = []
            # (1) refused bequest -- the base does real work, the override refuses
            if (m["bodyKind"] == "throw" and base["bodyKind"] == "normal"
                    and (m.get("throwType") or "") in REFUSAL_THROWS):
                signals.append("refused_bequest")
            # (2) precondition strengthened -- new argument guard that throws
            if m.get("guardThrows", 0) > base.get("guardThrows", 0) and base["bodyKind"] == "normal":
                signals.append("precondition_strengthened")
            # (3) postcondition weakened -- returns null where the base never does
            if (m.get("returnsNull") and not base.get("returnsNull")
                    and base["bodyKind"] == "normal" and m["returnType"] not in ("void", "")):
                signals.append("postcondition_weakened")
            out.append({
                "type_id": t["id"], "base_id": base_id, "method": m["signature"],
                "file_path": t["file"], "lineno": m["lineno"],
                "signals": ";".join(signals),
                "key": (t["id"], m["signature"]),
            })
    return out


def is_lsp_violation(pair: dict, strong: list[str]) -> bool:
    return any(s in strong for s in pair["signals"].split(";") if s)


# --------------------------------------------------------------------------
# ISP
# --------------------------------------------------------------------------

def contracts(ix: Index, th: dict) -> tuple[list[dict], list[dict]]:
    """-> (contract rows, implementer rows).

    Scope cuts carried over from the TypeScript port, where skipping them made
    the result an artefact: a contract must declare >=1 behavioural member AND
    have >=1 INTERNAL implementer. Without the second cut, every unimplemented
    marker interface counts.
    """
    impls: dict[str, list[str]] = {}
    for t in ix.types.values():
        for a in ix.ancestors(t["id"]):
            at = ix.types.get(a)
            if at and (at["kind"] == "interface" or at["isAbstract"]):
                impls.setdefault(a, []).append(t["id"])

    crows, irows = [], []
    for cid, members in ix.types.items():
        if members["kind"] not in ("interface",) and not members["isAbstract"]:
            continue
        decl = [m for m in members["methods"] if not m["isCtor"] and not m["isStatic"]]
        # abstract members are the contract; concrete ones are provided help
        abstract = [m for m in decl if m["isAbstract"]]
        width = len(abstract) if members["kind"] == "interface" else len(abstract)
        if width < 1 or cid not in impls:
            continue
        crows.append({
            "iface_id": cid, "file_path": members["file"], "lineno": members["lineno"],
            "kind": "interface" if members["kind"] == "interface" else "abstract_class",
            "n_members": width, "n_implementers": len(impls[cid]),
            "flagged": width >= th["fat_members"],
            # Implementer ids travel with the contract so the harness can attribute
            # ripple-wise: widening a contract forces stubs into implementers the
            # PR never opened, and a filter that sees only the contract's own file
            # cannot notice that. Not used by any metric -- attribution only.
            "impl_ids": tuple(impls[cid]),
            "key": cid,
        })
        want = {m["signature"] for m in abstract}
        for iid in impls[cid]:
            it = ix.types[iid]
            own = {m["signature"]: m for m in it["methods"] if not m["isAbstract"]}
            provided = [own[s] for s in want if s in own]
            stubs = [m for m in provided
                     if m["bodyKind"] == "empty"
                     or (m["bodyKind"] == "throw" and (m.get("throwType") or "") in REFUSAL_THROWS)]
            if not provided:
                continue
            irows.append({
                "iface_id": cid, "type_id": iid, "file_path": it["file"],
                "iface_width": width, "n_implemented": len(provided), "n_stubs": len(stubs),
                "coverage": len(provided) / width,
                "stub_ratio": len(stubs) / len(provided),
                "forced_stub": len(stubs) >= th["min_stubs"] and len(stubs) / len(provided) >= th["stub_ratio"],
                "key": (cid, iid),
            })
    return crows, irows


# --------------------------------------------------------------------------
# DIP -- reuses the validated Python graph maths verbatim
# --------------------------------------------------------------------------

def dip_modules(ix: Index) -> dict[str, dipgraph.ModuleInfo]:
    """Facts -> exactly dip/graph.py's ModuleInfo shape, so Tarjan SCC and the
    Martin A/I/D metrics are the SAME code that was validated on Python."""
    mods = {}
    for m in ix.modules:
        mods[m["name"]] = dipgraph.ModuleInfo(
            name=m["name"], n_classes=m["nClasses"], n_abstract_classes=m["nAbstractClasses"],
            imports=set(), parse_ok=m["parseOk"],
        )
    # imports are type FQNs; map each onto the module that declares it
    owner = {t["id"]: None for t in ix.types.values()}
    for m in ix.modules:
        for tid in ix.by_file.get(m["file"], []):
            owner[tid] = m["name"]
    for m in ix.modules:
        tgt = {owner.get(i) for i in m["imports"]}
        mods[m["name"]].imports = {x for x in tgt if x and x != m["name"]}
    return mods


# --------------------------------------------------------------------------
# self-check
# --------------------------------------------------------------------------

def _self_check() -> None:
    import json
    import subprocess
    import tempfile
    sys.path.insert(0, str(_HERE.parents[1]))
    from java_bridge import analyze_tree  # noqa: E402

    src = {
        "src/main/java/app/Repo.java":
            "package app;\npublic interface Repo {\n"
            "  void save(String s); void load(String s); void delete(String s);\n"
            "  void flush(); void close(); void reindex(); void vacuum();\n}\n",
        "src/main/java/app/Base.java":
            "package app;\npublic class Base { public String render(String in) { return String.valueOf(in); } }\n",
        "src/main/java/app/ReadOnly.java":
            "package app;\npublic class ReadOnly extends Base implements Repo {\n"
            "  private String buf;\n"
            "  public void save(String s) { throw new UnsupportedOperationException(); }\n"
            "  public void load(String s) { buf = s; }\n"
            "  public void delete(String s) { throw new UnsupportedOperationException(); }\n"
            "  public void flush() { buf = buf + \"!\"; } public void close() { buf = null; }\n"
            "  public void reindex() { } public void vacuum() { }\n"
            "  @Override public String render(String in) {\n"
            "    if (in == null) throw new IllegalArgumentException();\n"
            "    return in.trim(); }\n"
            "  public String pick(Object o) {\n"
            "    if (o instanceof String) return \"s\";\n"
            "    else if (o instanceof Integer) return \"i\";\n"
            "    else if (o instanceof Double) return \"d\";\n"
            "    return \"?\"; }\n}\n",
    }
    facts = analyze_tree(src, use_cache=False)
    assert facts["ok"], facts
    ix = build_index(facts)
    print("java detectors self-check")

    # SRP
    mv = srp_metrics(ix.types["app.ReadOnly"])
    assert mv["method_count"] == 9, mv
    assert mv["lcom"] is not None and mv["c3"] is not None
    print(f"  SRP  metrics computed (methods={mv['method_count']} lcom={mv['lcom']:.2f} c3={mv['c3']:.2f})")

    # LCOM call edges must be keyed in the SAME vocabulary as `names`. Regression
    # guard: `load`/`flush`/`close` all touch `buf`, so they must land in ONE
    # component -- with the old bare-name call sets this silently scored 1.0.
    cohesive = analyze_tree({"src/main/java/app/Chain.java":
                             "package app;\npublic class Chain {\n"
                             "  private String buf;\n"
                             "  public void a() { b(); }\n"
                             "  public void b() { c(); }\n"
                             "  public void c() { buf = \"x\"; }\n"
                             "  public String d() { return buf; }\n}\n"}, use_cache=False)
    cv = srp_metrics(build_index(cohesive).types["app.Chain"])
    assert cv["lcom"] == 0.0, f"call-linked chain must be ONE component, got {cv['lcom']}"
    print("  SRP  call edges link overload-aware signatures (LCOM regression guard)")

    # JAVA_STATUS.md 8.2: LCOM must be None when the graph CANNOT have an edge,
    # and must survive when it can. Both directions are pinned.
    flat = analyze_tree({"src/main/java/app/Flat.java":
                         "package app;\npublic interface Flat {\n"
                         + "".join(f"  void m{i}();\n" for i in range(6)) + "}\n"}, use_cache=False)
    fv = srp_metrics(build_index(flat).types["app.Flat"])
    assert fv["lcom"] is None, f"edgeless type must not get an LCOM value, got {fv['lcom']}"
    live = analyze_tree({"src/main/java/app/Live.java":
                         "package app;\npublic interface Live {\n"
                         "  void a(); void b();\n"
                         "  default void c() { a(); } default void d() { b(); }\n"
                         "  default void e() { c(); } default void f() { d(); }\n}\n"}, use_cache=False)
    lv = srp_metrics(build_index(live).types["app.Live"])
    assert lv["lcom"] is not None, "an interface with real default-method edges KEEPS its LCOM"
    print("  SRP  LCOM is None for an edgeless type, kept where default methods create edges")

    # OCP -- real subject, correct branch count
    ch = dispatch_chains(ix)
    assert len(ch) == 1, ch
    assert ch[0]["subject"] == "o" and ch[0]["n_branches"] == 3, ch[0]
    assert is_dispatch_smell(ch[0], {"type_branches": 3, "value_branches": 15})
    assert not is_dispatch_smell(dict(ch[0], closed_set=True), {"type_branches": 3, "value_branches": 15})
    print("  OCP  chain subject='o' branches=3, smell at bar 3, closed-set suppressed")

    # LSP -- javac-uncheckable signals only
    pairs = override_pairs(ix)
    ren = next(p for p in pairs if p["method"].startswith("render"))
    assert "precondition_strengthened" in ren["signals"], ren
    assert ren["base_id"] == "app.Base"
    assert not any(p for p in pairs if p["method"].startswith("equals"))
    assert len(pairs) == 1, f"only the concrete-base pair is in scope, got {pairs}"
    print(f"  LSP  {len(pairs)} pair in scope; render() flags precondition_strengthened vs app.Base")
    print("       (interface declarations excluded: no bequest to refuse)")

    # ISP
    th = {"fat_members": 6, "min_stubs": 2, "stub_ratio": 0.5}
    crows, irows = contracts(ix, th)
    repo = next(c for c in crows if c["iface_id"] == "app.Repo")
    assert repo["n_members"] == 7 and repo["flagged"], repo
    imp = next(i for i in irows if i["iface_id"] == "app.Repo")
    # save/delete throw, reindex/vacuum are empty -> 4 stubs; load/flush/close do work
    assert imp["n_stubs"] == 4 and imp["coverage"] == 1.0, imp
    assert imp["forced_stub"], imp
    print(f"  ISP  Repo width=7 flagged; ReadOnly stubs=4/7 (2 throw + 2 empty), forced_stub")

    # ISP scope cut: an unimplemented contract must NOT appear
    solo = analyze_tree({"src/main/java/app/Lonely.java":
                         "package app;\npublic interface Lonely { void a(); void b(); void c();"
                         " void d(); void e(); void f(); void g(); }\n"}, use_cache=False)
    c2, _ = contracts(build_index(solo), th)
    assert c2 == [], "contract with no internal implementer must be out of scope"
    print("  ISP  contract with zero internal implementers is correctly out of scope")

    # DIP
    mods = dip_modules(ix)
    assert "app.ReadOnly" in mods
    assert "app.Base" in mods["app.ReadOnly"].imports, mods["app.ReadOnly"].imports
    assert dipgraph.find_cycles(mods) == [], "no cycle in a tree-shaped fixture"
    mm = dipgraph.martin_metrics(mods)
    assert mm, "Martin metrics computed via the validated Python code"
    print(f"  DIP  {len(mods)} modules, edges resolved, Tarjan+Martin reused from dip/graph.py")
    print("ALL PASS")


if __name__ == "__main__":
    _self_check()
