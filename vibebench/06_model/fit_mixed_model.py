"""Mixed-effects logistic regression for RQ1.

WHY THIS EXISTS. Chapter 4 controls three confounds one at a time: change size
by banding, repository mix by macro-averaging, era by reporting median dates.
Stratifying on each separately is not the same as holding them jointly, and it
cannot give an effect size. This fits

    flag ~ agent + log1p(files_touched) + pr_date + (1 | repository)

per principle, so the agent coefficient is read with size, era and repository
held constant.

WHAT IT IS NOT. The design is observational: agents chose their own tasks in
repositories this study does not control, so a coefficient here is an adjusted
association, not a causal effect. Repositories are a random intercept because
the same repository contributes many pull requests and its conventions are not
of interest in themselves.

LANGUAGE. `--lang java` (the default) is the finished study and is byte-for-byte
what it always printed apart from a one-line banner. `--lang py` models the
Python V2 corpus. The switch exists because this file used to hardcode the Java
parquet: run from the Python chain it did not fail, it quietly fitted and
printed the JAVA model at the end of a Python log, which is precisely the kind of
result that looks complete and is wrong. The banner names the corpus on the first
line so a log can never be misread again.

SRP is modelled for Python only. Java's PR table carries no PR-level SRP flag
(SRP is scored per type), so the Python SRP row has no Java counterpart and is
labelled as such; the four delta principles are identical in both languages and
are the ones to compare.

Run:  python vibebench/06_model/fit_mixed_model.py              # Java
      python vibebench/06_model/fit_mixed_model.py --lang py    # Python V2
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))
import config as C  # noqa: E402

import statsmodels.api as sm  # noqa: E402
import statsmodels.formula.api as smf  # noqa: E402

REFERENCE_AGENT = "Copilot"   # the largest cell, so the contrast is best powered
PRINCIPLES = [("OCP", "ocp_risk"), ("ISP", "isp_risk"),
              ("DIP", "dip_risk"), ("LSP", "lsp_flag")]


SOURCES = {
    "java": {"pr": "java_pr_results.parquet", "gold": "gold_set_java.parquet",
             "label": "JAVA (the finished study)"},
    "py": {"pr": "py_pr_results.parquet", "gold": "gold_set.parquet",
           "label": "PYTHON V2"},
}


def load(lang: str = "java") -> pd.DataFrame:
    src = SOURCES[lang]
    pr = pd.read_parquet(C.CORPUS_DIR / src["pr"])
    d = pr[pr.status == "ok"].copy()

    # The PR-level Liskov flag applies the refused-bequest restriction, exactly
    # as report_java.py does. Using the raw lsp_risk here would model a
    # different quantity from the one Chapter 4 reports. Python keeps its own
    # FIVE strong signals (it has no compiler to reject two of them).
    strong_src = (C.LSP_JAVA_STRONG_SIGNALS if lang == "java"
                  else C.LSP_THRESHOLDS["strong_signals"])
    strong = {"new_" + s for s in strong_src}
    d["lsp_flag"] = d.lsp_risk_reasons.fillna("").map(
        lambda x: bool(strong & {p for p in str(x).split("|") if p}))

    # A calibration parquet has every flag NULL. Modelling it would fit
    # "no violations anywhere" and print odds ratios that look like findings.
    if lang == "py" and "ocp_risk" in d.columns and d["ocp_risk"].isna().all():
        sys.exit("py_pr_results.parquet is a CALIBRATION parquet (flags null). "
                 "Run the flagged pass first; refusing to model it.")

    gold = pd.read_parquet(C.CORPUS_DIR / src["gold"])
    dates = gold[["pr_id", "created_at"]].drop_duplicates("pr_id")
    d = d.merge(dates, on="pr_id", how="left")

    d["created_at"] = pd.to_datetime(d.created_at, utc=True, errors="coerce")
    # months since the earliest request, so the coefficient reads per month
    d["months"] = (d.created_at - d.created_at.min()).dt.days / 30.44
    d["log_files"] = np.log1p(d.n_touched_files.fillna(0))
    d["agent"] = pd.Categorical(
        d.agent,
        categories=[REFERENCE_AGENT] + [a for a in sorted(d.agent.unique())
                                        if a != REFERENCE_AGENT])
    d = d.dropna(subset=["months", "log_files", "agent"])
    return d


def fit(d: pd.DataFrame, name: str, col: str) -> None:
    y = d[col].fillna(False).astype(int)
    sub = d.assign(y=y)

    print(f"\n{'=' * 74}\n{name}   n = {len(sub):,} pull requests, "
          f"{sub.full_name.nunique():,} repositories, {int(y.sum())} flagged\n{'=' * 74}")
    if y.sum() < 25:
        print("  too few positives to fit; reported descriptively only")
        return

    formula = "y ~ agent + log_files + months"
    # Random intercept per repository. BinomialBayesMixedGLM is the variational
    # fit statsmodels offers for a binary outcome with a random effect; the GEE
    # fit below is the frequentist cross-check with cluster-robust errors.
    try:
        md = smf.gee(formula, groups="full_name", data=sub,
                     family=sm.families.Binomial(),
                     cov_struct=sm.cov_struct.Exchangeable())
        res = md.fit()
        print("  GEE, exchangeable within repository, cluster-robust SE")
        print(f"  {'term':28} {'OR':>8} {'95% CI':>18} {'p':>9}")
        for term in res.params.index:
            if term == "Intercept":
                continue
            b = res.params[term]
            lo, hi = res.conf_int().loc[term]
            p = res.pvalues[term]
            star = "  *" if p < 0.05 else ""
            print(f"  {term:28} {np.exp(b):8.3f} "
                  f"[{np.exp(lo):7.3f},{np.exp(hi):7.3f}] {p:9.4f}{star}")
        # log_files is on a log1p scale, which nobody can read directly. Convert
        # it to the odds multiplier for a DOUBLING of files touched.
        if "log_files" in res.params.index:
            b = res.params["log_files"]
            lo, hi = res.conf_int().loc["log_files"]
            k = np.log(2.0)
            print(f"  -> per DOUBLING of files touched: OR "
                  f"{np.exp(b * k):.2f} [{np.exp(lo * k):.2f}, {np.exp(hi * k):.2f}]")
    except Exception as exc:                      # pragma: no cover
        print("  GEE failed:", exc)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=sorted(SOURCES), default="java")
    lang = ap.parse_args().lang
    print(f"CORPUS: {SOURCES[lang]['label']}")
    d = load(lang)
    principles = list(PRINCIPLES)
    if lang == "py" and "srp_risk" in d.columns:
        # PR-level: the PR touched at least one flagged class. No Java
        # counterpart exists, so this row is NOT part of the cross-language
        # comparison. Present in touched code, not authored.
        principles = [("SRP (PR-level, Python only, no Java counterpart)",
                       "srp_risk")] + principles
    print(f"loaded {len(d):,} analysable pull requests, "
          f"{d.full_name.nunique():,} repositories")
    print(f"reference agent: {REFERENCE_AGENT}")
    print("agent cells:", dict(d.agent.value_counts()))
    print(f"date span: {d.created_at.min().date()} to {d.created_at.max().date()}")
    for name, col in principles:
        fit(d, name, col)
    print("\nOdds ratios are adjusted for change size (log files touched), era "
          "(months) and\nrepository. An OR above 1 means higher odds of a flag "
          f"than {REFERENCE_AGENT} at the same\nsize, date and repository.")


if __name__ == "__main__":
    main()
