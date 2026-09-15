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


def stale_present(value: str) -> bool:
    """Like claims(), but a bare number must not sit inside a larger one.

    Without this, the superseded corpus size 546 is 'found' inside the genuine
    V2 figure 1,546, and a real number cannot be written down.
    """
    if not value[0].isdigit():
        return claims(value)
    return re.search(r"(?<![\d,])" + re.escape(value) + r"(?![\d])", TXT) is not None


def check(label: str, expected: str, forbidden: list[str] | None = None) -> None:
    global oks
    if not claims(expected):
        fails.append(f"{label}: thesis does not state {expected!r}")
        print(f"  FAIL  {label}: expected {expected!r} not found")
        return
    for bad in (forbidden or []):
        if stale_present(bad):
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
# The population each stratum was drawn from, needed to weight recall. A sample
# is half flagged and half unflagged; the corpus is nowhere near that, so an
# unweighted recall silently assumes a base rate that does not exist.
POP = {'srp': 'srp_results_java.parquet', 'ocp': 'ocp_results_java.parquet',
       'lsp': 'lsp_results_java.parquet', 'isp': 'isp_results_java.parquet',
       'dip': 'dip_results_java.parquet'}


def _labels(p, col):
    """Every judged round for this principle, pooled.

    For DIP this includes the CENSUS round, which judged every remaining
    flagged pull request in the corpus, so DIP's flagged stratum is complete
    and its precision carries no sampling error.
    """
    frames = []
    for f in sorted(C.LABELS_DIR.glob(f"java_{p}_labeling_sample*.csv")):
        d = pd.read_csv(f)
        if f"llm_{p}_risk" in d.columns:
            frames.append(d)
    return pd.concat(frames, ignore_index=True)


def _population(p, col):
    r = pd.read_parquet(C.CORPUS_DIR / POP[p])
    if 'row_type' in r.columns:          # ISP mixes interface and implementer rows
        r = r[r.row_type == 'interface']
    v = r[col].fillna(False).astype(bool)
    return int(v.sum()), int((~v).sum())


for p, col in PRED.items():
    d = _labels(p, col)
    lab = d[f"llm_{p}_risk"].astype(str).str.strip().str.lower().map(
        {'true': True, 'false': False})
    flag = d[col].fillna(False).astype(bool)
    tp = int((flag & lab).sum()); fp = int((flag & ~lab).sum())
    fn = int((~flag & lab).sum())
    prec = tp / max(tp + fp, 1)
    nf, nu = int(flag.sum()), int((~flag).sum())
    popF, popU = _population(p, col)
    est_tp = tp * popF / max(nf, 1)
    est_fn = fn * popU / max(nu, 1)
    wrec = est_tp / max(est_tp + est_fn, 1e-9)
    check(f"{p.upper()} precision", f"{prec:.3f}")
    check(f"{p.upper()} weighted recall", f"{wrec:.3f}")

# --------------------------------------------------- refinements claimed
print("\n-- the two refinements --")
def _refined(path, labcol, mask):
    d = pd.read_csv(C.LABELS_DIR / path)
    lab = d[labcol].astype(str).str.strip().str.lower().map({'true': True, 'false': False})
    m = mask(d)
    tp = int((m & lab).sum()); fp = int((m & ~lab).sum())
    return tp, fp, tp / max(tp + fp, 1)

RB = lambda d: d.signals.fillna('').str.contains('refused_bequest')
W30 = lambda d: d['flagged'].fillna(False).astype(bool) & (d.n_members >= 30)

for name, stem, labcol, mask in [
        ("LSP restricted", "lsp", "llm_lsp_risk", RB),
        ("ISP width-restricted", "isp", "llm_isp_risk", W30)]:
    a = _refined(f"java_{stem}_labeling_sample.csv", labcol, mask)
    h = _refined(f"java_{stem}_labeling_sample_holdout.csv", labcol, mask)
    tp, fp = a[0] + h[0], a[1] + h[1]
    check(f"{name} precision, fitted sample", f"{a[2]:.3f}")
    check(f"{name} precision, HOLDOUT", f"{h[2]:.3f}")
    check(f"{name} precision, pooled", f"{tp/max(tp+fp,1):.3f}")

# ------------------------------------------------- proxy scope populations
print("\n-- the populations the proxy sections quote --")
_lsp = pd.read_parquet(C.CORPUS_DIR / "lsp_results_java.parquet")
_lv = _lsp["violation"].fillna(False).astype(bool)
check("LSP flagged override pairs", f"{int(_lv.sum()):,}")
check("LSP unflagged override pairs", f"{int((~_lv).sum()):,}")

_ocp = pd.read_parquet(C.CORPUS_DIR / "ocp_results_java.parquet")
check("OCP in-scope dispatch chains", f"{len(_ocp):,}")
check("OCP chains suppressed as a closed enum set",
      f"{int(_ocp['closed_set'].fillna(False).astype(bool).sum()):,}")

_isp = pd.read_parquet(C.CORPUS_DIR / "isp_results_java.parquet")
_ifc = _isp[_isp.row_type == "interface"]
_imp = _isp[_isp.row_type == "implementer"]
check("ISP contracts in scope", f"{len(_ifc):,}")
check("ISP contracts flagged as fat", f"{int(_ifc['flagged'].fillna(False).astype(bool).sum()):,}")
check("ISP implementer relationships with a forced stub",
      f"{int(_imp['forced_stub'].fillna(False).astype(bool).sum()):,}")

# --------------------------------------------------- the Python replication
# Added 2026-09-14 when the thesis became a two-language study. Every value is
# recomputed from the Python V2 parquets and py2_ label CSVs, never typed in.
print("\n-- the Python replication (Section 4.9) --")
_ppr = pd.read_parquet(C.CORPUS_DIR / "py_pr_results.parquet")
_pok = _ppr[_ppr.status == "ok"]
check("Python curated pull requests", f"{len(_ppr):,}")
check("Python analysable pull requests", f"{len(_pok):,}")
check("Python analysable repositories", f"{_pok.full_name.nunique():,}")
_pgold = pd.read_parquet(C.CORPUS_DIR / "gold_set.parquet")
check("Python curated repositories", f"{_pgold.full_name.nunique():,}")

_T = lambda s: s.astype(str).str.strip().str.lower().isin(["true", "1", "1.0"])  # noqa: E731
import json as _json  # noqa: E402
for p, col in PRED.items():
    d = pd.read_csv(C.LABELS_DIR / f"py2_{p}_labeling_sample.csv")
    pop = _json.loads((C.LABELS_DIR / f"py2_{p}_labeling_sample.population.json")
                      .read_text(encoding="utf-8"))["population"]
    f, lab = _T(d[col]), _T(d[f"llm_{p}_risk"])
    tp, fn = int((f & lab).sum()), int((~f & lab).sum())
    nf, nu = int(f.sum()), int((~f).sum())
    est_tp, est_fn = tp * pop["True"] / max(nf, 1), fn * pop["False"] / max(nu, 1)
    check(f"Python {p.upper()} precision", f"{tp / max(nf, 1):.3f}")
    check(f"Python {p.upper()} weighted recall", f"{est_tp / max(est_tp + est_fn, 1e-9):.3f}")
_isp_pop = _json.loads((C.LABELS_DIR / "py2_isp_labeling_sample.population.json")
                       .read_text(encoding="utf-8"))["population"]["True"]
check("Python ISP census size", f"all {_isp_pop} flagged contracts")

_pl = pd.read_parquet(C.CORPUS_DIR / "lsp_results.parquet")
_pl = _pl[_pl.pr_id.isin(_pok.pr_id)]
_rb = _pl.signals.fillna("").str.contains("refused_bequest") & _pl.violation.fillna(False).astype(bool)
_rb_present = _pl[_rb].drop_duplicates(["full_name", "module", "qualname", "base_id", "method"]).shape[0]
check("Python refused-bequest relationships present", f"{_rb_present} refused-bequest")
_rb_intro = int(_pok[_pok.lsp_risk.fillna(False).astype(bool)]
                .lsp_risk_reasons.fillna("").str.contains("refused_bequest").sum())
_words = {5: "five"}
check("Python pull requests introducing a refused bequest",
      f"{_words.get(_rb_intro, _rb_intro)} pull requests introduced one")

_d = pd.read_csv(C.LABELS_DIR / "py2_lsp_labeling_sample.csv")
_fl = _d[_T(_d.violation)]
_tpr = _fl[_T(_fl.llm_lsp_risk)]
_blocked = _tpr.signals.fillna("").str.contains("param_removed|param_required_added|param_type_narrowed")
check("Python LSP true positives resting on javac-rejectable signals",
      f"{int(_blocked.sum())} of the {len(_tpr)}")

_man = pd.read_parquet(C.CORPUS_DIR / "clone_manifest.parquet")
_per = _man.groupby("pr_id").extract_status.agg(
    lambda s: "usable" if (s == "ok").any()
    else ("loss" if set(s) & {"fetch_failed", "diff_failed", "file_missing"} else "oos"))
_loss = int((_per == "loss").sum())
check("Python retrieval loss", f"{_loss} Python pull requests ({100 * _loss / len(_per):.1f}")

_mlog = Path("logs_archive/py2_model.txt").read_text(encoding="utf-8")
_ocp_block = _mlog.split("OCP   n =")[1]
_cc = re.search(r"agent\[T\.Claude_Code\]\s+(\S+)\s+\[\s*(\S+),\s*(\S+)\]", _ocp_block)
check("Python OCP Claude Code odds ratio",
      f"{float(_cc.group(1)):.2f} ({float(_cc.group(2)):.2f}--{float(_cc.group(3)):.2f})")

# ------------------------------------------------------- forbidden text
print("\n-- residue from the superseded study --")
# Bare numbers are matched on a DIGIT BOUNDARY, so a V1 figure like 546 is not
# reported when it appears inside a genuine V2 one such as 1{,}546.
# The thesis became a two-language study on 2026-09-14, so "Python corpus",
# "Python pull request" and "2,921" (now the Python V2 repository count, a
# coincidence with a V1 figure) left this list. What remains is unique to the
# superseded study and must never reappear.
STALE = ["546", "867", "147 repositories", "27 repositories", "0.455", "0.679",
         "13/13", "five agents", "30,984", "6,618", "TypeScript", "JavaScript"]

found = [t for t in STALE if stale_present(t)]
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
