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
Java structural FACT extractor (tree-sitter), pure Python.

Same architecture as the TypeScript port: this module emits STRUCTURAL FACTS
only, and every metric -- C3 cohesion, LCOM, the rubric -- is computed by the
already-validated cores in `srp/metrics.py`. Nothing is re-implemented, so a
number proven in Python transfers to Java rather than needing re-proving.

WHY tree-sitter AND NOT the tools you would expect
--------------------------------------------------
Our analysis runs on bare `git archive` trees: no `mvn`, no `gradle`, no
classpath, no compiled `.class` files. That single constraint eliminates most
Java tooling:

  ArchUnit    reads BYTECODE off a classpath              -> needs a build
  JDepend     reads `.class` files                        -> needs a build
  SonarQube   server + project config                     -> not headless-batch
  PMD         source-based, but lint rules != structural facts
  javalang    pure Python, source-based, BUT measured 2026-08-02: fails on
              records, sealed types, text blocks, enhanced switch and pattern
              matching (Java 14+). Our targets are libraries maintained in
              2026, so the parse-error rate would be unacceptable.

  tree-sitter-java  parsed 7/7 modern constructs cleanly, needs no JVM, and is
                    actively maintained.  <-- CHOSEN

Note this REVERSES the decision made for TypeScript, where tree-sitter was
rejected because it carries no type information and that would have crippled
LSP and ISP. The reasoning does not transfer: Java states structurally what
TypeScript could only express through a type checker --

    `implements` / `extends`   explicit          -> ISP + LSP hierarchy
    `@Override`                explicit          -> exact override detection
    `interface`                unambiguous       -> no "82.6% are data shapes"
    `import a.b.C`             fully qualified   -> DIP graph without guessing
    method params              INVARIANT         -> no TypeScript bivariance trap

Self-check: python vibebench/03_principles/java_lang/javafacts.py
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import tree_sitter_java as tsjava
from tree_sitter import Language, Parser

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1] / "srp"))

_LANG = Language(tsjava.language())
_PARSER = Parser(_LANG)

# Decision points that add one to cyclomatic complexity. Mirrors what lizard
# counts for Python/TypeScript so CCN means the same thing in all three
# languages: branches, loops, catches, and each boolean operator.
_CCN_NODES = {
    "if_statement", "for_statement", "enhanced_for_statement", "while_statement",
    "do_statement", "catch_clause", "switch_block_statement_group",
    "switch_rule", "ternary_expression", "lambda_expression",
}
_CCN_OPERATORS = {"&&", "||"}

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass
class MethodFacts:
    name: str
    params: int
    ccn: int
    loc: int
    is_abstract: bool          # no body: interface method or `abstract`
    is_constructor: bool
    is_static: bool
    annotations: list[str]
    param_types: list[str]
    return_type: str
    fields: set[str] = field(default_factory=set)   # `this.x` / bare field reads
    calls: set[str] = field(default_factory=set)    # sibling methods invoked
    tokens: list[str] = field(default_factory=list) # identifier bag for C3
    body_stmts: int = 0
    throws_only: bool = False   # body is a single `throw` -> refused bequest


@dataclass
class TypeFacts:
    name: str
    kind: str                  # class | interface | enum | record
    lineno: int
    loc: int
    is_abstract: bool
    extends: str | None
    implements: list[str]
    fields: list[str]
    methods: list[MethodFacts]


@dataclass
class FileFacts:
    path: str
    package: str
    imports: list[str]
    types: list[TypeFacts]
    parse_ok: bool
    parse_error: str = ""


def _txt(src: bytes, node) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _child(node, field_name):
    return node.child_by_field_name(field_name)


def _modifiers(src: bytes, node) -> tuple[set[str], list[str]]:
    """(modifier keywords, @Annotation names) for a declaration node."""
    mods, anns = set(), []
    for c in node.children:
        if c.type != "modifiers":
            continue
        for m in c.children:
            if m.type in ("marker_annotation", "annotation"):
                nm = _child(m, "name")
                anns.append(_txt(src, nm) if nm else _txt(src, m).lstrip("@"))
            else:
                mods.add(_txt(src, m))
    return mods, anns


def _count_ccn(node) -> int:
    """Cyclomatic complexity: 1 + decision points, counted over the subtree."""
    n = 1
    stack = [node]
    while stack:
        cur = stack.pop()
        if cur.type in _CCN_NODES:
            n += 1
        elif cur.type == "binary_expression":
            op = cur.child_by_field_name("operator")
            if op is not None and op.type in _CCN_OPERATORS:
                n += 1
        stack.extend(cur.children)
    return n


def _method_facts(src: bytes, node, class_fields: set[str],
                  sibling_names: set[str], kind_is_interface: bool) -> MethodFacts:
    name_n = _child(node, "name")
    name = _txt(src, name_n) if name_n else "<anon>"
    body = _child(node, "body")
    mods, anns = _modifiers(src, node)

    params_n = _child(node, "parameters")
    ptypes: list[str] = []
    if params_n is not None:
        for p in params_n.children:
            if p.type in ("formal_parameter", "spread_parameter"):
                t = _child(p, "type")
                ptypes.append(_txt(src, t) if t else "")
    ret_n = _child(node, "type")
    ret = _txt(src, ret_n) if ret_n else ""

    fields_used, calls, tokens = set(), set(), []
    stmts = 0
    throws_only = False
    if body is not None:
        # statement count: direct children of the block that are statements
        stmts = sum(1 for c in body.children
                    if c.type.endswith("_statement") or c.type.endswith("_declaration"))
        real = [c for c in body.children
                if c.type not in ("{", "}", "line_comment", "block_comment")]
        throws_only = len(real) == 1 and real[0].type == "throw_statement"

        stack = [body]
        while stack:
            cur = stack.pop()
            if cur.type == "field_access":
                obj, fld = _child(cur, "object"), _child(cur, "field")
                if obj is not None and fld is not None and _txt(src, obj) == "this":
                    fields_used.add(_txt(src, fld))
            elif cur.type == "identifier":
                t = _txt(src, cur)
                if t in class_fields:
                    fields_used.add(t)
            elif cur.type == "method_invocation":
                m = _child(cur, "name")
                if m is not None:
                    mn = _txt(src, m)
                    if mn in sibling_names:
                        calls.add(mn)
            stack.extend(cur.children)
        tokens = [t.lower() for t in _IDENT_RE.findall(_txt(src, body))]

    # An interface method or one marked `abstract` has no body to judge.
    is_abstract = body is None or "abstract" in mods or (kind_is_interface and body is None)
    return MethodFacts(
        name=name,
        params=len(ptypes),
        ccn=_count_ccn(body) if body is not None else 0,
        loc=(node.end_point[0] - node.start_point[0] + 1),
        is_abstract=is_abstract,
        is_constructor=(node.type == "constructor_declaration"),
        is_static="static" in mods,
        annotations=anns,
        param_types=ptypes,
        return_type=ret,
        fields=fields_used,
        calls=calls,
        tokens=tokens,
        body_stmts=stmts,
        throws_only=throws_only,
    )


_TYPE_NODES = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
}


def _type_facts(src: bytes, node, kind: str) -> TypeFacts:
    name_n = _child(node, "name")
    mods, _ = _modifiers(src, node)

    sup = _child(node, "superclass")
    extends = None
    if sup is not None:
        extends = re.sub(r"^extends\s+", "", _txt(src, sup)).split("<")[0].strip()

    impls: list[str] = []
    for fname in ("interfaces", "super_interfaces"):
        it = _child(node, fname)
        if it is not None:
            raw = re.sub(r"^(implements|extends)\s+", "", _txt(src, it))
            impls += [x.split("<")[0].strip() for x in raw.split(",") if x.strip()]

    body = _child(node, "body")
    fields: list[str] = []
    method_nodes = []
    if body is not None:
        for c in body.children:
            if c.type == "field_declaration":
                for d in c.children:
                    if d.type == "variable_declarator":
                        vn = _child(d, "name")
                        if vn is not None:
                            fields.append(_txt(src, vn))
            elif c.type in ("method_declaration", "constructor_declaration"):
                method_nodes.append(c)

    fset = set(fields)
    names = {(_txt(src, _child(m, "name")) if _child(m, "name") else "") for m in method_nodes}
    methods = [_method_facts(src, m, fset, names, kind == "interface")
               for m in method_nodes]

    return TypeFacts(
        name=_txt(src, name_n) if name_n else "<anon>",
        kind=kind,
        lineno=node.start_point[0] + 1,
        loc=(node.end_point[0] - node.start_point[0] + 1),
        is_abstract=("abstract" in mods) or kind == "interface",
        extends=extends,
        implements=impls,
        fields=fields,
        methods=methods,
    )


def analyze_source(path: str, source: str) -> FileFacts:
    """Parse one .java file into structural facts. Never raises."""
    try:
        src = source.encode("utf-8", "replace")
        tree = _PARSER.parse(src)
        root = tree.root_node

        package, imports = "", []
        types: list[TypeFacts] = []
        stack = [root]
        while stack:
            cur = stack.pop()
            if cur.type == "package_declaration":
                package = _txt(src, cur).replace("package", "").strip(" ;\n\t")
            elif cur.type == "import_declaration":
                imports.append(_txt(src, cur).replace("import", "")
                               .replace("static", "").strip(" ;\n\t"))
            elif cur.type in _TYPE_NODES:
                types.append(_type_facts(src, cur, _TYPE_NODES[cur.type]))
                continue          # nested types are folded into the outer one
            stack.extend(cur.children)

        return FileFacts(path=path, package=package, imports=imports, types=types,
                         parse_ok=not root.has_error)
    except Exception as e:  # noqa: BLE001 -- one bad file must not stop a run
        return FileFacts(path=path, package="", imports=[], types=[],
                         parse_ok=False, parse_error=f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
def _self_check() -> None:
    from metrics import c3_from_token_bags, lcom_from_access_sets  # noqa: E402

    def die(m):
        print("SELF-CHECK FAIL:", m); raise SystemExit(1)

    god = analyze_source("God.java", """
package com.example;
import java.sql.Connection;
import com.example.mail.Smtp;
public class ReportManager {
    private Connection conn;
    private Smtp smtp;
    public void connectDb(String url) { this.conn = open(url); }
    public String queryRows(String sql) { return this.conn.exec(sql); }
    public void sendEmail(String to, String body) { this.smtp.send(to, body); }
    public String renderHtml(java.util.List rows) { return "<table>" + rows + "</table>"; }
}""")
    coh = analyze_source("Rect.java", """
package com.example;
public class Rectangle {
    private int width; private int height;
    public int area() { return this.width * this.height; }
    public int perimeter() { return 2 * (this.width + this.height); }
    public void scale(int f) { this.width = this.width * f; this.height = this.height * f; }
}""")
    modern = analyze_source("Modern.java", """
package com.example;
public sealed interface Shape permits Circle {}
record Point(int x, int y) {}
class Circle implements Shape {
    String describe(Object o) {
        if (o instanceof String s) { return s; }
        return switch (o.hashCode()) { case 1 -> "one"; default -> "other"; };
    }
}""")
    hier = analyze_source("Hier.java", """
package com.example;
public interface Storage { void save(String k, String v); String load(String k); }
public abstract class BaseService implements Storage {
    public abstract void process(java.util.List<String> items);
    public void save(String k, String v) { store(k, v); }
    public String load(String k) { return "x"; }
    void store(String k, String v) {}
}
class ReadOnly extends BaseService {
    @Override public void save(String k, String v) { throw new UnsupportedOperationException(); }
    @Override public void process(java.util.List<String> items) {}
}""")

    for f in (god, coh, modern, hier):
        if not f.parse_ok:
            die(f"{f.path} failed to parse: {f.parse_error}")

    # --- package + imports (DIP inputs) ---
    if god.package != "com.example":
        die(f"package, got {god.package!r}")
    if "java.sql.Connection" not in god.imports:
        die(f"imports, got {god.imports}")

    # --- modern syntax survives (the reason javalang was rejected) ---
    kinds = {t.name: t.kind for t in modern.types}
    if kinds.get("Point") != "record" or kinds.get("Shape") != "interface":
        die(f"modern declarations, got {kinds}")

    # --- hierarchy facts (LSP + ISP inputs) ---
    by = {t.name: t for t in hier.types}
    if by["Storage"].kind != "interface" or not by["Storage"].is_abstract:
        die("interface not recognised")
    if by["BaseService"].implements != ["Storage"]:
        die(f"implements, got {by['BaseService'].implements}")
    if by["ReadOnly"].extends != "BaseService":
        die(f"extends, got {by['ReadOnly'].extends}")
    ro_save = next(m for m in by["ReadOnly"].methods if m.name == "save")
    if "Override" not in ro_save.annotations:
        die(f"@Override not seen, got {ro_save.annotations}")
    if not ro_save.throws_only:
        die("refused-bequest body (sole throw) not detected")
    if not next(m for m in by["Storage"].methods if m.name == "save").is_abstract:
        die("interface method should be abstract")

    # --- the VALIDATED Python cores must consume these facts unchanged ---
    g = next(t for t in god.types if t.name == "ReportManager")
    c = next(t for t in coh.types if t.name == "Rectangle")

    def lcom(t):
        ms = [m for m in t.methods if not m.is_constructor]
        return lcom_from_access_sets([m.name for m in ms],
                                     {m.name: m.fields for m in ms},
                                     {m.name: m.calls for m in ms})

    def c3(t):
        return c3_from_token_bags([m.tokens for m in t.methods if not m.is_constructor])

    gl, cl = lcom(g), lcom(c)
    gc, cc = c3(g), c3(c)
    if gl is None or cl is None or not gl > cl:
        die(f"LCOM: god {gl} must exceed cohesive {cl}")
    if gc is None or cc is None or not cc > gc:
        die(f"C3: cohesive {cc} must exceed god {gc}")

    print(f"javafacts.py self-check OK: tree-sitter-java parses modern syntax; "
          f"god(lcom={gl:.3f} c3={gc:.3f}) vs cohesive(lcom={cl:.3f} c3={cc:.3f}); "
          f"@Override/implements/extends/abstract extracted; "
          f"validated Python C3+LCOM cores consume the facts unchanged")


if __name__ == "__main__":
    _self_check()
