"""Compare the Open/Closed detector with DesigniteJava's type-conditional smells.

Open/Closed was the one principle validated by the reading judge alone. It does
have a counterpart in a published tool after all: DesigniteJava reports
*Missing Hierarchy* (conditional logic on type codes where a hierarchy belongs)
and *Unexploited Encapsulation* (explicit type checks instead of using an
existing hierarchy). Both were already recorded by the cross-check but were
never compared with our flag.

This script closes that gap without re-running the cross-check. For each
repository in `java_crosscheck_status.csv` it re-reads the head tree recorded
there (by SHA, from the shared tree cache), runs the same dispatch-chain
detector the study uses, maps each chain to the class that encloses it, and
compares that class-level verdict with the two Designite smells already stored
per class in `java_crosscheck_classes.csv`.

Agreement is reported symmetrically, with Cohen's kappa, exactly as the
three-tool God Class comparison is.

Run:  python vibebench/05_crosscheck/compare_ocp_designite.py [--limit N]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))
sys.path.insert(0, str(_HERE.parents[1] / "03_principles"))
sys.path.insert(0, str(_HERE.parents[1] / "03_principles" / "java_lang"))
import config as C          # noqa: E402
import gitsnap              # noqa: E402
import java_bridge          # noqa: E402
import detectors as D       # noqa: E402

STATUS = C.REPORT_DIR / "java_crosscheck_status.csv" if hasattr(C, "REPORT_DIR") \
    else Path("vibebench/04_report/java_crosscheck_status.csv")
CLASSES = Path("vibebench/04_report/java_crosscheck_classes.csv")
OUT = Path("vibebench/04_report/ocp_designite_comparison.json")
DESIGNITE_OCP = ["Missing Hierarchy", "Unexploited Encapsulation"]


def kappa(a: pd.Series, b: pd.Series) -> float:
    n = len(a)
    if not n:
        return float("nan")
    po = float((a == b).mean())
    pe = float(a.mean() * b.mean() + (1 - a.mean()) * (1 - b.mean()))
    return (po - pe) / (1 - pe) if pe < 1 else float("nan")


def ocp_classes_for_tree(files: dict[str, str], th: dict) -> set[str]:
    """Type identifiers whose body contains a flagged dispatch chain."""
    facts = java_bridge.analyze_tree(files)
    if not facts:
        return set()
    ix = D.build_index(facts)
    hit = set()
    for ch in D.dispatch_chains(ix):
        if D.is_dispatch_smell(ch, th["ocp"]):
            hit.add(ch["type_id"])
    return hit


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    st = pd.read_csv(STATUS)
    st = st[(st.status == "ok") & st.sha.notna()]
    if a.limit:
        st = st.head(a.limit)
    cl = pd.read_csv(CLASSES)
    for c in DESIGNITE_OCP:
        if c not in cl.columns:
            sys.exit(f"{c} missing from {CLASSES}")
    cl = cl[cl.dj_seen.fillna(False).astype(bool)] if "dj_seen" in cl.columns else cl

    th = C.JAVA_DELTA_THRESHOLDS
    rows, failed = [], 0
    for i, r in enumerate(st.itertuples(), 1):
        try:
            files = gitsnap.src_tree(gitsnap.repo_dir(r.full_name), r.sha,
                                     C.JAVA_SOURCE_EXTENSIONS,
                                     C.JAVA_EXCLUDED_PATH_PARTS,
                                     full_name=r.full_name)
        except Exception:
            files = None
        if not files or gitsnap.TOO_LARGE in files:
            failed += 1
            continue
        try:
            hit = ocp_classes_for_tree(files, th)
        except Exception:
            failed += 1
            continue
        sub = cl[cl.full_name == r.full_name]
        if not len(sub):
            continue
        dj_any = sub[DESIGNITE_OCP].fillna(False).astype(bool).any(axis=1)
        rows.extend({
            "full_name": r.full_name,
            "type_id": tid,
            "ours_ocp": tid in hit,
            "designite": bool(flag),
        } for tid, flag in zip(sub.type_id, dj_any))
        if i % 20 == 0:
            print(f"  [{i}/{len(st)}] repositories, {len(rows):,} classes")

    if not rows:
        sys.exit("no classes compared; is the tree cache populated?")
    d = pd.DataFrame(rows)
    ours, dj = d.ours_ocp.astype(bool), d.designite.astype(bool)
    tp = int((ours & dj).sum()); fp = int((ours & ~dj).sum())
    fn = int((~ours & dj).sum()); tn = int((~ours & ~dj).sum())
    res = {
        "n_repositories": int(d.full_name.nunique()),
        "n_repositories_unavailable": failed,
        "n_classes": len(d),
        "flag_rate_ours": round(float(ours.mean()), 5),
        "flag_rate_designite": round(float(dj.mean()), 5),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision_vs_designite": round(tp / max(tp + fp, 1), 4),
        "recall_vs_designite": round(tp / max(tp + fn, 1), 4),
        "kappa": round(kappa(ours, dj), 4),
        "designite_smells": DESIGNITE_OCP,
        "note": "Class-level comparison on the cross-check head trees. Ours flags a class whose "
                "body holds a dispatch chain at or above the calibrated width and not over an enum.",
    }
    OUT.write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
