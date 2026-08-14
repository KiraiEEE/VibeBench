"""
SRP metric extraction for a single Python source file.

Three independent, complementary signals, each computed by a well-understood,
turnkey method -- no per-repo configuration, no semantic judgment of "what a
class is responsible for":

  1. lizard   -> per-function cyclomatic complexity (CCN), length, param count,
                 rolled up to per-class WMC (sum of method CCN) and method count.
  2. radon    -> module-level raw metrics (LOC) and Maintainability Index, as a
                 cross-check on lizard's numbers and a file-level LOC signal.
  3. ast LCOM -> a from-scratch LCOM4-style cohesion score per class: build a
                 graph where each method is a node, connect two methods if they
                 share access to the same instance attribute (self.x) or call
                 each other, then LCOM = (connected components - 1) / max(1, methods - 1).
                 0.0 = fully cohesive (one component), approaching 1.0 = split
                 into many unrelated method clusters (classic SRP smell).
  4. ast C3   -> a SEMANTIC cohesion signal: C3-style conceptual cohesion
                 (Marcus & Poshyvanyk, IEEE TSE 2008). Each method becomes a
                 bag of lexical terms (identifiers split on snake/camelCase +
                 docstring words); C3 = mean pairwise cosine similarity of the
                 methods' TF-IDF vectors. 1.0 = methods share one domain
                 vocabulary (cohesive), ~0.0 = methods talk about unrelated
                 things (mixed responsibilities). This variant computes TF-IDF
                 per class with sklearn-style smoothed IDF instead of the
                 paper's corpus-wide LSI -- deterministic, dependency-free, and
                 not an LLM. It complements the structural metrics: a large but
                 single-topic class scores high C3, a grab-bag utility class
                 scores low even when structurally small.

This module has no dependency on the AIDev dataset -- it works on any .py file
and can be reused/tested standalone.
"""
from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass, field

import lizard
from radon.metrics import mi_visit
from radon.raw import analyze as radon_raw


# --------------------------------------------------------------------------
# LCOM (lack of cohesion of methods), computed from the AST.
# --------------------------------------------------------------------------
class _AttrAndCallCollector(ast.NodeVisitor):
    """Collects `self.<attr>` accesses and `self.<method>(...)` calls within one method body."""

    def __init__(self) -> None:
        self.attrs: set[str] = set()
        self.calls: set[str] = set()

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.value, ast.Name) and node.value.id == "self":
            self.attrs.add(node.attr)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        f = node.func
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "self":
            self.calls.add(f.attr)
        self.generic_visit(node)


def _class_methods(cls_node: ast.ClassDef) -> list[ast.FunctionDef]:
    return [
        n for n in cls_node.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


_COHESION_EXCLUDED = {"__init__", "__new__", "__post_init__"}


def compute_lcom(cls_node: ast.ClassDef) -> float | None:
    """LCOM4-style: fraction of (method-connectivity components - 1) over (n-1).

    Constructors (`__init__`/`__new__`/`__post_init__`) are excluded from the
    cohesion graph: they conventionally touch every instance attribute to
    initialize it, which would trivially connect all methods into one
    component and mask real lack-of-cohesion (verified against a synthetic
    3-cluster god class during development -- including __init__ scored
    LCOM=0.0, i.e. "fully cohesive", which is wrong).

    Returns None for classes with < 2 non-constructor methods (not meaningful).
    """
    methods = [m for m in _class_methods(cls_node) if m.name not in _COHESION_EXCLUDED]
    if len(methods) < 2:
        return None

    per_method_attrs: dict[str, set[str]] = {}
    per_method_calls: dict[str, set[str]] = {}
    for m in methods:
        c = _AttrAndCallCollector()
        c.visit(m)
        per_method_attrs[m.name] = c.attrs
        per_method_calls[m.name] = c.calls
    return lcom_from_access_sets([m.name for m in methods],
                                 per_method_attrs, per_method_calls)


def lcom_from_access_sets(names: list[str],
                          attrs: dict[str, set[str]],
                          calls: dict[str, set[str]]) -> float | None:
    """The LCOM4 core: union-find over methods, edge = shared attribute or a
    call between them; LCOM = (components - 1) / (n - 1).

    Factored out (2026-07) so the TypeScript evaluator reuses the exact
    validated logic over `this.X` prop/call facts from the ts-morph sidecar.
    """
    if len(names) < 2:
        return None
    parent = {n: n for n in names}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i, a in enumerate(names):
        for b in names[i + 1:]:
            shares_attr = bool(attrs[a] & attrs[b])
            calls_each_other = b in calls[a] or a in calls[b]
            if shares_attr or calls_each_other:
                union(a, b)

    components = len({find(n) for n in names})
    return (components - 1) / (len(names) - 1)


# --------------------------------------------------------------------------
# C3 conceptual (semantic) cohesion, computed from the AST.
# --------------------------------------------------------------------------
_CAMEL_RE = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")
_WORD_RE = re.compile(r"[A-Za-z]+")
# Terms so ubiquitous in Python they carry no topical signal.
_TERM_STOPWORDS = {
    "self", "cls", "args", "kwargs", "arg", "kwarg", "value", "values",
    "result", "results", "data", "item", "items", "key", "keys", "obj",
    "none", "true", "false", "return", "returns", "the", "and", "for",
    "not", "with", "get", "set", "str", "int", "list", "dict",
}


def _split_identifier(name: str) -> list[str]:
    """'parseHTTPResponse_body' -> ['parse', 'http', 'response', 'body']."""
    parts: list[str] = []
    for chunk in name.split("_"):
        parts.extend(m.group(0).lower() for m in _CAMEL_RE.finditer(chunk))
    return parts


class _TermCollector(ast.NodeVisitor):
    """Collects lexical terms (identifier fragments) from one method body."""

    def __init__(self) -> None:
        self.terms: list[str] = []

    def _add(self, name: str) -> None:
        self.terms.extend(t for t in _split_identifier(name)
                          if len(t) > 2 and t not in _TERM_STOPWORDS)

    def visit_Name(self, node: ast.Name) -> None:
        self._add(node.id)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self._add(node.attr)
        self.generic_visit(node)

    def visit_arg(self, node: ast.arg) -> None:
        self._add(node.arg)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._add(node.name)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]


def _method_terms(m: ast.FunctionDef) -> list[str]:
    c = _TermCollector()
    c.visit(m)
    doc = ast.get_docstring(m)
    if doc:
        for w in _WORD_RE.findall(doc):
            w = w.lower()
            if len(w) > 2 and w not in _TERM_STOPWORDS:
                c.terms.append(w)
    return c.terms


def compute_c3(cls_node: ast.ClassDef) -> float | None:
    """C3-style conceptual cohesion: mean pairwise TF-IDF cosine over methods.

    Constructors are excluded (same rationale as LCOM: they mention every
    collaborator and would inflate similarity). Returns None for classes with
    < 2 non-constructor methods with any terms. Range [0, 1]; higher = more
    semantically cohesive. Uses sklearn-style smoothed IDF
    (log((1+N)/(1+df)) + 1) so vocabulary shared by ALL methods -- the very
    evidence of cohesion -- still contributes instead of being zeroed out.
    """
    methods = [m for m in _class_methods(cls_node) if m.name not in _COHESION_EXCLUDED]
    docs = [t for t in (_method_terms(m) for m in methods) if t]
    return c3_from_token_bags(docs)


def c3_from_token_bags(docs: list[list[str]]) -> float | None:
    """The C3 core: mean pairwise TF-IDF cosine over per-method token bags.

    Factored out (2026-07) so the TypeScript evaluator (srp_ts/run_srp_ts.py)
    reuses the exact validated math over token bags emitted by the ts-morph
    sidecar -- the language-specific part is only how the bags are extracted.
    Returns None for < 2 non-empty bags. Range [0, 1]; higher = more cohesive.
    """
    docs = [t for t in docs if t]
    if len(docs) < 2:
        return None

    n = len(docs)
    df: dict[str, int] = {}
    for terms in docs:
        for t in set(terms):
            df[t] = df.get(t, 0) + 1
    idf = {t: math.log((1 + n) / (1 + d)) + 1.0 for t, d in df.items()}

    vecs: list[dict[str, float]] = []
    for terms in docs:
        tf: dict[str, int] = {}
        for t in terms:
            tf[t] = tf.get(t, 0) + 1
        v = {t: c * idf[t] for t, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values()))
        vecs.append({t: x / norm for t, x in v.items()} if norm else {})

    sims = []
    for i in range(n):
        for j in range(i + 1, n):
            a, b = vecs[i], vecs[j]
            if len(b) < len(a):
                a, b = b, a
            sims.append(sum(x * b.get(t, 0.0) for t, x in a.items()))
    return max(0.0, min(1.0, sum(sims) / len(sims)))


def compute_c3_embed(cls_node: ast.ClassDef, source: str) -> float | None:
    """Embedding variant of C3 (config.SRP_C3_MODE == 'embed'): mean pairwise
    COSINE over per-method CODE EMBEDDINGS instead of TF-IDF token overlap. Sees
    semantic similarity of behaviour (`fetch_page` ~ `download_html`), which the
    lexical TF-IDF variant misses -- the sanctioned SRP precision upgrade for the
    'large but single-topic vs god class' false positive.

    Uses a small LOCAL non-LLM code encoder (03_principles/embeddings.py; default
    GraphCodeBERT), deterministic at inference. Returns None if the encoder is
    unavailable (caller falls back to compute_c3) or the class has < 2
    non-constructor methods. Constructors excluded (same rationale as TF-IDF C3).
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 03_principles
    import embeddings as E  # heavy/optional dep -- imported lazily, only in embed mode

    if not E.available():
        return None
    methods = [m for m in _class_methods(cls_node) if m.name not in _COHESION_EXCLUDED]
    texts = [seg for seg in (ast.get_source_segment(source, m) for m in methods)
             if seg and seg.strip()]
    if len(texts) < 2:
        return None
    return E.mean_pairwise_cosine(E.embed(texts))


# --------------------------------------------------------------------------
# Public result types
# --------------------------------------------------------------------------
@dataclass
class ClassMetrics:
    class_name: str
    lineno: int
    loc: int
    method_count: int
    wmc: int              # sum of per-method cyclomatic complexity
    max_method_ccn: int
    lcom: float | None
    c3: float | None      # conceptual (semantic) cohesion, 1=cohesive 0=mixed
    max_params: int


@dataclass
class FileMetrics:
    path: str
    file_loc: int
    maintainability_index: float | None
    max_func_ccn: int      # over module-level functions AND methods
    max_func_params: int
    classes: list[ClassMetrics] = field(default_factory=list)
    parse_ok: bool = True
    error: str | None = None


def analyze_file(path: str, source: str, c3_mode: str = "tfidf") -> FileMetrics:
    """Compute all SRP-relevant metrics for one Python source file.
    c3_mode: "tfidf" (default, deterministic, no deps) or "embed" (per-method
    code embeddings; run_srp only passes "embed" when the encoder is available)."""
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        return FileMetrics(
            path=path, file_loc=0, maintainability_index=None,
            max_func_ccn=0, max_func_params=0, parse_ok=False, error=str(e),
        )

    # --- lizard: per-function complexity/length/params ---
    # NOTE: lizard's long_name/name carry no class qualification (e.g. just
    # "method_one(...)", not "Foo.method_one(...)"), so functions are matched
    # to their enclosing class by AST line-range containment, not by name.
    liz = lizard.analyze_file.analyze_source_code(path, source)

    # --- radon: file-level LOC + Maintainability Index ---
    try:
        raw = radon_raw(source)
        file_loc = raw.loc
    except Exception:
        file_loc = source.count("\n") + 1
    try:
        mi = mi_visit(source, multi=True)
    except Exception:
        mi = None

    # Match each lizard function to its AST node by (name, start_line) -- lizard's
    # start_line is the `def` line, same as ast.FunctionDef.lineno (decorators excluded).
    liz_by_key = {(f.name, f.start_line): f for f in liz.function_list}

    classes: list[ClassMetrics] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        methods = _class_methods(node)
        method_ccns, method_params = [], []
        for m in methods:
            key = (m.name, m.lineno)
            fi = liz_by_key.get(key)
            if fi is not None:
                method_ccns.append(fi.cyclomatic_complexity)
                method_params.append(len(fi.parameters))
        end_line = getattr(node, "end_lineno", node.lineno)
        classes.append(ClassMetrics(
            class_name=node.name,
            lineno=node.lineno,
            loc=(end_line - node.lineno + 1),
            method_count=len(methods),
            wmc=sum(method_ccns) if method_ccns else 0,
            max_method_ccn=max(method_ccns) if method_ccns else 0,
            lcom=compute_lcom(node),
            c3=(compute_c3_embed(node, source) if c3_mode == "embed"
                else compute_c3(node)),
            max_params=max(method_params) if method_params else 0,
        ))

    all_ccns = [f.cyclomatic_complexity for f in liz.function_list]
    all_params = [len(f.parameters) for f in liz.function_list]

    return FileMetrics(
        path=path,
        file_loc=file_loc,
        maintainability_index=mi,
        max_func_ccn=max(all_ccns) if all_ccns else 0,
        max_func_params=max(all_params) if all_params else 0,
        classes=classes,
    )


# ---------------------------------------------------------------------------
# Self-check (added 2026-08-01). CLAUDE.md had documented this file as
# self-checking since the LCOM work, but no `__main__` block existed and the
# script exited silently -- so the one evaluator whose cores were factored out
# for the TypeScript port (c3_from_token_bags, lcom_from_access_sets) was the
# only one with no guard. Run: python vibebench/03_principles/srp/metrics.py
# ---------------------------------------------------------------------------
_GOD = '''
class ReportManager:
    def __init__(self):
        self.conn = None
        self.smtp = None
        self.cache = {}
    def connect_db(self):
        self.conn = object()
    def query_rows(self, sql):
        return self.conn and sql
    def close_db(self):
        self.conn = None
    def send_email(self, to, body):
        self.smtp.sendmail(to, body)
    def format_html(self, rows):
        return "<table>" + str(rows) + "</table>"
    def render_pdf(self, html):
        return html.encode()
'''

_COHESIVE = '''
class Rectangle:
    def __init__(self, width, height):
        self.width = width
        self.height = height
    def area(self):
        return self.width * self.height
    def perimeter(self):
        return 2 * (self.width + self.height)
    def scale(self, factor):
        self.width = self.width * factor
        self.height = self.height * factor
'''


def _self_check() -> None:
    def die(msg):
        print("SELF-CHECK FAIL:", msg)
        raise SystemExit(1)

    god = analyze_file("god.py", _GOD).classes[0]
    coh = analyze_file("cohesive.py", _COHESIVE).classes[0]

    # LCOM: the god class shares almost no state between its method groups;
    # the cohesive one touches the same two attributes throughout.
    if god.lcom is None or coh.lcom is None:
        die("lcom returned None on both fixtures")
    if not god.lcom > coh.lcom:
        die(f"god lcom {god.lcom} must exceed cohesive {coh.lcom}")

    # C3: conceptual cohesion is the semantic axis; the god class mixes db /
    # email / rendering vocabularies, the cohesive one speaks only geometry.
    if god.c3 is None or coh.c3 is None:
        die("c3 returned None on both fixtures")
    if not coh.c3 > god.c3:
        die(f"cohesive c3 {coh.c3} must exceed god {god.c3}")

    # The factored-out cores must behave the same when called directly -- they
    # are what the TypeScript sidecar reuses, so a regression here breaks BOTH
    # languages silently.
    empty = {"a": set(), "b": set()}
    disjoint = lcom_from_access_sets(["a", "b"], {"a": {"x"}, "b": {"y"}}, empty)
    shared = lcom_from_access_sets(["a", "b"], {"a": {"x"}, "b": {"x"}}, empty)
    if disjoint is None or shared is None or not disjoint > shared:
        die(f"lcom_from_access_sets: disjoint {disjoint} must exceed shared {shared}")

    far = c3_from_token_bags([["socket", "port", "bind"], ["pixel", "canvas", "draw"]])
    near = c3_from_token_bags([["socket", "port", "bind"], ["socket", "port", "listen"]])
    if far is None or near is None or not near > far:
        die(f"c3_from_token_bags: related {near} must exceed unrelated {far}")

    print(f"metrics.py self-check OK: god(lcom={god.lcom:.3f} c3={god.c3:.3f} "
          f"wmc={god.wmc}) vs cohesive(lcom={coh.lcom:.3f} c3={coh.c3:.3f} "
          f"wmc={coh.wmc}); factored LCOM+C3 cores agree")


if __name__ == "__main__":
    _self_check()
