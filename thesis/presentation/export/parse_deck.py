r"""Parse the Beamer deck into a JSON content model for the PowerPoint export.

Every number in the deck is copied from main.tex by this parser, never retyped,
because this project has a history of figures drifting when they were copied by
hand between documents.

What it understands, which is everything the deck uses:
  \begin{frame}{T} ... \end{frame}      a slide
  \framesubtitle{S}                     the grey subtitle after the title
  \divider{Part N}{Name}                a section divider slide
  \begin{columns} / \begin{column}{W}   side-by-side layout
  \begin{itemize} \item                 bullet list (one level)
  \begin{tabular}{spec} ... \\          table, with booktabs rules dropped
  \takeaway{...}                        the gold summary box
  \keynum[w]{big}{label}                the blue statistic box
  \thesisfigH/W{size}{name}             a figure \input from the thesis
  \[ ... \]                             display equation, rendered to an image
  \begin{tikzpicture} ... \end{...}     inline diagram, rendered to an image

Equations and inline tikzpictures become images because PowerPoint has no native
form for them; everything else becomes editable text, lists and tables.

Run:  python thesis/presentation/export/parse_deck.py
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
DECK = HERE.parent
FIGDIR = HERE / "figs"


# --------------------------------------------------------------- brace matching
def match_brace(s: str, i: int, op: str = "{", cl: str = "}") -> int:
    """Index just past the group starting at s[i] == op."""
    assert s[i] == op, (s[i], s[i - 20:i + 20])
    d = 0
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == op:
            d += 1
        elif s[i] == cl:
            d -= 1
            if d == 0:
                return i + 1
        i += 1
    raise ValueError("unbalanced braces")


def take_group(s: str, i: int) -> tuple[str, int]:
    """Read a {...} group at or after i, skipping whitespace. Returns (inner, next)."""
    while i < len(s) and s[i] in " \t\r\n":
        i += 1
    if i >= len(s) or s[i] != "{":
        return "", i
    j = match_brace(s, i)
    return s[i + 1:j - 1], j


def take_opt(s: str, i: int) -> tuple[str, int]:
    while i < len(s) and s[i] in " \t\r\n":
        i += 1
    if i < len(s) and s[i] == "[":
        j = match_brace(s, i, "[", "]")
        return s[i + 1:j - 1], j
    return "", i


# --------------------------------------------------------------- inline runs
RUN_CMDS = {
    "textbf": {"b": True}, "bfseries": {"b": True},
    "textit": {"i": True}, "emph": {"i": True}, "itshape": {"i": True},
    "texttt": {"code": True},
    "textsc": {"sc": True},
    "alert": {"b": True, "color": "ispcolor"},
}
COLOR_CMD = re.compile(r"\\textcolor\{([^}]*)\}")
SIZE_CMDS = {"tiny", "scriptsize", "footnotesize", "small", "normalsize", "large", "Large"}
# commands whose groups are layout settings, not text: drop that many groups
DROP_ARGS = {"setlength": 2, "renewcommand": 2, "definecolor": 3, "setbeamertemplate": 2,
             "addtolength": 2, "hspace": 1, "vspace": 1, "rule": 2, "resizebox": 3,
             "setcounter": 2}
SUPER = {"a": "ᵃ", "b": "ᵇ", "c": "ᶜ", "d": "ᵈ", "e": "ᵉ",
         "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴"}

SYMBOLS = {
    r"\%": "%", r"\&": "&", r"\_": "_", r"\#": "#", r"\$": "$",
    r"\,": "\u202f", r"\ ": " ", r"\\": "\n",
    r"\times": "\u00d7", r"\geq": "\u2265", r"\leq": "\u2264",
    r"\ge": "\u2265", r"\le": "\u2264", r"\neq": "\u2260", r"\approx": "\u2248",
    r"\kappa": "\u03ba", r"\tau": "\u03c4", r"\rho": "\u03c1", r"\alpha": "\u03b1",
    r"\wedge": "\u2227", r"\neg": "\u00ac", r"\exists": "\u2203", r"\in": "\u2208",
    r"\rightarrow": "\u2192", r"\to": "\u2192", r"\dots": "\u2026", r"\ldots": "\u2026",
    r"\textasciitilde": "~", r"\textendash": "\u2013", r"\textbullet": "\u2022",
    r"\ddagger": "\u2021", r"\dagger": "\u2020", r"\textsuperscript": "",
}


def runs(src: str, base: dict | None = None) -> list[dict]:
    """Turn LaTeX inline markup into a list of {text, b, i, code, color} runs."""
    base = dict(base or {})
    out: list[dict] = []

    def emit(txt: str, st: dict):
        if not txt:
            return
        if out and all(out[-1].get(k) == st.get(k) for k in ("b", "i", "code", "color", "sc", "sm")):
            out[-1]["text"] += txt
        else:
            d = {"text": txt}
            d.update({k: v for k, v in st.items() if v})
            out.append(d)

    i, buf, st = 0, "", dict(base)
    while i < len(src):
        c = src[i]
        if c == "\\":
            m = re.match(r"\\([a-zA-Z]+)\*?", src[i:])
            if not m:
                two = src[i:i + 2]
                if two in SYMBOLS:
                    buf += SYMBOLS[two]
                    i += 2
                    # \\[6pt] is a line break with spacing: drop the [6pt]
                    if two == "\\\\":
                        _opt, i = take_opt(src, i)
                    continue
                buf += src[i + 1:i + 2]
                i += 2
                continue
            name = m.group(1)
            j = i + m.end()
            if name in RUN_CMDS:
                inner, j2 = take_group(src, j)
                if j2 > j:
                    emit(buf, st); buf = ""
                    out.extend(runs(inner, {**st, **RUN_CMDS[name]}))
                    i = j2
                    continue
                st.update(RUN_CMDS[name])
                i = j
                continue
            if name == "finding":
                # \finding{claim}{source paper}: the claim leads, its source sits
                # underneath in small grey caps. Dropping it as an unknown command
                # would silently delete every state-of-the-art claim.
                claim, j2 = take_group(src, j)
                source, j3 = take_group(src, j2)
                emit(buf, st); buf = ""
                out.extend(runs(claim, st))
                out.append({"text": "\n"})
                out.extend(runs(source, {**st, "sm": True, "color": "accentgray"}))
                i = j3
                continue
            if name == "textcolor":
                col, j2 = take_group(src, j)
                inner, j3 = take_group(src, j2)
                emit(buf, st); buf = ""
                out.extend(runs(inner, {**st, "color": col}))
                i = j3
                continue
            if name in SIZE_CMDS:
                i = j
                continue
            if name in ("item",):
                i = j
                continue
            sym = "\\" + name
            if sym in SYMBOLS:
                buf += SYMBOLS[sym]
                i = j
                continue
            # commands whose arguments are all setup, never text
            if name in DROP_ARGS:
                for _ in range(DROP_ARGS[name]):
                    _inner, j = take_group(src, j)
                i = j
                continue
            # unknown command: drop it and any single group argument
            _inner, j2 = take_group(src, j)
            i = j2 if j2 > j else j
            continue
        if c == "$":
            k = src.find("$", i + 1)
            if k < 0:
                k = len(src)
            buf += math_text(src[i + 1:k])
            i = k + 1
            continue
        if c in "{}":
            i += 1
            continue
        if c == "~":
            buf += "\u00a0"
            i += 1
            continue
        buf += c
        i += 1
    emit(buf, st)
    for r in out:
        r["text"] = re.sub(r"[ \t]*\n[ \t]*", " ", r["text"])
        # TeX quoting: ``x'' -> "x", `x' -> 'x'
        r["text"] = r["text"].replace("``", "“").replace("''", "”")
        # en dash in numeric ranges (1--3, 1.88--1.95). Never an em dash: the
        # author's house rules forbid it.
        r["text"] = re.sub(r"(?<!-)--(?!-)", "–", r["text"])
    out = [r for r in out if r["text"]]
    return out or [{"text": ""}]


def math_text(s: str) -> str:
    """Inline math, flattened to unicode. Good enough for $b\\ge\\tau$ and $\\kappa$."""
    # table footnote markers: $^a$ -> superscript a
    s = re.sub(r"\^\{?([a-e0-4])\}?", lambda m: SUPER[m.group(1)], s)
    s = re.sub(r"\\d?frac\{([^{}]*)\}\{([^{}]*)\}", r"\1/\2", s)
    s = re.sub(r"\\mathrm\{([^}]*)\}", r"\1", s)
    s = re.sub(r"\\text\{([^}]*)\}", r"\1", s)
    s = re.sub(r"\\textbf\{([^}]*)\}", r"\1", s)
    for k, v in sorted(SYMBOLS.items(), key=lambda kv: -len(kv[0])):
        if k.startswith("\\") and len(k) > 2:
            s = s.replace(k, v)
    s = re.sub(r"\\([a-zA-Z]+)", r"\1", s)
    return s.replace("{", "").replace("}", "").strip()


# --------------------------------------------------------------- image rendering
class ImageBank:
    """Render snippets of LaTeX (equations, inline tikz) to cropped PNGs."""

    def __init__(self, outdir: Path, quiet: bool = True):
        self.outdir = outdir
        self.outdir.mkdir(parents=True, exist_ok=True)
        self.build = HERE / "build"
        self.build.mkdir(exist_ok=True)
        self.n = 0
        self.quiet = quiet
        self.made: list[str] = []

    def render(self, body: str, kind: str) -> str | None:
        if kind == "eq":
            # \[...\] does not typeset at the top level of the standalone class;
            # the same content as inline display math does.
            inner = body.strip()
            if inner.startswith("\\["):
                inner = inner[2:]
            if inner.endswith("\\]"):
                inner = inner[:-2]
            body = "$\\displaystyle " + inner.strip() + "$"
        self.n += 1
        name = f"{kind}_{self.n:02d}"
        png = self.outdir / f"{name}.png"
        tex = self.build / f"{name}.tex"
        preamble = (HERE / "figstandalone.tex").read_text(encoding="utf-8")
        preamble = preamble.replace(r"\input{../../latex/figures/\FIG.tex}", body)
        tex.write_text(preamble, encoding="utf-8")
        r = subprocess.run(
            ["xelatex", "-interaction=nonstopmode", "-halt-on-error",
             f"-output-directory={self.build}", str(tex)],
            capture_output=True, text=True, cwd=HERE)
        pdf = self.build / f"{name}.pdf"
        if r.returncode != 0 or not pdf.exists():
            print(f"  ! could not render {name} ({kind})")
            return None
        subprocess.run(["pdftoppm", "-png", "-r", "300", "-singlefile",
                        str(pdf), str(png.with_suffix(""))], check=True)
        self.made.append(name)
        return name


# --------------------------------------------------------------- block parsing
def strip_comments(s: str) -> str:
    out = []
    for line in s.split("\n"):
        m = re.search(r"(?<!\\)%", line)
        out.append(line[:m.start()] if m else line)
    return "\n".join(out)


def parse_tabular(spec: str, body: str) -> dict:
    ncol = len(re.findall(r"[lcrpLCR]", re.sub(r"\{[^{}]*\}", "", spec)))
    align = []
    for ch in re.sub(r"@\{[^{}]*\}|\{[^{}]*\}|[|\s]", "", spec):
        if ch in "lL":
            align.append("left")
        elif ch in "cC":
            align.append("center")
        elif ch in "rR":
            align.append("right")
        elif ch in "pLCR":
            align.append("left")
    body = re.sub(r"\\(top|mid|bottom)rule", "\x00RULE\x00", body)
    # \cmidrule underlines a spanning header cell; it is NOT a header/body
    # separator, so it must not be counted as one or the header split goes wrong
    body = re.sub(r"\\cmidrule(\([^)]*\))?(\[[^\]]*\])?\{[^}]*\}", "", body)
    body = re.sub(r"\\addlinespace(\[[^\]]*\])?", "", body)
    rows, head_rows = [], 0
    seen_rule = 0
    for raw in body.split("\\\\"):
        if "\x00RULE\x00" in raw:
            seen_rule += 1
            if seen_rule == 2 and rows:
                head_rows = len(rows)
            raw = raw.replace("\x00RULE\x00", "")
        if not raw.strip():
            continue
        cells, buf, depth = [], "", 0
        for ch in raw:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            if ch == "&" and depth == 0:
                cells.append(buf); buf = ""
            else:
                buf += ch
        cells.append(buf)
        parsed = []
        for c in cells:
            span = 1
            mc = re.match(r"\s*\\multicolumn\{(\d+)\}\{[^}]*\}", c)
            if mc:
                span = int(mc.group(1))
                c = c[mc.end():]
                c = c.strip()
                if c.startswith("{"):
                    c = c[1:match_brace(c, 0) - 1]
            parsed.append({"runs": runs(c.strip()), "span": span})
        rows.append(parsed)
    if head_rows == 0 and rows:
        head_rows = 1
    return {"t": "table", "align": align or ["left"] * ncol,
            "head": rows[:head_rows], "rows": rows[head_rows:]}


def parse_blocks(src: str, bank: ImageBank) -> list[dict]:
    """Walk a frame (or column) body and emit content blocks in order."""
    blocks: list[dict] = []
    i, text_start = 0, 0

    def flush(end: int):
        nonlocal text_start
        chunk = src[text_start:end]
        if chunk.strip():
            r = runs(chunk)
            if any(x["text"].strip() for x in r):
                blocks.append({"t": "text", "runs": r})
        text_start = end

    while i < len(src):
        # (?<!\\)\\\[ so that \\[6pt], a line break with spacing, is not read as
        # the start of a display equation.
        m = re.compile(
            r"\\begin\{(itemize|enumerate|tabular|tikzpicture|center|columns|column|flushleft)\}"
            r"|(?<!\\)\\\[|\\(takeaway|keynum|thesisfigH|thesisfigW|framesubtitle|divider)\b"
        ).search(src, i)
        if not m:
            break
        i = m.start()
        env = m.group(1)
        cmd = m.group(2)

        if env in ("center", "flushleft"):
            flush(i)
            j = src.index("\\end{" + env + "}", i) + len("\\end{" + env + "}")
            blocks.extend(parse_blocks(src[m.end():src.index("\\end{" + env + "}", i)], bank))
            i = text_start = j
            continue

        if env in ("itemize", "enumerate"):
            flush(i)
            close = find_end(src, i, env)
            inner = src[m.end():close]
            # \setcounter{enumi}{4} continues the numbering in a second column
            start = 1
            sc = re.search(r"\\setcounter\{enumi\}\{(\d+)\}", inner)
            if sc:
                start = int(sc.group(1)) + 1
            items = []
            for part in re.split(r"\\item\b", inner)[1:]:
                r = runs(part)
                if any(x["text"].strip() for x in r):
                    items.append(r)
            if items:
                blk = {"t": "list", "items": items}
                if env == "enumerate":
                    blk["ordered"] = True
                    blk["start"] = start
                blocks.append(blk)
            i = text_start = close + len("\\end{" + env + "}")
            continue

        if env == "tabular":
            flush(i)
            spec, j = take_group(src, m.end())
            close = find_end(src, i, "tabular")
            blocks.append(parse_tabular(spec, src[j:close]))
            i = text_start = close + len("\\end{tabular}")
            continue

        if env == "tikzpicture":
            flush(i)
            close = find_end(src, i, "tikzpicture")
            body = src[i:close + len("\\end{tikzpicture}")]
            name = bank.render(body, "tikz")
            if name:
                blocks.append({"t": "image", "name": name})
            i = text_start = close + len("\\end{tikzpicture}")
            continue

        if env in ("columns", "column"):
            # handled by the frame splitter; stop here
            break

        if m.group(0) == "\\[":
            flush(i)
            cm = re.compile(r"(?<!\\)\\\]").search(src, i + 2)
            if cm is None:
                i = m.end()
                continue
            close = cm.start()
            body = src[i:close + 2]
            name = bank.render(body, "eq")
            if name:
                blocks.append({"t": "image", "name": name, "eq": True})
            i = text_start = close + 2
            continue

        if cmd == "framesubtitle":
            flush(i)
            _s, j = take_group(src, m.end())
            i = text_start = j
            continue

        if cmd == "takeaway":
            flush(i)
            inner, j = take_group(src, m.end())
            blocks.append({"t": "takeaway", "runs": runs(inner)})
            i = text_start = j
            continue

        if cmd == "keynum":
            flush(i)
            _w, j = take_opt(src, m.end())
            big, j = take_group(src, j)
            lab, j = take_group(src, j)
            blocks.append({"t": "keynum", "big": runs(big), "label": runs(lab)})
            i = text_start = j
            continue

        if cmd in ("thesisfigH", "thesisfigW"):
            flush(i)
            _sz, j = take_group(src, m.end())
            fig, j = take_group(src, j)
            blocks.append({"t": "figure", "name": fig})
            i = text_start = j
            continue

        i = m.end()

    flush(len(src))
    return blocks


def find_end(s: str, start: int, env: str) -> int:
    """Index of the matching \\end{env}, honouring nesting."""
    b, e = "\\begin{" + env + "}", "\\end{" + env + "}"
    d, i = 0, start
    while i < len(s):
        nb, ne = s.find(b, i), s.find(e, i)
        if ne < 0:
            raise ValueError("no \\end{%s}" % env)
        if 0 <= nb < ne:
            d += 1
            i = nb + len(b)
        else:
            d -= 1
            if d == 0:
                return ne
            i = ne + len(e)
    raise ValueError("unbalanced %s" % env)


def split_columns(body: str, bank: ImageBank) -> list[dict] | None:
    if "\\begin{columns}" not in body:
        return None
    close = find_end(body, body.index("\\begin{columns}"), "columns")
    inner = body[body.index("\\begin{columns}"):close]
    cols = []
    for m in re.finditer(r"\\begin\{column\}", inner):
        w, j = take_group(inner, m.end())
        cend = find_end(inner, m.start(), "column")
        frac = 0.5
        fm = re.search(r"([\d.]+)\\textwidth", w)
        if fm:
            frac = float(fm.group(1))
        cols.append({"w": frac, "blocks": parse_blocks(inner[j:cend], bank)})
    return cols or None


# --------------------------------------------------------------- frames
def parse(tex: Path, bank: ImageBank) -> list[dict]:
    src = strip_comments(tex.read_text(encoding="utf-8"))
    src = src[src.index("\\begin{document}"):]
    slides: list[dict] = []
    section = None
    i = 0
    token = re.compile(r"\\begin\{frame\}|\\divider\b")
    while True:
        m = token.search(src, i)
        if not m:
            break
        if m.group(0) == "\\divider":
            part, j = take_group(src, m.end())
            name, j = take_group(src, j)
            section = f"{part}: {name}"
            slides.append({"kind": "divider", "part": part, "title": name,
                           "section": section, "blocks": []})
            i = j
            continue

        j = m.end()
        opt, j = take_opt(src, j)          # [plain] etc
        title_src, j2 = take_group(src, j)
        plain = "plain" in opt
        close = find_end(src, m.start(), "frame")
        body = src[j2:close]
        sub = ""
        sm = re.search(r"\\framesubtitle", body)
        if sm:
            sub, _ = take_group(body, sm.end())
        if plain and not title_src.strip():
            slides.append({"kind": "plain", "title": "", "section": section,
                           "raw": body.strip(), "blocks": []})
            i = close + len("\\end{frame}")
            continue
        cols = split_columns(body, bank)
        pre = body[:body.index("\\begin{columns}")] if cols else body
        post = body[find_end(body, body.index("\\begin{columns}"), "columns")
                    + len("\\end{columns}"):] if cols else ""
        slides.append({
            "kind": "content",
            "title": " ".join(x["text"] for x in runs(title_src)).strip(),
            "subtitle": " ".join(x["text"] for x in runs(sub)).strip() if sub else "",
            "section": section,
            "pre": parse_blocks(pre, bank),
            "columns": cols or [],
            "post": parse_blocks(post, bank),
        })
        i = close + len("\\end{frame}")
    return slides


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tex", type=Path, default=DECK / "main.tex")
    ap.add_argument("--out", type=Path, default=HERE / "deck.json")
    a = ap.parse_args()

    bank = ImageBank(FIGDIR)
    slides = parse(a.tex, bank)
    a.out.write_text(json.dumps(slides, indent=1, ensure_ascii=False), encoding="utf-8")

    kinds = {}
    for s in slides:
        kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
    print(f"{len(slides)} slides parsed: {kinds}")
    print(f"rendered {len(bank.made)} snippet images")
    for s in slides:
        nb = len(s.get("pre", [])) + len(s.get("post", [])) + \
             sum(len(c["blocks"]) for c in s.get("columns", []))
        print(f"  {s['kind'][:7]:8s} {nb:2d} blocks  {s.get('title','')[:62]}")


if __name__ == "__main__":
    main()
