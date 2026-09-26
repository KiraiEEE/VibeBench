"""Score the Java detectors against the judged labels.

This is the Java twin of `score_py2_labels.py`, and it exists because the Java
weighted recalls in the thesis were computed by hand in 2026-09 and were
weighted with the WRONG population: the raw result rows (test code and
duplicate rows included) instead of the population the sample was actually
drawn from (production entities, deduplicated), which is what
`make_java_labeling_sample.py` draws from. That mismatch moved four of the five
recall figures, for example ISP 0.552 against 0.464.

Rules, identical to the Python scorer:
  * the population comes from the sampler's own SPEC, so the numbers describe
    the population that was sampled and the one the report publishes;
  * precision is the flagged stratum; when every flagged entity was judged
    (DIP's census) it is exact and carries no interval;
  * recall is ALWAYS stratum-weighted, with a percentile bootstrap that
    resamples within each stratum, seeded from config.RANDOM_SEED;
  * the raw recall is printed but marked as not quotable.

Run:  python vibebench/labels/score_java_labels.py [--principle srp]
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))
sys.path.insert(0, str(_HERE.parent))
import config as C                      # noqa: E402
import make_java_labeling_sample as MJ  # noqa: E402

PRED = {"srp": "srp_risk", "ocp": "smell", "lsp": "violation",
        "isp": "flagged", "dip": "dip_risk"}


def truthy(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.lower().isin(["true", "1", "1.0"])


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - r) / d, (c + r) / d


def population(principle: str) -> tuple[int, int]:
    """Flagged and unflagged counts in the population the sampler draws from."""
    path, pred, _judge, keys, flt = MJ.SPEC[principle]
    d = pd.read_parquet(path)
    if flt is not None:
        d = flt(d)
    d = d.copy()
    d[pred] = d[pred].fillna(False).astype(bool)
    d = d.drop_duplicates([k for k in keys if k in d.columns])
    return int(d[pred].sum()), int((~d[pred]).sum())


def labels(principle: str) -> pd.DataFrame:
    """Every judged round for this principle, pooled (sample, holdout, census)."""
    judge = f"llm_{principle}_risk"
    frames = []
    for f in sorted(C.LABELS_DIR.glob(f"java_{principle}_labeling_sample*.csv")):
        d = pd.read_csv(f)
        if judge in d.columns:
            d = d[d[judge].notna()]
            if len(d):
                frames.append(d.assign(_round=f.stem))
    if not frames:
        sys.exit(f"no judged rows for {principle}")
    return pd.concat(frames, ignore_index=True)


def score(principle: str) -> None:
    pred = PRED[principle]
    judge = f"llm_{principle}_risk"
    d = labels(principle)
    d["_pred"] = truthy(d[pred])
    d["_lab"] = truthy(d[judge])

    F, U = d[d._pred], d[~d._pred]
    tp, fp = int(F._lab.sum()), int((~F._lab).sum())
    fn, tn = int(U._lab.sum()), int((~U._lab).sum())
    nF, nU = len(F), len(U)
    NF, NU = population(principle)

    print("=" * 74)
    print(f"{principle.upper()}   Java, {len(d)} judged rows from "
          f"{d._round.nunique()} round(s)")
    print("=" * 74)
    census = nF >= NF
    print(f"flagged stratum   : {nF} sampled of {NF:,} in the population"
          + ("   (CENSUS: precision is exact)" if census else ""))
    print(f"unflagged stratum : {nU} sampled of {NU:,}")
    print(f"TP={tp} FP={fp} FN={fn} TN={tn}")

    prec = tp / nF if nF else float("nan")
    lo, hi = wilson(tp, nF)
    print(f"PRECISION         : {tp}/{nF} = {prec:.3f}"
          + ("   exact, no sampling error" if census
             else f"   [95% CI {lo:.3f}, {hi:.3f}]"))

    def wrec(tp_: int, fn_: int) -> float:
        et = tp_ * NF / max(nF, 1)
        ef = fn_ * NU / max(nU, 1)
        return et / (et + ef) if (et + ef) else float("nan")

    rec = wrec(tp, fn)
    rng = np.random.default_rng(C.RANDOM_SEED)
    fl, ul = F._lab.to_numpy(), U._lab.to_numpy()
    boots = []
    for _ in range(4000):
        bf = rng.choice(fl, nF, replace=True) if nF else fl
        bu = rng.choice(ul, nU, replace=True) if nU else ul
        boots.append(wrec(int(bf.sum()), int(bu.sum())))
    boots = np.array([b for b in boots if not math.isnan(b)])
    blo, bhi = (float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5)))
    print(f"WEIGHTED RECALL   : {rec:.3f}   [bootstrap 95% CI {blo:.3f}, {bhi:.3f}]")
    raw = tp / (tp + fn) if (tp + fn) else float("nan")
    print(f"(raw recall {raw:.3f} is NOT quotable: it assumes a 50/50 base rate)")

    if "agent" in F.columns:
        print("\nPRECISION PER AGENT:")
        for ag, g in F.groupby("agent"):
            k, n = int(g._lab.sum()), len(g)
            l2, h2 = wilson(k, n)
            print(f"  {ag:<14} {k}/{n} = {k / n:.3f}   [CI {l2:.3f}, {h2:.3f}]")
    print()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--principle", choices=sorted(PRED))
    a = ap.parse_args()
    for p in ([a.principle] if a.principle else ["srp", "ocp", "lsp", "isp", "dip"]):
        score(p)


if __name__ == "__main__":
    main()
