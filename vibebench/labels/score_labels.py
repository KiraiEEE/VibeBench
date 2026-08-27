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


_TRUE_TOKENS = {"1", "1.0", "true", "yes", "y", "t"}
_FALSE_TOKENS = {"0", "0.0", "false", "no", "n", "f"}


def coerce_labels(s: pd.Series) -> pd.Series:
    """Map a label column to boolean, tolerating every encoding the label
    files have historically used (bool, 0/1 int, 0.0/1.0 float, and the
    string forms of all of those).

    Returns a boolean Series with pd.NA for genuinely unlabeled rows.

    Rationale: an earlier version filtered with `.isin([0, 1, "0", "1"])`,
    which silently dropped EVERY row of a file whose column had been written
    as the strings "False"/"True"/"0.0"/"1.0" -- reporting "no labeled rows"
    instead of failing. Unrecognized tokens now raise instead of vanishing.
    """
    out = pd.Series(pd.NA, index=s.index, dtype="object")
    unknown: set[str] = set()
    for i, v in s.items():
        if isinstance(v, bool):
            out.at[i] = v
            continue
        if v is None or (isinstance(v, float) and pd.isna(v)) or pd.isna(v):
            continue
        tok = str(v).strip().lower()
        if tok in ("", "nan", "none", "<na>"):
            continue
        if tok in _TRUE_TOKENS:
            out.at[i] = True
        elif tok in _FALSE_TOKENS:
            out.at[i] = False
        else:
            unknown.add(str(v))
    if unknown:
        raise ValueError(
            f"unrecognized label tokens {sorted(unknown)[:10]} -- refusing to "
            "silently drop them; normalize the column first"
        )
    return out


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

    # REJOIN THE WITHHELD OUTCOME COLUMNS. `make_java_census.py` keeps
    # `outcome` / `rejection_stratum` out of the judged CSV so the judge cannot
    # see whether a PR was merged (rubric rule 7), and parks them in a sidecar
    # keyed by `row_idx`. They are needed HERE, after judging, for the
    # per-outcome precision-evenness check that licenses the V2 contrast.
    side = Path(args.file)
    side = side if side.exists() else (C.LABELS_DIR / args.file)
    side = side.with_suffix("").with_suffix("")  # strip .csv
    side = Path(str(side) + ".outcome.csv")
    if side.exists() and "row_idx" in df.columns:
        extra = pd.read_csv(side)
        keep = [c for c in extra.columns
                if c not in df.columns or c == "row_idx"]
        df = df.merge(extra[keep], on="row_idx", how="left")
        print(f"[score] rejoined withheld outcome columns from {side.name}")

    coerced = coerce_labels(df[args.label_col])
    labeled = df[coerced.notna()].copy()
    labeled[args.label_col] = coerced[coerced.notna()].astype(bool).astype(int)
    n_unlabeled = len(df) - len(labeled)
    if n_unlabeled:
        print(f"[score] {n_unlabeled} rows still unlabeled (skipped)")
    # audit rule #7: always print counts beside rates
    print(f"[score] {len(labeled)}/{len(df)} rows labeled "
          f"({100 * len(labeled) / len(df):.1f}%)")
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
    print("\nPER AGENT -- the evenness check that LICENSES the agent ranking.")
    print("A detector that over-flags one agent more than another manufactures a")
    print("difference that is not in the code, so a ranking claim is only safe")
    print("when precision is comparable across agents.")
    for agent, sub in labeled.groupby("agent"):
        report(agent, sub)

    # V2: the same evenness argument applies to OUTCOME. If the detector is more
    # precise on merged PRs than on rejected ones, then a merged-vs-rejected
    # violation-rate difference is partly a difference in how often the detector
    # is right, not in how often the code is wrong. Without this check the
    # headline V2 comparison cannot be defended.
    if "outcome" in labeled.columns and labeled["outcome"].nunique() > 1:
        print("\nPER OUTCOME -- the V2 evenness check. Uneven precision here means")
        print("the merged-vs-rejected contrast is partly a detector artefact.")
        for outcome, sub in labeled.groupby("outcome"):
            report(str(outcome), sub)
        prec = {}
        for outcome, sub in labeled.groupby("outcome"):
            pred = sub[args.pred_col].astype(bool)
            tp = int((pred & (sub[args.label_col] == 1)).sum())
            fp = int((pred & (sub[args.label_col] == 0)).sum())
            if tp + fp:
                prec[str(outcome)] = tp / (tp + fp)
        if len(prec) > 1 and (max(prec.values()) - min(prec.values())) > 0.15:
            print("  !! precision differs by >15pp across outcomes "
                  f"({prec}). The outcome contrast is NOT safe to quote as a")
            print("     statement about code until this is explained.")
    if "rejection_stratum_api" in labeled.columns:
        nm = labeled[labeled["rejection_stratum_api"].notna()]
        strata = [s for s in nm["rejection_stratum_api"].unique()
                  if str(s) not in ("n/a", "merged", "nan")]
        if len(strata) > 1:
            print("\nPER REJECTION STRATUM (counts beside rates -- "
                  "changes_requested is small):")
            for stratum, sub in nm.groupby("rejection_stratum_api"):
                if str(stratum) in ("n/a", "merged"):
                    continue
                report(str(stratum), sub)


if __name__ == "__main__":
    main()
