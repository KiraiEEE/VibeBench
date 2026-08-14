"""
Step 5-JAVA - all five principles for the OpenAI Codex Java corpus.

SCOPE, stated up front because it governs every number: this is a WITHIN-AGENT
study of OpenAI Codex. No agent comparison is made, because no other agent has
usable Java in AIDev (Claude Code 8, Devin 34, Cursor 104, Copilot 0, against
Codex's 3,024 -- even with every quality filter removed).

Four corrections the earlier language reports learned the hard way, all of which
change the headline:

  1. ENTITY DEDUPLICATION. A class touched by N pull requests appears N times.
     Java's duplication factor is the worst of the three languages (1.86x vs
     Python 1.22x, TypeScript 1.32x) because the Java corpus is uncapped, so a
     single god class can contribute 23 rows. Rates are per DISTINCT entity.
  2. TEST CODE. ~72% of Java types are tests, far above Python's 34.7%, and test
     classes flag at a different rate. Production is the headline.
  3. AUTHORSHIP. `is_new` separates "Codex WROTE this class" from "Codex edited
     a pre-existing class someone else wrote". Charging the second to Codex is
     the single most misleading thing the void run did, and SRP is the only
     principle where it matters (the other four are already deltas).
  4. COUNTS BESIDE RATES (audit rule #7). Per-PR signal is single-digit for the
     delta principles; a percentage alone invites over-reading.

Run: python vibebench/04_report/report_java.py
"""
from __future__ import annotations

import math
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

pd.set_option("display.width", 200)
PR_RESULTS = C.CORPUS_DIR / "java_pr_results.parquet"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval -- correct at the small counts this corpus has,
    where the normal approximation would give negative lower bounds."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - m) / d), min(1.0, (c + m) / d))


def rate(k: int, n: int, label: str = "") -> str:
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {k/n:.1%}  [95% CI {lo:.1%}-{hi:.1%}]{label}"


def is_test(p: str) -> bool:
    return C.is_test_path(str(p), "java")


def header(t: str) -> None:
    print("\n" + "=" * 76)
    print(t)
    print("=" * 76)


# ------------------------------------------------------------------ SRP

def srp_section(prs: pd.DataFrame) -> None:
    if not C.SRP_RESULTS_JAVA.exists():
        print("\n[SRP] not run"); return
    d = pd.read_parquet(C.SRP_RESULTS_JAVA)
    header("SRP -- god classes   (scope: every type in a file the PR touched)")
    if "srp_risk" not in d or d.srp_risk.isna().all():
        print(f"  CALIBRATION MODE -- {len(d):,} type rows, no flags yet.")
        for c in ("class_loc", "wmc", "method_count", "max_method_ccn", "max_params", "lcom", "c3"):
            if c in d:
                print(f"    {c:<16} " + "  ".join(
                    f"p{int(q*100)}={d[c].quantile(q):.2f}" for q in (.5, .75, .9, .95, .99)))
        return

    d["is_test"] = d.file_path.map(is_test)
    d["entity"] = d.full_name + "::" + d.type_id
    print(f"  type rows            : {len(d):,}   ({d.is_test.mean():.1%} test code)")

    prod = d[~d.is_test].copy()
    # `is_new` must be aggregated BEFORE deduplication. A class authored in one
    # PR and edited in a later one appears twice; keeping only the largest
    # observation would silently relabel it as pre-existing and undercount what
    # Codex actually wrote. "Was this class ever new in this corpus" = any().
    ever_new = prod.groupby("entity").is_new.transform("max")
    prod["is_new"] = ever_new
    # keep the LARGEST observation of each distinct class
    uniq = prod.sort_values("class_loc").drop_duplicates("entity", keep="last")
    print(f"  production rows      : {len(prod):,}")
    print(f"  DISTINCT production  : {len(uniq):,}   "
          f"(duplication {len(prod)/max(len(uniq),1):.2f}x)")

    for name, sub in (("ALL distinct production classes", uniq),
                      ("classes Codex AUTHORED in this corpus (is_new)", uniq[uniq.is_new]),
                      ("pre-existing classes Codex only EDITED", uniq[~uniq.is_new])):
        if not len(sub):
            continue
        k, n = int(sub.srp_risk.sum()), len(sub)
        macro = sub.groupby("full_name").srp_risk.mean().mean()
        print(f"\n  {name}")
        print(f"    pooled  : {rate(k, n)}")
        print(f"    MACRO/repo: {macro:.1%}   <- mix-controlled, lead with this")
        print(f"    strict (>=3 hits): {int(sub.srp_risk_strict.sum())}/{n} = {sub.srp_risk_strict.mean():.1%}")

    print(f"\n  top-repo share       : {uniq.groupby('full_name').size().max()/len(uniq):.1%}")
    c = Counter()
    for h in uniq[uniq.srp_risk].hits.fillna(""):
        for x in str(h).split("|"):
            if x:
                c[x] += 1
    print("  signals that fire    : " + ", ".join(f"{k}={v}" for k, v in c.most_common()))
    print(f"  C3-suppressed        : {int(uniq.c3_suppressed.fillna(False).sum())} "
          f"(big but cohesive -> not flagged)")
    print("\n  worst distinct production classes:")
    cols = [x for x in ["type_name", "class_loc", "method_count", "wmc", "lcom", "c3",
                        "is_new", "full_name"] if x in uniq]
    print(uniq[uniq.srp_risk].nlargest(8, "wmc")[cols].to_string(index=False))


# ------------------------------------------------- delta principles

def delta_section(title: str, prs: pd.DataFrame, risk_col: str,
                  reason_col: str, extra=None) -> None:
    header(title)
    ok = prs[prs.status == "ok"]
    if risk_col not in ok.columns or ok[risk_col].isna().all():
        print(f"  CALIBRATION MODE -- {len(ok)} analysable PRs, no flags yet.")
        if extra:
            extra(ok)
        return
    k = int(ok[risk_col].fillna(False).sum())
    print(f"  analysable PRs : {len(ok)} of {len(prs)}")
    print(f"  FLAGGED        : {rate(k, len(ok))}")
    if k:
        r = Counter()
        for x in ok[ok[risk_col].fillna(False)][reason_col].fillna(""):
            for y in str(x).split("|"):
                if y:
                    r[y] += 1
        print("  reasons        : " + ", ".join(f"{a}={b}" for a, b in r.most_common()))
        by = ok[ok[risk_col].fillna(False)].full_name.value_counts()
        print(f"  repos involved : {len(by)}  (top: {dict(by.head(4))})")
    if extra:
        extra(ok)


def ocp_extra(ok: pd.DataFrame) -> None:
    if not C.OCP_RESULTS_JAVA.exists():
        return
    ch = pd.read_parquet(C.OCP_RESULTS_JAVA)
    if not len(ch):
        return
    ch = ch.drop_duplicates(["full_name", "type_id", "method", "subject"])
    print(f"  DISTINCT chains in scope: {len(ch):,}")
    print("    by kind: " + str(ch.groupby("kind").size().to_dict()))
    print(f"    closed-set (enum subject, exhaustive by construction): "
          f"{int(ch.closed_set.fillna(False).sum())}")
    print(f"    single-subject sanity: {ch.subject.nunique():,} distinct subjects "
          f"({ch.subject.nunique()/max(len(ch),1):.0%} of chains) "
          f"-- the void run had ONE literal subject for all 19,635")
    if "smell" in ch and not ch.smell.isna().all():
        print(f"    smells: {int(ch.smell.fillna(False).sum())}")


def lsp_extra(ok: pd.DataFrame) -> None:
    if not C.LSP_RESULTS_JAVA.exists():
        return
    ov = pd.read_parquet(C.LSP_RESULTS_JAVA)
    if not len(ov):
        return
    u = ov.drop_duplicates(["full_name", "type_id", "method"])
    print(f"  DISTINCT override pairs (concrete base only): {len(u):,}  "
          f"from {len(ov):,} rows ({len(ov)/max(len(u),1):.1f}x duplication)")
    v = u[u.violation.fillna(False)]
    print(f"  violating pairs: {len(v)}")
    if len(v):
        s = Counter()
        for x in v.signals.fillna(""):
            for y in str(x).split(";"):
                if y:
                    s[y] += 1
        print("    signals: " + ", ".join(f"{a}={b}" for a, b in s.most_common()))
        print(v.head(8)[["type_id", "method", "base_id", "signals"]].to_string(index=False))
    print("  NOTE: javac already enforces param-list, param-type, visibility and")
    print("        checked-exception compatibility, so those signals CANNOT occur")
    print("        in compiling Java. Only compiler-uncheckable signals are used.")


def isp_extra(ok: pd.DataFrame) -> None:
    if not C.ISP_RESULTS_JAVA.exists():
        return
    d = pd.read_parquet(C.ISP_RESULTS_JAVA)
    ifs = d[d.row_type == "interface"].drop_duplicates(["full_name", "iface_id"])
    ims = d[d.row_type == "implementer"].drop_duplicates(["full_name", "iface_id", "type_id"])
    if len(ifs):
        print(f"  DISTINCT contracts (>=1 abstract member AND >=1 internal implementer): {len(ifs):,}")
        print("    widths: " + "  ".join(f"p{int(q*100)}={ifs.n_members.quantile(q):.0f}"
                                         for q in (.5, .75, .9, .95)) +
              f"  max={ifs.n_members.max():.0f}")
        if "flagged" in ifs and not ifs.flagged.isna().all():
            print(f"    fat: {int(ifs.flagged.fillna(False).sum())}")
    if len(ims):
        print(f"  DISTINCT implementer pairs: {len(ims):,}")
        if "forced_stub" in ims and not ims.forced_stub.isna().all():
            print(f"    forced-stub: {int(ims.forced_stub.fillna(False).sum())}")


def dip_extra(ok: pd.DataFrame) -> None:
    if not len(ok) or "new_cycle" not in ok:
        return
    nc = int(ok.new_cycle.fillna(False).sum())
    print(f"  PRs creating a NEW import cycle: {rate(nc, len(ok))}   <- the trustworthy half")
    if "dip_risk_hits" in ok:
        md = int(ok.dip_risk_hits.fillna("").str.contains("mean_d_worse").sum())
        print(f"  mean_d_worse: {md}   <- scored 0/17 precision in Python AND")
        print("                        TypeScript validation. Indicative only.")


# ------------------------------------------------------------------ main

def main() -> None:
    print("\nVIBEBENCH -- JAVA (OpenAI Codex only)\n")
    if not PR_RESULTS.exists():
        print(f"no results yet: {PR_RESULTS}")
        return
    gold = pd.read_parquet(C.GOLD_SET_JAVA)
    prs = pd.read_parquet(PR_RESULTS)
    print(f"corpus  : {len(gold)} PRs across {gold.full_name.nunique()} repositories")
    print(f"analysed: {len(prs)}   status: {prs.status.value_counts().to_dict()}")
    ok = prs[prs.status == "ok"]
    if len(ok) and "resolved_supertype_rate" in ok:
        print(f"symbol resolution: mean {ok.resolved_supertype_rate.mean():.1%} of "
              f"supertype references resolved to a fully-qualified name")
    if len(ok) and "n_parse_errors" in ok:
        print(f"parse errors in touched files: {int(ok.n_parse_errors.sum())}")

    srp_section(prs)
    delta_section("OCP -- open/closed   (scope: PR delta on dispatch chains)",
                  prs, "ocp_risk", "ocp_risk_reasons", ocp_extra)
    delta_section("LSP -- substitutability   (scope: PR delta on override pairs)",
                  prs, "lsp_risk", "lsp_risk_reasons", lsp_extra)
    delta_section("ISP -- interface segregation   (scope: PR delta on contracts)",
                  prs, "isp_risk", "isp_risk_reasons", isp_extra)
    delta_section("DIP -- dependency inversion   (scope: PR delta on the package graph)",
                  prs, "dip_risk", "dip_risk_hits", dip_extra)

    header("READ THIS BEFORE QUOTING ANY NUMBER")
    print("  * SRP is not a delta. Read the AUTHORED (is_new) block for what Codex")
    print("    actually wrote; the ALL block includes classes it merely edited.")
    print("  * The other four ARE deltas: only what the PR changed is charged.")
    print("  * Counts are printed beside every rate. The delta principles have")
    print("    single-digit signal; do not quote a percentage on its own.")
    print("  * Validation status is recorded in JAVA_STATUS.md. Python's SRP screen")
    print("    fell from P=0.82 in-sample to P=0.33 out-of-sample -- assume nothing")
    print("    here is better until its own tuning+holdout loop has been run.")


if __name__ == "__main__":
    main()
