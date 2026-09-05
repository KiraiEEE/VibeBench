"""
PHASE 4 - propose Java thresholds from Java's OWN distribution.

The non-circular method used for OCP and for the TypeScript port: read the
percentiles BEFORE any labelling exists, anchor the SIZE knobs at ~p90, and
keep the SCALE-FREE knobs at their Python-validated values. Copying another
language's size bars is exactly what put the Java switch bar at p75 and made
the void run flag a quarter of all switches.

This script only PRINTS a proposal. Editing config is a deliberate human step,
so a threshold can never move as a side effect of running something.

Run: python vibebench/03_principles/java_lang/propose_thresholds.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import config as C  # noqa: E402

QS = (.5, .75, .9, .95, .99)
PR_RESULTS = C.CORPUS_DIR / "java_pr_results.parquet"


def pct(s: pd.Series) -> str:
    return "  ".join(f"p{int(q*100)}={s.quantile(q):.2f}" for q in QS)


def main() -> None:
    print("\nPHASE 4 -- threshold proposal from the Java distribution\n" + "=" * 70)

    # ---------------- SRP
    d = pd.read_parquet(C.SRP_RESULTS_JAVA)
    print(f"\nSRP  n={len(d):,} type rows")
    # Calibrate on PRODUCTION types only: ~72% of Java rows are tests, and test
    # classes have a different size distribution, so including them would drag
    # every percentile toward test shapes.
    d["is_test"] = d.file_path.map(lambda p: C.is_test_path(str(p), "java"))
    prod = d[~d.is_test].drop_duplicates(["full_name", "type_id"])
    print(f"     production, entity-deduplicated: n={len(prod):,} "
          f"({d.is_test.mean():.1%} of all rows are tests)")
    for c in ("class_loc", "wmc", "method_count", "max_method_ccn", "max_params", "lcom", "c3"):
        if c in prod:
            print(f"       {c:<16} {pct(prod[c])}")

    def p90(c):
        return prod[c].quantile(.9)

    print(f"""
SRP_JAVA_THRESHOLDS = {{
    # SIZE knobs anchored at ~p90 of the PRODUCTION, entity-deduplicated
    # JavaParser distribution (n={len(prod):,}). NOT copied from Python or TypeScript.
    "class_loc": {int(round(p90('class_loc'), -1))},          # p90={p90('class_loc'):.0f}
    "class_wmc": {int(round(p90('wmc')))},           # p90={p90('wmc'):.0f}
    "class_methods": {int(round(p90('method_count')))},       # p90={p90('method_count'):.0f}
    "func_ccn": {int(round(p90('max_method_ccn')))},            # p90={p90('max_method_ccn'):.0f}
    # SCALE-FREE knobs keep their Python-validated values, exactly as the TS
    # port did. Java's p90 for params is {p90('max_params'):.0f}, which is not a god-class
    # signal in any taxonomy -- a percentile is a tail heuristic, not a licence
    # to flag ordinary code.
    "func_params": 6,
    "class_lcom": 0.8,
    "lcom_min_methods": 4,
    "c3_suppress": 0.4,
    "min_hits": 2,
    "strict_hits": 3,
}}""")
    for k, v in (("class_loc", "class_loc"), ("class_wmc", "wmc"),
                 ("class_methods", "method_count"), ("func_ccn", "max_method_ccn")):
        share = (prod[v] >= p90(v)).mean()
        print(f"    sanity: {k:<15} would fire on {share:.1%} of production types")

    # ---------------- OCP
    ch = pd.read_parquet(C.OCP_RESULTS_JAVA)
    ch = ch.drop_duplicates(["full_name", "type_id", "method", "subject"])
    print(f"\nOCP  n={len(ch):,} distinct chains")
    print(f"     closed-set (enum subject): {int(ch.closed_set.fillna(False).sum())}")
    for k, g in ch.groupby("kind"):
        print(f"       {k:<14} n={len(g):<6} {pct(g.n_branches)}")
    tb = ch[ch.kind.isin(["instanceof", "type_eq"])].n_branches
    vb = ch[ch.kind.isin(["value_switch", "switch_tag"])].n_branches
    print(f"""
JAVA_DELTA_THRESHOLDS["ocp"] = {{
    # 3 is Martin's own minimum for a "switch" and is used unchanged in Python
    # and TypeScript, so type-based bars stay comparable across languages.
    "type_branches": 3,          # Java p90={tb.quantile(.9) if len(tb) else float('nan'):.0f}
    "value_branches": {int(vb.quantile(.9)) if len(vb) else 6},         # Java p90={vb.quantile(.9) if len(vb) else float('nan'):.0f}  (Python 6, TypeScript 8)
}}""")
    if len(vb):
        print(f"    sanity: value bar would fire on {(vb >= vb.quantile(.9)).mean():.1%} of value chains")

    # ---------------- ISP
    isp = pd.read_parquet(C.ISP_RESULTS_JAVA)
    ifs = isp[isp.row_type == "interface"].drop_duplicates(["full_name", "iface_id"])
    ims = isp[isp.row_type == "implementer"].drop_duplicates(["full_name", "iface_id", "type_id"])
    print(f"\nISP  n={len(ifs):,} distinct contracts, {len(ims):,} implementer pairs")
    if len(ifs):
        print(f"       n_members      {pct(ifs.n_members)}   max={ifs.n_members.max():.0f}")
    if len(ims):
        print(f"       stub_ratio     {pct(ims.stub_ratio)}")
    fat = int(round(ifs.n_members.quantile(.9))) if len(ifs) else 6
    print(f"""
JAVA_DELTA_THRESHOLDS["isp"] = {{
    "fat_members": {fat},           # Java p90={ifs.n_members.quantile(.9) if len(ifs) else float('nan'):.0f}  (TypeScript used 7)
    "min_stubs": 2,             # scale-free, carried over
    "stub_ratio": 0.5,          # scale-free, carried over
}}""")

    # ---------------- LSP signal balance (a caution, not a threshold)
    lsp = pd.read_parquet(C.LSP_RESULTS_JAVA)
    lsp = lsp.drop_duplicates(["full_name", "type_id", "method"])
    print(f"\nLSP  n={len(lsp):,} distinct override pairs (concrete base only)")
    from collections import Counter
    sc = Counter()
    for x in lsp.signals.fillna(""):
        for y in str(x).split(";"):
            if y:
                sc[y] += 1
    print("     signal counts: " + (", ".join(f"{a}={b}" for a, b in sc.most_common()) or "none"))
    print("     LSP has NO threshold to calibrate -- its signals are categorical,")
    print("     which is also why its holdout stays clean. If one signal dominates,")
    print("     that is a question for the LABELS, not for a knob.")

    # ---------------- statuses (PHASE 5)
    prs = pd.read_parquet(PR_RESULTS)
    print(f"\nPHASE 5 -- PR statuses over {len(prs):,} PRs")
    for k, v in prs.status.value_counts().items():
        print(f"       {k:<20} {v:>5}  ({v/len(prs):.1%})")


if __name__ == "__main__":
    main()
