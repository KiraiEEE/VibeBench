"""
Java label-set builder -- COMPLETE-FLAGGED enumeration + unflagged sample.

WHAT THIS BUILDS, AND WHY IT IS NOT A "SAMPLE".

Precision is the number this project defends, and precision is decided entirely
by the flagged rows: every flagged entity is either a true or a false positive,
and no amount of judging UNflagged rows moves it. So the flagged side is
enumerated COMPLETELY -- 100% of distinct flagged entities -- and precision then
carries no sampling error at all. It is a measurement, not an estimate.

Recall needs the other side, but only enough of it to catch false negatives, so
unflagged entities are sampled.

This mirrors exactly what the Python run ended up doing, which the file names
there ("*_census.csv") describe badly:

    principle   flagged judged        unflagged sampled
    OCP         533/533   (100%)      21.0%
    LSP         443/443   (100%)       5.5%
    ISP         144/144   (100%)       9.4%
    DIP          47/47    (100%)       8.6%
    SRP       2,625/2,865  (91.6%)     6.5%

Matching the METHOD is what makes the two languages comparable. Precision from a
stratified 30-per-stratum sample is not comparable to precision from complete
enumeration -- Python's SRP screen read P=0.33 the first way and P=0.064 the
second, on the same detector and the same corpus.

ENTITY DEDUPLICATION comes first. A Java class touched by N pull requests
appears N times (measured duplication 1.86x, the worst of the three languages),
so judging raw rows would weight god classes by their popularity.

Run:
  python vibebench/labels/make_java_census.py --principle srp
  python vibebench/labels/make_java_census.py --principle ocp --unflagged-frac 0.2
  python vibebench/labels/make_java_census.py --all
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
import safeio  # noqa: E402  -- crash-safe writes (power/network cuts)

PR_RESULTS = C.CORPUS_DIR / "java_pr_results.parquet"

# principle: (parquet, pred col, judge col, dedup keys, row filter)
# Keys and columns MUST match make_java_labeling_sample.SPEC so the two tools
# agree on what one entity is.
SPEC = {
    "srp": (C.SRP_RESULTS_JAVA, "srp_risk", "llm_srp_risk",
            ["full_name", "type_id"], None),
    "ocp": (C.OCP_RESULTS_JAVA, "smell", "llm_ocp_risk",
            ["full_name", "type_id", "method", "subject"], None),
    "lsp": (C.LSP_RESULTS_JAVA, "violation", "llm_lsp_risk",
            ["full_name", "type_id", "method"], None),
    "isp": (C.ISP_RESULTS_JAVA, "flagged", "llm_isp_risk",
            ["full_name", "iface_id"],
            lambda d: d[d.row_type == "interface"]),
    "dip": (PR_RESULTS, "dip_risk", "llm_dip_risk",
            ["full_name", "pr_number"], lambda d: d[d.status == "ok"]),
}


def build(principle: str, unflagged_frac: float, max_unflagged: int) -> None:
    path, pred, judge, keys, flt = SPEC[principle]
    if not Path(path).exists():
        print(f"[{principle}] no results at {path} -- run run_java.py first")
        return
    d = pd.read_parquet(path)
    if flt is not None:
        d = flt(d)
    if pred not in d.columns or d[pred].isna().all():
        print(f"[{principle}] '{pred}' is null -- harness is in CALIBRATION MODE. "
              f"Set thresholds, delete the parquets, re-run, then come back.")
        return

    d = d.copy()
    d[pred] = d[pred].fillna(False).astype(bool)
    keys = [k for k in keys if k in d.columns]
    before = len(d)
    # Keep the LARGEST observation of a class so the judge sees the worst case
    # (matches report_java.py, which also keeps the largest per entity).
    if principle == "srp" and "class_loc" in d.columns:
        d = d.sort_values("class_loc").drop_duplicates(keys, keep="last")
    else:
        d = d.drop_duplicates(keys)

    flagged = d[d[pred]]
    unflagged = d[~d[pred]]
    n_un = min(len(unflagged), max(int(round(len(unflagged) * unflagged_frac)), 0))
    if max_unflagged:
        n_un = min(n_un, max_unflagged)
    sample_un = (unflagged.sample(n_un, random_state=C.RANDOM_SEED)
                 if n_un else unflagged.iloc[:0])

    out = (pd.concat([flagged, sample_un])
           .sample(frac=1.0, random_state=C.RANDOM_SEED)   # shuffle: judge blind to flag
           .reset_index(drop=True))
    out[judge] = ""
    out["notes"] = pd.Series([""] * len(out), dtype=object)

    dst = C.LABELS_DIR / f"java_{principle}_census.csv"
    C.LABELS_DIR.mkdir(parents=True, exist_ok=True)

    # OUTCOME IS WITHHELD FROM THE JUDGED FILE (V2 blinding, rubric rule 7).
    #
    # The judge edits THIS CSV, so leaving `outcome` / `rejection_stratum` in it
    # defeats the blinding that the batch .txt files carefully preserve. V2's
    # whole question is whether conformance relates to acceptance, and a judge
    # who can see "rejected" beside the row can produce that answer out of
    # nothing -- the single easiest way to fabricate the headline result.
    #
    # The columns are not discarded: they go to a sidecar keyed by `row_idx`,
    # and `score_labels.py` joins them back AFTER judging so the per-outcome
    # precision-evenness check still runs. Withholding them from the judge and
    # withholding them from the analysis are different things.
    out = out.reset_index(drop=True)
    out.insert(0, "row_idx", out.index)
    hide = [c for c in ("outcome", "rejection_stratum", "rejection_stratum_api",
                        "outcome_final", "stratum_source", "merged_at",
                        "closed_at", "state") if c in out.columns]
    if hide:
        side = C.LABELS_DIR / f"java_{principle}_census.outcome.csv"
        safeio.to_csv(out[["row_idx", "pr_id"] + hide], side, index=False)
        out = out.drop(columns=hide)
        print(f"           blinding  : withheld {hide} -> {side.name} "
              f"(score_labels.py rejoins it)")
    safeio.to_csv(out, dst, index=False)

    print(f"[{principle}] {before:,} rows -> {len(d):,} distinct entities")
    print(f"           flagged   : {len(flagged):,}/{len(flagged):,} judged (100% -- "
          f"precision will be exact)")
    print(f"           unflagged : {len(sample_un):,} of {len(unflagged):,} "
          f"({len(sample_un)/max(len(unflagged),1):.1%}) for the recall estimate")
    print(f"           wrote {dst}  ({len(out):,} rows to judge)\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--principle", choices=sorted(SPEC))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--unflagged-frac", type=float, default=0.10,
                    help="fraction of unflagged entities to sample (default 0.10, "
                         "in the range Python used: 5.5%%-21%%)")
    ap.add_argument("--max-unflagged", type=int, default=1200,
                    help="hard cap on unflagged rows per principle, so one huge "
                         "population cannot dominate the labelling budget")
    a = ap.parse_args()
    if not a.principle and not a.all:
        ap.error("pass --principle X or --all")
    for p in (sorted(SPEC) if a.all else [a.principle]):
        build(p, a.unflagged_frac, a.max_unflagged)
    print("next: extract_java_label_batches.py --principle X --file java_X_census.csv")
    print("      (blind by default; pass --show-verdict only to debug)")


if __name__ == "__main__":
    main()
