"""
Step 4b - Score the SRP rubric against completed reference labels.

PROVENANCE: the reference labels in labels/srp_labeling_sample.csv were
produced by an LLM (Claude) reading each class's actual source code -- they are
NOT independent human-expert labels. Precision/recall reported here therefore
measures agreement between the numeric rubric and an LLM judge, not against
human ground truth. The column is named `llm_srp_risk` to keep this explicit.

Reads a labeling CSV (after `llm_srp_risk` is filled with 0/1 per row) and
reports precision/recall/F1 of the automated `srp_risk` rubric flag against
the reference label, overall and per agent.

Works for any principle's labels via --pred-col/--label-col (defaults keep
the original SRP behavior).

Run:  python vibebench/labels/score_labels.py                      # SRP tuning set
      python vibebench/labels/score_labels.py --file srp_holdout_sample.csv
      python vibebench/labels/score_labels.py --file isp_labeling_sample.csv \
          --pred-col isp_risk --label-col llm_isp_risk
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else float("nan")
    return precision, recall, f1


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="srp_labeling_sample.csv",
                    help="labels CSV inside vibebench/labels/ to score")
    ap.add_argument("--pred-col", default="srp_risk",
                    help="rubric prediction column (bool)")
    ap.add_argument("--label-col", default="llm_srp_risk",
                    help="reference label column (0/1)")
    args = ap.parse_args()

    path = C.LABELS_DIR / args.file
    df = pd.read_csv(path)

    labeled = df[df[args.label_col].isin([0, 1, "0", "1"])].copy()
    labeled[args.label_col] = labeled[args.label_col].astype(int)
    n_unlabeled = len(df) - len(labeled)
    if n_unlabeled:
        print(f"[score] {n_unlabeled} rows still unlabeled (skipped)")
    if labeled.empty:
        print(f"[score] no labeled rows yet -- fill in {args.label_col} (0/1) first")
        return

    def report(name: str, sub: pd.DataFrame) -> None:
        pred = sub[args.pred_col].astype(bool)
        ref = sub[args.label_col]
        tp = int((pred & (ref == 1)).sum())
        fp = int((pred & (ref == 0)).sum())
        fn = int((~pred & (ref == 1)).sum())
        tn = int((~pred & (ref == 0)).sum())
        p, r, f1 = prf(tp, fp, fn)
        print(f"{name:<14} n={len(sub):<4} TP={tp} FP={fp} FN={fn} TN={tn}  "
              f"precision={p:.3f} recall={r:.3f} f1={f1:.3f}")

    print(f"=== {args.pred_col} rubric vs LLM (Claude) reference label "
          f"-- {args.file} ===")
    report("OVERALL", labeled)
    print()
    for agent, sub in labeled.groupby("agent"):
        report(agent, sub)


if __name__ == "__main__":
    main()
