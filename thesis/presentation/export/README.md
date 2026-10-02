# PowerPoint export

`main.tex` (XeLaTeX/Beamer) stays the source of truth. Two PowerPoint files are
generated from it, for two different needs.

| File | Text editable? | Looks like the PDF? | Use it when |
|---|---|---|---|
| `../main.pptx` | **no**, one image per slide | **pixel-identical** | you just need to present from PowerPoint |
| `../main-editable.pptx` | **yes** | close, not identical | you need to change wording or numbers in PowerPoint |

## Regenerating

Build the deck first (`latexmk -xelatex main.tex` in `thesis/presentation`), then:

```bash
node thesis/presentation/export/to_pptx.js        # pixel-perfect, image per slide
python thesis/presentation/export/parse_deck.py   # main.tex -> deck.json + figure images
node thesis/presentation/export/build_native.js   # editable deck
```

`npm install pptxgenjs` inside this directory if the `require` fails.

## How the editable build works

`parse_deck.py` reads `main.tex` and writes `deck.json`. **Every number is copied
from the source, never retyped**, because this project has a history of figures
drifting when they were carried by hand between documents. A content check after
the build confirms 27 of 28 key figures appear as live text; the 28th (`2,343`)
is inside the present-vs-introduced figure, which is an image.

What stays an image, because PowerPoint has no native form for it:

* the ten thesis TikZ figures, rendered standalone through `figstandalone.tex`
* the twelve display equations
* the one inline TikZ diagram (the research-questions slide)

Everything else, titles, prose, bullets, numbered lists, tables, statistic boxes
and takeaway panels, is a real PowerPoint object.

`build_native.js` measures each slide: it stacks the blocks, estimates their
height and steps the body font down from 15.5pt until the content fits the text
area. That is what keeps 56 slides free of overflow without tuning each one by
hand. Four slides currently shrink below 10pt.

## Checking a rebuild

`validate.py` from the pptx skill must pass, and the slides should be rendered
and looked at. PowerPoint itself is the right renderer here (LibreOffice is not
installed, and its font substitution makes overflow checks unreliable anyway):

```powershell
$ppt = New-Object -ComObject PowerPoint.Application
$p = $ppt.Presentations.Open("D:\VibeBench\thesis\presentation\main-editable.pptx", $true, $false, $false)
$p.Export("<some empty folder>", "PNG", 1920, 1080)
$p.Close(); $ppt.Quit()
```

Defects this caught, all of which a quick look would have missed: `\finding`
deleting every state-of-the-art claim, `\begin{enumerate}` running the outline
into one paragraph, `\cmidrule` being counted as a header separator, equations
sized to column width, and a table overflowing into the takeaway box below it.
