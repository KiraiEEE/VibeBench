"""
Step 5-PYTHON - every headline rate for the Python V2 corpus, all five principles.

Mirrors `report_java.py`'s layout so the two languages can be read side by side,
and reads the parquets `py_lang/run_python.py` writes.

THE HOUSE RULES THIS FILE EXISTS TO OBEY:
  * ALL FIVE PRINCIPLES IN EVERY TABLE. Leaving SRP out of the delta tables was
    a repeated complaint against earlier drafts.
  * LEAD WITH MACRO-PER-REPO, and print counts beside every rate. A pooled rate
    is dominated by whichever repository contributed most rows; the per-repo
    mean answers "in a typical repository", which is the question asked.
  * THE OUTCOME AXIS IS REPORTED BY STRATUM, never as one merged-versus-rejected
    number. In Java only 171 of 1,177 not-merged PRs were ever reviewed, so a
    single contrast silently compares reviewed code against abandoned code.
  * PRODUCTION AND TEST CODE ARE SEPARATED. Test code is ~28% of this corpus and
    is flagged at a different rate, so pooling dilutes whichever agent writes
    more tests and can reorder the agents.

WHAT THIS FILE DOES NOT DO. It reports rates. It does not adjudicate whether one
agent is better: change size dominates every principle in the Java joint model
(odds ratio ~1.9 per doubling of files touched), so a raw per-agent table is a
description, not a ranking. `06_model/fit_mixed_model.py` is what answers that,
and its output is the number the write-up should lead with.

Run:  python vibebench/04_report/report_python.py
      python vibebench/04_report/report_python.py --pilot
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

PR_RESULTS = C.CORPUS_DIR / "py_pr_results.parquet"
RESULTS = {"pr": PR_RESULTS, "srp": C.SRP_RESULTS, "ocp": C.OCP_RESULTS,
           "lsp": C.LSP_RESULTS, "isp": C.ISP_RESULTS}


def use_pilot_paths() -> None:
    global RESULTS, PR_RESULTS
    RESULTS = {k: v.with_suffix(".PILOT.parquet") for k, v in RESULTS.items()}
    PR_RESULTS = RESULTS["pr"]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval, correct at the small counts this corpus has, where
    the normal approximation would give negative lower bounds."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - m) / d), min(1.0, (c + m) / d))


def rate(k: int, n: int) -> str:
    lo, hi = wilson(k, n)
    return f"{k:,}/{n:,} = {k/max(n,1):.1%}  [95% CI {lo:.1%}-{hi:.1%}]"


def is_test(p) -> bool:
    return C.is_test_path(str(p), "py")


def header(t: str) -> None:
    print("\n" + "=" * 76)
    print(t)
    print("=" * 76)


def ensure_outcome(df: pd.DataFrame, gold: pd.DataFrame) -> pd.DataFrame:
    """Guarantee `outcome` / `rejection_stratum` / `created_at` on a frame.

    run_python.py writes outcome onto every row, so this is a fallback. It JOINS
    rather than defaulting: silently labelling unknown rows "merged" would
    manufacture the very contrast the report exists to test. `created_at` lives
    only in the gold set, and without it the era check would sit in the report
    looking like a control that passed when it had never run.
    """
    # An EMPTY parquet has no columns at all, not even `pr_id`, so the merge
    # below would raise KeyError rather than return nothing. A principle can
    # legitimately produce zero rows (LSP fires on 0.4% of PRs in Java), and a
    # report that crashes on the quietest principle is worse than useless.
    if df is None or not len(df) or "pr_id" not in df.columns:
        return df
    want = ("outcome", "rejection_stratum", "created_at")
    need = [c for c in want if c not in df.columns and c in gold.columns]
    if not need:
        return df
    out = df.merge(gold[["pr_id"] + need].drop_duplicates("pr_id"),
                   on="pr_id", how="left")
    miss = int(out["outcome"].isna().sum()) if "outcome" in out else 0
    if miss:
        print(f"  !! {miss} rows have no outcome after the join, EXCLUDED from "
              f"every outcome split")
    return out


def per_agent(sub: pd.DataFrame, flag: str, indent: str = "    ") -> None:
    """Flag rate per agent. Macro-per-repo first, counts beside rates."""
    if not len(sub) or flag not in sub.columns:
        return
    print(f"{indent}{'agent':<14}{'pooled':>22}{'macro/repo':>13}{'repos':>8}")
    for agent, g in sub.groupby("agent"):
        f = g[flag].fillna(False).astype(bool)
        k, n = int(f.sum()), len(g)
        macro = g.assign(_f=f).groupby("full_name")["_f"].mean().mean()
        lo, hi = wilson(k, n)
        print(f"{indent}{agent:<14}{f'{k:,}/{n:,} = {k/max(n,1):.1%}':>22}"
              f"{100*macro:12.1f}%{g.full_name.nunique():8,}"
              f"   [CI {100*lo:.1f}%-{100*hi:.1f}%]")


def by_outcome(sub: pd.DataFrame, flag: str, indent: str = "    ") -> None:
    """BY STRATUM, never as one merged-versus-rejected number. See the docstring."""
    if "outcome" not in sub.columns or not len(sub):
        return
    s = sub[sub["outcome"].notna()]
    col = "rejection_stratum" if "rejection_stratum" in s.columns else "outcome"
    print(f"{indent}{'stratum':<22}{'n':>8}{'flagged':>10}{'rate':>9}")
    for strat, g in s.groupby(col):
        f = g[flag].fillna(False).astype(bool)
        print(f"{indent}{str(strat):<22}{len(g):>8,}{int(f.sum()):>10,}"
              f"{f.mean():>9.1%}")
    # TIER-MATCHED CONTRAST, as in report_java.py. Tier 1 (merge commit's first
    # parent) exists only for merged PRs, so a pooled merged-vs-rejected rate
    # compares tight deltas with loose ones. Tiers 2, 3 and 4 all take the PR's
    # own branch point as "before" and are open to every outcome.
    if "pair_tier" in s.columns:
        t = s[s["pair_tier"].isin([2.0, 3.0, 4.0])]
        rows = []
        for oc in ("merged", "open", "rejected"):
            g = t[t["outcome"] == oc]
            if len(g) >= 30:
                f = g[flag].fillna(False).astype(bool)
                lo, hi = wilson(int(f.sum()), len(g))
                rows.append((oc, int(f.sum()), len(g), lo, hi))
        if len(rows) >= 2:
            print(f"{indent}TIER-MATCHED (tiers 2+3+4, quote this one):")
            for oc, k, n, lo, hi in rows:
                print(f"{indent}  {oc:<9}{k:>5,}/{n:<6,}= {k/n:.1%}  "
                      f"[CI {100*lo:.1f}%-{100*hi:.1f}%]")
            rate_of = {oc: k / n for oc, k, n, _, _ in rows}
            if "merged" in rate_of and "rejected" in rate_of:
                print(f"{indent}  difference (rejected - merged): "
                      f"{100*(rate_of['rejected'] - rate_of['merged']):+.1f} pp")


def principle_section(title: str, prs: pd.DataFrame, flag: str,
                      entity: pd.DataFrame | None = None,
                      entity_flag: str | None = None,
                      entity_label: str = "entities") -> None:
    header(title)
    ok = prs[prs.status == "ok"]
    if flag not in ok.columns:
        print(f"  {flag} not present, run the flagged pass (not --calibrate)")
        return
    f = ok[flag].fillna(False).astype(bool)
    if ok[flag].isna().all():
        print("  FLAGS ARE NULL: this is a CALIBRATION parquet. Numbers below "
              "would be meaningless, so they are not printed. Set thresholds, "
              "delete the parquets and re-run.")
        return
    print(f"\n  PR-level flag rate: {rate(int(f.sum()), len(ok))}")
    print("\n  per agent:")
    per_agent(ok, flag)
    print("\n  by outcome stratum:")
    by_outcome(ok, flag)
    if entity is not None and len(entity) and entity_flag in getattr(entity, "columns", ()):
        ef = entity[entity_flag].fillna(False).astype(bool)
        print(f"\n  {entity_label}: {rate(int(ef.sum()), len(entity))} flagged")
    elif entity is not None and not len(entity):
        print(f"\n  {entity_label}: none in scope in this corpus")


def srp_section(prs: pd.DataFrame, gold: pd.DataFrame) -> None:
    header("SRP  (per class, touched files, AFTER tree)")
    if not RESULTS["srp"].exists():
        print("  no SRP parquet")
        return
    d = ensure_outcome(pd.read_parquet(RESULTS["srp"]), gold)
    if "srp_risk" not in d.columns or d["srp_risk"].isna().all():
        print("  FLAGS ARE NULL: calibration parquet, nothing to report yet.")
        return
    d["is_test"] = d["file_path"].map(is_test)
    prod = d[~d.is_test]
    print(f"\n  classes: {len(d):,} total, {len(prod):,} production "
          f"({d.is_test.mean():.1%} test code)")
    print(f"  PRODUCTION flag rate : {rate(int(prod.srp_risk.fillna(False).sum()), len(prod))}")
    tst = d[d.is_test]
    if len(tst):
        print(f"  test flag rate       : {rate(int(tst.srp_risk.fillna(False).sum()), len(tst))}")
    print("\n  per agent, PRODUCTION classes:")
    per_agent(prod, "srp_risk")

    # AUTHORED vs EDITED. The strongest fidelity claim available: a violation
    # PRESENT in code the agent touched is a different statement from one the
    # agent WROTE, and only SRP can separate the two.
    if "is_new" in prod.columns:
        auth = prod[prod.is_new.fillna(False).astype(bool)]
        print(f"\n  AUTHORED by the agent (is_new): {len(auth):,} classes, "
              f"{rate(int(auth.srp_risk.fillna(False).sum()), len(auth))} flagged")
        print(f"  edited pre-existing           : {len(prod)-len(auth):,} classes")
        print("  (attribution for the other four principles is TOUCHED, not "
              "AUTHORED; only SRP can make this split)")
    print("\n  by outcome stratum, production classes:")
    by_outcome(prod, "srp_risk")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true")
    a = ap.parse_args()
    if a.pilot:
        use_pilot_paths()

    if not PR_RESULTS.exists():
        sys.exit(f"missing {PR_RESULTS.name}. Run phase 3 first:\n"
                 f"  python vibebench/03_principles/py_lang/run_python.py")

    gold = pd.read_parquet(C.GOLD_SET)
    prs = ensure_outcome(pd.read_parquet(PR_RESULTS), gold)

    header("PYTHON V2 CORPUS")
    print(f"\n  gold set     : {len(gold):,} PRs over {gold.full_name.nunique():,} repos")
    print(f"  analysed     : {len(prs):,} PRs")
    st = prs.status.value_counts()
    print(f"  status       : {dict(st)}")
    ok = prs[prs.status == "ok"]
    print(f"  ANALYSABLE   : {len(ok):,} PRs over {ok.full_name.nunique():,} repos")
    if len(ok):
        print("\n  analysable per agent x outcome:")
        print(pd.crosstab(ok.agent, ok.outcome, margins=True).to_string()
              .replace("\n", "\n    ").rjust(0))
        # TIER MIX. Tier 1 (merge commit + first parent) is available only to
        # merged PRs, so if the mix differed sharply by outcome the contrast
        # would partly compare how the before-tree was built.
        if "pair_tier" in ok.columns:
            print("\n  reconstruction tier x outcome (tier 1 is merged-only by "
                  "construction):")
            print(pd.crosstab(ok.pair_tier, ok.outcome).to_string()
                  .replace("\n", "\n    "))

    srp_section(prs, gold)

    ocp = ensure_outcome(pd.read_parquet(RESULTS["ocp"]), gold) if RESULTS["ocp"].exists() else None
    lsp = ensure_outcome(pd.read_parquet(RESULTS["lsp"]), gold) if RESULTS["lsp"].exists() else None
    isp_all = pd.read_parquet(RESULTS["isp"]) if RESULTS["isp"].exists() else None
    isp = (ensure_outcome(isp_all[isp_all.row_type == "interface"], gold)
           if isp_all is not None and len(isp_all) and "row_type" in isp_all.columns
           else isp_all)

    principle_section("OCP  (dispatch chains the PR created or widened)",
                      prs, "ocp_risk", ocp, "smell", "dispatch chains in scope")
    principle_section("LSP  (override pairs, FIVE signals: Python has no "
                      "compiler to reject the other two)",
                      prs, "lsp_risk", lsp, "violation", "override pairs in scope")
    principle_section("ISP  (contracts and forced stubs)",
                      prs, "isp_risk", isp, "flagged", "contracts in scope")
    principle_section("DIP  (module cycles the PR newly created)",
                      prs, "dip_risk")

    header("READ THIS BEFORE QUOTING ANY TABLE ABOVE")
    print("""
  The per-agent tables are DESCRIPTIONS, not a ranking. In the Java study change
  size dominated every principle (odds ratio ~1.9 per doubling of files touched,
  p < 1e-4) and only OCP survived adjustment. Run the joint model before drawing
  any conclusion about agents:

      python vibebench/06_model/fit_mixed_model.py

  Attribution is TOUCHED, not AUTHORED, for OCP, LSP, ISP and DIP. Only SRP
  separates the two, and it is reported above.

  Recall, when it is computed, is ALWAYS stratum-weighted. The label sample is
  half flagged and this corpus is nowhere near half flagged, so a raw recall
  answers a question about the sample rather than about the code.
""")


if __name__ == "__main__":
    main()
