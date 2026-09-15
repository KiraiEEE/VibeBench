"""
Score a judged Python V2 labelling sample: precision, STRATUM-WEIGHTED recall,
evenness across agents and outcomes, and the rubric's drift self-check.

WHY NOT score_labels.py. That script reports a RAW recall. The sample is half
flagged and half unflagged; the corpus is nowhere near that (LSP: 2,069 flagged
against 44,004 unflagged production pairs). A raw recall silently assumes a base
rate that does not exist, and this study never quotes one. Recall here is
weighted with the population sizes make_py2_labeling_sample.py recorded at draw
time:

    est_TP = TP * N_flagged   / n_flagged_sampled
    est_FN = FN * N_unflagged / n_unflagged_sampled
    recall = est_TP / (est_TP + est_FN)

The interval on weighted recall is a percentile bootstrap that resamples WITHIN
each stratum, since the strata were drawn independently.

THE DRIFT CHECK is mandatory under PY2_JUDGING_RUBRIC.md: the sample is shuffled,
so the first and second halves by position should agree. A gap above ~1.5x in
either stratum's positive rate means the standard moved while judging.

Run:  python vibebench/labels/score_py2_labels.py --principle lsp
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

PRED = {"srp": "srp_risk", "ocp": "smell", "lsp": "violation",
        "isp": "flagged", "dip": "dip_risk"}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - m) / d), min(1.0, (c + m) / d))


def to_bool(s: pd.Series) -> pd.Series:
    return s.map(lambda v: None if pd.isna(v) or str(v).strip() == "" else
                 str(v).strip().lower() in ("true", "1", "1.0"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--principle", required=True, choices=sorted(PRED))
    ap.add_argument("--round", default="")
    a = ap.parse_args()
    p, pred = a.principle, PRED[a.principle]

    csv = C.LABELS_DIR / f"py2_{p}_labeling_sample{a.round}.csv"
    pop = json.loads(csv.with_suffix(".population.json").read_text(encoding="utf-8"))
    d = pd.read_csv(csv)
    d["_pred"] = d[pred].map(lambda v: str(v).strip().lower() in ("true", "1", "1.0"))
    d["_lab"] = to_bool(d[f"llm_{p}_risk"])
    judged = d[d["_lab"].notna()].copy()
    judged["_lab"] = judged["_lab"].astype(bool)
    print(f"=== {p.upper()} : {len(judged)}/{len(d)} rows judged ===")
    if len(judged) < len(d):
        print(f"  !! {len(d) - len(judged)} rows unjudged; numbers below are provisional")

    F = judged[judged._pred]
    U = judged[~judged._pred]
    tp, fp = int(F._lab.sum()), int((~F._lab).sum())
    fn, tn = int(U._lab.sum()), int((~U._lab).sum())
    nF, nU = len(F), len(U)
    NF, NU = pop["population"].get("True", 0), pop["population"].get("False", 0)

    prec = tp / nF if nF else float("nan")
    lo, hi = wilson(tp, nF)
    census = nF == NF
    print(f"\nflagged stratum   : {nF} sampled of {NF:,} in the population"
          + ("  (CENSUS: precision is exact)" if census else ""))
    print(f"unflagged stratum : {nU} sampled of {NU:,}")
    print(f"\nTP={tp} FP={fp} FN={fn} TN={tn}")
    print(f"PRECISION         : {tp}/{nF} = {prec:.3f}"
          + ("  exact, no sampling error" if census else f"  [95% CI {lo:.3f}, {hi:.3f}]"))

    def wrec(tp_, fn_, nF_, nU_):
        et = tp_ * NF / max(nF_, 1)
        ef = fn_ * NU / max(nU_, 1)
        return et / (et + ef) if (et + ef) else float("nan")

    rec = wrec(tp, fn, nF, nU)
    rng = np.random.default_rng(C.RANDOM_SEED)
    fl, ul = F._lab.to_numpy(), U._lab.to_numpy()
    boots = []
    for _ in range(4000):
        bf = rng.choice(fl, nF, replace=True) if nF else fl
        bu = rng.choice(ul, nU, replace=True) if nU else ul
        boots.append(wrec(int(bf.sum()), int(bu.sum()), nF, nU))
    boots = np.array([b for b in boots if not math.isnan(b)])
    blo, bhi = (np.percentile(boots, 2.5), np.percentile(boots, 97.5)) if len(boots) else (float("nan"),) * 2
    print(f"WEIGHTED RECALL   : {rec:.3f}  [bootstrap 95% CI {blo:.3f}, {bhi:.3f}]")
    raw = tp / (tp + fn) if (tp + fn) else float("nan")
    print(f"(raw recall {raw:.3f} is NOT quotable: it assumes a 50/50 base rate)")

    print("\nPRECISION PER AGENT (evenness licenses any agent comparison):")
    for ag, g in F.groupby("agent"):
        k, n = int(g._lab.sum()), len(g)
        l2, h2 = wilson(k, n)
        print(f"  {ag:<14} {k}/{n} = {k/n:.3f}  [CI {l2:.3f}, {h2:.3f}]")

    if "outcome" in F.columns:
        print("\nPRECISION PER OUTCOME (evenness licenses the outcome contrast):")
        pr = {}
        for oc, g in F.groupby("outcome"):
            k, n = int(g._lab.sum()), len(g)
            pr[oc] = k / n
            print(f"  {oc:<10} {k}/{n} = {k/n:.3f}")
        if len(pr) > 1 and max(pr.values()) - min(pr.values()) > 0.15:
            print("  !! precision spread across outcomes exceeds 15 points")

    # PR CLUSTERING (added after the LSP round). The entity sample is deduplicated
    # by entity, not by pull request, so one PR that touched many similar entities
    # can contribute dozens of rows sharing one verdict. In LSP two PRs held 50 of
    # 100 flagged rows, one of them 20 rows with no true positive, and that alone
    # produced a 46-point precision spread across outcomes that the judging did
    # not. Row-level precision is still the estimate; these lines show how much of
    # it rests on a few PRs, so a spread can be diagnosed rather than explained
    # away or, worse, "fixed" by re-judging labels that were never outcome-aware.
    if nF:
        g = (F.groupby(["full_name", "pr_number"])
               .agg(rows=("_lab", "size"), tp=("_lab", "sum"),
                    outcome=("outcome", "first") if "outcome" in F.columns else ("_lab", "size"),
                    agent=("agent", "first"))
               .reset_index().sort_values("rows", ascending=False))
        g["prec"] = g.tp / g.rows
        top2 = int(g.rows.head(2).sum())
        print(f"\nPR CLUSTERING: {nF} flagged rows from {len(g)} PRs; the 2 largest hold "
              f"{top2} rows ({top2 / nF:.0%})")
        for _, r in g.head(3).iterrows():
            print(f"  {r.full_name}#{int(r.pr_number)} ({r.agent}, {r.outcome}): "
                  f"{int(r.tp)}/{int(r.rows)} true")
        print(f"  PR-weighted precision (each PR counts once): {g.prec.mean():.3f}")
        keep = set(zip(g.full_name.iloc[2:], g.pr_number.iloc[2:]))
        rest = F[[(a, b) in keep for a, b in zip(F.full_name, F.pr_number)]]
        if len(rest):
            print(f"  precision without the 2 largest PRs: {int(rest._lab.sum())}/{len(rest)} "
                  f"= {rest._lab.mean():.3f}")
            if "outcome" in rest.columns:
                print("    by outcome: " + ", ".join(
                    f"{k} {int(v._lab.sum())}/{len(v)}={v._lab.mean():.2f}"
                    for k, v in rest.groupby("outcome")))
            print("    by agent  : " + ", ".join(
                f"{k} {int(v._lab.sum())}/{len(v)}={v._lab.mean():.2f}"
                for k, v in rest.groupby("agent")))

    print("\nDRIFT SELF-CHECK (rubric, mandatory): positive rate by position half")
    half = len(d) // 2
    for name, part in (("flagged", judged[judged._pred]), ("unflagged", judged[~judged._pred])):
        first = part[part.index < half]
        second = part[part.index >= half]
        r1 = first._lab.mean() if len(first) else float("nan")
        r2 = second._lab.mean() if len(second) else float("nan")
        ratio = (max(r1, r2) / min(r1, r2)) if min(r1, r2) > 0 else (float("inf") if max(r1, r2) > 0 else 1.0)
        flag = "OK" if ratio <= 1.5 or (abs(r1 - r2) * min(len(first), len(second)) < 3) else "DRIFT?"
        print(f"  {name:<9} first half {first._lab.sum()}/{len(first)} = {r1:.3f}   "
              f"second half {second._lab.sum()}/{len(second)} = {r2:.3f}   ratio {ratio:.2f}  {flag}")


if __name__ == "__main__":
    main()
