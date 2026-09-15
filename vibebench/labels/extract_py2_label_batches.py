"""
Dump the source a judge needs to label a Python V2 sample, one batch file per row.

Mirrors extract_java_label_batches.py, whose rules were each established the
hard way:
  * LSP is a RELATION: the override AND its base method, side by side.
  * OCP is open versus closed: a branch count says nothing, so the WHOLE
    enclosing function is shown with the dispatch chain marked `>>>`.
  * ISP needs the contract AND its implementers, to see who is forced to stub.
  * DIP needs the pairs that became cyclic in modules the PR really changed, and
    the NEW import edges with each target marked abstract or CONCRETE, computed
    as a DIFFERENCE against the before tree (the Java extractor once printed the
    whole repository graph as though the PR had created it).
  * SRP needs the whole class body.

BLINDING. No detector verdict, hit list, signal list or outcome is ever printed.
The judge reads only this file, never the sample CSV, which carries the
prediction column.

Python-specific:
  * rows name MODULES; each tree is mapped module -> file with the same
    `path_to_module` the detectors used, so the file shown is the one scored.
  * entities are located with `ast` in the reconstructed AFTER tree (the tree
    the detector scored), and a window that does not contain the entity is
    reported loudly instead of printing plausible but wrong code.
  * the git environment matches the recovery pass: LFS downloads skipped (no
    `.py` file is LFS-tracked in any affected repository) and a longer timeout.

A row whose entity cannot be located is written as UNJUDGEABLE, never as a
header with no body, and `_manifest.json` is written LAST, only on a clean exit.

Run:  python vibebench/labels/extract_py2_label_batches.py --principle ocp
      ... [--only-missing]
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from collections import OrderedDict
from pathlib import Path

os.environ["GIT_LFS_SKIP_SMUDGE"] = "1"

import pandas as pd  # noqa: E402

_HERE = Path(__file__).resolve()
_P = _HERE.parents[1] / "03_principles"
for _d in (_HERE.parents[1], _P, _P / "dip", _P / "isp", _P / "lsp"):
    sys.path.insert(0, str(_d))
import config as C            # noqa: E402
import gitsnap                # noqa: E402
import graph as DIP           # noqa: E402
import interfaces as ISP      # noqa: E402

C.GIT_TIMEOUT_S = 1800
PRINCIPLES = ["srp", "ocp", "lsp", "isp", "dip"]
_GOLD: pd.DataFrame | None = None
_PRS: pd.DataFrame | None = None


def gold_row(full_name: str, pr_number: int):
    global _GOLD
    if _GOLD is None:
        _GOLD = pd.read_parquet(C.GOLD_SET)
    g = _GOLD[(_GOLD.full_name == full_name) & (_GOLD.pr_number == pr_number)]
    return g.iloc[0] if len(g) else None


def pr_row(full_name: str, pr_number: int):
    global _PRS
    if _PRS is None:
        _PRS = pd.read_parquet(C.CORPUS_DIR / "py_pr_results.parquet")
    r = _PRS[(_PRS.full_name == full_name) & (_PRS.pr_number == pr_number)]
    return r.iloc[0] if len(r) else None


def trees(full_name: str, pr_number: int) -> tuple[dict | None, dict | None]:
    """(before, after) using the SHAs the flagged pass recorded, so the batch
    shows exactly the pair that was scored rather than re-deriving it."""
    pr = pr_row(full_name, pr_number)
    if pr is None or pd.isna(pr.get("after_sha")) or pd.isna(pr.get("before_sha")):
        return None, None
    d = gitsnap.ensure_repo(full_name)
    with gitsnap.repo_lock(full_name):
        bt = gitsnap.src_tree(d, pr.before_sha, C.SOURCE_EXTENSIONS, (), full_name)
        at = gitsnap.src_tree(d, pr.after_sha, C.SOURCE_EXTENSIONS, (), full_name)
    if at is None or gitsnap.TOO_LARGE in at:
        return None, None
    if bt is not None and gitsnap.TOO_LARGE in bt:
        bt = None
    return bt, at


def module_map(tree: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in tree:
        m = DIP.path_to_module(p)
        if m and m not in out:
            out[m] = p
    return out


def span(src: str, first: int, last: int, mark: int | None = None,
         cap: int = 400) -> str:
    """Numbered lines [first, last] (1-based). `mark` gets a `>>>` gutter."""
    lines = src.splitlines()
    if first < 1 or first > len(lines):
        return f"<BAD WINDOW: line {first} outside the file ({len(lines)} lines), do not judge>"
    last = min(max(last, first), len(lines))
    if mark is not None and not (first <= mark <= last):
        first, last = max(1, mark - 40), min(len(lines), mark + 40)
    out = []
    for i in range(first, last + 1):
        g = ">>>" if mark is not None and i == mark else "   "
        out.append(f"{g}{i:>6}  {lines[i - 1]}")
        if len(out) >= cap:
            out.append(f"       ... truncated at {cap} lines ...")
            break
    return "\n".join(out)


def parse(src: str):
    try:
        return ast.parse(src)
    except (SyntaxError, ValueError):
        return None


def find_class(tree_ast, name: str, lineno: int | None = None):
    best = None
    for n in ast.walk(tree_ast):
        if isinstance(n, ast.ClassDef) and n.name == name.split(".")[-1]:
            if lineno is None or n.lineno == lineno:
                return n
            best = best or n
    return best


def find_method(cls: ast.ClassDef, name: str):
    for n in cls.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def node_first(n) -> int:
    decos = getattr(n, "decorator_list", None) or []
    return min([n.lineno] + [x.lineno for x in decos])


def enclosing_function(tree_ast, lineno: int):
    best = None
    for n in ast.walk(tree_ast):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if n.lineno <= lineno <= (n.end_lineno or n.lineno):
                if best is None or (n.end_lineno - n.lineno) < (best.end_lineno - best.lineno):
                    best = n
    return best


def render(p: str, r, before: dict | None, after: dict) -> tuple[list[str], list[str]]:
    head = [f"row {int(r.name)}   {r.full_name}#{int(r.pr_number)}", "=" * 76]
    body: list[str] = []
    mm = module_map(after)

    if p == "srp":
        src = after.get(r.file_path)
        head.append(f"CLASS {r.class_name} in {r.file_path}   loc={r.get('class_loc')} "
                    f"methods={r.get('method_count')} wmc={r.get('wmc')} "
                    f"lcom={r.get('lcom')}")
        head.append("QUESTION: does this class have MORE THAN ONE reason to change?")
        t = parse(src) if src else None
        c = find_class(t, r.class_name, int(r.lineno)) if t else None
        if c is not None:
            body.append(span(src, node_first(c), c.end_lineno))

    elif p == "ocp":
        path = mm.get(r.module)
        src = after.get(path) if path else None
        head.append(f"CHAIN in {r.module}.{r.qualname}   kind={r.kind} "
                    f"subject={r.subject!r} branches={r.n_branches}")
        head.append("QUESTION: is the set of variants OPEN (a new one is expected, and")
        head.append("          would force editing this chain) or CLOSED by nature?")
        head.append("SHOWING THE WHOLE ENCLOSING FUNCTION -- a branch count alone cannot answer this.")
        head.append(">>> marks the first line of the dispatch chain itself.")
        L = int(r.lineno)
        # Two files can map to one module name (a src/ layout beside a copy),
        # and module_map keeps the first. A line number is only meaningful in
        # the file the detector read, so prefer the candidate whose enclosing
        # function carries the chain's own name (row 114 once showed another
        # class's __call__ at the right line of the wrong file).
        want = str(r.qualname).split(".")[-1]
        for cand in sorted(p_ for p_ in after if DIP.path_to_module(p_) == r.module):
            ct = parse(after[cand])
            cf = enclosing_function(ct, L) if ct is not None else None
            if cf is not None and cf.name == want:
                path, src = cand, after[cand]
                break
        t = parse(src) if src else None
        if t is not None:
            fn = enclosing_function(t, L)
            if fn is not None:
                body.append(f"file {path}")
                body.append(span(src, node_first(fn), fn.end_lineno, mark=L))
            else:
                body.append(f"file {path} (chain at module level)")
                body.append(span(src, max(1, L - 30), L + 60, mark=L))

    elif p == "lsp":
        sub_path = mm.get(r.module)
        base_mod, base_cls = str(r.base_id).split(":", 1)
        base_path = mm.get(base_mod)
        head.append(f"OVERRIDE  {r.module}.{r.qualname}.{r.method}")
        head.append(f"BASE      {base_mod}.{base_cls}.{r.method}")
        head.append("QUESTION: can an instance of the SUBCLASS be used anywhere the BASE")
        head.append("          is expected, without breaking the caller?")
        bsrc = after.get(base_path) if base_path else None
        ssrc = after.get(sub_path) if sub_path else None
        bt, stt = (parse(bsrc) if bsrc else None), (parse(ssrc) if ssrc else None)
        bc = find_class(bt, base_cls) if bt else None
        sc = find_class(stt, r.qualname) if stt else None
        bm = find_method(bc, r.method) if bc is not None else None
        sm = find_method(sc, r.method) if sc is not None else None
        if bm is not None and sm is not None:
            body.append(f"---------------- BASE CLASS HEADER ({base_path}) ----------------")
            body.append(span(bsrc, node_first(bc), bc.lineno))
            body.append("---------------- BASE METHOD ----------------")
            body.append(span(bsrc, node_first(bm), bm.end_lineno))
            body.append(f"\n---------------- SUBCLASS HEADER ({sub_path}) ----------------")
            body.append(span(ssrc, node_first(sc), sc.lineno))
            body.append("---------------- OVERRIDE ----------------")
            body.append(span(ssrc, node_first(sm), sm.end_lineno))

    elif p == "isp":
        imod, icls = str(r.iface_id).split(":", 1)
        path = mm.get(imod)
        src = after.get(path) if path else None
        head.append(f"CONTRACT {r.iface_id}   kind={r.get('kind')} "
                    f"abstract_methods={r.get('n_abstract_methods')} "
                    f"implementers={r.get('n_implementers')}")
        head.append("QUESTION: are implementers or clients FORCED to depend on members")
        head.append("          they do not use (a contract too wide for its users)?")
        t = parse(src) if src else None
        c = find_class(t, icls) if t else None
        if c is not None:
            body.append(f"file {path}")
            body.append(span(src, node_first(c), c.end_lineno, cap=200))
            idx = ISP.build_class_index(after)
            ifaces = ISP.find_interfaces(idx, C.ISP_IMPLICIT_MIN_STUBS)
            if r.iface_id in ifaces:
                members = set(ifaces[r.iface_id].abstract)
                stats = [s for s in ISP.implementer_stats(idx, ifaces)
                         if s.iface_id == r.iface_id]
                # Each implementer is shown as its class header plus ONLY the
                # methods that implement a member of this contract. Whole classes
                # ran to ~800 lines per batch and buried the one thing ISP asks:
                # how does each implementer deal with each member it was handed?
                for s in stats[:6]:
                    jmod, jcls = s.impl_id.split(":", 1)
                    jp = mm.get(jmod)
                    js = after.get(jp) if jp else None
                    jt = parse(js) if js else None
                    jc = find_class(jt, jcls) if jt else None
                    body.append(f"\n---------- IMPLEMENTER {s.impl_id}  "
                                f"stubs={s.n_stubbed}/{s.n_iface_methods} ----------")
                    if jc is None:
                        continue
                    body.append(span(js, node_first(jc), jc.lineno))
                    impl_ms = [n for n in jc.body
                               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                               and n.name in members]
                    for n in impl_ms:
                        body.append(span(js, node_first(n), n.end_lineno, cap=60))
                    missing = sorted(members - {n.name for n in impl_ms})
                    if missing:
                        body.append(f"      (does not define: {', '.join(missing)}; "
                                    f"inherited or absent)")
                if len(stats) > 6:
                    body.append(f"\n({len(stats) - 6} further implementers not shown)")
                if not stats:
                    body.append("\n(no internal implementers found in this tree)")

    elif p == "dip":
        head.append(f"PR {r.full_name}#{int(r.pr_number)}   cycles touched "
                    f"before={r.get('before_touched_cycles')} "
                    f"after={r.get('after_touched_cycles')}")
        head.append("QUESTION: does this PR make a module depend on a CONCRETE module in a")
        head.append("          way that closes an import cycle, or otherwise invert the")
        head.append("          dependency direction?")
        before = before or {}
        amods, bmods = DIP.build_module_graph(after), DIP.build_module_graph(before)
        apairs = DIP.cyclic_pairs(DIP.find_cycles(amods))
        bpairs = DIP.cyclic_pairs(DIP.find_cycles(bmods)) if bmods else set()
        # "changed" = the module's file TEXT differs between the two trees, not
        # the declared touched list: in Java the only systematic DIP false
        # positive was a module declared touched whose text was identical.
        changed = {m for m, pth in mm.items() if after.get(pth) != before.get(pth)}
        new = apairs - bpairs
        charged = sorted(q for q in new if q[0] in changed or q[1] in changed)
        # the set is built ONCE: rebuilt per element this was quadratic and
        # hung for 30 minutes on a transformers-sized SCC
        charged_set = set(charged)
        other = [q for q in new if q not in charged_set]
        if charged:
            body.append(f"NEWLY CYCLIC PAIRS involving a module this PR changed ({len(charged)}):")
            for u, v in charged[:20]:
                body.append(f"    {u}  <->  {v}")
            if len(charged) > 20:
                body.append(f"    ... and {len(charged) - 20} more")
        else:
            body.append("NO newly cyclic pair involves a module this PR changed.")
        if other:
            body.append(f"({len(other)} other pairs became cyclic, in modules this PR did not change.)")
        body.append(f"pre-existing cyclic pairs (NOT this PR): {len(bpairs)}")
        bedges = {(m, t) for m, mi in bmods.items() for t in mi.imports}
        body.append("\nNEW IMPORT EDGES (absent from the before tree; target marked "
                    "abstract or CONCRETE):")
        n_new = 0
        for m, mi in sorted(amods.items()):
            for tgt in sorted(mi.imports):
                if (m, tgt) in bedges or tgt not in amods:
                    continue
                kind = "abstract" if amods[tgt].n_abstract_classes else "CONCRETE"
                body.append(f"  {m} -> {tgt}   [{kind}]")
                n_new += 1
                if n_new >= 60:
                    break
            if n_new >= 60:
                body.append("  ... truncated at 60 edges")
                break
        if not n_new:
            body.append("  (none: this PR added no import edge between internal modules)")
        # the changed code itself, so the judge can see WHY an edge exists
        shown = 0
        for q in charged[:3]:
            for m in q:
                pth = mm.get(m)
                if m in changed and pth and shown < 2:
                    body.append(f"\n---------- CHANGED MODULE {m} ({pth}) ----------")
                    body.append(span(after[pth], 1, 120, cap=120))
                    shown += 1
    return head, body


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--principle", required=True, choices=PRINCIPLES)
    ap.add_argument("--round", default="")
    ap.add_argument("--only-missing", action="store_true")
    a = ap.parse_args()

    csv = C.LABELS_DIR / f"py2_{a.principle}_labeling_sample{a.round}.csv"
    df = pd.read_csv(csv)
    out = C.LABELS_DIR / f"py2_{a.principle}_batches{a.round}"
    out.mkdir(parents=True, exist_ok=True)
    man = out / "_manifest.json"
    if man.exists():
        man.unlink()          # a stale manifest must never certify a new run

    have: set[int] = set()
    if a.only_missing:
        for fn in out.iterdir():
            m = re.match(r"^(\d+)_", fn.name)
            if m:
                have.add(int(m.group(1)))
        print(f"[extract] {len(have)} batches on disk, extracting {len(df) - len(have)}")

    CACHE_MAX = 4
    cache: "OrderedDict[tuple, tuple]" = OrderedDict()
    n_written = n_empty = n_unavail = 0
    for i, r in df.iterrows():
        if i in have:
            continue
        key = (r.full_name, int(r.pr_number))
        if key in cache:
            cache.move_to_end(key)
        else:
            cache[key] = trees(*key)
            while len(cache) > CACHE_MAX:
                cache.popitem(last=False)
        before, after = cache[key]
        slug_src = (r.get("class_name") if a.principle in ("srp",) else
                    r.get("qualname") if a.principle in ("ocp", "lsp") else
                    r.get("iface_id") if a.principle == "isp" else r.full_name)
        slug = re.sub(r"[^0-9A-Za-z_-]", "_", str(slug_src))[:60]
        if after is None:
            (out / f"{i:03d}_UNAVAILABLE_{slug}.txt").write_text(
                f"row {i}   {r.full_name}#{int(r.pr_number)}\n"
                f"tree unavailable, DO NOT JUDGE THIS ROW\n", encoding="utf-8")
            n_unavail += 1
            continue
        head, body = render(a.principle, r, before, after)
        text = "\n".join(body).strip()
        if not text or "<BAD WINDOW" in text:
            (out / f"{i:03d}_UNJUDGEABLE_{slug}.txt").write_text(
                "\n".join(head) + "\n\n<entity not located in the scored tree, "
                "DO NOT JUDGE THIS ROW>\n", encoding="utf-8")
            n_empty += 1
            continue
        (out / f"{i:03d}_{slug}.txt").write_text("\n".join(head + [""] + body),
                                                encoding="utf-8")
        n_written += 1
        if (n_written + n_empty + n_unavail) % 25 == 0:
            print(f"  {i + 1}/{len(df)}", flush=True)

    man.write_text(json.dumps({
        "sample_file": csv.name, "rows": int(len(df)), "written": n_written,
        "unjudgeable": n_empty, "unavailable": n_unavail, "complete": True,
    }, indent=2), encoding="utf-8")
    print(f"{a.principle}: wrote {n_written}, unjudgeable {n_empty}, "
          f"unavailable {n_unavail} -> {out.name}")


if __name__ == "__main__":
    main()
