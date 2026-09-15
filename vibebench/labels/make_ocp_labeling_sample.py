"""
Step 4a-OCP - Draw a stratified sample of OCP dispatch rows for reference labeling.

PROVENANCE NOTE: as with SRP/ISP, these samples are labeled by an LLM (Claude)
reading the actual source -- NOT by human experts. The label column is named
`llm_ocp_risk` to keep that explicit; human labels, if ever collected, go in a
separate `human_ocp_risk` column.

Pulls the head-state ENTITY rows (row_type == "dispatch") from
corpus/ocp_results.parquet -- labeling a concrete dispatch chain (does adding a
new variant force an edit here?) is far easier and more reliable than labeling
a PR-level delta. Strata: (agent, smell). Because run_ocp emits EVERY in-scope
chain (>=2 branches), both smell and non-smell rows are available, so the
sample naturally contains positives and negatives for the `smell` flag.

Labeling criterion for llm_ocp_risk (fill 1/0, blank if unsure):
  1 -- a genuine OCP violation: the branches enumerate an OPEN-ENDED family of
       types/variants that will plausibly grow, so adding one means editing this
       ladder. Polymorphism / a registry / strategy would close it.
  0 -- legitimate / closed dispatch: exhaustive handling of a FIXED bounded set
       (e.g. the 6 JSON types, a frozen wire protocol, AST node kinds of a fixed
       grammar), a guard/validation ladder, or otherwise not variant dispatch.

Run:  python vibebench/labels/make_ocp_labeling_sample.py [--n-per-stratum 8]
      python vibebench/labels/make_ocp_labeling_sample.py --holdout
        (--holdout: fresh seed, excludes rows already in the tuning sample,
         writes ocp_holdout_sample.csv)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sample_paths as SP  # noqa: E402

COLS = [
    "pr_id", "agent", "full_name", "pr_number", "merge_sha", "row_type",
    "module", "qualname", "subject", "kind", "n_branches", "lineno",
    "smell", "llm_ocp_risk", "notes",
]


def _s(v) -> str:
    """NaN/None-safe string so keys built from the CSV and the parquet match
    (missing values normalized to '')."""
    if v is None:
        return ""
    s = str(v)
    return "" if s in ("nan", "None") else s


def row_key(r) -> tuple:
    """Only columns present identically in both parquet and CSV (see the ISP
    sampler bug: keying on a column absent from the CSV excludes nothing)."""
    return (r.pr_id, _s(r.module), _s(r.qualname), _s(r.subject))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-per-stratum", type=int, default=8,
                    help="rows per (agent, smell) stratum")
    ap.add_argument("--holdout", action="store_true",
                    help="fresh seed + exclude tuning rows; write "
                         "ocp_holdout_sample.csv")
    SP.add_args(ap)
    args = ap.parse_args()

    seed = C.RANDOM_SEED + 1000 if args.holdout else C.RANDOM_SEED

    results = pd.read_parquet(C.OCP_RESULTS)
    ents = results[results.row_type == "dispatch"].copy()
    if ents.empty:
        print("[ocp-labels] no dispatch rows in ocp_results.parquet yet")
        return
    ents["smell"] = ents["smell"].astype(bool)

    if args.holdout:
        prev = pd.read_csv(SP.sample_path("ocp", args.lang))
        seen = {row_key(r) for r in prev.itertuples(index=False)}
        keys = [row_key(r) for r in ents.itertuples(index=False)]
        ents = ents[[k not in seen for k in keys]]
        print(f"[ocp-labels] holdout mode: {len(seen)} tuning rows excluded, "
              f"{len(ents)} dispatch chains eligible")

    parts = []
    for (agent, smell), grp in ents.groupby(["agent", "smell"]):
        n = min(args.n_per_stratum, len(grp))
        parts.append(grp.sample(n, random_state=seed))
    sample = (pd.concat(parts).sample(frac=1, random_state=seed)
              .reset_index(drop=True))

    sample["llm_ocp_risk"] = ""
    sample["notes"] = ""
    for c in COLS:
        if c not in sample.columns:
            sample[c] = ""

    C.LABELS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = SP.resolve("ocp", args.lang, holdout=args.holdout, force=args.force)
    sample[COLS].to_csv(out_path, index=False)

    print(f"[ocp-labels] wrote {len(sample)} rows to {out_path}")
    print(sample.groupby(["agent", "smell"]).size().to_string())
    print("\nNext: python vibebench/labels/extract_ocp_label_batches.py "
          f"--file {name} --out-dir <dir>, review each batch, fill "
          "llm_ocp_risk with 1/0, then score with\n  python "
          f"vibebench/labels/score_labels.py --file {name} "
          "--pred-col smell --label-col llm_ocp_risk")


if __name__ == "__main__":
    main()
