"""
The four cross-language questions of PROMPT.txt section 2, answered from the
Python V2 data and labels, beside the Java values the finished thesis reports.

  1. Does change size still dominate?          (read from the joint model log)
  2. Does the Open/Closed agent effect repeat?  (read from the joint model log)
  3. Is risk still present-not-introduced?      (parquets, precision-corrected)
  4. Does the categorical-vs-numeric refinement
     lesson repeat?                             (Java rules applied, unchanged,
                                                 to the Python judgements)

Question 4 is an OUT-OF-LANGUAGE HOLDOUT: both Java refinements were fixed on
Java data before any Python label existed, so applying them to the Python
judgements cannot fit anything.

Precision is recomputed here from the judged samples rather than typed in, so
this script disagrees with any document that has gone stale.

Run:  python vibebench/04_report/py2_cross_language.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

MODEL_LOG = C.ROOT / "logs_archive" / "py2_model.txt" if hasattr(C, "ROOT") else \
    Path(__file__).resolve().parents[2] / "logs_archive" / "py2_model.txt"
PRED = {"srp": "srp_risk", "ocp": "smell", "lsp": "violation", "isp": "flagged", "dip": "dip_risk"}
JAVA = {  # the finished thesis, chapter 4; never recomputed here
    "doubling": 1.9, "ocp_cc": "2.22 [1.21, 4.07]",
    "present": {"srp": "11,822 / ~4,100", "ocp": "2,577 / ~850", "isp": "1,090 / ~350",
                "lsp": "681 / ~270", "dip": "195 / ~175"},
    "introduced": {"srp": "n/a", "ocp": "2.2%", "isp": "2.1%", "lsp": "0.4%", "dip": "4.1%"},
    "rb": "681 present, 16 introduced", "lsp_rule": "1.000 (64/64, holdout 33/33)",
    "isp_rule": "0.862 fitted -> 0.704 holdout",
}


def truthy(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.lower().isin(["true", "1", "1.0"])


def precision(p: str) -> tuple[float, int, int]:
    d = pd.read_csv(C.LABELS_DIR / f"py2_{p}_labeling_sample.csv")
    f = d[truthy(d[PRED[p]])]
    lab = truthy(f[f"llm_{p}_risk"])
    return lab.mean(), int(lab.sum()), len(f)


def model_lines() -> None:
    log = MODEL_LOG
    if not log.exists():
        print(f"  (model log {log} missing; run fit_mixed_model.py --lang py)")
        return
    txt = log.read_text(encoding="utf-8")
    # Names come from each section's own header line; an assumed order printed
    # LSP's odds ratios under ISP's name the first time this ran.
    blocks = re.split(r"={20,}\n", txt)
    name = None
    for b in blocks:
        h = re.match(r"\s*(SRP|OCP|LSP|ISP|DIP)\b", b)
        if h and "per DOUBLING" not in b:
            name = h.group(1)
            continue
        if "per DOUBLING" not in b or name is None:
            continue
        dbl = re.search(r"per DOUBLING of files touched: OR (\S+ \[[^\]]+\])", b).group(1)
        cc = re.search(r"agent\[T\.Claude_Code\]\s+(\S+)\s+\[\s*(\S+),\s*(\S+)\]\s+(\S+)", b)
        print(f"  {name:<4} doubling OR {dbl:<22} Claude_Code OR {cc.group(1)} "
              f"[{cc.group(2)} {cc.group(3)}] p={cc.group(4)}")


def main() -> None:
    pr = pd.read_parquet(C.CORPUS_DIR / "py_pr_results.parquet")
    ok = pr[pr.status == "ok"]
    n_ok = len(ok)
    B = lambda s: s.fillna(False).astype(bool)  # noqa: E731

    print("=" * 78)
    print("Q1/Q2  JOINT MODEL (GEE, agent + log files + months, clustered by repository)")
    print("=" * 78)
    model_lines()
    print(f"  Java: OR {JAVA['doubling']} per doubling on every principle; OCP Claude_Code {JAVA['ocp_cc']}")

    print("\n" + "=" * 78)
    print(f"Q3  PRESENT IN TOUCHED CODE vs INTRODUCED, corrected by measured precision")
    print("=" * 78)
    print(f"  {'':<5}{'flagged present':>16}{'est. genuine':>14}{'PRs introduced':>17}{'corrected':>11}"
          f"   Java present / genuine, introduced")
    intro = {"srp": "srp_risk_authored", "ocp": "ocp_risk", "lsp": "lsp_risk",
             "isp": "isp_risk", "dip": "dip_risk"}
    for p in ["srp", "ocp", "isp", "lsp", "dip"]:
        pop = json.loads((C.LABELS_DIR / f"py2_{p}_labeling_sample.population.json")
                         .read_text(encoding="utf-8"))["population"]
        present = int(pop.get("True", 0))
        prec, _, _ = precision(p)
        n_int = int(B(ok[intro[p]]).sum())
        print(f"  {p.upper():<5}{present:>16,}{present * prec:>14,.0f}{n_int:>11,} "
              f"({n_int / n_ok:.1%}){n_int * prec / n_ok:>10.1%}"
              f"   {JAVA['present'][p]}, {JAVA['introduced'][p]}")
    print("  SRP 'introduced' = PRs whose flagged production class is newly authored "
          "(Java did not measure it)")

    lsp = pd.read_parquet(C.CORPUS_DIR / "lsp_results.parquet")
    lsp = lsp[lsp.pr_id.isin(ok.pr_id)]
    key = ["full_name", "module", "qualname", "base_id", "method"]
    rb = lsp.signals.fillna("").str.contains("refused_bequest")
    rb_present = lsp[B(lsp.violation) & rb].drop_duplicates(key).shape[0]
    rb_intro = int(ok[B(ok.lsp_risk)].lsp_risk_reasons.fillna("").str.contains("refused_bequest").sum())
    print(f"\n  refused bequest: {rb_present} relationships present in touched code, "
          f"{rb_intro} PRs introduced one   (Java: {JAVA['rb']})")

    print("\n" + "=" * 78)
    print("Q4  THE JAVA REFINEMENTS, UNCHANGED, ON THE PYTHON JUDGEMENTS (out-of-language holdout)")
    print("=" * 78)
    d = pd.read_csv(C.LABELS_DIR / "py2_lsp_labeling_sample.csv")
    f = d[truthy(d.violation)]
    lab = truthy(f.llm_lsp_risk)
    sig = f.signals.fillna("")
    for s in ["refused_bequest", "param_removed", "param_required_added", "param_type_narrowed",
              "return_type_widened"]:
        m = sig.str.contains(s)
        if m.any():
            print(f"  LSP signal {s:<22} {int(lab[m].sum())}/{int(m.sum())} = {lab[m].mean():.3f}")
    print(f"  -> categorical rule 'LSP restricted to refused_bequest'   Java {JAVA['lsp_rule']}")
    i = pd.read_csv(C.LABELS_DIR / "py2_isp_labeling_sample.csv")
    fi = i[truthy(i.flagged)]
    li = truthy(fi.llm_isp_risk)
    for cut in (7, 13, 30):
        m = fi.n_abstract_methods >= cut
        print(f"  ISP abstract members >= {cut:<3} {int(li[m].sum())}/{int(m.sum())} = "
              f"{(li[m].mean() if m.any() else float('nan')):.3f}   (census of flagged contracts)")
    print(f"  -> numeric rule 'ISP restricted to >= 30 members'        Java {JAVA['isp_rule']}")
    print("  NOTE Java counted all members; Python counts abstract members (the V2 contract size).")

    print("\n" + "=" * 78)
    print("Q5  OUTCOME, tier-matched (tiers 2-4), rejected vs merged, ADJUSTED for size and agent")
    print("=" * 78)
    import numpy as np
    import statsmodels.api as sm
    import statsmodels.formula.api as smf
    t = ok[ok.pair_tier.isin([2.0, 3.0, 4.0]) & ok.outcome.isin(["merged", "rejected"])].copy()
    t["rej"] = (t.outcome == "rejected").astype(int)
    t["log_files"] = np.log1p(t.n_touched_files)
    # dummies by hand: the module alias C (config) shadows patsy's C() here
    t["cc"] = (t.agent == "Claude_Code").astype(int)
    t["codex"] = (t.agent == "OpenAI_Codex").astype(int)
    print(f"  n = {len(t):,}; median files merged {t[t.rej == 0].n_touched_files.median():.0f}, "
          f"rejected {t[t.rej == 1].n_touched_files.median():.0f}")
    for fcol in ["ocp_risk", "isp_risk", "dip_risk", "lsp_risk"]:
        t["y"] = B(t[fcol]).astype(int)
        m = smf.gee("y ~ rej + log_files + cc + codex", groups="full_name",
                    data=t, family=sm.families.Binomial(),
                    cov_struct=sm.cov_struct.Exchangeable()).fit()
        lo, hi = np.exp(m.conf_int().loc["rej"])
        print(f"  {fcol[:3].upper()}  rejected OR {np.exp(m.params['rej']):.2f} "
              f"[{lo:.2f}, {hi:.2f}]  p={m.pvalues['rej']:.4f}   "
              f"(flagged {int(t.y.sum())})")

    print("\n  Precision used above:")
    for p in PRED:
        v, k, n = precision(p)
        print(f"    {p.upper():<4} {k}/{n} = {v:.3f}")


if __name__ == "__main__":
    main()
