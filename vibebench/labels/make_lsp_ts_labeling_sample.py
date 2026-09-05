"""
Step 4a-LSP-TS - Stratified sample of TypeScript override pairs for labeling.

Samples `override` rows (every in-scope subclass->ancestor pair, violating or
not) so precision AND recall are measurable, stratified by (agent, violation).
Same provenance rules as every other sampler: labels are LLM-produced
(`llm_lsp_risk`), NOT human; the holdout uses a fresh seed and excludes tuning
rows via a key built only from columns present in both parquet and CSV.

Run:  python vibebench/labels/make_lsp_ts_labeling_sample.py [--n-per-stratum 8]
      python vibebench/labels/make_lsp_ts_labeling_sample.py --holdout --n-per-stratum 5
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

KEY = ["pr_id", "module", "qualname", "method"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-per-stratum", type=int, default=8)
    ap.add_argument("--holdout", action="store_true")
    args = ap.parse_args()

    seed = C.RANDOM_SEED + 1000 if args.holdout else C.RANDOM_SEED
    df = pd.read_parquet(C.LSP_RESULTS_TS)
    ovr = df[df.row_type == "override"].copy()
    if ovr["violation"].isna().all():
        raise SystemExit("[labels-lsp-ts] violation is null -- nothing to label.")

    if args.holdout:
        prev = pd.read_csv(C.LABELS_DIR / "lsp_ts_labeling_sample.csv")
        for k in KEY:
            prev[k] = prev[k].astype(str)
            ovr[k] = ovr[k].astype(str)
        seen = set(map(tuple, prev[KEY].values))
        keys = list(map(tuple, ovr[KEY].values))
        ovr = ovr[[k not in seen for k in keys]]
        print(f"[labels-lsp-ts] holdout: {len(seen)} tuning rows excluded, "
              f"{len(ovr)} pairs eligible")

    parts = []
    for (agent, viol), grp in ovr.groupby(["agent", "violation"]):
        parts.append(grp.sample(min(args.n_per_stratum, len(grp)), random_state=seed))
    sample = pd.concat(parts).sample(frac=1, random_state=seed).reset_index(drop=True)
    sample["llm_lsp_risk"] = ""
    sample["notes"] = ""

    cols = ["pr_id", "agent", "full_name", "pr_number", "merge_sha", "module",
            "qualname", "base_id", "method", "lineno", "file_path", "signals",
            "violation", "llm_lsp_risk", "notes"]
    name = ("lsp_ts_holdout_sample.csv" if args.holdout
            else "lsp_ts_labeling_sample.csv")
    out = C.LABELS_DIR / name
    sample[cols].to_csv(out, index=False)
    print(f"[labels-lsp-ts] wrote {len(sample)} rows to {out}")
    print(sample.groupby(["agent", "violation"]).size().to_string())


if __name__ == "__main__":
    main()
