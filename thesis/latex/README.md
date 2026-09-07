# Thesis LaTeX project

**Compile with XeLaTeX** (required for `polyglossia` + Arabic script):

```
xelatex main.tex
bibtex main
xelatex main.tex
xelatex main.tex
```

On **Overleaf**: create a new project from this folder (or upload the `latex/` directory plus
`../references.bib` one level up), then set **Menu → Compiler → XeLaTeX**. Overleaf ships the
`Amiri` / `Scheherazade New` Arabic fonts and a full TeX Live, so no extra package installation
should be needed.

## Layout

```
main.tex                  master file , preamble, metadata, \input order
front/
  cover.tex                title page (edit \topiccode, \defencedate, jury names here or in main.tex)
  dedication.tex           placeholder , personalise
  acknowledgments.tex      placeholder , personalise
  abstract_en.tex          English abstract + keywords
  abstract_fr.tex          French résumé + mots-clés
  abstract_ar.tex          Arabic ملخص + كلمات مفتاحية
  acronyms.tex             list of acronyms
chapters/
  chapter1_introduction.tex
  chapter2_literature.tex
  chapter3_methodology.tex
  chapter4_results.tex
  chapter5_conclusion.tex  (new , synthesises Chapters 3-4's findings; Chapter 5 did not exist before)
  appendix.tex
figures/                   one .tex per TikZ/pgfplots figure, \input by the chapters
../references.bib          unchanged; only entries actually cited in the source chapters are used
```

## Things to fill in before submission

Search for these placeholders (all in `main.tex` or `front/cover.tex`):

- `\topiccode` , topic code / ID, still unknown per your message
- `\defencedate` , defence date, still unknown
- jury president / examiner names in `front/cover.tex`
- `front/dedication.tex` and `front/acknowledgments.tex` , personalise both
- a real institutional crest, if you obtain one: replace the TikZ-drawn emblem at the top of
  `front/cover.tex` with `\includegraphics[width=2.6cm]{front/emblem.pdf}`

## Notes on fidelity to the source chapters

Chapters 1–4 are a structural conversion of `thesis/Chapter 1.md`–`Chapter 4.md`: section
headings, tables, and inline citations were mapped onto `\citep`/`\citet` keys already present in
`references.bib` (only entries that source text actually cites are pulled into the printed
bibliography , three background entries, `perry2023security`, `vaithilingam2022usability`,
`barke2023grounded`, are verified but uncited in the source, so they are left out rather than
force-cited). Chapter 5 (Conclusion) did not exist before this conversion; it is new text that
directly answers RQ1–RQ3 and states contributions/limitations/future work using only findings
already established in Chapters 3–4 , no new claims or numbers were introduced.

Nine original figures were added (none existed in the Markdown source): a tool-evolution timeline,
a SOLID-principles wheel, the before/after delta-harness diagram, the five-stage pipeline diagram,
Martin's abstractness/instability plane, the intra-unit-vs-inter-unit diagram for Finding 1, and
three data charts (class availability by language, SRP rate by agent, validation precision/recall
by principle) plotted directly from the tables already in Chapter 4.
