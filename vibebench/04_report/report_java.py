"""
Step 5-JAVA - all five principles for the 3-agent Java corpus.

SCOPE (rewritten 2026-08-19): OpenAI_Codex, Copilot and Claude_Code, the same
three agents as Python, so the two languages form a clean factorial. This was a
WITHIN-AGENT study of OpenAI Codex under AIDev-full, where no other agent had
usable Java; on AIDev-7.6M all three clear the bar and the report must break
every number down BY AGENT.

That rewrite was not cosmetic. Every rate in this file used to be computed over
the whole frame with no `groupby("agent")` anywhere, while the title said
"OpenAI Codex only". Run unchanged on the 3-agent corpus it would have silently
pooled three agents into one number and labelled it as one agent's -- a wrong
result that looks completely normal.

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

# PILOT MODE: `--pilot` reads the *.PILOT.parquet files that
# `run_java.py --pilot` writes, so the end-to-end rehearsal can be inspected
# with the real reporting code without either run touching the other's data.
RESULTS = {"pr": PR_RESULTS, "srp": C.SRP_RESULTS_JAVA, "ocp": C.OCP_RESULTS_JAVA,
           "lsp": C.LSP_RESULTS_JAVA, "isp": C.ISP_RESULTS_JAVA}


def use_pilot_paths() -> None:
    global RESULTS, PR_RESULTS
    RESULTS = {k: v.with_suffix(".PILOT.parquet") for k, v in RESULTS.items()}
    PR_RESULTS = RESULTS["pr"]


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


def ensure_outcome(df: pd.DataFrame, gold: pd.DataFrame) -> pd.DataFrame:
    """Guarantee `outcome` / `rejection_stratum` on a result frame.

    run_java.py writes both onto every row (REDESIGN_V2.md 3), so this is a
    fallback for parquets produced before that change. It joins rather than
    defaulting, because silently labelling every unknown row "merged" would
    manufacture the very merged-vs-rejected contrast the report exists to test.
    """
    # `created_at` is deliberately in this list. It lives only in the gold set,
    # never in the result parquets, so without joining it the PR-AGE confound
    # check could never fire at all -- it would sit in the report looking like a
    # control that had passed, when in fact it had never run. A check that is
    # silently inert is worse than an absent one.
    want = ("outcome", "rejection_stratum", "created_at")
    need = [c for c in want if c not in df.columns and c in gold.columns]
    if not need:
        return df
    keys = ["pr_id"] + need
    out = df.merge(gold[keys].drop_duplicates("pr_id"), on="pr_id", how="left")
    miss = out["outcome"].isna().sum() if "outcome" in out else 0
    if miss:
        print("  !! %d rows have no outcome after the join (PRs absent from the "
              "gold set) -- they are EXCLUDED from every outcome split" % miss)
    return out


def per_cell_rates(sub: pd.DataFrame, flag_col: str, indent: str = "    ") -> None:
    """Flag rate per (agent x outcome) cell. Macro first, counts beside rates.

    This is the V2 headline layout: the merged-vs-rejected contrast is only
    interpretable WITHIN an agent, because the agents draw different repository
    mixes and different amounts of Java.
    """
    if "outcome" not in sub.columns or not len(sub):
        return
    s = sub[sub["outcome"].notna()]
    if not len(s):
        return
    print("%s%-14s%-10s%20s%13s%8s" % (indent, "agent", "outcome", "pooled",
                                       "macro/repo", "repos"))
    for (agent, outcome), g in s.groupby(["agent", "outcome"]):
        k, n = int(g[flag_col].fillna(False).sum()), len(g)
        macro = g.groupby("full_name")[flag_col].mean().mean()
        lo, hi = wilson(k, n)
        print("%s%-14s%-10s%20s%12.1f%%%8d   [CI %.1f%%-%.1f%%]"
              % (indent, agent, outcome, "%d/%d = %.1f%%" % (k, n, 100 * k / n),
                 100 * macro, g.full_name.nunique(), 100 * lo, 100 * hi))


def size_confound(ok: pd.DataFrame, indent: str = "  ") -> None:
    """PR SIZE by outcome. Not in the V2 plan; it belongs there.

    A PR that touches more files has more chances to introduce a violation,
    purely mechanically. If rejected PRs are systematically larger (a plausible
    reason for rejection in itself) then a higher rejected violation rate is
    partly an artefact of size, not a statement about conformance. The size
    distribution is therefore printed beside every outcome contrast, and a
    per-touched-file rate is given as the size-normalised comparison.
    """
    if "outcome" not in ok.columns or "n_touched_files" not in ok.columns:
        return
    s = ok[ok["outcome"].notna()]
    if not len(s):
        return
    print("\n%sPR SIZE BY OUTCOME (the mechanical confound: bigger PR, more "
          "chances to violate):" % indent)
    t = s.groupby("outcome")["n_touched_files"].agg(
        ["size", "median", "mean", lambda x: x.quantile(.9)])
    t.columns = ["prs", "median_files", "mean_files", "p90_files"]
    print(t.round(2).to_string())
    med = t["median_files"]
    if len(med) > 1 and med.max() >= 1.5 * max(med.min(), 1e-9):
        print("%s  !! median PR size differs by >1.5x across outcomes. Any raw"
              % indent)
        print("%s     outcome contrast is partly a size contrast. Use the"
              % indent)
        print("%s     size-normalised rate below, or stratify by size." % indent)

    # PR AGE. The second mechanical confound. A PR's "before" tree is the
    # repository as it stood when that PR branched, so if rejected PRs are
    # systematically older they sit in a systematically different codebase, and
    # the head-state inventory ("violations PRESENT") is measured on a different
    # population than the merged one. The delta ("violations INTRODUCED") is
    # immune, because it compares a PR against its own base.
    if "created_at" in s.columns:
        d = pd.to_datetime(s["created_at"], errors="coerce", utc=True)
        if d.notna().any():
            ages = s.assign(_d=d).groupby("outcome")["_d"].agg(
                ["min", "median", "max"])
            print("%s  PR creation date by outcome (affects PRESENT, not "
                  "INTRODUCED):" % indent)
            print(ages.to_string())
            spanned = ages["median"].max() - ages["median"].min()
            if len(ages) > 1 and abs(spanned.days) > 60:
                print("%s  !! median PR age differs by %d days across outcomes."
                      % (indent, abs(spanned.days)))
                print("%s     Head-state inventory numbers are then measured on"
                      % indent)
                print("%s     different repository states. Quote the DELTA." % indent)


def within_repo_contrast(ok: pd.DataFrame, flag_col: str,
                         indent: str = "  ") -> None:
    """merged vs not-merged INSIDE the same repository, paired.

    The strongest available form of hypothesis 5. Comparing all merged PRs to
    all rejected PRs mixes a repository effect into the outcome effect: the two
    sets are not drawn from the same projects, and projects differ enormously in
    baseline design discipline. Restricting to repositories that contributed
    BOTH outcomes removes the repository as a variable entirely, at the cost of
    sample size, and the paired difference is what should be quoted if the two
    numbers disagree.
    """
    if "outcome" not in ok.columns or not len(ok):
        return
    s = ok[ok["outcome"].isin(["merged", "rejected"])]
    if not len(s):
        return
    counts = s.groupby(["full_name", "outcome"]).size().unstack(fill_value=0)
    if "merged" not in counts or "rejected" not in counts:
        return
    both = counts[(counts["merged"] > 0) & (counts["rejected"] > 0)].index
    p = s[s["full_name"].isin(both)]
    print("\n%sWITHIN-REPO PAIRED CONTRAST (repos contributing BOTH outcomes -- "
          "removes" % indent)
    print("%s  the repository effect, which the pooled contrast cannot):" % indent)
    if not len(both):
        print("%s  no repository contributed both outcomes: the paired contrast "
              "does not exist" % indent)
        print("%s  for this corpus, and the pooled contrast is confounded by "
              "repository." % indent)
        return
    print("%s  %d repositories, %d PRs (%d merged, %d not-merged)"
          % (indent, len(both), len(p),
             int((p["outcome"] == "merged").sum()),
             int((p["outcome"] == "rejected").sum())))
    for outcome, g in p.groupby("outcome"):
        k, n = int(g[flag_col].fillna(False).sum()), len(g)
        lo, hi = wilson(k, n)
        print("%s    %-10s %d/%d = %.1f%%  [CI %.1f%%-%.1f%%]"
              % (indent, outcome, k, n, 100 * k / n, 100 * lo, 100 * hi))
    # Paired per-repo difference: each repo contributes one number, so a repo
    # with 40 PRs cannot outvote a repo with 2.
    diffs = []
    for repo, g in p.groupby("full_name"):
        m_ = g[g["outcome"] == "merged"][flag_col].fillna(False).mean()
        r_ = g[g["outcome"] == "rejected"][flag_col].fillna(False).mean()
        diffs.append(r_ - m_)
    dser = pd.Series(diffs)
    print("%s    paired per-repo difference (not-merged minus merged): "
          "mean %+.1f pp, median %+.1f pp"
          % (indent, 100 * dser.mean(), 100 * dser.median()))
    print("%s    repos where not-merged is worse: %d, better: %d, equal: %d"
          % (indent, int((dser > 0).sum()), int((dser < 0).sum()),
             int((dser == 0).sum())))


CONFOUND_NOTE_SHOWN = [False]


def outcome_contrast(sub: pd.DataFrame, flag_col: str, indent: str = "  ") -> None:
    """merged vs not-merged, then the not-merged REJECTION STRATA separately.

    THE RULE THIS ENFORCES (REDESIGN_V2.md 2.4): never publish a bare
    merged-vs-rejected rate as if rejection meant a quality refusal. Measured on
    the eligible pool, a rejected PR is half as likely to carry ANY recorded
    review as a merged one, so most closures are superseded / stale / duplicate /
    abandoned rather than judged and refused. The strata are what carry the
    quality signal, and `changes_requested` is small enough that its count must
    always be printed beside its rate.
    """
    if "outcome" not in sub.columns or not len(sub):
        return
    s = sub[sub["outcome"].notna()]
    if not len(s):
        return
    print("\n%sOUTCOME CONTRAST (macro/repo first -- the mix-controlled number):"
          % indent)
    for outcome, g in s.groupby("outcome"):
        k, n = int(g[flag_col].fillna(False).sum()), len(g)
        macro = g.groupby("full_name")[flag_col].mean().mean()
        lo, hi = wilson(k, n)
        print("%s  %-10s macro %5.1f%%   pooled %d/%d = %.1f%%  [CI %.1f%%-%.1f%%]"
              % (indent, outcome, 100 * macro, k, n, 100 * k / n,
                 100 * lo, 100 * hi))

    nm = s[(s["outcome"] != "merged") & s["rejection_stratum"].notna()]
    if len(nm):
        print("%s  not-merged BY STRATUM (the quality signal lives here, not in "
              "the pooled rejected rate):" % indent)
        for stratum, g in nm.groupby("rejection_stratum"):
            k, n = int(g[flag_col].fillna(False).sum()), len(g)
            macro = g.groupby("full_name")[flag_col].mean().mean()
            lo, hi = wilson(k, n)
            print("%s    %-22s macro %5.1f%%   pooled %d/%d = %.1f%%  "
                  "[CI %.1f%%-%.1f%%]"
                  % (indent, stratum, 100 * macro, k, n, 100 * k / n,
                     100 * lo, 100 * hi))
    if not CONFOUND_NOTE_SHOWN[0]:
        print("%s  ^ CONFOUND, state it once and never bury it: `rejected` is not"
              % indent)
        print("%s    `refused on quality`. Only `changes_requested` carries a"
              % indent)
        print("%s    maintainer's explicit objection; `closed_unreviewed` is the"
              % indent)
        print("%s    majority and mostly means superseded/stale/abandoned."
              % indent)
        CONFOUND_NOTE_SHOWN[0] = True


def per_agent_rates(sub: pd.DataFrame, flag_col: str, indent: str = "    ") -> None:
    """Pooled + macro-per-repo flag rate for each agent, counts beside rates.

    Lead with MACRO: it is the mix-controlled number, and repo concentration
    differs sharply between agents (see the corpus block in main()). Pooled is
    printed too because the two can disagree, and when they do that disagreement
    is itself the finding -- it is what exposed OpenAI_Codex's concentration in
    the Python run.
    """
    if "agent" not in sub.columns or not len(sub):
        return
    print(f"{indent}{'agent':<14}{'pooled':>22}{'macro/repo':>13}{'repos':>8}")
    for agent, g in sub.groupby("agent"):
        k, n = int(g[flag_col].fillna(False).sum()), len(g)
        macro = g.groupby("full_name")[flag_col].mean().mean()
        lo, hi = wilson(k, n)
        print(f"{indent}{agent:<14}{f'{k}/{n} = {k/n:.1%}':>22}"
              f"{macro:>12.1%}{g.full_name.nunique():>8}"
              f"   [CI {lo:.1%}-{hi:.1%}]")


# ------------------------------------------------------------------ SRP

def srp_section(prs: pd.DataFrame, gold: pd.DataFrame) -> None:
    if not RESULTS["srp"].exists():
        print("\n[SRP] not run"); return
    d = ensure_outcome(pd.read_parquet(RESULTS["srp"]), gold)
    header("SRP -- god classes   (scope: every type in a file the PR touched)")
    d["is_test"] = d.file_path.map(is_test)
    d["entity"] = d.full_name + "::" + d.type_id

    if "srp_risk" not in d or d.srp_risk.isna().all():
        # SAME POPULATION CORRECTION AS run_java.py::_distributions. This block
        # used to print percentiles over every row, while config.py documents
        # SRP_JAVA_THRESHOLDS as calibrated on PRODUCTION, ENTITY-DEDUPLICATED
        # types. Test classes are smaller and are the majority of Java types
        # here, so the raw p90 sits LOW and a threshold anchored to it would
        # fire on far more than the ~10% a p90 anchor is meant to -- inflating
        # every SRP rate in the study, uniformly enough to look normal.
        cols = ("class_loc", "wmc", "method_count", "max_method_ccn",
                "max_params", "lcom", "c3")
        prod = d[~d.is_test]
        uniq = prod.sort_values("class_loc").drop_duplicates("entity", keep="last")
        print(f"  CALIBRATION MODE -- {len(d):,} type rows, no flags yet "
              f"({d.is_test.mean():.1%} test code).")
        print(f"  ALL ROWS -- do NOT calibrate on this:")
        for c in cols:
            if c in d:
                print(f"    {c:<16} " + "  ".join(
                    f"p{int(q*100)}={d[c].quantile(q):.2f}" for q in (.5, .75, .9, .95, .99)))
        print(f"  PRODUCTION, ENTITY-DEDUPLICATED (n={len(uniq):,}) -- "
              f"*** SET SRP_JAVA_THRESHOLDS FROM ~p90 OF THIS ***")
        for c in cols:
            if c in uniq:
                print(f"    {c:<16} " + "  ".join(
                    f"p{int(q*100)}={uniq[c].quantile(q):.2f}" for q in (.5, .75, .9, .95, .99)))
        return
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

    # DEDUPLICATION UNIT FOR THE OUTCOME SPLIT (V2). `uniq` collapses a class to
    # ONE row corpus-wide, which is right for the overall and per-agent rates.
    # It is WRONG for the outcome split: a class touched by both a merged and a
    # rejected PR would be assigned to whichever of the two happened to carry the
    # larger `class_loc`, i.e. by an irrelevant tiebreak. With 174 repositories
    # contributing both outcomes, that is not a rare case.
    #
    # The outcome question is "among classes touched by MERGED PRs, what fraction
    # is flagged", and separately for not-merged. So a class touched under both
    # outcomes legitimately contributes one observation to EACH, and the
    # deduplication key gains `outcome`.
    uniq_out = uniq
    if "outcome" in prod.columns:
        po = prod[prod["outcome"].notna()].copy()
        po["_eo"] = po["outcome"].astype(str) + "||" + po["entity"]
        uniq_out = po.sort_values("class_loc").drop_duplicates("_eo", keep="last")
        shared = len(uniq_out) - len(
            uniq_out.drop_duplicates("entity"))
        print(f"  DISTINCT per OUTCOME : {len(uniq_out):,}   "
              f"({shared:,} classes appear under more than one outcome and "
              f"contribute one observation to each)")

    for name, sub in (("ALL distinct production classes", uniq),
                      ("classes the agent AUTHORED in this corpus (is_new)", uniq[uniq.is_new]),
                      ("pre-existing classes the agent only EDITED", uniq[~uniq.is_new])):
        if not len(sub):
            continue
        k, n = int(sub.srp_risk.sum()), len(sub)
        macro = sub.groupby("full_name").srp_risk.mean().mean()
        print(f"\n  {name}")
        print(f"    ALL AGENTS pooled  : {rate(k, n)}")
        print(f"    ALL AGENTS macro/repo: {macro:.1%}")
        print(f"    strict (>=3 hits): {int(sub.srp_risk_strict.sum())}/{n} = {sub.srp_risk_strict.mean():.1%}")
        per_agent_rates(sub, "srp_risk")

    # The outcome split runs ONCE, on the outcome-aware deduplication, rather
    # than inside the authored/edited loop on the corpus-wide one.
    for name, sub in (("ALL distinct production classes", uniq_out),
                      ("AUTHORED (is_new)", uniq_out[uniq_out.is_new]),
                      ("EDITED (pre-existing)", uniq_out[~uniq_out.is_new])):
        if not len(sub) or "outcome" not in sub.columns:
            continue
        print("\n  OUTCOME SPLIT -- %s" % name)
        per_cell_rates(sub, "srp_risk")
        outcome_contrast(sub, "srp_risk", indent="    ")

    # SIZE DISTRIBUTION BY OUTCOME. The SRP rubric is percentile-calibrated on
    # the POOLED production distribution and then applied uniformly, which is
    # correct: calibrating separately per outcome would erase the very effect
    # being measured. But it makes the SRP outcome contrast partly a SIZE
    # contrast, so the size distributions have to be printed beside it. If
    # not-merged classes are simply bigger, "not-merged code violates SRP more"
    # and "not-merged code is bigger" are the same sentence, and only the second
    # is a finding about size.
    if "outcome" in uniq_out.columns and uniq_out["outcome"].nunique() > 1:
        print("\n  CLASS-SIZE DISTRIBUTION BY OUTCOME (the SRP rubric is a size "
              "rubric; a")
        print("  size difference between outcomes IS most of any SRP difference "
              "between them):")
        cols = [c for c in ("class_loc", "wmc", "method_count") if c in uniq_out]
        print(uniq_out.groupby("outcome")[cols].median().round(1).to_string())
        loc = uniq_out.groupby("outcome")["class_loc"].median()
        if len(loc) > 1 and loc.max() >= 1.3 * max(loc.min(), 1e-9):
            print("  !! median class size differs by >1.3x across outcomes. Read "
                  "the SRP")
            print("     outcome contrast as a size contrast unless a "
                  "size-matched comparison")
            print("     says otherwise.")

    print(f"\n  top-repo share (all agents): "
          f"{uniq.groupby('full_name').size().max()/len(uniq):.1%}")
    if "agent" in uniq.columns:
        print("  top-repo share by agent:")
        for agent, g in uniq.groupby("agent"):
            t = g.full_name.value_counts()
            print(f"    {agent:<14} top-1 {t.iloc[0]/len(g):>6.1%}   "
                  f"top-3 {t.head(3).sum()/len(g):>6.1%}   ({g.full_name.nunique()} repos)")
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
    ok = prs[prs.status == "ok"].copy()
    if risk_col not in ok.columns or ok[risk_col].isna().all():
        print(f"  CALIBRATION MODE -- {len(ok)} analysable PRs, no flags yet.")
        if extra:
            extra(ok)
        return
    k = int(ok[risk_col].fillna(False).sum())
    print(f"  analysable PRs : {len(ok)} of {len(prs)}")
    print(f"  FLAGGED        : {rate(k, len(ok))}")
    print("\n  by agent (counts beside rates -- delta signal is single-digit):")
    per_agent_rates(ok, risk_col, indent="    ")
    print("\n  by agent x OUTCOME (V2 -- the survivorship-bias check):")
    per_cell_rates(ok, risk_col, indent="    ")
    outcome_contrast(ok, risk_col, indent="  ")
    size_confound(ok, indent="  ")
    within_repo_contrast(ok, risk_col, indent="  ")
    if "agent" in prs.columns:
        # Analysability is itself agent-dependent: file lists arrive by two
        # routes and git_diff rows attrit harder, so an agent with fewer
        # analysable PRs is not necessarily an agent with less Java.
        an = prs.groupby("agent").status.apply(lambda s: (s == "ok").sum())
        tot = prs.groupby("agent").size()
        print("    analysable/drawn: " +
              ", ".join(f"{a} {an[a]}/{tot[a]}" for a in tot.index))
    print()
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
    if not RESULTS["ocp"].exists():
        return
    ch = pd.read_parquet(RESULTS["ocp"])
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
    if not RESULTS["lsp"].exists():
        return
    ov = pd.read_parquet(RESULTS["lsp"])
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
    if not RESULTS["isp"].exists():
        return
    d = pd.read_parquet(RESULTS["isp"])
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
    print(f"  PRs creating a NEW import cycle: {rate(nc, len(ok))}")
    print("    ^ this IS the DIP flag now (config.DIP_FLAG_INCLUDES_MEAN_D=False)")
    if "dip_risk_hits" in ok:
        md = int(ok.dip_risk_hits.fillna("").str.contains("mean_d_worse").sum())
        both = int((ok.dip_risk_hits.fillna("").str.contains("mean_d_worse")
                    & ~ok.new_cycle.fillna(False)).sum())
        print(f"  mean_d_worse fired on {md} PRs ({both} of them with NO new cycle)")
        print("    ^ DESCRIPTIVE ONLY, excluded from the flag: 0/7 true positives in")
        print("      the Python census and 0/10 in TypeScript. Reporting it inside")
        print("      the flag would add false positives to a detector that scored")
        print("      13/13 on new_cycle, i.e. it would make the best result look worse.")


# ------------------------------------------------------------------ main

def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true",
                    help="read the *.PILOT.parquet rehearsal outputs")
    if ap.parse_args().pilot:
        use_pilot_paths()
        print("\n*** PILOT REHEARSAL OUTPUT -- not the real corpus ***")
    print(f"\nVIBEBENCH -- JAVA ({', '.join(C.JAVA_ONLY_AGENTS)})\n")
    if not PR_RESULTS.exists():
        print(f"no results yet: {PR_RESULTS}")
        return
    gold = pd.read_parquet(C.GOLD_SET_JAVA)
    if "outcome" not in gold.columns:      # pre-V2 gold set
        gold["outcome"] = "merged"
        gold["rejection_stratum"] = "merged"
    prs = ensure_outcome(pd.read_parquet(PR_RESULTS), gold)
    print(f"corpus  : {len(gold)} PRs across {gold.full_name.nunique()} repositories")
    print(f"analysed: {len(prs)}   status: {prs.status.value_counts().to_dict()}")

    # V2 CORPUS DESIGN BLOCK. Cell sizes and concentration are printed together
    # because a rate is only as good as the cell it was computed in, and because
    # A BALANCED CORPUS IS NOT A REPRESENTATIVE ONE: the drawn merged:rejected
    # ratio is roughly 1:1 while the real population is 80.9% merged. Every
    # per-outcome rate below is valid as drawn; any pooled ACROSS outcomes is a
    # balanced-design figure and is labelled as one.
    print("\ncorpus by agent x outcome (cells, repos, concentration):")
    print(f"  {'agent':<14}{'outcome':<10}{'PRs':>7}{'repos':>8}{'top-1':>9}{'top-3':>9}")
    for (agent, outcome), g in gold.groupby(["agent", "outcome"]):
        t = g.full_name.value_counts()
        print(f"  {agent:<14}{outcome:<10}{len(g):>7}{g.full_name.nunique():>8}"
              f"{t.iloc[0]/len(g):>9.1%}{t.head(3).sum()/len(g):>9.1%}")
    cells = gold.groupby(["agent", "outcome"]).size()
    bal = cells[cells.index.get_level_values("outcome").isin(C.V2_OUTCOMES)]
    if len(bal) > 1:
        print("  balanced-axis cell spread: min %d, max %d (%.0f%% of max)"
              % (bal.min(), bal.max(), 100 * bal.min() / bal.max()))
    print("\nrejection strata in the drawn corpus:")
    print(pd.crosstab(gold.agent, gold.rejection_stratum).to_string())

    # WHEN each agent worked. Not a defect, a corpus property that has to be
    # disclosed: the three agents entered the dataset at different times, so a
    # cross-agent comparison is also, in part, a comparison of different
    # repository eras. Measured 2026-08-25 the median PR date is OpenAI_Codex
    # mid-2025 against Claude_Code early 2026. The DELTA principles are immune
    # (each PR is compared against its own base) but the head-state inventory
    # is not.
    if "created_at" in gold.columns:
        d = pd.to_datetime(gold["created_at"], errors="coerce", utc=True)
        if d.notna().any():
            print("\nmedian PR date per agent x outcome (agents worked in "
                  "different eras -- disclose it):")
            print(gold.assign(_d=d).groupby(["agent", "outcome"])["_d"]
                      .median().to_string())
    # A status that is agent-skewed is a data-loss bias, not a neutral filter.
    # Two such biases were found and fixed in curation on 2026-08-19; print the
    # cross-tab so a third one cannot hide.
    if "agent" in prs.columns:
        print("\nstatus x agent (watch for a status that hits ONE agent):")
        print(pd.crosstab(prs.agent, prs.status).to_string())
    if "outcome" in prs.columns:
        # A status that hits ONE OUTCOME harder is the V2 version of the same
        # bias: rejected PRs resolve through gitsnap tier 3 (refs/pull/N/head +
        # merge-base) rather than a merge commit, so a systematically higher
        # failure rate there would make the merged-vs-rejected contrast a
        # comparison of reconstruction success, not of code.
        print("\nstatus x outcome (a status that hits ONE outcome breaks the "
              "whole V2 contrast):")
        ct = pd.crosstab(prs.outcome, prs.status)
        print(ct.to_string())
        if "ok" in ct.columns:
            okr = ct["ok"] / ct.sum(axis=1)
            print("  analysable rate by outcome: " +
                  ", ".join("%s %.1f%%" % (o, 100 * v) for o, v in okr.items()))
            if len(okr) > 1 and (okr.max() - okr.min()) > 0.10:
                print("  !! >10pp gap in analysable rate between outcomes. The")
                print("     merged-vs-rejected comparison is then partly a")
                print("     comparison of RECONSTRUCTABILITY. Say so, or drop it.")
    ok = prs[prs.status == "ok"]
    if len(ok) and "pair_tier" in ok.columns and "outcome" in ok.columns:
        # WHICH TIER BUILT THE BEFORE-TREE, PER OUTCOME. Tier 1 = merge commit
        # and its first parent (merged PRs only). Tiers 2/3 = the PR's own
        # branch point, which is what a not-merged PR must use. A large split
        # here would mean the merged and rejected sides were reconstructed
        # differently, so part of any contrast between them would be method, not
        # code. Most merged PRs on AIDev-7.6M also use tier 3, because
        # pr_timeline covers 2.6% of the dataset.
        print("\nbefore/after reconstruction tier x outcome "
              "(1 = merge commit, 2 = PR commit set, 3 = refs/pull/N/head):")
        print(pd.crosstab(ok.outcome, ok.pair_tier).to_string())
        share3 = ok.groupby("outcome").pair_tier.apply(lambda s: (s == 1).mean())
        if len(share3) > 1 and (share3.max() - share3.min()) > 0.25:
            print("  !! tier-1 share differs by >25pp across outcomes. The two")
            print("     sides do NOT build `before` the same way; disclose it.")
    if len(ok) and "resolved_supertype_rate" in ok:
        # SYMBOL RESOLUTION IS A RECALL CEILING FOR LSP AND ISP, AND IT IS NOT
        # UNIFORM. Resolution is source-only (no classpath), so a supertype that
        # lives in an external jar/aar can never resolve: a Capacitor Android
        # plugin whose classes extend framework types resolves ~25%, while a
        # self-contained server codebase resolves ~95%. Unresolved supertypes
        # mean override pairs and implementer links are never seen, so those two
        # principles under-count exactly where resolution is low.
        # A MEAN HIDES THIS -- the distribution is bimodal by repository. Print
        # the spread and the per-agent breakdown, because if resolution
        # correlates with agent then so does LSP/ISP recall, and that would be a
        # measurement artefact masquerading as an agent difference.
        r = ok.resolved_supertype_rate
        print(f"symbol resolution: median {r.median():.1%}  "
              f"(p10 {r.quantile(.10):.1%}  p90 {r.quantile(.90):.1%})  "
              f"-- source-only, external supertypes cannot resolve")
        if "agent" in ok.columns:
            per = ok.groupby("agent").resolved_supertype_rate.median()
            print("  median by agent: " +
                  ", ".join(f"{a} {v:.1%}" for a, v in per.items()))
            if per.max() - per.min() > 0.15:
                print("  !! resolution differs by >15pp across agents -- LSP/ISP")
                print("     recall is NOT comparable across agents; say so.")
        low = (r < 0.5).sum()
        if low:
            print(f"  {low} of {len(ok)} PRs resolve <50% "
                  f"(framework-heavy repos; LSP/ISP under-count there)")
    if len(ok) and "n_parse_errors" in ok:
        print(f"parse errors in touched files: {int(ok.n_parse_errors.sum())}")

    srp_section(prs, gold)
    delta_section("OCP -- open/closed   (scope: PR delta on dispatch chains)",
                  prs, "ocp_risk", "ocp_risk_reasons", ocp_extra)
    delta_section("LSP -- substitutability   (scope: PR delta on override pairs)",
                  prs, "lsp_risk", "lsp_risk_reasons", lsp_extra)
    delta_section("ISP -- interface segregation   (scope: PR delta on contracts)",
                  prs, "isp_risk", "isp_risk_reasons", isp_extra)
    delta_section("DIP -- dependency inversion   (scope: PR delta on the package graph)",
                  prs, "dip_risk", "dip_risk_hits", dip_extra)

    header("V2 RULES -- READ BEFORE QUOTING ANY OUTCOME NUMBER")
    print("  * A BALANCED CORPUS IS NOT A REPRESENTATIVE ONE. The drawn corpus is")
    print("    roughly 1:1 merged to not-merged; the real population is %.1f%% merged."
          % (100 * C.V2_NATURAL_MIX["merged"]))
    print("    Every PER-OUTCOME rate above is valid exactly as printed. Any figure")
    print("    POOLED ACROSS outcomes is a balanced-design figure and is wrong for")
    print("    the world unless it is reweighted to %s."
          % ({k: round(v, 3) for k, v in C.V2_NATURAL_MIX.items()}))
    print("    Say which one every headline number is. That is one sentence in the")
    print("    methods section and it removes an easy attack.")
    print("  * `REJECTED` IS NOT `REFUSED ON QUALITY`. Measured on the eligible pool,")
    print("    a rejected PR is HALF as likely to carry any recorded review as a")
    print("    merged one. Only the `changes_requested` stratum carries an explicit")
    print("    maintainer objection, and it is small -- print its COUNT beside every")
    print("    rate computed on it. Never publish a bare merged-vs-rejected rate as")
    print("    if rejection meant refusal.")
    print("  * PREFER THE WITHIN-REPO PAIRED CONTRAST when it disagrees with the")
    print("    pooled one. The pooled contrast mixes a repository effect into the")
    print("    outcome effect because the two sides are not drawn from the same")
    print("    projects; the paired one removes it.")
    print("  * CHECK THE SIZE AND AGE BLOCKS before reading an outcome difference as")
    print("    a conformance difference. A bigger PR has more chances to violate,")
    print("    mechanically, and an older PR sits in a different codebase.")
    print("  * PRECISION MUST BE EVEN ACROSS OUTCOMES. score_labels.py prints it per")
    print("    outcome; a spread above 15 points means the contrast is partly a")
    print("    detector artefact and is not a statement about code.")
    print("  * THE JUDGE NEVER SEES THE OUTCOME. make_java_census.py withholds it")
    print("    into a sidecar and check_label_file.py FAILS if it leaks. A judge who")
    print("    can see `rejected` can produce this result out of nothing.")

    header("READ THIS BEFORE QUOTING ANY NUMBER")
    print("  * SRP is not a delta. Read the AUTHORED (is_new) block for what the")
    print("    agent actually wrote; ALL includes classes it merely edited.")
    print("  * The other four ARE deltas: only what the PR changed is charged.")
    print("  * Counts are printed beside every rate. The delta principles have")
    print("    single-digit signal; do not quote a percentage on its own.")
    print("  * LEAD WITH MACRO/REPO for any agent comparison. Pooled and macro")
    print("    disagreed for OpenAI_Codex in Python (DIP pooled 1.4% vs macro")
    print("    3.5%) purely because of repo concentration, and the pooled number")
    print("    reversed the ranking.")
    print("  * The Java corpus is REPO-STRATIFIED (config.JAVA_REPO_STRATIFIED_DRAW)")
    print("    and the Python corpus is not. State this when comparing languages.")
    print("  * Precision here is NOT comparable to a sampled estimate. Python's")
    print("    SRP screen read P=0.33 on a stratified sample and P=0.064 on the")
    print("    full census; the census number is the honest one. Match the")
    print("    labelling method before comparing precision across languages.")


if __name__ == "__main__":
    main()
