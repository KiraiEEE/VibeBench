"""
Compact SRP label-batch extractor (census-scale).

For SRP the decisive evidence is WHAT THE CLASS DOES: its name, its docstring,
and the set of member names (methods reveal the responsibilities). Full method
bodies dominate line count without changing the judgement, so this extractor
emits a structural summary instead:

    class name + bases, docstring (truncated), every method signature,
    and instance-attribute names assigned in __init__.

BLIND BY DEFAULT: the rubric's own verdict (srp_risk) and the metric vector are
withheld unless --show-verdict, so the judge is not anchored to the tool.

Run: python vibebench/labels/extract_srp_compact.py --file srp_census_remaining.csv \
         --start 0 --end 60 --out <path>
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import config as C  # noqa: E402

DOC_MAX = 240


def _sig(fn: ast.AST) -> str:
    a = fn.args
    parts = [p.arg for p in a.posonlyargs] + [p.arg for p in a.args]
    if a.vararg:
        parts.append("*" + a.vararg.arg)
    if a.kwonlyargs:
        if not a.vararg:
            parts.append("*")
        parts += [p.arg for p in a.kwonlyargs]
    if a.kwarg:
        parts.append("**" + a.kwarg.arg)
    decos = []
    for d in fn.decorator_list:
        n = d.id if isinstance(d, ast.Name) else (
            d.attr if isinstance(d, ast.Attribute) else
            (d.func.id if isinstance(d, ast.Call) and isinstance(d.func, ast.Name) else None))
        if n in ("property", "staticmethod", "classmethod", "abstractmethod",
                 "cached_property", "abstractproperty"):
            decos.append("@" + n)
    pre = "async " if isinstance(fn, ast.AsyncFunctionDef) else ""
    d = (" ".join(decos) + " ") if decos else ""
    return f"{d}{pre}{fn.name}({', '.join(parts)})"


def _doc(node: ast.AST) -> str:
    try:
        s = ast.get_docstring(node) or ""
    except Exception:
        return ""
    s = " ".join(s.split())
    return (s[:DOC_MAX] + "...") if len(s) > DOC_MAX else s


def _bases(cd: ast.ClassDef) -> str:
    out = []
    for b in cd.bases:
        try:
            out.append(ast.unparse(b))
        except Exception:
            pass
    return ("(" + ", ".join(out) + ")") if out else ""


def _init_attrs(cd: ast.ClassDef) -> list[str]:
    attrs: list[str] = []
    for n in cd.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "__init__":
            for sub in ast.walk(n):
                if isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        if (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                                and t.value.id == "self"):
                            if t.attr not in attrs:
                                attrs.append(t.attr)
    return attrs


def render(row, show_verdict: bool) -> str:
    L: list[str] = []
    head = f"=== ROW {row.name} | {row['agent']} | {row['full_name']}#{row['pr_number']} | class={row['class_name']} ==="
    if show_verdict:
        head += f"\n[VERDICT srp_risk={row['srp_risk']} loc={row['class_loc']} wmc={row['wmc']} methods={row['method_count']} lcom={row['lcom']}]"
    L.append(head)
    L.append(f"file: {row['file_path']}")
    lp = row.get("local_path")
    try:
        src = Path(str(lp)).read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        L.append(f"<<source unavailable: {e}>>")
        return "\n".join(L) + "\n"
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        L.append(f"<<parse error: {e}>>")
        return "\n".join(L) + "\n"

    target = None
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef) and n.name == str(row["class_name"]):
            target = n
            break
    if target is None:
        L.append(f"<<class {row['class_name']} not found>>")
        return "\n".join(L) + "\n"

    L.append(f"class {target.name}{_bases(target)}")
    d = _doc(target)
    if d:
        L.append(f'  """{d}"""')
    attrs = _init_attrs(target)
    if attrs:
        L.append(f"  self attrs ({len(attrs)}): " + ", ".join(attrs))
    meths = [n for n in target.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    nested = [n.name for n in target.body if isinstance(n, ast.ClassDef)]
    L.append(f"  methods ({len(meths)}):")
    for m in meths:
        md = _doc(m)
        line = "    - " + _sig(m)
        if md:
            line += "   # " + (md[:100] + "..." if len(md) > 100 else md)
        L.append(line)
    if nested:
        L.append(f"  nested classes: {', '.join(nested)}")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=60)
    ap.add_argument("--out", required=True)
    ap.add_argument("--show-verdict", action="store_true")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.is_absolute() and not path.exists():
        path = C.LABELS_DIR / args.file
    df = pd.read_csv(path)
    sub = df.iloc[args.start:args.end]

    chunks = [render(r, args.show_verdict) for _, r in sub.iterrows()]
    Path(args.out).write_text("\n".join(chunks), encoding="utf-8")
    print(f"wrote rows {args.start}..{args.end} ({len(sub)}) -> {args.out}")


if __name__ == "__main__":
    main()
