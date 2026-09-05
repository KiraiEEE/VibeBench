"""
Step 4a-ISP - Draw a stratified sample of ISP entity rows for reference labeling.

PROVENANCE NOTE: as with SRP, these samples are labeled by an LLM (Claude)
reading the actual source -- NOT by human experts. The label column is named
`llm_isp_risk` to keep that explicit; human labels, if ever collected, go in a
separate `human_isp_risk` column.

Pulls the head-state ENTITY rows (row_type interface/implementer) from
corpus/isp_results.parquet -- labeling a concrete interface or an
(implementer, interface) pair is far easier and more reliable than labeling a
PR-level delta. Strata: (agent, row_type, isp_risk). Flagged rows are sparse
(DIP lesson: sparse positives make labeling the bottleneck), so ALL flagged
rows are taken when a stratum has fewer than --n-per-stratum.

Labeling criterion for llm_isp_risk (fill 1/0, blank if unsure):
  1 -- a client or implementer is forced to depend on / stub out methods it
       does not use; segregating this interface would genuinely help
  0 -- the width/stubs are cohesive or idiomatic (optional-capability mixins,
       template-method hooks, single-topic wide interfaces)

Run:  python vibebench/labels/make_isp_labeling_sample.py [--n-per-stratum 6]
      python vibebench/labels/make_isp_labeling_sample.py --holdout
        (--holdout: fresh seed, excludes rows already in the tuning sample,
         writes isp_holdout_sample.csv)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

COLS = [
    "pr_id", "agent", "full_name", "pr_number", "merge_sha", "row_type",
    "module", "class_name", "iface_id", "kind", "iface_kind",
    "n_abstract_methods", "n_implementers", "mean_coverage",
    "n_iface_methods", "n_implemented", "n_stubbed", "coverage", "stub_ratio",
    "isp_risk", "llm_isp_risk", "notes",
]


def _s(v) -> str:
    """NaN/None-safe string: the parquet has NaN where the CSV has '' (and the
    CSV lacks impl_id entirely), so keys must be built only from columns that
    exist identically in both, with missing values normalized to ''."""
    if v is None:
        return ""
    s = str(v)
    return "" if s in ("nan", "None") else s


def row_key(r) -> tuple:
    return (r.pr_id, r.row_type, _s(r.module), _s(r.class_name),
            _s(r.iface_id))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-per-stratum", type=int, default=6,
                    help="rows per (agent, row_type, isp_risk) stratum")
    ap.add_argument("--holdout", action="store_true",
                    help="fresh seed + exclude tuning rows; write "
                         "isp_holdout_sample.csv")
    args = ap.parse_args()

    seed = C.RANDOM_SEED + 1000 if args.holdout else C.RANDOM_SEED

    results = pd.read_parquet(C.ISP_RESULTS)
    ents = results[results.row_type.isin(["interface", "implementer"])].copy()
    if ents.empty:
        print("[isp-labels] no entity rows in isp_results.parquet yet")
        return
    # Implicit-kind interfaces are inventory-only in v1 -- exclude from
    # labeling so P/R reflects the flaggable population (abc/protocol).
    kind = ents["kind"].fillna(ents.get("iface_kind"))
    ents = ents[kind != "implicit"]

    if args.holdout:
        prev = pd.read_csv(C.LABELS_DIR / "isp_labeling_sample.csv")
        seen = {row_key(r) for r in prev.itertuples(index=False)}
        keys = [row_key(r) for r in ents.itertuples(index=False)]
        ents = ents[[k not in seen for k in keys]]
        print(f"[isp-labels] holdout mode: {len(seen)} tuning rows excluded, "
              f"{len(ents)} entities eligible")

    parts = []
    for (agent, row_type, risk), grp in ents.groupby(
            ["agent", "row_type", "isp_risk"]):
        n = min(args.n_per_stratum, len(grp))
        parts.append(grp.sample(n, random_state=seed))
    sample = (pd.concat(parts).sample(frac=1, random_state=seed)
              .reset_index(drop=True))

    sample["llm_isp_risk"] = ""
    sample["notes"] = ""
    for c in COLS:
        if c not in sample.columns:
            sample[c] = ""

    C.LABELS_DIR.mkdir(parents=True, exist_ok=True)
    name = "isp_holdout_sample.csv" if args.holdout else "isp_labeling_sample.csv"
    out_path = C.LABELS_DIR / name
    sample[COLS].to_csv(out_path, index=False)

    print(f"[isp-labels] wrote {len(sample)} rows to {out_path}")
    print(sample.groupby(["agent", "row_type", "isp_risk"]).size().to_string())
    print("\nNext: python vibebench/labels/extract_isp_label_batches.py "
          f"--file {name} --out-dir <dir>, review each batch, fill "
          "llm_isp_risk with 1/0, then score with\n  python "
          f"vibebench/labels/score_labels.py --file {name} "
          "--pred-col isp_risk --label-col llm_isp_risk")


if __name__ == "__main__":
    main()
