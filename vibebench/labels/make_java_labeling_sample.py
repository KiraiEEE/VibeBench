"""
Draw a stratified labelling sample for any Java principle.

One script for all five, because the Java results share a schema. Conventions
are identical to the Python and TypeScript label loops so the numbers stay
comparable:

  * seed = C.RANDOM_SEED for tuning, C.RANDOM_SEED + 1000 for --holdout
  * --holdout EXCLUDES every tuning row, matched on a row_key built ONLY from
    columns present in BOTH the parquet and the existing tuning CSV (the ISP
    sampler bug: keying on an absent column excludes nothing)
  * stratified on the PREDICTION so positives and negatives are both sampled --
    a sample drawn only from flagged rows can measure precision but never recall
  * the judge column is left EMPTY; nothing pre-fills it

Run:
  python vibebench/labels/make_java_labeling_sample.py --principle srp [--holdout]
  ... --principle {srp,ocp,lsp,isp,dip}
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

PR_RESULTS = C.CORPUS_DIR / "java_pr_results.parquet"

SPEC = {
    # principle: (parquet, pred column, judge column, dedup keys, row filter)
    "srp": (C.SRP_RESULTS_JAVA, "srp_risk", "llm_srp_risk",
            ["full_name", "type_id"], None),
    "ocp": (C.OCP_RESULTS_JAVA, "smell", "llm_ocp_risk",
            ["full_name", "type_id", "method", "subject"], None),
    "lsp": (C.LSP_RESULTS_JAVA, "violation", "llm_lsp_risk",
            ["full_name", "type_id", "method"], None),
    "isp": (C.ISP_RESULTS_JAVA, "flagged", "llm_isp_risk",
            ["full_name", "iface_id"], lambda d: d[d.row_type == "interface"]),
    "dip": (PR_RESULTS, "dip_risk", "llm_dip_risk",
            ["full_name", "pr_number"], lambda d: d[d.status == "ok"]),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--principle", required=True, choices=sorted(SPEC))
    ap.add_argument("--holdout", action="store_true")
    ap.add_argument("--n-per-stratum", type=int, default=30)
    a = ap.parse_args()

    path, pred, judge, keys, flt = SPEC[a.principle]
    if not path.exists():
        sys.exit(f"no results at {path} -- run run_java.py first")
    d = pd.read_parquet(path)
    if flt is not None:
        d = flt(d)
    if pred not in d.columns or d[pred].isna().all():
        sys.exit(f"'{pred}' is null -- the harness is still in CALIBRATION MODE. "
                 f"Set the thresholds in config first (PHASE 4).")

    d = d.copy()
    d[pred] = d[pred].fillna(False).astype(bool)
    # ENTITY DEDUPLICATION before sampling. Without it the sample is biased
    # toward classes that many PRs touched, and Java's duplication is 1.86x.
    keys = [k for k in keys if k in d.columns]
    before = len(d)
    d = d.drop_duplicates(keys)
    print(f"{a.principle}: {before:,} rows -> {len(d):,} distinct entities")

    out = C.LABELS_DIR / f"java_{a.principle}_labeling_sample{'_holdout' if a.holdout else ''}.csv"
    tuning = C.LABELS_DIR / f"java_{a.principle}_labeling_sample.csv"

    d["row_key"] = d[keys].astype(str).agg("||".join, axis=1)
    if a.holdout:
        if not tuning.exists():
            sys.exit(f"--holdout needs the tuning sample first: {tuning}")
        t = pd.read_csv(tuning)
        shared = [k for k in keys if k in t.columns]
        if len(shared) != len(keys):
            sys.exit(f"cannot build a comparable row_key: tuning CSV lacks "
                     f"{set(keys) - set(shared)}. Refusing to draw a holdout that "
                     f"might overlap the tuning set.")
        burned = set(t[shared].astype(str).agg("||".join, axis=1))
        n0 = len(d)
        d = d[~d.row_key.isin(burned)]
        print(f"  excluded {n0 - len(d):,} tuning rows; {len(d):,} remain")

    seed = C.RANDOM_SEED + (1000 if a.holdout else 0)
    parts = []
    for val, g in d.groupby(pred):
        n = min(a.n_per_stratum, len(g))
        parts.append(g.sample(n=n, random_state=seed))
        print(f"  {pred}={val}: sampled {n} of {len(g):,}")
    s = pd.concat(parts).sample(frac=1.0, random_state=seed).reset_index(drop=True)
    s[judge] = ""
    s["notes"] = pd.Series([""] * len(s), dtype=object)

    C.LABELS_DIR.mkdir(parents=True, exist_ok=True)
    s.to_csv(out, index=False)
    print(f"\nwrote {out}  ({len(s)} rows, {int(s[pred].sum())} flagged)")
    print(f"next: python vibebench/labels/extract_java_label_batches.py "
          f"--principle {a.principle} --file {out.name}")


if __name__ == "__main__":
    main()
