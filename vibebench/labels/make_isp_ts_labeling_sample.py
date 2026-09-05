"""
Step 4a-ISP-TS - Stratified sample of TypeScript ISP entity rows for labeling.

PROVENANCE: labels are LLM-produced (`llm_isp_risk`), NOT human. Human labels,
if ever collected, go in a separate `human_isp_risk` column.

Samples head-state ENTITY rows (row_type interface/implementer) rather than PR
deltas -- a concrete contract or (implementer, contract) pair can actually be
judged from source. Strata: (agent, row_type, flag), where `flag` is `flagged`
for interfaces and `forced_stub` for implementers. Positives are sparse, so a
stratum smaller than --n-per-stratum is taken whole.

Labeling criterion for llm_isp_risk (fill 1/0, blank if unsure):
  1 -- an implementer is genuinely forced to supply members it does not use;
       splitting this contract would help a real client
  0 -- the width is cohesive or idiomatic (one topic, template-method hooks,
       optional-capability members the implementers actually use)

Run:  python vibebench/labels/make_isp_ts_labeling_sample.py [--n-per-stratum 8]
      python vibebench/labels/make_isp_ts_labeling_sample.py --holdout
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

KEY = ["pr_id", "row_type", "iface_id", "qualname"]
COLS = ["pr_id", "agent", "full_name", "pr_number", "merge_sha", "row_type",
        "iface_id", "module", "qualname", "kind", "n_members", "iface_width",
        "lineno", "file_path", "n_implemented", "n_stubs", "coverage",
        "stub_ratio", "flagged", "forced_stub", "pred_isp_risk"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-per-stratum", type=int, default=8)
    ap.add_argument("--holdout", action="store_true")
    args = ap.parse_args()

    seed = C.RANDOM_SEED + 1000 if args.holdout else C.RANDOM_SEED
    df = pd.read_parquet(C.ISP_RESULTS_TS)
    ent = df[df.row_type.isin(["interface", "implementer"])].copy()
    if not len(ent):
        raise SystemExit("[labels-isp-ts] no entity rows -- run run_isp_ts.py first.")

    # One prediction column so score_labels.py can be reused unchanged: an
    # interface row is predicted risky when it is fat, an implementer row when
    # it is a forced stub.
    ent["pred_isp_risk"] = ent["flagged"].fillna(False) | ent["forced_stub"].fillna(False)

    for c in COLS:
        if c not in ent.columns:
            ent[c] = None

    if args.holdout:
        prev = pd.read_csv(C.LABELS_DIR / "isp_ts_labeling_sample.csv")
        for k in KEY:
            prev[k] = prev[k].astype(str)
            ent[k] = ent[k].astype(str)
        seen = set(map(tuple, prev[KEY].values))
        keys = list(map(tuple, ent[KEY].values))
        ent = ent[[k not in seen for k in keys]]
        print(f"[labels-isp-ts] holdout: {len(seen)} tuning rows excluded, "
              f"{len(ent)} entities eligible")

    parts = []
    for _, grp in ent.groupby(["agent", "row_type", "pred_isp_risk"]):
        parts.append(grp.sample(min(args.n_per_stratum, len(grp)), random_state=seed))
    sample = pd.concat(parts).sample(frac=1, random_state=seed).reset_index(drop=True)
    sample["llm_isp_risk"] = ""
    sample["notes"] = ""

    name = ("isp_ts_holdout_sample.csv" if args.holdout
            else "isp_ts_labeling_sample.csv")
    out = C.LABELS_DIR / name
    sample[COLS + ["llm_isp_risk", "notes"]].to_csv(out, index=False)
    print(f"[labels-isp-ts] wrote {len(sample)} rows to {out}")
    print(sample.groupby(["row_type", "pred_isp_risk"]).size().to_string())


if __name__ == "__main__":
    main()
