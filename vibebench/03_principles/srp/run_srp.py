"""
Step 3 - SRP evaluator: run metrics.py over every extracted AI-touched file and
apply the SRP-risk rubric from config.py.

Reads corpus/clone_manifest.parquet (Step 2 output), analyzes each successfully
extracted file, and writes one row per class (plus one file-level row when a
file has zero classes) to corpus/srp_results.parquet.

Run:  python vibebench/03_principles/srp/run_srp.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "srp"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import config as C  # noqa: E402
from metrics import analyze_file  # noqa: E402


def srp_risk_hits(row: dict, *, func_ccn: int, func_params: int,
                  is_class_row: bool) -> list[str]:
    """Which rubric thresholds this row crosses. See config.SRP_THRESHOLDS.

    `func_ccn`/`func_params` are passed explicitly rather than read from `row`
    because the right scope differs by row type: for a class row they must be
    the MAX OVER THAT CLASS'S OWN METHODS (ClassMetrics.max_method_ccn /
    .max_params), not the file-wide max -- otherwise one huge unrelated
    function elsewhere in the file would flag every other class in that file.
    For a file-only row (no classes) the file-wide max is the only option.

    Label-validated adjustments (see config.py for the tuning evidence):
    - LCOM only counts for classes with >= LCOM_MIN_METHODS methods (the metric
      saturates to 0/1 below that, its biggest false-positive source).
    - file_loc only counts for file-level rows: it's context, not a property of
      any individual class in the file.
    """
    hits = []
    t = C.SRP_THRESHOLDS
    if row.get("class_loc") is not None and row["class_loc"] > t["class_loc"]:
        hits.append("class_loc")
    if row.get("wmc") is not None and row["wmc"] > t["class_wmc"]:
        hits.append("class_wmc")
    if row.get("method_count") is not None and row["method_count"] > t["class_methods"]:
        hits.append("class_methods")
    if (row.get("lcom") is not None and row["lcom"] > t["class_lcom"]
            and (row.get("method_count") or 0) >= C.LCOM_MIN_METHODS):
        hits.append("class_lcom")
    if not is_class_row and row["file_loc"] > t["file_loc"]:
        hits.append("file_loc")
    if func_ccn > t["func_ccn"]:
        hits.append("func_ccn")
    if func_params > t["func_params"]:
        hits.append("func_params")
    return hits


def main() -> None:
    manifest = pd.read_parquet(C.CLONE_MANIFEST)
    ok = manifest[manifest.extract_status == "ok"].copy()
    print(f"[srp] analyzing {len(ok)} extracted files")

    # C3 backend: "tfidf" (default) or "embed" (per-method code embeddings).
    # Fall back to tfidf if "embed" is requested but the encoder is unavailable.
    c3_mode = getattr(C, "SRP_C3_MODE", "tfidf")
    if c3_mode == "embed":
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 03_principles
        import embeddings as E  # noqa: E402
        if E.available():
            print(f"[srp] C3 backend = EMBED ({getattr(C, 'EMBED_MODEL', '?')})")
        else:
            print(f"[srp] SRP_C3_MODE=embed but encoder unavailable "
                  f"({E.unavailable_reason()}) -> falling back to tfidf")
            c3_mode = "tfidf"

    rows: list[dict] = []
    errors = 0
    for i, r in enumerate(ok.itertuples(index=False), 1):
        local_path = Path(r.local_path)
        try:
            source = local_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            errors += 1
            continue

        fm = analyze_file(r.file_path, source, c3_mode=c3_mode)
        base = {
            "pr_id": r.pr_id, "agent": r.agent, "full_name": r.full_name,
            "pr_number": r.pr_number, "file_path": r.file_path,
            "file_loc": fm.file_loc, "maintainability_index": fm.maintainability_index,
            "max_func_ccn": fm.max_func_ccn, "max_func_params": fm.max_func_params,
            "parse_ok": fm.parse_ok, "parse_error": fm.error,
        }

        if not fm.parse_ok:
            errors += 1
            rows.append({**base, "class_name": None, "class_loc": None,
                         "method_count": None, "wmc": None, "max_method_ccn": None,
                         "lcom": None, "c3": None, "max_class_params": None,
                         "srp_risk_hits": [], "srp_risk": False})
            continue

        if not fm.classes:
            # Module with no classes: no per-class scope exists, so the file-wide
            # max is the only option here (this row IS the whole file).
            hits = srp_risk_hits(base, func_ccn=fm.max_func_ccn,
                                 func_params=fm.max_func_params, is_class_row=False)
            rows.append({**base, "class_name": None, "class_loc": None,
                         "method_count": None, "wmc": None, "max_method_ccn": None,
                         "lcom": None, "c3": None, "max_class_params": None,
                         "srp_risk_hits": hits, "srp_risk": len(hits) >= C.SRP_RISK_MIN_HITS})
            continue

        for c in fm.classes:
            row = {
                **base,
                "class_name": c.class_name, "class_loc": c.loc,
                "method_count": c.method_count, "wmc": c.wmc,
                "max_method_ccn": c.max_method_ccn, "lcom": c.lcom,
                "c3": c.c3, "max_class_params": c.max_params,
            }
            # Class-scoped complexity/params, NOT the file-wide max (see srp_risk_hits docstring).
            hits = srp_risk_hits(row, func_ccn=c.max_method_ccn, func_params=c.max_params,
                                 is_class_row=True)
            row["srp_risk_hits"] = hits
            risk = len(hits) >= C.SRP_RISK_MIN_HITS
            # Semantic suppressor: high conceptual cohesion (C3) overrides a
            # size/structure-based flag -- see config.SRP_C3_SUPPRESS.
            suppressed = bool(
                risk and C.SRP_C3_SUPPRESS is not None
                and c.c3 is not None and c.c3 >= C.SRP_C3_SUPPRESS
            )
            row["c3_suppressed"] = suppressed
            row["srp_risk"] = risk and not suppressed
            # Stricter operating point (holdout: P=0.43/R=0.90 vs 0.33/1.00).
            row["srp_risk_strict"] = (len(hits) >= C.SRP_RISK_STRICT_MIN_HITS
                                      and not suppressed)
            rows.append(row)

        if i % 200 == 0:
            print(f"  [{i}/{len(ok)}]")

    results = pd.DataFrame(rows)
    results.to_parquet(C.SRP_RESULTS, index=False)

    print(f"\n[srp] results written to {C.SRP_RESULTS}")
    print(f"[srp] rows: {len(results)}  parse errors: {errors}")
    print("\nSRP-risk rate per agent (class-level rows only):")
    class_rows = results[results.class_name.notna()]
    summary = class_rows.groupby("agent").agg(
        classes=("class_name", "size"),
        srp_risk_rate=("srp_risk", "mean"),
        median_loc=("class_loc", "median"),
        median_wmc=("wmc", "median"),
        median_lcom=("lcom", "median"),
    ).round(3)
    print(summary.to_string())


if __name__ == "__main__":
    main()
