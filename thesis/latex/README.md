# Thesis LaTeX project

Compiled locally with **MiKTeX 25.12** and **XeLaTeX** (required for `fontspec`, `polyglossia` and the
Arabic abstract). `latexmk` ships with MiKTeX and runs under Perl:

```
latexmk -xelatex main.tex
```

or by hand: `xelatex main`, `bibtex main`, `xelatex main`, `xelatex main`.

The Arabic abstract uses `Amiri` or `Scheherazade New` when installed and otherwise `Sakkal
Majalla`, which ships with Windows.

## Layout

```
main.tex              preamble, metadata (title, author, supervisor, cover fields), \input order
references.bib        every entry checked against its source on 15 September 2026
front/
  cover.tex           title page
  dedication.tex
  acknowledgments.tex
  abstracts.tex       Arabic, English and French abstracts on one page
  acronyms.tex
chapters/
  chapter1_introduction.tex
  chapter2_literature.tex
  chapter3_methodology.tex
  chapter4_results.tex     Java setup and results; \input's chapter4_python.tex at the end
  chapter4_python.tex      the Python replication (RQ4)
  chapter5_conclusion.tex  answers, contributions, limitations, future work
  appendix.tex             run order and the label judge
figures/              one TikZ file per figure, also \input by the defence deck
```

## Before submission

The cover fields that are not yet known are blank lines, defined in `main.tex`:

- `\jurypresident`
- `\defencedate`
- `\topiccode`

Replace `\blankfield{...}` with the real text once the department sets them. Do not put guessed
values there.

## Checks

```
python vibebench/07_verify/verify_thesis_numbers.py    # from the repository root: 53 checks, 0 FAIL
```

The review that produced the current text is recorded in `../JURY_REVIEW.md`. The defence deck is
in `../presentation/` and builds the same way.
