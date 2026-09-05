"""
Phase verification - run after EVERY pipeline phase, before quoting any number.

WHY THIS EXISTS. Every defect this project has found was a SILENT one: a wrong
number that looked completely normal. The no-merge-SHA drop (17x agent bias), the
`pr_commit_details` inner join (40x), the hardcoded Copilot exception, the
report with no `groupby("agent")`, the failed before-tree scored as an empty one,
the CRLF line doubling. None of them raised an error. Each was found by someone
looking, and each was found late.

So the looking is written down here instead. Each check answers one question
that has previously had a wrong answer, prints the evidence, and returns
PASS / WARN / FAIL. Nothing here computes a result; it only decides whether the
results are safe to compute.

THE STANDING RULES IT ENFORCES:
  * a loss that hits one AGENT harder is a bias, not an inconvenience
  * a loss that hits one OUTCOME harder invalidates the whole V2 contrast
  * a coverage check must distinguish MISSING from NOT-APPLICABLE, or it
    manufactures bias findings (this check itself had that bug once)
  * counts beside rates, always
  * an empty or tiny result in this dataset is far more often a join or casing
    bug than a real finding

Run:  python vibebench/07_verify/verify_phase.py --phase curate  --lang java
      python vibebench/07_verify/verify_phase.py --phase extract --lang java
      python vibebench/07_verify/verify_phase.py --phase enrich  --lang java
      python vibebench/07_verify/verify_phase.py --phase analyse --lang java
      python vibebench/07_verify/verify_phase.py --phase all     --lang java
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "03_principles"))
import gitsnap  # noqa: E402  -- the shared in-scope rule, checked below

PATHS = {
    "java": {
        "gold": C.GOLD_SET_JAVA, "manifest": C.CLONE_MANIFEST_JAVA,
        "pr": C.CORPUS_DIR / "java_pr_results.parquet",
        "enrich": C.CORPUS_DIR / "pr_outcomes_java.parquet",
        "entities": {"srp": C.SRP_RESULTS_JAVA, "ocp": C.OCP_RESULTS_JAVA,
                     "lsp": C.LSP_RESULTS_JAVA, "isp": C.ISP_RESULTS_JAVA},
        "exts": C.JAVA_SOURCE_EXTENSIONS, "excl": C.JAVA_EXCLUDED_PATH_PARTS,
    },
    "py": {
        "gold": C.GOLD_SET, "manifest": C.CLONE_MANIFEST,
        "pr": None, "enrich": C.CORPUS_DIR / "pr_outcomes_py.parquet",
        "entities": {"srp": C.SRP_RESULTS, "ocp": C.OCP_RESULTS,
                     "lsp": C.LSP_RESULTS, "isp": C.ISP_RESULTS,
                     "dip": C.DIP_RESULTS},
        "exts": C.SOURCE_EXTENSIONS, "excl": getattr(C, "PY_EXCLUDED_PATH_PARTS", ()),
    },
}

RESULTS: list[tuple[str, str, str]] = []


def record(status: str, name: str, detail: str = "") -> None:
    RESULTS.append((status, name, detail))
    mark = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]",
            "SKIP": "[skip]"}[status]
    print("%s %s%s" % (mark, name, ("  -- " + detail) if detail else ""))


def spread_check(df: pd.DataFrame, value: pd.Series, by: list[str], name: str,
                 warn_pp: float = 15.0, fail_pp: float = 30.0,
                 applicable: pd.Series | None = None) -> None:
    """Per-cell rate of a boolean, and the spread across cells.

    `applicable` masks rows where the value CANNOT exist. Omitting it is how a
    coverage check reports a false gap: an open PR has no close event, so
    pooling open cells with closed ones once produced a 59pp "bias" on a column
    that was 99.4-100% complete wherever it applied.
    """
    d, v = (df, value) if applicable is None else (df[applicable], value[applicable])
    if not len(d):
        record("SKIP", name, "no applicable rows")
        return
    t = d.assign(_v=v.astype(bool)).groupby(by)["_v"].agg(["size", "sum"])
    t["pct"] = (t["sum"] / t["size"] * 100).round(1)
    print(t.to_string())
    spread = float(t["pct"].max() - t["pct"].min())
    detail = "spread %.1f pp over %d cells, %d of %d rows applicable" % (
        spread, len(t), len(d), len(df))
    if spread >= fail_pp:
        record("FAIL", name, detail)
    elif spread >= warn_pp:
        record("WARN", name, detail)
    else:
        record("PASS", name, detail)


# ------------------------------------------------------------------ curate

def verify_curate(P: dict) -> None:
    print("\n=== CURATE ===")
    gold = pd.read_parquet(P["gold"])
    assert len(gold), "empty gold set"
    record("PASS", "gold set non-empty",
           "%d PRs, %d repos" % (len(gold), gold["full_name"].nunique()))

    if "outcome" not in gold.columns:
        record("FAIL", "outcome column present", "gold set predates V2")
        return
    dupes = int(gold["pr_id"].duplicated().sum())
    record("PASS" if dupes == 0 else "FAIL", "no duplicate pr_id",
           "%d duplicates" % dupes)

    print("\ncells (agent x outcome):")
    cells = gold.groupby(["agent", "outcome"]).size().unstack(fill_value=0)
    print(cells.to_string())
    bal = gold[gold["outcome"].isin(C.V2_OUTCOMES)]
    bc = bal.groupby(["agent", "outcome"]).size()
    if len(bc):
        ratio = bc.min() / bc.max()
        record("PASS" if ratio >= 0.85 else "WARN", "balanced-axis cells within 15%",
               "min %d, max %d (%.0f%% of max)" % (bc.min(), bc.max(), 100 * ratio))

    # Concentration: a cell that is mostly one repository cannot support a
    # pooled rate, and macro-per-repo must lead there.
    print("\nconcentration per cell (top-1 / top-3 repo share):")
    worst = 0.0
    for (a, o), g in gold.groupby(["agent", "outcome"]):
        rc = g["full_name"].value_counts()
        t1, t3 = rc.iloc[0] / len(g), rc.head(3).sum() / len(g)
        worst = max(worst, t1)
        print("  %-13s %-9s n=%-5d repos=%-4d top1=%.1f%% top3=%.1f%%"
              % (a, o, len(g), g["full_name"].nunique(), 100 * t1, 100 * t3))
    record("PASS" if worst < 0.25 else "WARN", "no cell dominated by one repo",
           "worst top-1 share %.1f%%" % (100 * worst))

    # The file-attribution gap that caused a 40x agent suppression once.
    if "files_source" in gold.columns:
        print("\nfile-list route per agent (the 25x-bias column):")
        spread_check(gold, gold["files_source"].eq("dataset"), ["agent"],
                     "file-list coverage per agent", warn_pp=101, fail_pp=101)
        print("  ^ a large spread here is EXPECTED and is not itself a defect: it")
        print("    is why the git_diff fallback exists. What must not be skewed is")
        print("    the USABLE rate after extraction, checked in the extract phase.")


# ----------------------------------------------------------------- extract

def verify_scope_rules(P: dict) -> None:
    """Checks on the IN-SCOPE rule itself, before any count computed with it.

    Added 2026-08-27 after two defects that every other check in this file
    passed straight over. Both were invisible to bias tests because they were
    applied CONSISTENTLY: a uniformly wrong filter shifts every agent, so a
    spread check sees an even distribution and reports PASS.

    These assert properties of the RULE rather than of the DATA, which is the
    only way to catch a mistake that is uniform by construction.
    """
    print("\n=== SCOPE RULES ===")
    exts = P["exts"]
    excl = P["excl"]

    # (1) OVER-MATCHING. Every one of these paths CONTAINS an excluded fragment
    # and is real hand-written source. `part in path` fails all four.
    keep = ["gradlebuild/docs/Doc", "com/riksdagen/Vote", "codegen/Generator",
            "mytarget/A", "oxygen/Mask", "prebuild/Setup", "a/generatedly/B"]
    drop = ["build/A", "a/target/A", "a/generated/A", "a/out/A",
            "vavr/src-gen/io/API", "a/X.generated"]
    ext = sorted(exts)[0]
    bad_keep = [x for x in keep if not gitsnap.is_source_file(x + ext, exts, excl)]
    bad_drop = [x for x in drop if gitsnap.is_source_file(x + ext, exts, excl)]
    record("PASS" if not bad_keep else "FAIL",
           "exclusion rule does not OVER-match real source",
           "wrongly excluded: %s" % (bad_keep or "none"))
    record("PASS" if not bad_drop else "FAIL",
           "exclusion rule still excludes generated/build output",
           "wrongly kept: %s" % (bad_drop or "none"))

    # (2) CACHE-KEY COMPLETENESS. Any argument that changes a cached function's
    # OUTPUT must change its KEY, or a rule change is served stale results and
    # the repair silently does nothing -- the code looks fixed and the data does
    # not move. This is the defect that would have made fix (1) a no-op.
    k = gitsnap._cache_key("o/r", "sha", exts, excl)
    checks = {
        "sha": gitsnap._cache_key("o/r", "sha2", exts, excl),
        "repo": gitsnap._cache_key("o/r2", "sha", exts, excl),
        "extensions": gitsnap._cache_key("o/r", "sha", {".zzz"}, excl),
        "exclusion rule": gitsnap._cache_key("o/r", "sha", exts, tuple(excl) + ("zz/",)),
    }
    collide = [n for n, v in checks.items() if v == k]
    record("PASS" if not collide else "FAIL",
           "tree-cache key covers every output-changing input",
           "these do NOT change the key: %s" % (collide or "none"))

    # (3) STALE GIT LOCKS. A lock surviving a reboot makes every later git call
    # in that repo fail instantly, and those failures are recorded as ordinary
    # fetch failures -- silent loss correlated with whichever repos were in
    # flight, and therefore with agent.
    #
    # A LOCK IS ONLY STALE IF NO GIT IS RUNNING. The first version of this check
    # counted every lock file and so fired on every run that was still going,
    # reporting live locks as damage. A safety check that cries wolf on a healthy
    # run is worse than no check, because the next real warning gets ignored.
    # Skip entirely while git is live, and age-filter otherwise.
    import subprocess as _sp
    import time as _time
    try:
        _live = _sp.run(["tasklist", "/FI", "IMAGENAME eq git.exe", "/NH"],
                        capture_output=True, text=True, timeout=20)
        git_running = "git.exe" in (_live.stdout or "")
    except Exception:                        # non-Windows or tasklist absent
        git_running = False
    locks = 0
    if C.CLONES_DIR.exists():
        seen = set()
        for pat in ("*/.git/*.lock", "*/.git/**/*.lock"):
            for lk in C.CLONES_DIR.glob(pat):
                if lk in seen:
                    continue
                seen.add(lk)
                try:                          # only locks older than 10 minutes
                    if _time.time() - lk.stat().st_mtime > 600:
                        locks += 1
                except OSError:
                    pass
    if git_running:
        record("SKIP", "no stale git locks from a hard stop",
               "a git process is live, so locks here are held, not stale")
        return
    record("PASS" if locks == 0 else "WARN", "no stale git locks from a hard stop",
           "%d lock file(s); the next run sweeps them at start-up" % locks)


def verify_extract(P: dict) -> None:
    print("\n=== EXTRACT ===")
    if not Path(P["manifest"]).exists():
        record("SKIP", "clone manifest", "not written yet")
        return
    gold = pd.read_parquet(P["gold"])
    man = pd.read_parquet(P["manifest"])
    assert len(man), "empty manifest"

    stray = set(man["pr_id"]) - set(gold["pr_id"])
    record("PASS" if not stray else "FAIL", "manifest contains no stale PRs",
           "%d PRs not in the current gold set" % len(stray))

    missing = set(gold["pr_id"]) - set(man["pr_id"])
    record("PASS" if not missing else "WARN", "every drawn PR reached the manifest",
           "%d of %d PRs have no manifest row at all" % (len(missing), len(gold)))

    print("\nextract status counts:")
    print(man["extract_status"].value_counts().to_string())

    per = (man.sort_values("extract_status").groupby("pr_id")
              .agg(agent=("agent", "first"),
                   any_ok=("extract_status", lambda s: (s == "ok").any())))
    per = per.join(gold.set_index("pr_id")[["outcome", "files_source"]])

    print("\nusable-PR rate per agent x outcome:")
    spread_check(per, per["any_ok"], ["agent", "outcome"],
                 "usable rate even across agent x outcome")

    # THE REAL BIAS TEST. dataset rows are pre-filtered to touch the language;
    # git_diff rows are not and are deliberately over-drawn, so the two routes
    # SHOULD attrit differently. Bias means one AGENT loses more WITHIN a route.
    print("\nusable-PR rate per agent, WITHIN each file-list route "
          "(the route-conditioned test):")
    for route, g in per.groupby("files_source"):
        print("  route = %s (n=%d)" % (route, len(g)))
        small = g.groupby("agent").size().min()
        if small < 20:
            print("    smallest agent cell is %d rows: too few to conclude" % small)
            record("SKIP", "route %s usable rate even across agents" % route,
                   "smallest agent cell %d" % small)
            continue
        spread_check(g, g["any_ok"], ["agent"],
                     "route %s usable rate even across agents" % route)

    print("\nstatus x outcome (a status hitting ONE outcome breaks the contrast):")
    print(pd.crosstab(man["extract_status"], man["outcome"]).to_string())

    # file_missing is usually a mid-PR rename: pr_commit_details lists the
    # filename per COMMIT, so an intermediate path can be absent at the PR head
    # while the final path extracts fine. Quantify rather than assume.
    fm = man[man["extract_status"] == "file_missing"]
    if len(fm):
        ok = man[man["extract_status"] == "ok"]
        okbase = (ok.assign(b=ok["file_path"].map(os.path.basename))
                    .groupby("pr_id")["b"].apply(set))
        hit = fm.assign(b=fm["file_path"].map(os.path.basename)).apply(
            lambda r: r["b"] in okbase.get(r["pr_id"], set()), axis=1)
        frac = float(hit.mean())
        residue = int((~hit).sum())
        record("PASS" if residue / max(len(man), 1) < 0.02 else "WARN",
               "file_missing is mid-PR renames, not data loss",
               "%.1f%% recovered under the final name; residue %d rows = %.2f%% "
               "of all file rows" % (100 * frac, residue,
                                     100 * residue / max(len(man), 1)))


# ------------------------------------------------------------------ enrich

def verify_enrich(P: dict) -> None:
    print("\n=== ENRICH ===")
    if not Path(P["enrich"]).exists():
        record("SKIP", "enrichment table", "not fetched yet")
        return
    gold = pd.read_parquet(P["gold"])
    enr = pd.read_parquet(P["enrich"])
    m = gold.merge(enr, on="pr_id", how="left", suffixes=("", "_e"))
    assert len(m) == len(gold), "enrichment join changed the row count"

    avail = m["api_available"].fillna(False)
    record("PASS" if avail.mean() > 0.95 else "WARN", "API resolved the corpus",
           "%d of %d (%.1f%%)" % (avail.sum(), len(m), 100 * avail.mean()))

    print("\nclose-actor coverage, over CLOSED PRs only:")
    is_closed = ~m["api_state"].fillna("").eq("OPEN")
    spread_check(m, m["api_closed_by"].notna(), ["agent", "outcome"],
                 "close-actor coverage even across cells", applicable=is_closed)

    if "outcome_final" in m.columns:
        bal = m[m["outcome"].isin(C.V2_OUTCOMES)]
        drift = float((bal["outcome"] != bal["outcome_final"]).mean())
        record("PASS" if drift < 0.05 else "WARN",
               "outcome drift inside the balanced cells is small",
               "%.1f%% of %d PRs changed label since the dataset cutoff"
               % (100 * drift, len(bal)))

    if "rejection_stratum_api" in m.columns:
        cr = int(m["rejection_stratum_api"].eq("changes_requested").sum())
        record("PASS" if cr >= 30 else "WARN",
               "changes_requested stratum has usable size",
               "%d PRs -- under 30 means descriptive only, no powered comparison"
               % cr)

    # The trap that would have produced a spectacular false headline.
    if "api_author" in m.columns:
        print("\nPR-author identity per agent (why self-closed is NOT abandonment):")
        for a, d in m.groupby("agent"):
            top = d["api_author"].value_counts()
            if not len(top):
                continue
            share = top.iloc[0] / len(d)
            print("  %-13s %d distinct authors, top %s = %.1f%% of its PRs"
                  % (a, d["api_author"].nunique(), top.index[0], 100 * share))
        shares = m.groupby("agent")["api_author"].apply(
            lambda s: (s.value_counts().iloc[0] / len(s)) if len(s.dropna()) else 0.0)
        record("WARN" if (shares.max() - shares.min()) > 0.5 else "PASS",
               "PR-author identity comparable across agents",
               "top-author share spread %.0f pp -- if large, any statistic "
               "derived from PR-account identity (self-closed, author-closed) "
               "is NOT comparable and must be dropped"
               % (100 * (shares.max() - shares.min())))


# ----------------------------------------------------------------- analyse

def verify_analyse(P: dict) -> None:
    print("\n=== ANALYSE ===")
    if P["pr"] is None or not Path(P["pr"]).exists():
        record("SKIP", "PR results", "harness not run yet")
        return
    gold = pd.read_parquet(P["gold"])
    prs = pd.read_parquet(P["pr"])

    stray = set(prs["pr_id"]) - set(gold["pr_id"])
    record("PASS" if not stray else "FAIL", "results contain no stale PRs",
           "%d PRs not in the current gold set -- a resumed run merged an old "
           "corpus" % len(stray))

    print("\nstatus counts:")
    print(prs["status"].value_counts().to_string())
    ok = prs[prs["status"] == "ok"].copy()
    record("PASS" if len(ok) else "FAIL", "analysable PRs exist", "%d" % len(ok))
    if not len(ok):
        return

    if "outcome" in prs.columns:
        print("\nanalysable rate per agent x outcome:")
        spread_check(prs, prs["status"].eq("ok"), ["agent", "outcome"],
                     "analysable rate even across agent x outcome")
        print("\nstatus x outcome:")
        print(pd.crosstab(prs["outcome"], prs["status"]).to_string())

    # Reconstruction tier: tier 1 needs a merge commit, so only merged PRs can
    # use it. A large split by outcome means the two sides build `before`
    # differently and part of any contrast is method, not code.
    if "pair_tier" in ok.columns and "outcome" in ok.columns:
        print("\nreconstruction tier x outcome (1=merge commit, 2=PR commits, "
              "3=refs/pull/N/head):")
        print(pd.crosstab(ok["outcome"], ok["pair_tier"]).to_string())
        t1 = ok.groupby("outcome")["pair_tier"].apply(lambda s: (s == 1).mean())
        gap = float(t1.max() - t1.min()) if len(t1) > 1 else 0.0
        record("PASS" if gap <= 0.25 else "WARN",
               "before/after built the same way across outcomes",
               "tier-1 share spread %.0f pp" % (100 * gap))

    # Symbol resolution bounds LSP and ISP recall, and it is bimodal by
    # repository kind, so an agent-correlated spread is a measurement artefact
    # that would masquerade as an agent difference.
    if "resolved_supertype_rate" in ok.columns:
        r = ok["resolved_supertype_rate"]
        print("\nsymbol resolution: median %.1f%% (p10 %.1f%%, p90 %.1f%%)"
              % (100 * r.median(), 100 * r.quantile(.10), 100 * r.quantile(.90)))
        per = ok.groupby("agent")["resolved_supertype_rate"].median()
        print("  median by agent: " +
              ", ".join("%s %.1f%%" % (a, 100 * v) for a, v in per.items()))
        gap = float(per.max() - per.min())
        record("PASS" if gap <= 0.15 else "WARN",
               "symbol resolution comparable across agents",
               "median spread %.0f pp -- above 15 pp, LSP/ISP recall is NOT "
               "comparable across agents and must be said so" % (100 * gap))

    # HEAD DRIFT. Step 2 extracted files at `head_sha`; Step 3 analysed the tree
    # at `after_sha`. For a still-OPEN PR the author can push between the two, and
    # the failure is silent: the cached touched-file list names paths that are not
    # in the analysed tree, and they simply contribute no rows. This is why `open`
    # is supplementary and never in the headline.
    if "after_sha" in ok.columns and Path(P["manifest"]).exists():
        man = pd.read_parquet(P["manifest"], columns=["pr_id", "head_sha"])
        man = man[man["head_sha"].astype(str).str.len() == 40].drop_duplicates("pr_id")
        j = ok.merge(man, on="pr_id", how="inner")
        if len(j):
            drift = j["after_sha"].astype(str) != j["head_sha"].astype(str)
            by_out = j.assign(_d=drift).groupby("outcome")["_d"].agg(["size", "sum"]) \
                if "outcome" in j.columns else None
            if by_out is not None:
                print("\nhead-SHA drift between extraction and analysis, per outcome:")
                by_out["pct"] = (by_out["sum"] / by_out["size"] * 100).round(1)
                print(by_out.to_string())
            closed = j[j["outcome"] != "open"] if "outcome" in j.columns else j
            cd = float((closed["after_sha"].astype(str)
                        != closed["head_sha"].astype(str)).mean()) if len(closed) else 0.0
            record("PASS" if cd < 0.02 else "WARN",
                   "no head drift on CLOSED PRs (merged/rejected)",
                   "%.1f%% of %d differ -- a closed PR's head must not move"
                   % (100 * cd, len(closed)))

    # Calibration leftovers: rows emitted with a null flag by a calibration run,
    # resumed into a flagged run, would silently make the corpus mostly unflagged.
    for name, path in P["entities"].items():
        if not Path(path).exists():
            continue
        d = pd.read_parquet(path)
        col = {"srp": "srp_risk", "ocp": "smell", "lsp": "violation",
               "isp": "flagged", "dip": "dip_risk"}.get(name)
        if col not in d.columns:
            continue
        nulls = int(d[col].isna().sum())
        if nulls == len(d):
            record("SKIP", "%s flags computed" % name, "calibration mode")
        else:
            record("PASS" if nulls == 0 else "FAIL",
                   "%s has no calibration-leftover rows" % name,
                   "%d of %d rows have a null flag" % (nulls, len(d)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="all",
                    choices=["scope", "curate", "extract", "enrich",
                             "analyse", "all"])
    ap.add_argument("--lang", default="java", choices=sorted(PATHS))
    a = ap.parse_args()
    P = PATHS[a.lang]
    phases = (["scope", "curate", "extract", "enrich", "analyse"]
              if a.phase == "all" else [a.phase])
    # `scope` runs alongside extract and analyse too: both consume the in-scope
    # rule, and a wrong rule is uniform, so no downstream count reveals it.
    if a.phase in ("extract", "analyse"):
        phases = ["scope"] + phases
    fn = {"scope": verify_scope_rules, "curate": verify_curate,
          "extract": verify_extract, "enrich": verify_enrich,
          "analyse": verify_analyse}
    for ph in phases:
        try:
            fn[ph](P)
        except Exception as e:  # noqa: BLE001 -- a broken check must not hide the rest
            record("FAIL", "%s phase raised" % ph, "%s: %s" % (type(e).__name__, e))

    print("\n" + "=" * 70)
    counts = pd.Series([s for s, _, _ in RESULTS]).value_counts().to_dict()
    print("VERIFICATION SUMMARY (%s, %s): %s" % (a.lang, a.phase, counts))
    for s, n, d in RESULTS:
        if s in ("FAIL", "WARN"):
            print("  %s %s -- %s" % (s, n, d))
    print("=" * 70)
    if any(s == "FAIL" for s, _, _ in RESULTS):
        print("FAIL means a number computed now would be wrong. Fix before "
              "quoting anything.")
        sys.exit(1)
    print("No FAILs. WARNs are things to DISCLOSE, not necessarily to fix.")


if __name__ == "__main__":
    main()
