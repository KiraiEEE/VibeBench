"""
Draw a stratified labelling sample for any Python V2 principle.

A line-for-line mirror of make_java_labeling_sample.py, so the two languages'
precision and recall are produced the same way:

  * STRATIFIED ON THE PREDICTION, 100 flagged + 100 unflagged by default. A
    sample drawn only from flagged rows can measure precision and can never
    discover a violation the detector missed.
  * ENTITY-DEDUPLICATED before sampling, or the sample leans toward whatever the
    agents touched most often.
  * PRODUCTION CODE ONLY, because that is the population the report publishes.
    Validating on a population you do not report produces a precision that
    belongs to no number in the write-up.
  * the judge column is left EMPTY, nothing pre-fills it.

Python-specific:
  * OCP, LSP and ISP rows carry a MODULE (`pkg.sub.mod`), not a file path, so
    the production filter is applied to the module rendered as a path. The test
    rule matches directory components and `test_` prefixes, which survive that
    rendering unchanged.
  * files are written with the `py2_` prefix that record_labels.use_lang('py2')
    and audit_label_batches --lang py2 expect, through sample_paths, which
    refuses to overwrite a sample that already holds judgements.
  * the POPULATION stratum sizes are written beside the sample. Recall in this
    study is ALWAYS stratum-weighted (the sample is half flagged, the corpus is
    not), and weighting needs N flagged and N unflagged, which only exist here.

Run:  python vibebench/labels/make_py2_labeling_sample.py --principle srp
      ... --principle {srp,ocp,lsp,isp,dip} [--n-per-stratum 100] [--holdout]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))
sys.path.insert(0, str(_HERE.parent))
import config as C          # noqa: E402
import sample_paths as SP   # noqa: E402

PR_RESULTS = C.CORPUS_DIR / "py_pr_results.parquet"


def _is_prod_path(p: str) -> bool:
    return not (C.is_test_path(str(p), "py") or C.is_generated_path(str(p)))


def _module_path(m: str) -> str:
    return str(m).replace(".", "/") + ".py"


def prod_by_file(d: pd.DataFrame) -> pd.DataFrame:
    return d[d.file_path.map(_is_prod_path)]


def prod_by_module(d: pd.DataFrame) -> pd.DataFrame:
    return d[d.module.map(lambda m: _is_prod_path(_module_path(m)))]


SPEC = {
    # principle: (parquet, prediction column, dedup keys, row filter)
    "srp": (C.SRP_RESULTS, "srp_risk",
            ["full_name", "file_path", "class_name"], prod_by_file),
    "ocp": (C.OCP_RESULTS, "smell",
            ["full_name", "module", "qualname", "subject"], prod_by_module),
    "lsp": (C.LSP_RESULTS, "violation",
            ["full_name", "module", "qualname", "base_id", "method"], prod_by_module),
    "isp": (C.ISP_RESULTS, "flagged", ["full_name", "iface_id"],
            lambda d: prod_by_module(d[d.row_type == "interface"])),
    "dip": (PR_RESULTS, "dip_risk", ["full_name", "pr_number"],
            lambda d: d[d.status == "ok"]),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--principle", required=True, choices=sorted(SPEC))
    ap.add_argument("--n-per-stratum", type=int, default=100)
    ap.add_argument("--holdout", action="store_true")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--force", action="store_true",
                    help="overwrite a sample that already holds judgements")
    a = ap.parse_args()

    path, pred, keys, flt = SPEC[a.principle]
    if not path.exists():
        sys.exit(f"no results at {path}; run the flagged pass first")
    done = C.CORPUS_DIR / "py_pr_results.DONE.json"
    if not done.exists():
        sys.exit("no py_pr_results.DONE.json: the merged corpus is not certified "
                 "complete, refusing to sample from it")
    if json.loads(done.read_text(encoding="utf-8")).get("calibrate"):
        sys.exit("the merged corpus is a CALIBRATION pass (flags null); refusing")

    d = pd.read_parquet(path)
    n0 = len(d)
    d = flt(d)
    print(f"{a.principle}: {n0:,} rows -> {len(d):,} after production filter")
    if not len(d) or pred not in d.columns or d[pred].isna().all():
        sys.exit(f"no usable '{pred}' values; refusing to draw an empty sample")

    d = d.copy()
    d[pred] = d[pred].fillna(False).astype(bool)
    before = len(d)
    d = d.drop_duplicates(keys)
    print(f"{a.principle}: {before:,} rows -> {len(d):,} distinct entities")
    d["row_key"] = d[keys].astype(str).agg("||".join, axis=1)

    # `round_suffix`, not `holdout=`: the audit gate and record_labels look for
    # `py2_{p}_labeling_sample_holdout.csv`, while sample_paths' holdout flag
    # builds the V1 name `py2_{p}_holdout_sample.csv`, which neither would find.
    out = SP.resolve(a.principle, "py2",
                     round_suffix="_holdout" if a.holdout else "", force=a.force)

    if a.holdout:
        tuning = SP.sample_path(a.principle, "py2")
        if not tuning.exists():
            sys.exit(f"--holdout needs a judged tuning sample first: {tuning.name}")
        t = pd.read_csv(tuning)
        judge = f"llm_{a.principle}_risk"
        judged = t[t[judge].notna()] if judge in t.columns else t
        burned = set(judged["row_key"].astype(str))
        n1 = len(d)
        d = d[~d.row_key.isin(burned)]
        print(f"  excluded {n1 - len(d):,} previously judged entities")

    seed = a.seed if a.seed is not None else C.RANDOM_SEED + (1000 if a.holdout else 0)
    parts, population = [], {}
    for val, g in d.groupby(pred):
        n = min(a.n_per_stratum, len(g))
        parts.append(g.sample(n=n, random_state=seed))
        population[str(bool(val))] = int(len(g))
        print(f"  {pred}={val}: sampled {n} of {len(g):,}")
    s = pd.concat(parts).sample(frac=1.0, random_state=seed).reset_index(drop=True)
    s[f"llm_{a.principle}_risk"] = ""
    s["notes"] = pd.Series([""] * len(s), dtype=object)

    C.LABELS_DIR.mkdir(parents=True, exist_ok=True)
    s.to_csv(out, index=False)
    out.with_suffix(".population.json").write_text(json.dumps({
        "principle": a.principle, "pred_col": pred, "seed": seed,
        "population": population,
        "sampled": {str(k): int(v) for k, v in s[pred].value_counts().items()},
        "note": "population = distinct production entities per prediction stratum; "
                "use these, not the sample counts, to weight recall",
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {out.name} ({len(s)} rows, {int(s[pred].sum())} flagged) "
          f"and {out.with_suffix('.population.json').name}")


if __name__ == "__main__":
    main()
