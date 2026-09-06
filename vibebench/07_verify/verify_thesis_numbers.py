"""Check the numbers written in the thesis against the data they claim to describe.

A thesis assembled by editing an older one carries a specific risk: a figure
survives the rewrite because nobody re-derived it. Grepping for known-stale
values only finds the ones already suspected. This script inverts that: it
recomputes each headline quantity from the parquet outputs and the label CSVs,
then asserts that the thesis source contains that value and does not contain a
contradicting one.

It is deliberately narrow. It checks the numbers a reader would quote, not every
digit, because a check nobody maintains is worse than no check.

Run:  python vibebench/07_verify/verify_thesis_numbers.py
Exit: 1 if any claim in the thesis disagrees with the data.
"""
from __future__ import annotations

import glob
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

TEX = sorted(glob.glob('thesis/latex/chapters/*.tex')) + \
      sorted(glob.glob('thesis/latex/front/*.tex'))

fails: list[str] = []
oks = 0


def body() -> str:
    out = []
    for f in TEX:
        out.append(Path(f).read_text(encoding='utf-8', errors='replace'))
    return "\n".join(out)


SRC = body()


def norm(s: str) -> str:
    """LaTeX writes thousands as 4{,}245 and French as 4~245."""
    return s.replace('{,}', ',').replace('~', ',').replace(' ', ',')


TXT = norm(SRC)


def claims(value: str) -> bool:
    return value in TXT


def check(label: str, expected: str, forbidden: list[str] | None = None) -> None:
    global oks
    if not claims(expected):
        fails.append(f"{label}: thesis does not state {expected!r}")
        print(f"  FAIL  {label}: expected {expected!r} not found")
        return
    for bad in (forbidden or []):
        if claims(bad):
            fails.append(f"{label}: thesis still contains stale {bad!r}")
            print(f"  FAIL  {label}: stale value {bad!r} still present")
            return
    oks += 1
    print(f"  ok    {label}: {expected}")


print("=" * 70)
print("THESIS NUMBERS vs THE DATA THEY DESCRIBE")
print("=" * 70)

# ---------------------------------------------------------------- corpus
pr = pd.read_parquet(C.CORPUS_DIR / "java_pr_results.parquet")
n_all = len(pr)
n_ok = int((pr.status == "ok").sum())
n_repo = int(pr.full_name.nunique())
n_agents = int(pr.agent.nunique())

print("\n-- corpus --")
check("curated pull requests", f"{n_all:,}", ["546", "867"])
check("analysable pull requests", f"{n_ok:,}")
check("repositories", f"{n_repo:,}", ["147 repositories", "27 repositories"])
if n_agents != 3:
    fails.append(f"agent count is {n_agents}, thesis says three")
else:
    print(f"  ok    agents: {n_agents}")
    oks += 1

# ------------------------------------------------------------ validation
print("\n-- validation, recomputed from the label CSVs --")
PRED = {'srp': 'srp_risk', 'ocp': 'smell', 'lsp': 'violation',
        'isp': 'flagged', 'dip': 'dip_risk'}
for p, col in PRED.items():
    f = C.LABELS_DIR / f"java_{p}_labeling_sample.csv"
    d = pd.read_csv(f)
    lab = d[f"llm_{p}_risk"].astype(str).str.strip().str.lower().map(
        {'true': True, 'false': False})
    flag = d[col].fillna(False).astype(bool)
    tp = int((flag & lab).sum()); fp = int((flag & ~lab).sum())
    fn = int((~flag & lab).sum())
    prec = tp / max(tp + fp, 1)
    rec = tp / max(tp + fn, 1)
    check(f"{p.upper()} precision", f"{prec:.3f}")
    check(f"{p.upper()} recall", f"{rec:.3f}")

# --------------------------------------------------- refinements claimed
print("\n-- the two refinements --")
d = pd.read_csv(C.LABELS_DIR / "java_lsp_labeling_sample.csv")
lab = d.llm_lsp_risk.astype(str).str.strip().str.lower().map({'true': True, 'false': False})
nar = d.signals.fillna('').str.contains('refused_bequest')
tp = int((nar & lab).sum()); fp = int((nar & ~lab).sum()); fn = int((~nar & lab).sum())
check("LSP restricted precision", f"{tp/max(tp+fp,1):.3f}")
check("LSP restricted recall", f"{tp/max(tp+fn,1):.3f}")

d = pd.read_csv(C.LABELS_DIR / "java_isp_labeling_sample.csv")
lab = d.llm_isp_risk.astype(str).str.strip().str.lower().map({'true': True, 'false': False})
wide = d['flagged'].fillna(False).astype(bool) & (d.n_members >= 30)
tp = int((wide & lab).sum()); fp = int((wide & ~lab).sum()); fn = int((~wide & lab).sum())
check("ISP width-restricted precision", f"{tp/max(tp+fp,1):.3f}")
check("ISP width-restricted recall", f"{tp/max(tp+fn,1):.3f}")

# ------------------------------------------------------- forbidden text
print("\n-- residue from the superseded study --")
STALE = ["546", "867", "147 repositories", "27 repositories", "0.455", "0.679",
         "13/13", "five agents", "30,984", "2,921", "6,618",
         "Python pull request", "Python corpus", "TypeScript", "JavaScript"]
found = [t for t in STALE if claims(t)]
if found:
    for t in found:
        fails.append(f"stale text present: {t!r}")
        print(f"  FAIL  stale text: {t!r}")
else:
    print("  ok    none of the superseded study's figures or labels appear")
    oks += 1

print("\n" + "=" * 70)
print(f"{oks} checks passed, {len(fails)} FAIL")
print("=" * 70)
sys.exit(1 if fails else 0)
