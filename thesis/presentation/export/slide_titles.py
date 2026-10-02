"""Derive a title for every slide of the Beamer deck.

pdftotext merges a frame's title with its \\framesubtitle onto one line, so each
page's first line is cleaned by matching it against the real \\begin{frame}{...}
titles in the source. Pages produced by \\divider are flagged, because those are
the only ones that should open a PowerPoint section: a backup CONTENT slide is
also titled "Backup: ...", and must not.

Run on its own to inspect the result:
    python thesis/presentation/export/slide_titles.py
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
DECK = HERE.parent


def detex(s: str) -> str:
    s = re.sub(r"\\(?:textbf|textit|emph|alert|texttt|textsc)\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\$[^$]*\$", "", s)
    s = s.replace("\\&", "&").replace("\\%", "%").replace("\\,", " ")
    s = re.sub(r"\\[a-zA-Z]+", "", s)
    s = s.replace("{", "").replace("}", "")
    s = s.replace("``", '"').replace("''", '"')
    return re.sub(r"\s+", " ", s).strip()


def frame_titles(tex: Path) -> list[str]:
    src = tex.read_text(encoding="utf-8")
    out = [detex(m) for m in re.findall(
        r"\\begin\{frame\}(?:<[^>]*>)?(?:\[[^\]]*\])?\{([^\n]*?)\}\s*$", src, re.M)]
    # \divider{Part N}{Name} puts its text in the frame body, not in a frame title
    out += [f"{detex(a)}: {detex(b)}"
            for a, b in re.findall(r"\\divider\{([^}]*)\}\{([^}]*)\}", src)]
    return [t for t in out if t]


def slide_titles(pdf: Path, tex: Path) -> list[dict]:
    tex_titles = frame_titles(tex)
    raw = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True,
                         text=True, encoding="utf-8", errors="replace").stdout
    pages = [p for p in raw.split("\f")]
    titles: list[dict] = []
    for i, pg in enumerate(pages, 1):
        lines = [l.strip() for l in pg.splitlines() if l.strip()]
        lines = [l for l in lines
                 if not l.startswith("Evaluating Coding Agent Quality")
                 and not re.fullmatch(r"\d+\s*/\s*\d+", l)]
        if not lines:
            continue
        t = re.sub(r"\s+", " ", lines[0])
        divider = bool(re.fullmatch(r"(Part \d+|Backup)", t)) and len(lines) > 1
        if divider:
            t = t + ": " + re.sub(r"\s+", " ", lines[1])
        cands = [x for x in tex_titles if t.startswith(x[:min(len(x), 28)])]
        if cands:
            t = max(cands, key=len)
        titles.append({"title": t[:120], "divider": divider})
    if titles:
        titles[0] = {"title": "Evaluating Coding Agent Quality", "divider": False}
    return titles


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", type=Path, default=DECK / "main.pdf")
    ap.add_argument("--tex", type=Path, default=DECK / "main.tex")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()

    titles = slide_titles(a.pdf, a.tex)
    if a.out:
        a.out.write_text(json.dumps(titles, indent=1, ensure_ascii=False), encoding="utf-8")
    for i, d in enumerate(titles, 1):
        print(f"{i:3d} {'SEC ' if d['divider'] else '    '} {d['title']}")


if __name__ == "__main__":
    main()
