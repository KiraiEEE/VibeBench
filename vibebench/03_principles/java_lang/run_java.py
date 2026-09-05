"""
Step 3-JAVA - all five principles in ONE delta pass over the Codex Java corpus.

Replaces run_delta_java.py + run_srp_java.py, which were built on the void
tree-sitter layer.

WHY ONE PASS: each PR needs two fully-parsed, symbol-resolved trees. Parsing a
tree costs ~10-20 s, so running five harnesses would parse each tree five
times. One pass parses each tree once (and the java_bridge facts cache means
trees shared between PRs in the same repo are parsed once overall -- with 867
PRs over 27 repos that is a large saving).

SCOPE PER PRINCIPLE, and why they differ (the defect-#13 fix):
  SRP  every type in a file the PR touched, scored on the AFTER tree, with
       `is_new` recorded so "Codex WROTE a god class" is separable from
       "Codex edited a pre-existing one". The void run conflated them.
  OCP  per dispatch chain keyed (type, method, subject); charged only when the
       PR creates a new smell or widens an existing chain past the bar.
  LSP  per override pair; needs the whole-repo resolved hierarchy. A pair is
       charged when the PR touched EITHER side -- subclass or base (ripple-aware
       attribution; scoping on the subclass alone missed base-class edits that
       break untouched subclasses).
  ISP  per contract + implementer; same either-side rule.
  DIP  package graph over the whole repo; only packages the PR touched are
       charged, matching run_dip.py.

CALIBRATION MODE: when a principle's thresholds are None in config, its flags
are left null and the raw distribution is printed at the end, so thresholds are
set from Java's own percentiles BEFORE any labelling. That is the non-circular
method the OCP and TypeScript ports used; the void run copied Python's numbers
and put the switch bar at Java's p75.

Run:  python vibebench/03_principles/java_lang/run_java.py [--limit N] [--workers N]
      [--calibrate]   force calibration mode regardless of config
"""
from __future__ import annotations

import argparse
import random
import sys
import threading
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))            # vibebench/
sys.path.insert(0, str(_HERE.parents[1]))            # 03_principles/
sys.path.insert(0, str(_HERE.parent))                # java_lang/
sys.path.insert(0, str(_HERE.parents[1] / "dip"))

import config as C            # noqa: E402
import safeio  # noqa: E402  -- crash-safe writes + disk guard
import gitsnap                # noqa: E402
import graph as dipgraph      # noqa: E402
import detectors as D         # noqa: E402
from java_bridge import analyze_tree  # noqa: E402

CHECKPOINT_EVERY = 25
# WHAT MAY BE CACHED ACROSS RUNS (narrowed 2026-08-19 after a smoke test).
#
# `no_delta_pair` and `no_touched_files` used to be listed here as permanent.
# Neither is safe to cache, and `no_delta_pair` is actively dangerous:
# gitsnap.delta_pair reaches it after THREE git fetches (merge SHA, the PR's own
# commits, refs/pull/N/head), so any transient network failure lands on the same
# status as "this PR genuinely cannot be reconstructed". A permanent status then
# freezes that transient failure forever -- the PR is never retried.
#
# Observed directly: with extraction running concurrently at 16 workers, a
# 12-PR smoke run lost 4 PRs to `no_delta_pair` -- Copilot 3/4, OpenAI_Codex
# 0/4. Re-fetching those exact SHAs by hand afterwards succeeded immediately.
# Left as-is that is silent, agent-biased data loss frozen into the corpus,
# which is the same failure family as the no-merge-SHA drop (17x) and the
# pr_commit_details inner join.
#
# `no_touched_files` is cheap to recompute (a file read, no network) but depends
# on Step 2 having extracted the PR, so caching it can freeze a Step-2 gap too.
# Retrying both costs a few fast git calls per genuinely-dead PR per run; that
# is the right trade against freezing a wrong answer.
#
# `repo_too_large` stays permanent: it is a deterministic size check, not I/O.
PERMANENT = {"ok", "repo_too_large"}

_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def java_pkg(module: str) -> str:
    """Compilation-unit name -> its Java package (`a.b.C` -> `a.b`).

    The Java replacement for `dipgraph.top_package`, which returns the first
    dotted segment and is meaningless for reverse-DNS packages.
    """
    return module.rsplit(".", 1)[0] if "." in module else module


# ---------------------------------------------------------------- snapshots

def snapshot(ix: D.Index, touched: set[str], th: dict) -> dict:
    """Everything the delta needs from ONE tree, restricted to touched scope."""
    tp = set()
    for f in touched:
        for tid in ix.by_file.get(f, []):
            t = ix.types.get(tid)
            if t and t.get("pkg"):
                tp.add(t["pkg"])

    chains = {c["key"]: c for c in D.dispatch_chains(ix)
              if c["file_path"] in touched}
    # ATTRIBUTION IS RIPPLE-AWARE: a relation is charged to the PR when the PR
    # touched EITHER side of it. Scoping on the flagged entity alone missed the
    # reverse direction -- an agent editing a BASE class or WIDENING an
    # interface breaks subclasses and implementers in files it never opened,
    # and those are untouched by construction. Measured exposure before the fix:
    # 4% of PRs, and where it occurred the invisible pairs were ~0.5x the
    # attributed ones.
    def _file_of(tid):
        t = ix.types.get(tid)
        return t["file"] if t else None

    pairs = {p["key"]: p for p in D.override_pairs(ix)
             if p["file_path"] in touched or _file_of(p["base_id"]) in touched}
    crows, irows = D.contracts(ix, th["isp"])
    contracts = {c["key"]: c for c in crows
                 if c["file_path"] in touched
                 or any(_file_of(i) in touched for i in c.get("impl_ids", ()))}
    impls = {i["key"]: i for i in irows
             if i["file_path"] in touched or _file_of(i["iface_id"]) in touched}

    mods = D.dip_modules(ix)
    cycles = dipgraph.find_cycles(mods)
    # AFFERENT / EFFERENT COUPLING AT COMPILATION-UNIT LEVEL (V2, REDESIGN 5).
    # Ca(m) = how many other modules import m; Ce(m) = how many m imports.
    # `martin_metrics` already computes both, but at PACKAGE level, and the
    # supervisor's SRP example ("many things import a class and it keeps being
    # edited") is about a TYPE. Computed here because it is free: the import
    # graph is already built, and re-deriving it later would mean a second pass
    # over every tree at 10-20 s each.
    # DESCRIPTIVE ONLY. No flag reads these. Any metric derived from them goes
    # through the same tuning + holdout validation as every other detector
    # before it is allowed to accuse anything (config note: `mean_d` is the
    # cautionary tale, it validated at precision 0.000 in two languages).
    fan_in: dict[str, int] = {m: 0 for m in mods}
    for m, info in mods.items():
        for t in info.imports:
            if t in fan_in:
                fan_in[t] += 1
    fan_out = {m: len(info.imports) for m, info in mods.items()}
    # type id -> its module, so a per-TYPE row can carry its module's coupling
    type_module: dict[str, str] = {}
    for m in ix.modules:
        for tid in ix.by_file.get(m["file"], []):
            type_module[tid] = m["name"]
    # THE JAVA PACKAGE UNIT. `dipgraph.top_package` takes the FIRST dotted
    # segment, which is right for Python/TypeScript (`pkg.mod`) and catastrophic
    # for Java: reverse-DNS names collapse the whole corpus into `net`/`com`/
    # `org`. `tp` holds real package names, so the filter below never matched and
    # `martin[p]` was never found -- which is why DIP-Java reported new_cycle
    # False on all 867 PRs and a null mean_d on 866 of them. A module here is a
    # compilation unit (`net.openhft.chronicle.core.Maths`), so its package is
    # the name minus the type.
    touched_cycles = [c for c in cycles if any(java_pkg(m) in tp for m in c)]
    martin = dipgraph.martin_metrics(mods, package_of=java_pkg)
    ds = [martin[p]["D"] for p in tp if p in martin and martin[p].get("D") is not None]
    return {
        "chains": chains, "pairs": pairs, "contracts": contracts, "impls": impls,
        # PAIR-level novelty (dipgraph.cyclic_pairs). Comparing whole SCCs makes
        # any membership change in a large pre-existing component look brand new
        # -- including a PR that REMOVES a module from a cycle. A pair (u,v) is
        # newly cyclic iff the two became mutually reachable.
        "cyclic_pairs": dipgraph.cyclic_pairs(cycles),
        # MATCHED-SCOPE cycles, for the cross-language comparison only.
        # Python's module graph sees intra-package edges (Python requires an
        # import statement even within a package) and Java's cannot (Java
        # requires none), so the two languages' raw cycle counts are not
        # comparable. Restricting both to cross-package edges puts them on the
        # same footing. For Java this should be almost identical to the
        # unrestricted count, and that near-identity is itself the check that
        # the asymmetry has been correctly characterised.
        "cyclic_pairs_xpkg": dipgraph.cyclic_pairs(
            dipgraph.find_cycles(dipgraph.cross_package_only(mods, java_pkg))),
        "n_touched_cycles": len(touched_cycles),
        # compilation units (not packages) that the PR touched
        "touched_modules": {m["name"] for m in ix.modules if m["file"] in touched},
        "mean_d": (sum(ds) / len(ds)) if ds else None,
        "touched_pkgs": tp,
        # V2 descriptive additions (REDESIGN 5). `abstract_dep_ratio` already
        # existed in dip/graph.py and was simply never reported: of all internal
        # imports made by the touched modules, the fraction landing on a module
        # that defines at least one abstraction. That is a DIRECT statement of
        # dependency inversion and costs nothing to emit.
        "abstract_dep_ratio": dipgraph.abstract_dep_ratio(
            mods, {m["name"] for m in ix.modules if m["file"] in touched}),
        "fan_in": fan_in, "fan_out": fan_out, "type_module": type_module,
    }


def analyse_pr(pr, th: dict, calibrate: dict) -> dict:
    """-> {'pr': {...}, 'srp': [...], 'ocp': [...], 'lsp': [...], 'isp': [...], 'impl': [...]}"""
    # V2: `outcome` and `rejection_stratum` ride on EVERY row of EVERY parquet
    # (REDESIGN_V2.md 3). Carrying them here rather than joining them back in
    # the report means a downstream analysis cannot accidentally compute a
    # merged-vs-rejected contrast against the wrong gold set, and it keeps the
    # entity-level parquets (SRP types, OCP chains, LSP pairs, ISP contracts)
    # splittable by outcome without a join at all.
    base = {"pr_id": pr.pr_id, "agent": pr.agent, "full_name": pr.full_name,
            "pr_number": int(pr.pr_number),
            "outcome": getattr(pr, "outcome", "merged"),
            "rejection_stratum": getattr(pr, "rejection_stratum", "merged")}
    out = {"pr": dict(base), "srp": [], "ocp": [], "lsp": [], "isp": [], "impl": []}

    touched = set(gitsnap.touched_files(pr, C.JAVA_SOURCE_EXTENSIONS,
                                        C.JAVA_EXCLUDED_PATH_PARTS,
                                        extract_dir=C.EXTRACT_DIR_JAVA))
    if not touched:
        out["pr"]["status"] = "no_touched_files"
        return out

    d = gitsnap.ensure_repo(pr.full_name)
    # RECORD WHICH TIER RECONSTRUCTED THE PAIR. Tier 1 (merge commit + first
    # parent) is available only to merged PRs; tiers 2 and 3 take the PR's own
    # branch point. If the tier mix differed sharply by outcome, the V2
    # merged-vs-rejected contrast would be partly a comparison of how the
    # before-tree was built. The report prints tier x outcome so that is
    # measured rather than assumed.
    info: dict = {}
    with gitsnap.repo_lock(pr.full_name):
        pair = gitsnap.delta_pair(d, pr, info)
        if pair is None:
            out["pr"]["status"] = "no_delta_pair"
            return out
        out["pr"]["pair_tier"] = info.get("tier")
        # SHA PROVENANCE. Recorded so the analysed tree can be reconciled
        # against the tree Step 2 extracted from (`head_sha` in the clone
        # manifest). They can legitimately differ for a still-OPEN PR whose
        # author pushed between the two stages, and the failure would be silent:
        # `_touched.json` would list paths absent from the analysed tree, which
        # simply contribute no rows. 424 of the drawn PRs were still open, so
        # this is a real population, and it is exactly why `open` is
        # supplementary and never in the headline.
        out["pr"]["before_sha"], out["pr"]["after_sha"] = pair
        before_sha, after_sha = pair
        bt = gitsnap.src_tree(d, before_sha, C.JAVA_SOURCE_EXTENSIONS,
                              C.JAVA_EXCLUDED_PATH_PARTS, pr.full_name)
        at = gitsnap.src_tree(d, after_sha, C.JAVA_SOURCE_EXTENSIONS,
                              C.JAVA_EXCLUDED_PATH_PARTS, pr.full_name)
    if bt is None or at is None:
        out["pr"]["status"] = "archive_failed"
        return out
    if gitsnap.TOO_LARGE in bt or gitsnap.TOO_LARGE in at:
        out["pr"]["status"] = "repo_too_large"
        return out

    bf, af = analyze_tree(bt), analyze_tree(at)
    if not af.get("ok"):
        out["pr"]["status"] = "sidecar_failed"
        out["pr"]["error"] = str(af.get("error"))[:200]
        return out
    # A FAILED BEFORE-TREE IS NOT AN EMPTY BEFORE-TREE (fixed 2026-08-19).
    # This used to substitute `{"types": [], "modules": [], ...}` when the
    # sidecar failed on the parent commit. Every delta in this file is computed
    # as "present in AFTER and absent in BEFORE", so an empty BEFORE makes the
    # whole after-tree look newly authored: new_dispatch_smell (OCP), the new
    # LSP violations, new_flagged_iface (ISP) and the new DIP cyclic pairs all
    # fire at once. That inflates the "violations the PR INTRODUCED" counts --
    # the headline number of the study -- and does so silently, with no status
    # and no counter to notice it by.
    # An genuinely empty parent tree does NOT come through here: src_tree returns
    # {} and analyze_tree({}) succeeds with empty lists. Reaching this branch
    # means a real sidecar failure (timeout, non-zero exit, bad JSON), so the PR
    # is dropped with its own retryable status instead of being scored wrong.
    if not bf.get("ok"):
        out["pr"]["status"] = "before_sidecar_failed"
        out["pr"]["error"] = str(bf.get("error"))[:200]
        return out

    bix, aix = D.build_index(bf), D.build_index(af)
    b = snapshot(bix, touched, th)
    a = snapshot(aix, touched, th)

    out["pr"]["status"] = "ok"
    out["pr"]["n_touched_files"] = len(touched)
    out["pr"]["n_parse_errors"] = sum(
        1 for p, f in aix.files.items() if p in touched and not f["parseOk"])
    out["pr"]["resolved_supertype_rate"] = (
        af["resolvedSupertypes"] / max(af["resolvedSupertypes"] + af["unresolvedSupertypes"], 1))

    # ---------------- SRP (per type, after tree, touched files)
    before_ids = set(bix.types)
    for f in sorted(touched):
        for tid in aix.by_file.get(f, []):
            t = aix.types[tid]
            mv = D.srp_metrics(t)
            _mod = a["type_module"].get(tid)
            row = dict(base, type_id=tid, type_name=t["name"], file_path=f,
                       kind=t["kind"], is_nested=t["isNested"], is_abstract=t["isAbstract"],
                       is_new=tid not in before_ids,
                       # DESCRIPTIVE ONLY, never read by srp_risk. The
                       # change-magnet metric (fan-in x churn) is built from
                       # these at step 9 and validated before it ships.
                       module=_mod,
                       fan_in=a["fan_in"].get(_mod),
                       fan_out=a["fan_out"].get(_mod),
                       **mv)
            if calibrate["srp"]:
                row.update(srp_risk=None, srp_risk_strict=None, hits="", c3_suppressed=None)
            else:
                risk, strict, hits, sup = D.srp_risk(mv, th["srp"])
                row.update(srp_risk=risk, srp_risk_strict=strict,
                           hits="|".join(hits), c3_suppressed=sup)
            out["srp"].append(row)

    # ---------------- OCP
    reasons = Counter()
    for k, c in a["chains"].items():
        smell = None if calibrate["ocp"] else D.is_dispatch_smell(c, th["ocp"])
        out["ocp"].append(dict(base, type_id=c["type_id"], method=c["method"],
                               subject=c["subject"], subject_type=c["subject_type"],
                               kind=c["kind"], n_branches=c["n_branches"],
                               lineno=c["lineno"], file_path=c["file_path"],
                               type_names=c["type_names"], closed_set=c["closed_set"],
                               smell=smell))
        if calibrate["ocp"]:
            continue
        old = b["chains"].get(k)
        if smell and old is None:
            reasons["new_dispatch_smell"] += 1
        elif smell and old is not None and c["n_branches"] > old["n_branches"]:
            reasons["dispatch_widened"] += 1
    out["pr"]["n_chains_in_scope"] = len(a["chains"])
    out["pr"]["n_smells_in_scope"] = sum(1 for c in out["ocp"] if c["smell"])
    out["pr"]["ocp_risk_reasons"] = "|".join(sorted(reasons))
    out["pr"]["ocp_risk"] = None if calibrate["ocp"] else bool(reasons)

    # ---------------- LSP
    strong = getattr(C, "LSP_JAVA_STRONG_SIGNALS",
                     ["refused_bequest", "precondition_strengthened", "postcondition_weakened"])
    lr = Counter()
    for k, p in a["pairs"].items():
        viol = D.is_lsp_violation(p, strong)
        out["lsp"].append(dict(base, type_id=p["type_id"], base_id=p["base_id"],
                               method=p["method"], file_path=p["file_path"],
                               lineno=p["lineno"], signals=p["signals"], violation=viol))
        if not viol:
            continue
        old = b["pairs"].get(k)
        if old is None or not D.is_lsp_violation(old, strong):
            for s in p["signals"].split(";"):
                if s in strong:
                    lr[f"new_{s}"] += 1
    out["pr"]["n_overrides_in_scope"] = len(a["pairs"])
    out["pr"]["n_violations_in_scope"] = sum(1 for p in out["lsp"] if p["violation"])
    out["pr"]["lsp_risk_reasons"] = "|".join(sorted(lr))
    out["pr"]["lsp_risk"] = bool(lr)

    # ---------------- ISP
    ir = Counter()
    for k, c in a["contracts"].items():
        out["isp"].append(dict(base, iface_id=c["iface_id"], file_path=c["file_path"],
                               lineno=c["lineno"], kind=c["kind"], n_members=c["n_members"],
                               n_implementers=c["n_implementers"],
                               flagged=None if calibrate["isp"] else c["flagged"]))
        if calibrate["isp"]:
            continue
        old = b["contracts"].get(k)
        if c["flagged"] and old is None:
            ir["new_flagged_iface"] += 1
        elif c["flagged"] and old is not None and c["n_members"] > old["n_members"]:
            ir["widened_fat_iface"] += 1
    for k, i in a["impls"].items():
        out["impl"].append(dict(base, iface_id=i["iface_id"], type_id=i["type_id"],
                                file_path=i["file_path"], iface_width=i["iface_width"],
                                n_implemented=i["n_implemented"], n_stubs=i["n_stubs"],
                                coverage=i["coverage"], stub_ratio=i["stub_ratio"],
                                forced_stub=None if calibrate["isp"] else i["forced_stub"]))
        if calibrate["isp"]:
            continue
        old = b["impls"].get(k)
        if i["forced_stub"] and (old is None or not old["forced_stub"]):
            ir["new_forced_stub"] += 1
    out["pr"]["n_contracts_in_scope"] = len(a["contracts"])
    out["pr"]["isp_risk_reasons"] = "|".join(sorted(ir))
    out["pr"]["isp_risk"] = None if calibrate["isp"] else bool(ir)

    # ---------------- DIP
    # A cycle is NEW only if the before graph did not contain it AT ALL;
    # it is charged to this PR only if it also involves a touched package.
    new_pairs = a["cyclic_pairs"] - b["cyclic_pairs"]
    # Filter at COMPILATION-UNIT level, matching run_dip.py. Filtering by package
    # is far too coarse for the common single-package Android/Java layout: one
    # touched file then admits every new pair in the whole package. Measured on
    # Harmonic-HN#220 that was 44 pairs where only ONE involved a touched file.
    tm_ = a["touched_modules"]
    new_cycles = {q for q in new_pairs if q[0] in tm_ or q[1] in tm_}
    dh = []
    if new_cycles:
        dh.append("new_cycle")
    dd = None
    if a["mean_d"] is not None and b["mean_d"] is not None:
        dd = a["mean_d"] - b["mean_d"]
        if dd >= th["dip"]["delta_mean_d"]:
            # Recorded either way so the reason string stays diagnostic, but it
            # only reaches `dip_risk` when config says so. See
            # DIP_FLAG_INCLUDES_MEAN_D: validated at P=0.000 in both Python and
            # TypeScript, so by default it describes and does not accuse.
            dh.append("mean_d_worse")
    flag_hits = [h for h in dh
                 if h != "mean_d_worse" or C.DIP_FLAG_INCLUDES_MEAN_D]
    out["pr"].update(
        before_touched_cycles=b["n_touched_cycles"], after_touched_cycles=a["n_touched_cycles"],
        new_cycle=bool(new_cycles), before_mean_d=b["mean_d"], after_mean_d=a["mean_d"],
        delta_mean_d=dd, dip_risk_hits="|".join(dh), dip_risk=bool(flag_hits),
        n_touched_packages=len(a["touched_pkgs"]),
        # V2 descriptive (REDESIGN 5): the fraction of the touched modules'
        # internal imports that land on a module defining an abstraction. This
        # is the most direct statement of dependency inversion the graph can
        # make, it already existed in dip/graph.py, and it was never reported.
        # It does NOT enter dip_risk: a change signal needs its own validation
        # round, and `mean_d` is the standing proof of what happens without one.
        # Matched-scope new-cycle flag, DESCRIPTIVE, for the cross-language
        # comparison. Not part of dip_risk: the shipped flag stays exactly what
        # was validated, and a second definition entering the headline without
        # its own label round is how `mean_d` got in.
        new_cycle_xpkg=bool({q for q in (a["cyclic_pairs_xpkg"] - b["cyclic_pairs_xpkg"])
                             if q[0] in tm_ or q[1] in tm_}),
        before_abstract_dep_ratio=b["abstract_dep_ratio"],
        after_abstract_dep_ratio=a["abstract_dep_ratio"],
        delta_abstract_dep_ratio=(
            None if a["abstract_dep_ratio"] is None or b["abstract_dep_ratio"] is None
            else a["abstract_dep_ratio"] - b["abstract_dep_ratio"]),
    )
    return out


# ---------------------------------------------------------------- driver

PR_OUT = C.CORPUS_DIR / "java_pr_results.parquet"

# PILOT MODE (V2). `--pilot` reroutes EVERY output to a parallel `.PILOT.`
# filename so an end-to-end rehearsal on a handful of PRs cannot contaminate the
# real parquets. This matters because `run_java.py` resumes by keeping rows with
# a permanent status: a pilot writing into the real files would leave a few
# dozen PRs frozen in place, and the full run would silently skip them. That is
# the same failure family as the calibration-leftover defect this file already
# guards against.
_OUT_PATHS = {
    "pr": PR_OUT, "srp": C.SRP_RESULTS_JAVA, "ocp": C.OCP_RESULTS_JAVA,
    "lsp": C.LSP_RESULTS_JAVA, "isp": C.ISP_RESULTS_JAVA,
    "dip": C.DIP_RESULTS_JAVA,
}
OUT = dict(_OUT_PATHS)


def use_pilot_paths() -> None:
    global OUT, PR_OUT
    OUT = {k: v.with_suffix(".PILOT.parquet") for k, v in _OUT_PATHS.items()}
    PR_OUT = OUT["pr"]


def write_all(prs: list[dict], buckets: dict[str, list]) -> None:
    gitsnap.write_parquet_atomic(pd.DataFrame(prs), OUT["pr"], index=False)
    gitsnap.write_parquet_atomic(pd.DataFrame(buckets["srp"]), OUT["srp"], index=False)
    gitsnap.write_parquet_atomic(pd.DataFrame(buckets["ocp"]), OUT["ocp"], index=False)
    gitsnap.write_parquet_atomic(pd.DataFrame(buckets["lsp"]), OUT["lsp"], index=False)
    isp = pd.DataFrame(buckets["isp"]).assign(row_type="interface")
    imp = pd.DataFrame(buckets["impl"]).assign(row_type="implementer")
    gitsnap.write_parquet_atomic(pd.concat([isp, imp], ignore_index=True), OUT["isp"], index=False)
    gitsnap.write_parquet_atomic(pd.DataFrame(prs), OUT["dip"], index=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=getattr(C, "ANALYZE_WORKERS", 6))
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--fresh", action="store_true", help="ignore existing results")
    ap.add_argument("--pilot", action="store_true",
                    help="end-to-end rehearsal: write to *.PILOT.parquet so the "
                         "real results are untouched. Combine with --cells.")
    ap.add_argument("--cells", type=int, default=0,
                    help="pilot: take N PRs from EVERY (agent x outcome) cell, so "
                         "the rehearsal exercises every cell rather than whichever "
                         "one happens to sort first")
    a = ap.parse_args()
    if a.pilot:
        use_pilot_paths()
        log("PILOT MODE: writing to *.PILOT.parquet -- real results untouched")

    th = {"srp": C.SRP_JAVA_THRESHOLDS, **C.JAVA_DELTA_THRESHOLDS}
    calibrate = {k: (a.calibrate or th.get(k) is None) for k in ("srp", "ocp", "isp")}
    # During calibration the rows must still be EMITTED so their distribution can
    # be measured; only the flags are suppressed. These placeholders are used for
    # scoping only and never reach a published number.
    CAL_DEFAULTS = {
        "srp": {"class_loc": 10 ** 9, "class_wmc": 10 ** 9, "class_methods": 10 ** 9,
                "func_ccn": 10 ** 9, "func_params": 10 ** 9, "class_lcom": 2.0,
                "lcom_min_methods": 4, "c3_suppress": 2.0, "min_hits": 99, "strict_hits": 99},
        "ocp": {"type_branches": 10 ** 9, "value_branches": 10 ** 9},
        "isp": {"fat_members": 10 ** 9, "min_stubs": 10 ** 9, "stub_ratio": 2.0},
    }
    for k in ("srp", "ocp", "isp"):
        if th.get(k) is None:
            th[k] = CAL_DEFAULTS[k]
    if any(calibrate.values()):
        log(f"CALIBRATION MODE for: {[k for k, v in calibrate.items() if v]} "
            f"-- flags left null, distributions printed at the end")

    log("free disk: %.1f GB" % safeio.check_disk())
    # Locks left by a previous run killed hard (reboot / power cut) would make
    # every git call in those repos fail instantly. Swept before any worker
    # starts -- see gitsnap.sweep_stale_locks.
    swept = gitsnap.sweep_stale_locks()
    if swept:
        log("swept %d stale git lock(s) from a previous hard stop" % swept)
    gold = pd.read_parquet(C.GOLD_SET_JAVA)
    done: set[str] = set()
    prs: list[dict] = []
    buckets = {k: [] for k in ("srp", "ocp", "lsp", "isp", "impl")}
    if PR_OUT.exists() and not a.fresh:
        old = pd.read_parquet(PR_OUT)
        keep = old[old.status.isin(PERMANENT)]
        done = set(keep.pr_id)
        prs = keep.to_dict("records")
        for k, p in (("srp", OUT["srp"]), ("ocp", OUT["ocp"]),
                     ("lsp", OUT["lsp"])):
            if p.exists():
                dfp = pd.read_parquet(p)
                buckets[k] = dfp[dfp.pr_id.isin(done)].to_dict("records")
        if OUT["isp"].exists():
            dfp = pd.read_parquet(OUT["isp"])
            dfp = dfp[dfp.pr_id.isin(done)]
            buckets["isp"] = dfp[dfp.row_type == "interface"].drop(columns=["row_type"]).to_dict("records")
            buckets["impl"] = dfp[dfp.row_type == "implementer"].drop(columns=["row_type"]).to_dict("records")
        # DO NOT RESUME CALIBRATION ROWS INTO A FLAGGED RUN (added 2026-08-19).
        #
        # Calibration mode emits every row with its flag left null so the
        # distribution can be measured. Those rows carry status "ok", which is
        # permanent, so a later run WITH thresholds resumed them untouched and
        # only analysed whatever was left. Demonstrated on a 12-PR smoke run:
        # the SRP parquet came out with 112 null-flag rows and 6 flagged ones in
        # the same file, and nothing anywhere said so -- the report and the label
        # sets would both have been built on a corpus that was 95% unflagged.
        #
        # CLAUDE.md tells the operator to delete the parquets after a threshold
        # change, which is correct and also the kind of instruction that gets
        # missed once. Detect it instead: evict any cached PR whose rows are null
        # for a principle this run is actually flagging, so it is recomputed.
        flag_cols = {"srp": ("srp", "srp_risk"), "ocp": ("ocp", "smell"),
                     "isp": ("isp", "flagged")}
        stale: set = set()
        for principle, (bucket, col) in flag_cols.items():
            if calibrate.get(principle):
                continue                      # still calibrating: nulls expected
            rows_ = buckets.get(bucket) or []
            stale |= {r["pr_id"] for r in rows_
                      if col in r and pd.isna(r[col])}
        for pr_col in ("ocp_risk", "isp_risk", "dip_risk"):
            if pr_col in keep.columns:
                principle = pr_col.split("_")[0]
                if not calibrate.get(principle, False):
                    stale |= set(keep.loc[keep[pr_col].isna(), "pr_id"])
        if stale:
            log(f"evicting {len(stale)} cached PRs whose flags are null but this "
                f"run computes them (calibration leftovers) -- they get recomputed")
            done -= stale
            prs = [r for r in prs if r["pr_id"] not in stale]
            for k in buckets:
                buckets[k] = [r for r in buckets[k] if r["pr_id"] not in stale]
        log(f"resuming: {len(done)} PRs already have a permanent status")

    todo = [r for r in gold.itertuples() if r.pr_id not in done]
    # SHUFFLE, SEEDED (2026-08-27). Not cosmetic -- it is a ~2.5x speed-up.
    #
    # All git work for one repository is serialised by `gitsnap.repo_lock`,
    # because a git directory does not tolerate concurrent commands. The gold set
    # is repo-stratified from curation, so in its natural order 75.7% of
    # CONSECUTIVE PRs share a repository, and a window of 5 (the worker count)
    # spans a mean of only 1.96 distinct repos. Every worker therefore queues on
    # the same lock: measured live, 5 configured workers ran as 1-2 active JVMs
    # at ~55% CPU. Shuffling lifts distinct-repos-per-window to 4.91.
    #
    # The seed is FIXED so the run order is reproducible; order affects only
    # scheduling, never any computed value, since each PR is analysed
    # independently and results are keyed by pr_id.
    random.Random(C.RANDOM_SEED).shuffle(todo)
    if a.cells:
        # N PRs from EVERY (agent x outcome) cell. `--limit` takes the head of a
        # frame sorted by agent, so it would rehearse one agent's merged cell and
        # nothing else -- which is precisely the coverage a rehearsal must not
        # have.
        seen: Counter = Counter()
        picked = []
        for r in todo:
            key = (r.agent, getattr(r, "outcome", "merged"))
            if seen[key] < a.cells:
                seen[key] += 1
                picked.append(r)
        todo = picked
        log("pilot cells: " + str({"%s/%s" % k: v for k, v in sorted(seen.items())}))
    if a.limit:
        todo = todo[:a.limit]
    log(f"{len(todo)} PRs to analyse, {a.workers} workers")

    t0, n = time.time(), 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(analyse_pr, pr, th, calibrate): pr for pr in todo}
        for fut in as_completed(futs):
            pr = futs[fut]
            try:
                res = fut.result()
            except Exception as e:
                res = {"pr": {"pr_id": pr.pr_id, "agent": pr.agent, "full_name": pr.full_name,
                              "pr_number": int(pr.pr_number), "status": "error",
                              "error": f"{type(e).__name__}: {e}"[:200]},
                       "srp": [], "ocp": [], "lsp": [], "isp": [], "impl": []}
                log(f"  ERROR {pr.pr_id}: {type(e).__name__}: {e}")
                traceback.print_exc(limit=2)
            prs.append(res["pr"])
            for k in buckets:
                buckets[k].extend(res[k])
            n += 1
            if n % CHECKPOINT_EVERY == 0:
                write_all(prs, buckets)
                el = time.time() - t0
                log(f"  [{n}/{len(todo)}] {res['pr']['status']:<16} "
                    f"{el/60:.1f} min elapsed, ~{el/n*(len(todo)-n)/60:.1f} min left")

    write_all(prs, buckets)
    df = pd.DataFrame(prs)
    log(f"\ndone in {(time.time()-t0)/60:.1f} min")
    log(f"status: {df.status.value_counts().to_dict()}")

    ok = df[df.status == "ok"]
    if len(ok):
        log(f"analysable PRs: {len(ok)}   mean supertype resolution: "
            f"{ok.resolved_supertype_rate.mean():.1%}")

    _distributions(buckets, calibrate)


def _distributions(buckets: dict, calibrate: dict) -> None:
    """The non-circular calibration step: print Java's OWN percentiles so
    thresholds are set from this corpus, never copied from Python.

    THE POPULATION MATTERS AS MUCH AS THE PERCENTILE, and this function used to
    get it wrong. `SRP_JAVA_THRESHOLDS` is documented in config.py as calibrated
    on "PRODUCTION, ENTITY-DEDUPLICATED types (n=976), not on all 6,453 rows",
    for two stated reasons: most Java types in this corpus are TESTS and have a
    different size distribution, and a class is repeated once per PR that touched
    it (measured duplication 1.86x, the worst of the three languages).

    But the printout was taken over the RAW row list, so an operator reading p90
    off it and pasting it into config would set a bar calibrated on a population
    the config does not describe. Test classes are smaller, so the raw p90 sits
    LOW and the threshold would have fired on far more than the ~10% a p90 anchor
    is supposed to. Both populations are printed now, with the one to use marked,
    so the discrepancy is visible instead of silent.

    The same correction applies to OCP and ISP: chains and contracts are
    deduplicated to the same keys the report and the label census use, so the
    calibration population, the reported population and the judged population are
    one population rather than three.
    """
    qs = [.5, .75, .9, .95, .99]

    def _pct(frame: pd.DataFrame, cols) -> None:
        for c in cols:
            if c in frame and frame[c].notna().any():
                log(f"    {c:<16} " + "  ".join(
                    f"p{int(q*100)}={frame[c].quantile(q):.2f}" for q in qs))

    if calibrate["srp"] and buckets["srp"]:
        d = pd.DataFrame(buckets["srp"])
        cols = ("class_loc", "wmc", "method_count", "max_method_ccn",
                "max_params", "lcom", "c3")
        d["_test"] = d["file_path"].map(lambda p: C.is_test_path(str(p), "java"))
        prod = d[~d["_test"]].copy()
        prod["_entity"] = prod["full_name"] + "::" + prod["type_id"]
        uniq = (prod.sort_values("class_loc")
                    .drop_duplicates("_entity", keep="last"))
        log(f"\nSRP percentiles -- ALL {len(d):,} ROWS "
            f"({d['_test'].mean():.1%} test code, duplicated). "
            f"DO NOT CALIBRATE ON THIS:")
        _pct(d, cols)
        log(f"\nSRP percentiles -- PRODUCTION, ENTITY-DEDUPLICATED "
            f"(n={len(uniq):,}, duplication {len(prod)/max(len(uniq),1):.2f}x). "
            f"*** SET SRP_JAVA_THRESHOLDS FROM ~p90 OF THIS ONE ***")
        _pct(uniq, cols)

    if calibrate["ocp"] and buckets["ocp"]:
        d = pd.DataFrame(buckets["ocp"])
        u = d.drop_duplicates(["full_name", "type_id", "method", "subject"])
        log(f"\nOCP branch-count percentiles BY KIND, DISTINCT chains "
            f"(n={len(u):,} of {len(d):,} rows). Type-based and tag-based bars "
            f"differ:")
        for k, g in u.groupby("kind"):
            log(f"    {k:<14} n={len(g):<6} " + "  ".join(
                f"p{int(q*100)}={g.n_branches.quantile(q):.0f}" for q in qs))
        log(f"    closed-set chains (enum subject, exhaustive by construction): "
            f"{int(u.closed_set.fillna(False).sum())} of {len(u)}")
        log("    type_branches stays 3 (Martin's own minimum for a switch, used "
            "unchanged in")
        log("    all three languages so type-based bars stay comparable); "
            "value_branches is")
        log("    the one to set from ~p90 above.")

    if calibrate["isp"] and buckets["isp"]:
        d = pd.DataFrame(buckets["isp"])
        u = d.drop_duplicates(["full_name", "iface_id"])
        log(f"\nISP contract-width percentiles, DISTINCT contracts "
            f"(n={len(u):,} of {len(d):,} rows):")
        _pct(u.rename(columns={"n_members": "n_members"}), ("n_members",))
        log("    CAVEAT for the write-up: interface WIDTH is the weaker half of "
            "ISP. The")
        log("    semantically meaningful signal is `forced_stub` (an implementer "
            "that must")
        log("    stub methods it does not need), which is scale-free and needs "
            "no threshold.")


def _self_check() -> None:
    """Offline assertions on snapshot()'s V2 additions. `--self-check`.

    Pins the EXACT semantics of the coupling columns, because they are easy to
    over-read. A Java module edge is built from (a) `import` declarations
    pointing at an internal package and (b) supertypes. Java requires no import
    for a SAME-PACKAGE reference, so an intra-package use-dependency is invisible
    to this graph while an intra-package inheritance edge is visible.

    THAT IS A REAL CROSS-LANGUAGE ASYMMETRY AND IT MUST BE STATED, NOT ASSUMED
    AWAY. Python always requires an import statement, even within a package, so
    `dip/graph.py::build_module_graph` sees intra-package edges that the Java
    extractor cannot. Consequence: Java's compilation-unit cycles are in effect
    CROSS-PACKAGE cycles, while Python's include intra-package ones, so the two
    languages' raw cycle counts are not directly comparable. For package-level
    Martin A/I/D the exclusion is correct by definition (an intra-package edge
    crosses no package boundary), and for the Acyclic Dependencies Principle the
    cross-package scope is the architecturally meaningful one. The cross-language
    DIP comparison therefore has to be made at a matched scope, which is done in
    the cross-language step rather than by pretending the raw counts line up.
    """
    import config as _C
    from java_bridge import analyze_tree as _at
    th = {"srp": _C.SRP_JAVA_THRESHOLDS, **_C.JAVA_DELTA_THRESHOLDS}

    # Cross-package: Java requires the import, so every edge is visible.
    xs = {
        "src/main/java/a/Iface.java": "package a;\npublic interface Iface { void go(); }\n",
        "src/main/java/b/Impl.java": "package b;\nimport a.Iface;\n"
                                     "public class Impl implements Iface { public void go(){} }\n",
        "src/main/java/c/C1.java": "package c;\nimport b.Impl;\n"
                                   "public class C1 { Impl i; void m(){ i.go(); } }\n",
        "src/main/java/d/C2.java": "package d;\nimport b.Impl;\nimport a.Iface;\n"
                                   "public class C2 { Impl i; Iface f; void m(){ i.go(); f.go(); } }\n",
    }
    f = _at(xs, use_cache=False)
    assert f.get("ok"), f
    s = snapshot(D.build_index(f), set(xs), th)
    assert s["fan_in"]["b.Impl"] == 2, s["fan_in"]
    assert s["fan_in"]["a.Iface"] == 2, s["fan_in"]
    assert s["fan_out"]["d.C2"] == 2, s["fan_out"]
    # 4 internal edges, 2 of them landing on the module that declares the
    # interface -> half of the touched modules' dependencies are on abstractions.
    assert abs(s["abstract_dep_ratio"] - 0.5) < 1e-9, s["abstract_dep_ratio"]
    assert s["type_module"]["b.Impl"] == "b.Impl", s["type_module"]
    print("  [ok  ] cross-package fan_in/fan_out and abstract_dep_ratio")

    # Same package: no import needed, so USE is invisible, inheritance is not.
    sp = {
        "src/main/java/a/Iface.java": "package a;\npublic interface Iface { void go(); }\n",
        "src/main/java/a/Impl.java": "package a;\n"
                                     "public class Impl implements Iface { public void go(){} }\n",
        "src/main/java/a/C1.java": "package a;\n"
                                   "public class C1 { Impl i; void m(){ i.go(); } }\n",
    }
    f2 = _at(sp, use_cache=False)
    assert f2.get("ok"), f2
    s2 = snapshot(D.build_index(f2), set(sp), th)
    assert s2["fan_in"]["a.Impl"] == 0, (
        "DOCUMENTED GAP: same-package USE dependencies are not captured")
    assert s2["fan_in"]["a.Iface"] == 1, (
        "same-package INHERITANCE must still be captured")
    print("  [ok  ] intra-package gap is exactly inheritance-only, as documented")

    # Descriptive columns must never influence a flag.
    src = Path(__file__).read_text(encoding="utf-8")
    body = src.split("def srp_risk", 1)[0]
    for col in ("fan_in", "fan_out", "abstract_dep_ratio"):
        assert ("th[\"srp\"]" not in body.split(col)[0][-200:]), col
    assert "fan_in" not in src.split("D.srp_risk(", 1)[1][:200], (
        "fan_in must not reach the SRP flag without a validation round")
    print("  [ok  ] coupling columns are descriptive only, no flag reads them")
    print("\nrun_java.py self-check ALL PASS")


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        _self_check()
    else:
        main()
