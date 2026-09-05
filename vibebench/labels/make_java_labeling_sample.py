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

def production_only(d):
    """Restrict to the population the report actually publishes.

    ADDED 2026-09-01, after an audit found the drawn samples did not match the
    reported population at all. The report has always excluded test code, and
    now excludes machine-generated code as well; the sampler excluded neither,
    so 18% of the drawn SRP rows and 25% of the ISP rows were protoc output.

    Validating on a population you do not report is not a minor mismatch: the
    precision estimate it produces belongs to no number in the thesis. Generated
    protobuf classes flag at 60% where real code flags at 12%, so their presence
    would have moved measured precision substantially, in a direction that has
    nothing to do with how the detector performs on code anyone wrote.
    """
    if "file_path" not in d.columns:
        return d                      # DIP is PR-scoped and has no file column
    keep = ~d.file_path.map(lambda p: C.is_test_path(str(p), "java")
                            or C.is_generated_path(str(p)))
    return d[keep]


SPEC = {
    # principle: (parquet, pred column, judge column, dedup keys, row filter)
    "srp": (C.SRP_RESULTS_JAVA, "srp_risk", "llm_srp_risk",
            ["full_name", "type_id"], production_only),
    "ocp": (C.OCP_RESULTS_JAVA, "smell", "llm_ocp_risk",
            ["full_name", "type_id", "method", "subject"], production_only),
    "lsp": (C.LSP_RESULTS_JAVA, "violation", "llm_lsp_risk",
            ["full_name", "type_id", "method"], production_only),
    "isp": (C.ISP_RESULTS_JAVA, "flagged", "llm_isp_risk",
            ["full_name", "iface_id"],
            lambda d: production_only(d[d.row_type == "interface"])),
    "dip": (PR_RESULTS, "dip_risk", "llm_dip_risk",
            ["full_name", "pr_number"], lambda d: d[d.status == "ok"]),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--principle", required=True, choices=sorted(SPEC))
    ap.add_argument("--holdout", action="store_true")
    ap.add_argument("--n-per-stratum", type=int, default=30)
    ap.add_argument("--seed", type=int, default=None,
                    help="override the sampling seed to draw a FRESH round. The "
                         "2026-09-05 audit voided the first round; re-drawing on "
                         "the same seed would hand back the identical rows, and a "
                         "judge who has already labelled a row is anchored on it "
                         "rather than reading it again.")
    ap.add_argument("--suffix", default="",
                    help="filename suffix, so a re-judged round does not silently "
                         "overwrite the round it is replacing.")
    a = ap.parse_args()

    path, pred, judge, keys, flt = SPEC[a.principle]
    if not path.exists():
        sys.exit(f"no results at {path} -- run run_java.py first")
    d = pd.read_parquet(path)
    _n0 = len(d)
    if flt is not None:
        d = flt(d)
    print(f"{a.principle}: {_n0:,} rows -> {len(d):,} after production filter "
          f"(test + machine-generated excluded, matching the report)")
    if not len(d):
        sys.exit("production filter left no rows -- refusing to draw an empty sample")
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

    out = C.LABELS_DIR / (f"java_{a.principle}_labeling_sample"
                          f"{'_holdout' if a.holdout else ''}{a.suffix}.csv")
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

    seed = a.seed if a.seed is not None else C.RANDOM_SEED + (1000 if a.holdout else 0)
    print(f"  seed={seed}")
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
