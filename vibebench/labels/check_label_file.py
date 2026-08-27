"""
Preflight validator for label / census CSVs -- run this BEFORE score_labels.py.

WHY THIS EXISTS. On 2026-08-19 a full-census SRP file scored as "no labeled rows"
because its label column had been written across two passes in two different
encodings ("0.0"/"1.0" from one pass, "False"/"True" from another). The scorer's
membership filter matched neither, so it silently dropped all 4,425 rows instead
of failing. Silent row loss is the dangerous failure mode: the run "succeeds" and
reports nothing, or reports a rate computed over a subset nobody noticed.

This script makes that class of bug loud and catches it before any number is
quoted. Run it on every label file of every principle and language -- Java
included -- before scoring.

Checks
  1. label column parses cleanly to boolean (no unrecognized tokens)
  2. label column uses ONE consistent encoding (mixed encodings are a warning:
     legal, but the sign that two passes disagreed about format)
  3. prediction column exists, has no nulls, coerces to boolean
  4. no duplicate entity keys (double-labeled rows would double-count)
  5. reports labeled/total with counts beside rates (audit rule #7)

Exit code 0 = safe to score. Non-zero = do not quote any number from this file.

Run:
  python vibebench/labels/check_label_file.py --file lsp_census.csv \
         --pred-col violation --label-col llm_lsp_risk
  python vibebench/labels/check_label_file.py --all      # every known census file
  python vibebench/labels/check_label_file.py --self-check
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

TRUE_TOKENS = {"1", "1.0", "true", "yes", "y", "t"}
FALSE_TOKENS = {"0", "0.0", "false", "no", "n", "f"}
NULL_TOKENS = {"", "nan", "none", "<na>", "na"}

# (file, pred_col, label_col) for every label file the rerun produces.
# Python is a full census; Java uses the sampled loop, so its files only exist
# once make_java_labeling_sample.py has been run -- missing ones are reported,
# not treated as failures, by --all.
KNOWN = [
    # Python census
    ("srp_census_remaining.csv", "srp_risk", "llm_srp_risk"),
    ("ocp_census.csv", "smell", "llm_ocp_risk"),
    ("lsp_census.csv", "violation", "llm_lsp_risk"),
    ("isp_census.csv", "isp_risk", "llm_isp_risk"),
    ("dip_census.csv", "dip_risk", "llm_dip_risk"),
    # JAVA V2 -- complete-flagged enumeration plus an unflagged sample, written
    # by make_java_census.py. CORRECTED 2026-08-25: this list previously named
    # only `java_*_labeling_sample.csv`, the OLD 30-per-stratum sampled loop, so
    # `--all` reported every census file as "missing" and checked none of them.
    # A guard that silently skips the files it exists to guard is worse than no
    # guard. Pred columns must match make_java_census.SPEC.
    ("java_srp_census.csv", "srp_risk", "llm_srp_risk"),
    ("java_ocp_census.csv", "smell", "llm_ocp_risk"),
    ("java_lsp_census.csv", "violation", "llm_lsp_risk"),
    ("java_isp_census.csv", "flagged", "llm_isp_risk"),
    ("java_dip_census.csv", "dip_risk", "llm_dip_risk"),
    # Legacy sampled loop, kept so old files are still checkable if present.
    ("java_srp_labeling_sample.csv", "srp_risk", "llm_srp_risk"),
    ("java_srp_labeling_sample_holdout.csv", "srp_risk", "llm_srp_risk"),
    ("java_ocp_labeling_sample.csv", "smell", "llm_ocp_risk"),
    ("java_ocp_labeling_sample_holdout.csv", "smell", "llm_ocp_risk"),
    ("java_lsp_labeling_sample.csv", "violation", "llm_lsp_risk"),
    ("java_lsp_labeling_sample_holdout.csv", "violation", "llm_lsp_risk"),
    ("java_isp_labeling_sample.csv", "flagged", "llm_isp_risk"),
    ("java_isp_labeling_sample_holdout.csv", "flagged", "llm_isp_risk"),
    ("java_dip_labeling_sample.csv", "dip_risk", "llm_dip_risk"),
    ("java_dip_labeling_sample_holdout.csv", "dip_risk", "llm_dip_risk"),
]

# V2 BLINDING GUARD. These columns must NOT appear in a file a judge edits: the
# study asks whether conformance relates to acceptance, so a judge who can see
# the outcome beside the row can produce that answer out of nothing.
# make_java_census.py parks them in a `*.outcome.csv` sidecar and
# score_labels.py rejoins them after judging. See JAVA_JUDGING_RUBRIC.md rule 7.
BLINDING_FORBIDDEN = ["outcome", "outcome_final", "rejection_stratum",
                      "rejection_stratum_api", "merged_at", "state"]

# Entity-key columns. Used only if present in the file; the intersection with
# the file's own columns becomes the duplicate key.
#
# `type_id` is Java's entity identifier and its absence here was a real defect:
# without it a Java SRP file keyed on pr_id + file_path, so TWO TYPES DECLARED IN
# ONE FILE -- ordinary Java, and the norm for nested and package-private types --
# were reported as duplicates. A guard that cries wolf on normal input is a guard
# that gets ignored, which costs more than having no guard at all.
KEY_CANDIDATES = [
    "pr_id", "module", "qualname", "method", "base_id", "subject",
    "iface_id", "impl_id", "type_id", "class_name", "file_path", "lineno",
]


def classify(v) -> str:
    """Return 'true' | 'false' | 'null' | 'unknown', plus the encoding family."""
    if isinstance(v, bool):
        return "true" if v else "false"
    try:
        if v is None or pd.isna(v):
            return "null"
    except (TypeError, ValueError):
        pass
    tok = str(v).strip().lower()
    if tok in NULL_TOKENS:
        return "null"
    if tok in TRUE_TOKENS:
        return "true"
    if tok in FALSE_TOKENS:
        return "false"
    return "unknown"


def encoding_family(v) -> str | None:
    if isinstance(v, bool):
        return "bool"
    tok = str(v).strip()
    if tok.lower() in NULL_TOKENS:
        return None
    if tok in ("0", "1"):
        return "int-str"
    if tok in ("0.0", "1.0"):
        return "float-str"
    if tok.lower() in ("true", "false"):
        return "boolstr" if tok not in ("True", "False") else "bool-str"
    return None


def check_one(fname: str, pred_col: str, label_col: str) -> list[str]:
    """Return list of PROBLEM strings (empty == clean)."""
    problems: list[str] = []
    path = C.LABELS_DIR / fname
    if not path.exists():
        return [f"FILE MISSING: {path}"]

    df = pd.read_csv(path)
    n = len(df)
    print(f"\n=== {fname}  ({n} rows) ===")

    # -- label column ------------------------------------------------------
    if label_col not in df.columns:
        return [f"label column '{label_col}' not in {fname}"]

    kinds = df[label_col].map(classify)
    counts = kinds.value_counts().to_dict()
    n_true = counts.get("true", 0)
    n_false = counts.get("false", 0)
    n_null = counts.get("null", 0)
    n_unknown = counts.get("unknown", 0)
    labeled = n_true + n_false

    print(f"  label  {label_col}: labeled={labeled}/{n} "
          f"({100 * labeled / n:.1f}%)  pos={n_true}  neg={n_false}  "
          f"unlabeled={n_null}")

    if n_unknown:
        bad = sorted({str(v) for v, k in zip(df[label_col], kinds)
                      if k == "unknown"})[:8]
        problems.append(
            f"{fname}: {n_unknown} UNRECOGNIZED label tokens {bad} -- these "
            "would be silently dropped by a membership filter")
    if labeled == 0:
        problems.append(f"{fname}: ZERO rows labeled -- scoring would be vacuous")
    if n_null:
        print(f"  NOTE   {n_null} rows still unlabeled")

    fams = {f for f in df[label_col].map(encoding_family) if f}
    if len(fams) > 1:
        print(f"  WARN   label column mixes encodings {sorted(fams)} -- "
              "normalize to bool before scoring (this is the 2026-08-19 bug)")

    # -- prediction column -------------------------------------------------
    if pred_col not in df.columns:
        problems.append(f"{fname}: pred column '{pred_col}' missing")
    else:
        pk = df[pred_col].map(classify)
        pu = int((pk == "unknown").sum())
        pn = int((pk == "null").sum())
        npos = int((pk == "true").sum())
        print(f"  pred   {pred_col}: pos={npos}  null={pn}  unknown={pu}")
        if pn:
            problems.append(f"{fname}: pred column '{pred_col}' has {pn} nulls")
        if pu:
            problems.append(f"{fname}: pred column '{pred_col}' has {pu} "
                            "unrecognized values")

    # -- duplicate entity keys --------------------------------------------
    keys = [c for c in KEY_CANDIDATES if c in df.columns]
    if keys:
        dups = int(df.duplicated(subset=keys).sum())
        print(f"  keys   {keys} -> duplicate rows: {dups}")
        if dups:
            problems.append(f"{fname}: {dups} duplicate entity rows on {keys} "
                            "-- would double-count in P/R")

    # V2 BLINDING GUARD. The judge edits this file, so an `outcome` column in it
    # defeats the blinding the batch .txt files preserve, and lets the judge
    # produce the merged-vs-rejected finding out of nothing. This is a FAIL, not
    # a warning: a breach cannot be corrected after the fact, only re-judged.
    leaked = [c for c in BLINDING_FORBIDDEN if c in df.columns]
    if leaked:
        problems.append(
            f"{fname}: BLINDING BREACH -- judged file exposes {leaked}. "
            "Rebuild it with make_java_census.py, which parks these in a "
            "*.outcome.csv sidecar (JAVA_JUDGING_RUBRIC.md rule 7).")
    else:
        print("  blind  no outcome/merge columns exposed to the judge")

    return problems


def self_check() -> None:
    """Prove the validator catches the exact bug that motivated it."""
    import tempfile
    print("[self-check] running...")

    # classify() must handle every encoding the corpus has used
    assert classify(True) == "true" and classify(False) == "false"
    assert classify(1) == "true" and classify(0) == "false"
    assert classify(1.0) == "true" and classify(0.0) == "false"
    assert classify("1.0") == "true" and classify("0.0") == "false"
    assert classify("True") == "true" and classify("False") == "false"
    assert classify(None) == "null" and classify(float("nan")) == "null"
    assert classify("maybe") == "unknown"

    # the regression case: a file written in two encodings must be READ fully
    tmp = Path(tempfile.mkdtemp())
    f = tmp / "mixed.csv"
    pd.DataFrame({
        "pr_id": [1, 2, 3, 4],
        "srp_risk": [True, False, True, False],
        "llm_srp_risk": ["0.0", "1.0", "False", "True"],
    }).to_csv(f, index=False)
    d = pd.read_csv(f)
    kinds = d["llm_srp_risk"].map(classify)
    assert (kinds == "unknown").sum() == 0, "mixed encodings must all parse"
    assert (kinds == "true").sum() == 2, "must find both positive encodings"
    # and the OLD filter must be shown to fail on it
    old = d[d["llm_srp_risk"].isin([0, 1, "0", "1"])]
    assert len(old) == 0, "old filter should drop everything (that was the bug)"

    print("[self-check] OK -- validator catches mixed-encoding silent drop")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--file")
    ap.add_argument("--pred-col")
    ap.add_argument("--label-col")
    ap.add_argument("--all", action="store_true",
                    help="check every known census file")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()

    if args.self_check:
        self_check()
        return

    targets = KNOWN if args.all else [(args.file, args.pred_col, args.label_col)]
    if not args.all and not args.file:
        ap.error("pass --file (with --pred-col/--label-col), --all, or --self-check")

    problems: list[str] = []
    missing: list[str] = []
    for fname, pred, lab in targets:
        # --all spans two languages and Java's files appear only after its
        # labelling loop runs, so "not created yet" is reported as absent
        # rather than counted as a defect. An explicit --file that is missing
        # IS a failure, because the caller named it.
        if args.all and not (C.LABELS_DIR / fname).exists():
            missing.append(fname)
            continue
        problems += check_one(fname, pred, lab)
    if missing:
        print(f"\n[not yet created: {len(missing)}] " + ", ".join(missing))

    print("\n" + "=" * 60)
    if problems:
        print(f"FAIL -- {len(problems)} problem(s); do NOT quote numbers from these:")
        for p in problems:
            print("  *", p)
        sys.exit(1)
    print("PASS -- all checked label files are safe to score")


if __name__ == "__main__":
    main()
