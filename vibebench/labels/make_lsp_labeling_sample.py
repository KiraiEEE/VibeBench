"""
Step 4a-LSP - Draw a stratified sample of LSP override rows for reference labeling.

PROVENANCE NOTE: as with SRP/ISP/OCP, these samples are labeled by an LLM
(Claude) reading the actual source -- NOT by human experts. The label column is
named `llm_lsp_risk` to keep that explicit; human labels, if ever collected, go
in a separate `human_lsp_risk` column.

Pulls the head-state ENTITY rows (row_type == "override") from
corpus/lsp_results.parquet -- labeling a concrete (subclass, base, method)
override triple (would substituting this subclass break a base-contract caller?)
is far easier and more reliable than labeling a PR-level delta. Strata:
(agent, violation). Because run_lsp emits EVERY in-scope override pair (violating
or not), the sample naturally contains positives and negatives for the
`violation` flag.

Labeling criterion for llm_lsp_risk (fill 1/0, blank if unsure):
  1 -- a genuine LSP violation: substituting this subclass for its base would
       break a caller relying on the base's contract. The override REFUSES work
       the base does (raises where the base has a real body), DEMANDS more than
       the base (a new required argument, or a narrower parameter type), or
       PROMISES less (a wider / None / Optional return where the base returns a
       concrete value).
  0 -- substitutable / benign: the override is a compatible refinement (same or
       WIDER parameter types, same or NARROWER return, honours the contract), OR
       the flagged signal is a false positive -- the base method was itself a
       placeholder/abstract, *args/**kwargs absorbs the difference, the raise is
       a documented intentional sentinel that base callers already expect, or the
       annotation difference is cosmetic (aliases / both valid, not sub/super).

Run:  python vibebench/labels/make_lsp_labeling_sample.py [--n-per-stratum 12]
      python vibebench/labels/make_lsp_labeling_sample.py --holdout
        (--holdout: fresh seed, excludes rows already in the tuning sample,
         writes lsp_holdout_sample.csv)
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
    "module", "qualname", "base_id", "method", "lineno", "signals",
    "violation", "llm_lsp_risk", "notes",
]


def _s(v) -> str:
    if v is None:
        return ""
    s = str(v)
    return "" if s in ("nan", "None") else s


def row_key(r) -> tuple:
    """Only columns present identically in both parquet and CSV (see the ISP
    sampler bug: keying on a column absent from the CSV excludes nothing)."""
    return (r.pr_id, _s(r.module), _s(r.qualname), _s(r.method))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-per-stratum", type=int, default=12,
                    help="rows per (agent, violation) stratum")
    ap.add_argument("--holdout", action="store_true",
                    help="fresh seed + exclude tuning rows; write "
                         "lsp_holdout_sample.csv")
    args = ap.parse_args()

    seed = C.RANDOM_SEED + 1000 if args.holdout else C.RANDOM_SEED

    results = pd.read_parquet(C.LSP_RESULTS)
    ents = results[results.row_type == "override"].copy()
    if ents.empty:
        print("[lsp-labels] no override rows in lsp_results.parquet yet")
        return
    ents["violation"] = ents["violation"].astype(bool)

    if args.holdout:
        prev = pd.read_csv(C.LABELS_DIR / "lsp_labeling_sample.csv")
        seen = {row_key(r) for r in prev.itertuples(index=False)}
        keys = [row_key(r) for r in ents.itertuples(index=False)]
        ents = ents[[k not in seen for k in keys]]
        print(f"[lsp-labels] holdout mode: {len(seen)} tuning rows excluded, "
              f"{len(ents)} override pairs eligible")

    parts = []
    for (agent, violation), grp in ents.groupby(["agent", "violation"]):
        n = min(args.n_per_stratum, len(grp))
        parts.append(grp.sample(n, random_state=seed))
    sample = (pd.concat(parts).sample(frac=1, random_state=seed)
              .reset_index(drop=True))

    sample["llm_lsp_risk"] = ""
    sample["notes"] = ""
    for c in COLS:
        if c not in sample.columns:
            sample[c] = ""

    C.LABELS_DIR.mkdir(parents=True, exist_ok=True)
    name = "lsp_holdout_sample.csv" if args.holdout else "lsp_labeling_sample.csv"
    out_path = C.LABELS_DIR / name
    sample[COLS].to_csv(out_path, index=False)

    print(f"[lsp-labels] wrote {len(sample)} rows to {out_path}")
    print(sample.groupby(["agent", "violation"]).size().to_string())
    print("\nNext: python vibebench/labels/extract_lsp_label_batches.py "
          f"--file {name} --out-dir <dir>, review each batch, fill "
          "llm_lsp_risk with 1/0, then score with\n  python "
          f"vibebench/labels/score_labels.py --file {name} "
          "--pred-col violation --label-col llm_lsp_risk")


if __name__ == "__main__":
    main()
