"""
Step 3-PYTHON (V2) - all five principles in ONE delta pass over the Python corpus.

Replaces the four separate V1 delta passes (ocp/run_ocp.py, lsp/run_lsp.py,
isp/run_isp.py, dip/run_dip.py) and the file-driven srp/run_srp.py.

WHY ONE PASS, all four reasons measured rather than assumed:
  * The four V1 passes share gitsnap's per-(repo, sha) tree cache, but
    MAX_CACHE_BYTES is 25 MB, so every tree above that is refetched by each
    pass. Four passes, four `git archive` calls on exactly the trees that cost
    the most.
  * Four passes means FOUR resume states, so four chances at a partial result
    that looks complete. That is this project's recurring bug and it has cost
    real data three times.
  * Calibration existed only in run_ocp.py and run_lsp.py. SRP, ISP and DIP had
    none, and phase 4 cannot happen without it. Here all three threshold-bearing
    principles calibrate from one pass.
  * run_java.py already proves the shape works end to end.

WHAT IS AND IS NOT SHARED WITH JAVA. The framework is language-parameterised:
only fact extraction is language-specific. Java needs a JVM sidecar because
JavaParser+SymbolSolver is the only way to resolve supertypes. Python does not:
the five V1 detector modules already take {path: source} and parse with the
stdlib `ast`, so they are reused HERE UNCHANGED and this file contains no
detector logic of its own. Every rubric decision stays in the module that was
label-validated.

SCOPE PER PRINCIPLE, and why they differ:
  SRP  every class in a file the PR touched, scored on the AFTER tree, with
       `is_new` recorded so "the agent WROTE a god class" stays separable from
       "the agent edited a pre-existing one".
  OCP  per dispatch chain keyed (module, qualname, subject); charged only when
       the PR creates a new smell or widens an existing chain past the bar.
  LSP  per override pair, charged when the PR touched EITHER side (ripple-aware:
       editing a BASE class breaks subclasses in files the agent never opened).
  ISP  per contract and per (implementer, contract); same either-side rule.
  DIP  module graph over the whole repo; a pair is newly cyclic only if the two
       modules were not mutually reachable before, and it is charged only when a
       touched module is in it.

ATTRIBUTION IS TOUCHED, NOT AUTHORED, for the four relation-level principles.
SRP is the only one that can separate the two, and it does.

CALIBRATION MODE: when a principle's thresholds are None in config, its flags
are left null and the raw distribution is printed at the end, so thresholds are
set from THIS corpus's own percentiles BEFORE any labelling. Java's numbers
(OCP 3/8 branches, ISP >= 13 members, SRP loc 362 / wmc 45 / methods 20) are
Java percentiles and mean nothing here; V1's are percentiles of a 546-PR corpus
and mean little in a 7,050-PR one. After setting a threshold, DELETE THE
PARQUETS AND RE-RUN so that no flag predates the threshold that produced it.
A run that forgets is caught anyway: see the calibration-leftover eviction in
main().

Run:  python vibebench/03_principles/py_lang/run_python.py --self-check
      python vibebench/03_principles/py_lang/run_python.py --calibrate --workers 6
      python vibebench/03_principles/py_lang/run_python.py --workers 6
      python vibebench/03_principles/py_lang/run_python.py --pilot --cells 2
"""
from __future__ import annotations

import argparse
import random
import sys
import threading
import time
import traceback
from collections import Counter
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))                  # vibebench/
sys.path.insert(0, str(_HERE.parents[1]))                  # 03_principles/
for _d in ("srp", "ocp", "lsp", "isp", "dip"):
    sys.path.insert(0, str(_HERE.parents[1] / _d))

import config as C            # noqa: E402
import safeio                 # noqa: E402  -- crash-safe writes + disk guard
import gitsnap                # noqa: E402

# The five label-validated detector modules, imported under their own names so
# the two `build_index`-shaped APIs (lsp.overrides, isp.interfaces) cannot
# shadow each other.
import metrics as SRP         # noqa: E402  srp/metrics.py
import dispatch as OCP        # noqa: E402  ocp/dispatch.py
import overrides as LSP       # noqa: E402  lsp/overrides.py
import interfaces as ISP      # noqa: E402  isp/interfaces.py
import graph as DIP           # noqa: E402  dip/graph.py

CHECKPOINT_EVERY = 25

# WHAT MAY BE CACHED ACROSS RUNS. Deliberately NARROWER than the V1 Python
# passes, which kept {"ok", "no_touched_modules", "repo_too_large"}.
#
# `no_touched_modules` / `no_touched_files` depends on Step 2 having extracted
# the PR (the touched-file list for a git_diff-sourced PR comes from Step 2's
# `_touched.json`), so caching it freezes a Step-2 GAP as if it were a property
# of the pull request. That matters acutely right now: Step 2 and this pass can
# legitimately overlap, and every PR Step 2 has not yet reached would otherwise
# be frozen as unanalysable forever.
#
# `no_delta_pair` is worse. gitsnap.delta_pair reaches it only after THREE git
# fetches, so any transient network failure lands on the same status as "this PR
# genuinely cannot be reconstructed", and a permanent status then freezes that
# transient failure. Measured in Java: a 12-PR smoke run with extraction
# competing for the network lost 4 PRs this way, agent-skewed 3/4 to one agent.
#
# `repo_too_large` stays permanent: it is a deterministic size check, no I/O.
PERMANENT = {"ok", "repo_too_large"}

_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


# ---------------------------------------------------------------- snapshots

def snapshot(files: dict[str, str], touched_paths: set[str],
             touched_mods: set[str], th: dict, calibrate: dict) -> dict:
    """Everything the delta needs from ONE tree.

    Entity tables are built over the WHOLE tree and marked `in_scope`, never
    filtered to the touched set. The distinction is load-bearing: novelty is
    decided against the full BEFORE table (so a pre-existing violation is never
    re-debited to this PR) while attribution is decided by `in_scope`.
    """
    out: dict = {}

    # ---- OCP: dispatch chains, keyed by (module, qualname, subject)
    suppress_closed = getattr(C, "OCP_CLOSED_SET_SUPPRESS", False)
    chains: dict = {}
    for ch in OCP.find_dispatch_chains(files):
        smell = (None if calibrate["ocp"]
                 else OCP.is_dispatch_smell(ch, th["ocp"],
                                            suppress_closed=suppress_closed))
        prev = chains.get(ch.key)
        if prev is not None and prev["n_branches"] >= ch.n_branches:
            continue          # keep the widest chain per location key
        chains[ch.key] = {
            "module": ch.module, "qualname": ch.qualname, "subject": ch.subject,
            "kind": ch.kind, "n_branches": ch.n_branches, "lineno": ch.lineno,
            "smell": smell, "in_scope": ch.module in touched_mods,
        }
    out["chains"] = chains

    # ---- LSP: override pairs. FIVE signals here, not Java's three: `javac`
    # already rejects a changed parameter list, a narrowed parameter type and
    # reduced visibility, so those three are dead weight in Java. Python has no
    # compiler, so they are live and the detector legitimately keeps them.
    lidx = LSP.build_index(files)
    pairs: dict = {}
    for ov in LSP.find_overrides(lidx,
                                 suppress_fps=getattr(C, "LSP_SUPPRESS_FPS", False)):
        pairs[ov.key] = {
            "module": ov.module, "subclass": ov.subclass, "base_id": ov.base_id,
            "method": ov.method, "lineno": ov.lineno, "signals": list(ov.signals),
            "violation": LSP.is_lsp_violation(ov, th["lsp"]),
            # RIPPLE-AWARE: charged when the PR touched either side.
            "in_scope": (ov.module in touched_mods
                         or ov.base_id.split(":")[0] in touched_mods),
        }
    out["pairs"] = pairs

    # ---- ISP: contracts and (implementer, contract) relationships
    iidx = ISP.build_class_index(files)
    ifaces = ISP.find_interfaces(iidx, C.ISP_IMPLICIT_MIN_STUBS)
    stats = ISP.implementer_stats(iidx, ifaces)
    by_iface: dict[str, list] = {}
    for s in stats:
        by_iface.setdefault(s.iface_id, []).append(s)

    contracts: dict = {}
    for iface_id, iface in ifaces.items():
        istats = by_iface.get(iface_id, [])
        hits = [] if calibrate["isp"] else ISP.interface_hits(iface, istats, th["isp"])
        usage = ISP.client_usage(iidx, iface_id, iface.abstract)
        covs = [s.coverage for s in istats]
        contracts[iface_id] = {
            "module": iface.module, "class_name": iface.name, "kind": iface.kind,
            "lineno": iface.lineno,
            "n_abstract_methods": len(iface.abstract),
            "n_implementers": len(istats),
            "mean_coverage": (sum(covs) / len(covs)) if covs else None,
            "n_forced_stub_impls": (
                None if calibrate["isp"]
                else sum(1 for s in istats if ISP.forced_stub(s, th["isp"]))),
            "n_clients": len(usage),
            "mean_client_usage": (sum(usage) / len(usage)) if usage else None,
            "hits": hits,
            # v1 policy, unchanged: implicit interfaces are inventory, never
            # flagged. A template-method hook that intentionally raises is a
            # known false-positive source.
            "flagged": (None if calibrate["isp"]
                        else bool(len(hits) >= C.ISP_RISK_MIN_HITS
                                  and iface.kind != "implicit")),
            "in_scope": (iface.module in touched_mods
                         or any(s.impl_id.split(":")[0] in touched_mods
                                for s in istats)),
        }
    out["contracts"] = contracts

    impls: dict = {}
    for s in stats:
        impls[(s.impl_id, s.iface_id)] = {
            "module": s.impl_id.split(":")[0],
            "class_name": s.impl_id.split(":")[1],
            "iface_id": s.iface_id, "iface_kind": ifaces[s.iface_id].kind,
            "n_iface_methods": s.n_iface_methods,
            "n_implemented": s.n_implemented, "n_stubbed": s.n_stubbed,
            "coverage": s.coverage, "stub_ratio": s.stub_ratio,
            "forced_stub": (None if calibrate["isp"]
                            else ISP.forced_stub(s, th["isp"])),
            # RIPPLE-AWARE, matching the contract row: widening a touched
            # interface forces stubs into implementers never opened.
            "in_scope": (s.impl_id.split(":")[0] in touched_mods
                         or ifaces[s.iface_id].module in touched_mods),
        }
    out["impls"] = impls
    out["n_classes"] = len(iidx.classes)
    out["n_parse_errors"] = iidx.n_parse_errors

    # ---- DIP: module graph
    g = DIP.build_module_graph(files)
    scope = {m for m in touched_mods if m in g}
    cycles = DIP.find_cycles(g)
    mm = DIP.martin_metrics(g)
    pkgs = {DIP.top_package(m) for m in scope}
    ds = [mm[p]["D"] for p in pkgs if p in mm and mm[p].get("D") is not None]
    out.update(
        n_modules=len(g),
        touched_in_graph=len(scope),
        touched_fan_out=sum(len(g[m].imports) for m in scope),
        touched_cycles=sum(1 for c in cycles if c & scope),
        repo_cycles=len(cycles),
        abstract_dep_ratio=DIP.abstract_dep_ratio(g, scope),
        mean_d=(sum(ds) / len(ds)) if ds else None,
        # PAIR-level novelty. Comparing whole SCCs makes any membership change
        # in a large pre-existing component look brand new, including a PR that
        # REMOVES a module from a cycle. A pair (u, v) is newly cyclic iff the
        # two became mutually reachable.
        cyclic_pairs=DIP.cyclic_pairs(cycles),
        # MATCHED-SCOPE cycles, for the cross-language comparison ONLY, never
        # for a flag. Python requires an import statement even within a package,
        # so its module graph sees intra-package edges that Java's cannot.
        # Restricting both to cross-package edges puts the two on one footing.
        cyclic_pairs_xpkg=DIP.cyclic_pairs(
            DIP.find_cycles(DIP.cross_package_only(g, DIP.top_package))),
        touched_pkgs=pkgs,
    )

    # ---- coupling, DESCRIPTIVE ONLY (mirrors run_java.py). Ca(m) = how many
    # other modules import m; Ce(m) = how many m imports. No flag reads these;
    # any metric derived from them goes through its own validation round first.
    # `mean_d` is the standing proof of what happens without one: it validated
    # at precision 0.000 in two languages independently.
    fan_in = {m: 0 for m in g}
    for m, info in g.items():
        for t in info.imports:
            if t in fan_in:
                fan_in[t] += 1
    out["fan_in"] = fan_in
    out["fan_out"] = {m: len(info.imports) for m, info in g.items()}

    # ---- SRP: per-class metrics over TOUCHED FILES only. Unlike the four
    # relation principles this needs no repo-wide table, because a class's size
    # and cohesion are properties of the class alone, and `analyze_file` runs
    # lizard + radon per file, which is the expensive part of this pass.
    srp: dict[tuple[str, str], dict] = {}
    srp_files: dict[str, SRP.FileMetrics] = {}
    for path in sorted(touched_paths):
        src = files.get(path)
        if src is None:
            continue
        fm = SRP.analyze_file(path, src, c3_mode=C.SRP_C3_MODE)
        srp_files[path] = fm
        for cm in fm.classes:
            srp[(path, cm.class_name)] = {"file": fm, "cls": cm}
    out["srp"], out["srp_files"] = srp, srp_files
    return out


def srp_hits(row: dict, *, func_ccn: int, func_params: int,
             is_class_row: bool, t: dict) -> list[str]:
    """Which rubric thresholds this row crosses.

    A VERBATIM port of srp/run_srp.py::srp_risk_hits, with the threshold dict
    passed in rather than read from config, so calibration mode can neutralise
    it. The rules it encodes are label-validated and are not re-derived here:
      * `func_ccn` / `func_params` are passed explicitly because the right scope
        differs by row type. For a class row they must be the max over THAT
        CLASS'S OWN methods, or one huge unrelated function elsewhere in the
        file flags every class in the file.
      * LCOM only counts at >= LCOM_MIN_METHODS methods; below that the metric
        saturates to 0/1 and is its own biggest false-positive source.
      * file_loc only counts for a file-level row: it is context, not a property
        of any individual class in the file.
    """
    hits = []
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


# ---------------------------------------------------------------- per PR

def analyse_pr(pr, th: dict, calibrate: dict) -> dict:
    """-> {'pr': {...}, 'srp': [...], 'ocp': [...], 'lsp': [...],
            'isp': [...], 'impl': [...]}"""
    # `outcome` and `rejection_stratum` ride on EVERY row of EVERY parquet, so a
    # downstream analysis cannot compute an outcome contrast against the wrong
    # gold set, and the entity-level parquets stay splittable without a join.
    # `merge_sha` rides along because four of the label-batch extractors read it
    # to check out the exact tree they show the judge. Leaving it off would not
    # fail here, it would fail six phases later at the extraction gate.
    base = {"pr_id": pr.pr_id, "agent": pr.agent, "full_name": pr.full_name,
            "pr_number": int(pr.pr_number),
            "merge_sha": getattr(pr, "merge_sha", None),
            "outcome": getattr(pr, "outcome", "merged"),
            "rejection_stratum": getattr(pr, "rejection_stratum", "merged")}
    out = {"pr": dict(base), "srp": [], "ocp": [], "lsp": [], "isp": [], "impl": []}

    touched_paths = set(gitsnap.touched_files(pr, C.SOURCE_EXTENSIONS, (),
                                              extract_dir=C.EXTRACT_DIR))
    if not touched_paths:
        out["pr"]["status"] = "no_touched_files"
        return out
    touched_mods = {m for m in (DIP.path_to_module(p) for p in touched_paths) if m}
    if not touched_mods:
        out["pr"]["status"] = "no_touched_modules"
        return out

    d = gitsnap.ensure_repo(pr.full_name)
    # RECORD WHICH TIER RECONSTRUCTED THE PAIR. Tier 1 (merge commit + first
    # parent) is available only to merged PRs; the later tiers take the PR's own
    # branch point. If the tier mix differed sharply by outcome, a
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
        # SHA PROVENANCE, so the analysed tree can be reconciled against the
        # tree Step 2 extracted from. They can legitimately differ for a still
        # OPEN pull request whose author pushed between the two stages, and the
        # failure would be silent: `_touched.json` would list paths absent from
        # the analysed tree, contributing no rows. 750 of the drawn PRs are
        # open, which is exactly why `open` is supplementary and never headline.
        out["pr"]["before_sha"], out["pr"]["after_sha"] = pair
        bt = gitsnap.src_tree(d, pair[0], C.SOURCE_EXTENSIONS, (), pr.full_name)
        at = gitsnap.src_tree(d, pair[1], C.SOURCE_EXTENSIONS, (), pr.full_name)
    if bt is None or at is None:
        out["pr"]["status"] = "archive_failed"
        return out
    if gitsnap.TOO_LARGE in bt or gitsnap.TOO_LARGE in at:
        out["pr"]["status"] = "repo_too_large"
        return out

    b = snapshot(bt, touched_paths, touched_mods, th, calibrate)
    a = snapshot(at, touched_paths, touched_mods, th, calibrate)

    out["pr"]["status"] = "ok"
    out["pr"]["n_touched_files"] = len(touched_paths)
    out["pr"]["n_touched_modules"] = len(touched_mods)
    out["pr"]["n_parse_errors"] = sum(
        1 for fm in a["srp_files"].values() if not fm.parse_ok)
    out["pr"]["n_classes"] = a["n_classes"]

    # ---------------- SRP (per class, after tree, touched files)
    before_classes = set(b["srp"])
    before_files = set(b["srp_files"])
    for (path, cname), rec in sorted(a["srp"].items()):
        fm, cm = rec["file"], rec["cls"]
        mod = DIP.path_to_module(path)
        # COLUMN NAMES ARE THE V1 ONES ON PURPOSE. `max_class_params`, not
        # `max_params`, and the file-wide `max_func_*` alongside the
        # class-scoped `max_method_ccn`: labels/make_labeling_sample.py and the
        # batch extractors read these names, and the whole point of reusing the
        # validated guard chain is that it needs no edits to run on Python V2.
        row = dict(base, file_path=path, class_name=cname, lineno=cm.lineno,
                   class_loc=cm.loc, method_count=cm.method_count, wmc=cm.wmc,
                   max_method_ccn=cm.max_method_ccn,
                   max_class_params=cm.max_params,
                   lcom=cm.lcom, c3=cm.c3,
                   file_loc=fm.file_loc,
                   max_func_ccn=fm.max_func_ccn,
                   max_func_params=fm.max_func_params,
                   maintainability_index=fm.maintainability_index,
                   parse_ok=fm.parse_ok, parse_error=fm.error,
                   # AUTHORED vs EDITED. NULL is not False: if the before state
                   # could not be resolved we do not know, and defaulting to
                   # True would inflate the "the agent authored this" count,
                   # which is the strongest fidelity claim the study makes.
                   is_new=(path, cname) not in before_classes,
                   file_is_new=path not in before_files,
                   # DESCRIPTIVE ONLY, never read by the flag.
                   module=mod,
                   fan_in=a["fan_in"].get(mod), fan_out=a["fan_out"].get(mod))
        if calibrate["srp"]:
            row.update(srp_risk_hits="", srp_risk=None, srp_risk_strict=None,
                       c3_suppressed=None)
        else:
            hits = srp_hits(row, func_ccn=cm.max_method_ccn,
                            func_params=cm.max_params, is_class_row=True,
                            t=th["srp"])
            risk = len(hits) >= C.SRP_RISK_MIN_HITS
            # Semantic suppressor: high conceptual cohesion overrides a
            # size/structure flag. See config.SRP_C3_SUPPRESS.
            sup = bool(risk and C.SRP_C3_SUPPRESS is not None
                       and cm.c3 is not None and cm.c3 >= C.SRP_C3_SUPPRESS)
            row.update(srp_risk_hits="|".join(hits),
                       srp_risk=bool(risk and not sup),
                       srp_risk_strict=bool(
                           len(hits) >= C.SRP_RISK_STRICT_MIN_HITS and not sup),
                       c3_suppressed=sup)
        out["srp"].append(row)
    out["pr"]["n_srp_classes"] = len(out["srp"])
    out["pr"]["srp_risk"] = (
        None if calibrate["srp"] else bool(any(r["srp_risk"] for r in out["srp"])))
    out["pr"]["srp_risk_authored"] = (
        None if calibrate["srp"]
        else bool(any(r["srp_risk"] and r["is_new"] for r in out["srp"])))

    # ---------------- OCP
    ocp_r: Counter = Counter()
    for k, c in a["chains"].items():
        if not c["in_scope"]:
            continue
        # `row_type` and the `ocp_risk` alias reproduce the V1 entity schema
        # exactly, so labels/make_ocp_labeling_sample.py (which selects
        # row_type == "dispatch" and keys on module/qualname/subject) runs
        # unchanged.
        out["ocp"].append(dict(base, row_type="dispatch",
                               module=c["module"], qualname=c["qualname"],
                               subject=c["subject"], kind=c["kind"],
                               n_branches=c["n_branches"], lineno=c["lineno"],
                               smell=c["smell"], ocp_risk=c["smell"]))
        if calibrate["ocp"]:
            continue
        old = b["chains"].get(k)
        if c["smell"] and (old is None or not old["smell"]):
            ocp_r["new_dispatch_smell"] += 1
        if old is not None and c["n_branches"] > old["n_branches"]:
            ocp_r["dispatch_widened"] += 1
    out["pr"]["n_chains_in_scope"] = len(out["ocp"])
    out["pr"]["n_smells_in_scope"] = sum(1 for c in out["ocp"] if c["smell"])
    out["pr"]["ocp_risk_reasons"] = "|".join(sorted(ocp_r))
    out["pr"]["ocp_risk"] = (
        None if calibrate["ocp"] else bool(len(ocp_r) >= C.OCP_RISK_MIN_HITS))

    # ---------------- LSP
    lsp_r: Counter = Counter()
    for k, p in a["pairs"].items():
        if not p["in_scope"]:
            continue
        # V1 entity schema exactly: the subclass is carried as `qualname` (which
        # is what make_lsp_labeling_sample.py keys on) and the signal list is
        # SEMICOLON-joined, not pipe-joined. A different separator here would
        # not error, it would silently break every downstream signal split.
        out["lsp"].append(dict(base, row_type="override",
                               module=p["module"], qualname=p["subclass"],
                               base_id=p["base_id"], method=p["method"],
                               lineno=p["lineno"], signals=";".join(p["signals"]),
                               violation=p["violation"], lsp_risk=p["violation"]))
        if not p["violation"]:
            continue
        old = b["pairs"].get(k)
        prev_sig = set(old["signals"]) if old is not None else set()
        # Signals NEW on this override (absent before) are what this PR caused.
        for sig in set(p["signals"]) - prev_sig:
            if sig in th["lsp"]["strong_signals"]:
                lsp_r[f"new_{sig}"] += 1
    out["pr"]["n_overrides_in_scope"] = len(out["lsp"])
    out["pr"]["n_violations_in_scope"] = sum(1 for p in out["lsp"] if p["violation"])
    out["pr"]["lsp_risk_reasons"] = "|".join(sorted(lsp_r))
    out["pr"]["lsp_risk"] = bool(len(lsp_r) >= C.LSP_RISK_MIN_HITS)

    # ---------------- ISP
    isp_r: Counter = Counter()
    for k, c in a["contracts"].items():
        if not c["in_scope"]:
            continue
        out["isp"].append(dict(base, iface_id=k, module=c["module"],
                               class_name=c["class_name"], kind=c["kind"],
                               lineno=c["lineno"],
                               n_abstract_methods=c["n_abstract_methods"],
                               n_implementers=c["n_implementers"],
                               mean_coverage=c["mean_coverage"],
                               n_forced_stub_impls=c["n_forced_stub_impls"],
                               n_clients=c["n_clients"],
                               mean_client_usage=c["mean_client_usage"],
                               hits="|".join(c["hits"]), flagged=c["flagged"],
                               isp_risk=c["flagged"]))
        if calibrate["isp"]:
            continue
        old = b["contracts"].get(k)
        if c["flagged"] and (old is None or not old["flagged"]):
            isp_r["new_flagged_iface"] += 1
        elif (old is not None and "fat_iface" in c["hits"]
                and c["n_abstract_methods"] > old["n_abstract_methods"]):
            isp_r["widened_fat_iface"] += 1
    for k, i in a["impls"].items():
        if not i["in_scope"]:
            continue
        out["impl"].append(dict(base, impl_id=k[0], iface_id=i["iface_id"],
                                module=i["module"], class_name=i["class_name"],
                                iface_kind=i["iface_kind"],
                                n_iface_methods=i["n_iface_methods"],
                                n_implemented=i["n_implemented"],
                                n_stubbed=i["n_stubbed"], coverage=i["coverage"],
                                stub_ratio=i["stub_ratio"],
                                forced_stub=i["forced_stub"],
                                # V1 alias. An implicit interface is inventory,
                                # never an accusation, so it never carries risk.
                                isp_risk=(None if i["forced_stub"] is None else
                                          bool(i["forced_stub"]
                                               and i["iface_kind"] != "implicit"))))
        if calibrate["isp"] or i["iface_kind"] == "implicit":
            continue
        old = b["impls"].get(k)
        if i["forced_stub"] and (old is None or not old["forced_stub"]):
            isp_r["new_forced_stub"] += 1
    out["pr"]["n_contracts_in_scope"] = len(out["isp"])
    out["pr"]["isp_risk_reasons"] = "|".join(sorted(isp_r))
    # PR-level flag is "any delta reason", matching run_isp.py. Note this is a
    # DIFFERENT bar from C.ISP_RISK_MIN_HITS, which gates the interface ROW on
    # 2 of its 3 signals and is applied above.
    out["pr"]["isp_risk"] = None if calibrate["isp"] else bool(isp_r)

    # ---------------- DIP
    # A pair is NEW only if the before graph did not contain it at all, and it
    # is charged to this PR only if a TOUCHED MODULE is in it. Filtering at
    # module level, not package level: one touched file in a single-package
    # layout would otherwise admit every new pair in the whole package.
    new_pairs = a["cyclic_pairs"] - b["cyclic_pairs"]
    new_cycles = {q for q in new_pairs
                  if q[0] in touched_mods or q[1] in touched_mods}
    dh = []
    if len(new_cycles) > th["dip"]["new_touched_cycles"]:
        dh.append("new_cycle")
    dd = None
    if a["mean_d"] is not None and b["mean_d"] is not None:
        dd = a["mean_d"] - b["mean_d"]
        if dd > th["dip"]["delta_mean_d"]:
            # Recorded either way so the reason string stays diagnostic, but it
            # reaches `dip_risk` only when config says so. Validated at
            # precision 0.000 in Python (0/7) and TypeScript (0/10): it fires on
            # graph RECOMPOSITION, not on real dependency inversion.
            dh.append("mean_d_worse")
    flag_hits = [h for h in dh
                 if h != "mean_d_worse" or C.DIP_MEAN_D_COUNTS_AS_RISK]
    out["pr"].update(
        before_n_modules=b["n_modules"], after_n_modules=a["n_modules"],
        before_touched_cycles=b["touched_cycles"],
        after_touched_cycles=a["touched_cycles"],
        before_repo_cycles=b["repo_cycles"], after_repo_cycles=a["repo_cycles"],
        touched_fan_out=a["touched_fan_out"],
        n_new_cyclic_pairs=len(new_cycles), new_cycle=bool(new_cycles),
        before_mean_d=b["mean_d"], after_mean_d=a["mean_d"], delta_mean_d=dd,
        mean_d_worse=("mean_d_worse" in dh),
        # Matched-scope new-cycle flag, DESCRIPTIVE, for the cross-language
        # comparison. Never part of dip_risk: the shipped flag stays exactly
        # what was validated, and a second definition reaching the headline
        # without its own label round is how `mean_d` got in the first time.
        new_cycle_xpkg=bool({q for q in (a["cyclic_pairs_xpkg"] - b["cyclic_pairs_xpkg"])
                             if q[0] in touched_mods or q[1] in touched_mods}),
        before_abstract_dep_ratio=b["abstract_dep_ratio"],
        after_abstract_dep_ratio=a["abstract_dep_ratio"],
        delta_abstract_dep_ratio=(
            None if a["abstract_dep_ratio"] is None or b["abstract_dep_ratio"] is None
            else a["abstract_dep_ratio"] - b["abstract_dep_ratio"]),
        n_touched_packages=len(a["touched_pkgs"]),
        dip_risk_hits="|".join(dh), dip_risk=bool(flag_hits),
    )
    return out


# ---------------------------------------------------------------- driver

_OUT_PATHS = {
    "pr": C.CORPUS_DIR / "py_pr_results.parquet",
    "srp": C.SRP_RESULTS, "ocp": C.OCP_RESULTS, "lsp": C.LSP_RESULTS,
    "isp": C.ISP_RESULTS, "dip": C.DIP_RESULTS,
}
OUT = dict(_OUT_PATHS)


def use_pilot_paths() -> None:
    """Reroute EVERY output to a parallel `.PILOT.` filename.

    An end-to-end rehearsal on a handful of PRs must not contaminate the real
    parquets, because this file resumes by keeping rows with a permanent status:
    a pilot writing into the real files would leave a few dozen PRs frozen in
    place and the full run would silently skip them. Same failure family as the
    calibration-leftover defect guarded against in main().
    """
    global OUT
    OUT = {k: v.with_suffix(".PILOT.parquet") for k, v in _OUT_PATHS.items()}


# SHARDING (added 2026-09-14). WHY: MEASURED, NOT ASSUMED.
#
# The calibration pass was sampled at 1.04 CPU cores busy out of 12, with the
# machine 82% idle. The workers are THREADS, and ast / lizard / radon are pure
# Python, so the interpreter lock serialises the parsing no matter how many
# threads there are. Raising --workers from 4 to 6 changed nothing measurable.
# Separate PROCESSES are the only way to use the idle cores.
#
# WHY SHARDS ARE REPOSITORY-DISJOINT, which is the whole safety argument:
#   * a git directory does not tolerate concurrent commands, and
#     gitsnap.repo_lock is a threading.Lock, so it serialises only WITHIN a
#     process. Two processes on one repository would race.
#   * gitsnap's tree cache is keyed by (repo, sha, ...). Disjoint repositories
#     means disjoint keys, so no two processes ever write the same cache file.
#   * every shard writes its own parquet files, so there is never more than one
#     writer on a file.
# A shard is assigned WHOLE repositories, balanced greedily by PR count. The
# assignment is a pure function of the gold set, so every process computes the
# same one independently.
#
# A SHARD NEVER SWEEPS GIT LOCKS. gitsnap.sweep_stale_locks deletes every
# `.git/*.lock` in every clone; a shard starting while another is mid-command
# would delete that shard's LIVE lock. The orchestrator sweeps once, before any
# shard starts, when no git process can be running.

def shard_assignment(gold: pd.DataFrame, n: int) -> dict[str, int]:
    """full_name -> shard index. Deterministic, repo-disjoint, PR-balanced."""
    counts = (gold.groupby("full_name").size().reset_index(name="n")
                  .sort_values(["n", "full_name"], ascending=[False, True]))
    load = [0] * n
    out: dict[str, int] = {}
    for full_name, k in zip(counts.full_name, counts.n):
        i = min(range(n), key=lambda j: (load[j], j))
        out[full_name] = i
        load[i] += int(k)
    return out


def use_shard_paths(i: int, n: int) -> None:
    """Suffix every output (pilot or real) with `.shard{i}of{n}`."""
    global OUT
    OUT = {k: v.with_name(f"{v.stem}.shard{i}of{n}{v.suffix}") for k, v in OUT.items()}


def done_marker() -> Path:
    """Written LAST, only when every PR in scope has a row. A parquet on disk
    proves only that a checkpoint happened; this proves the pass finished."""
    return OUT["pr"].with_name(OUT["pr"].stem + ".DONE.json")


# THE SCHEMA CONTRACT WITH THE LABEL CHAIN, enforced rather than hoped for.
#
# `labels/` and `04_report/` were written against the V1 Python parquets and are
# reused unchanged, which is only true while these column names hold. A rename
# here does not fail here: it fails at phase 6, after the whole corpus has been
# analysed, which is the most expensive place in the pipeline to discover it.
# Checked at every checkpoint instead, so it costs one dict lookup and fails on
# the first 25 PRs.
_REQUIRED_COLS = {
    "pr": {"pr_id", "agent", "full_name", "pr_number", "merge_sha", "status"},
    "srp": {"pr_id", "full_name", "file_path", "class_name", "class_loc",
            "method_count", "wmc", "max_method_ccn", "max_class_params",
            "max_func_ccn", "max_func_params", "lcom", "c3", "is_new",
            "srp_risk", "srp_risk_hits"},
    "ocp": {"row_type", "module", "qualname", "subject", "kind", "n_branches",
            "lineno", "smell", "ocp_risk"},
    "lsp": {"row_type", "module", "qualname", "base_id", "method", "lineno",
            "signals", "violation", "lsp_risk"},
    "isp": {"row_type", "iface_id", "module", "class_name", "flagged", "isp_risk"},
    "dip": {"status", "dip_risk", "dip_risk_hits", "n_touched_modules"},
}


# FLAG COLUMNS ARE NORMALISED TO A NULLABLE BOOLEAN BEFORE EVERY WRITE.
#
# Found 2026-09-14 when a re-merge crashed with "Could not convert 0.0 ... to
# boolean" on `forced_stub`. The ISP file holds interface rows (no forced_stub,
# so NaN) and implementer rows (True/False). When a shard RESUMES, the interface
# rows come back with an all-NaN float column, and pandas' concat can upcast the
# implementers' booleans to 0.0/1.0 to match. It happened in one shard of six,
# depending only on which rows that shard resumed. Every value was verified
# identical to the pre-rerun truth value, so nothing was wrong except the type;
# but a float flag in one file and a bool in the others is exactly what a
# downstream `== True` or a strict writer trips over.
#
# The normaliser REFUSES anything other than True, False, 0, 1 or null, so a
# genuine bug that put some other number in a flag cannot be quietly coerced.
FLAG_COLUMNS = (
    "forced_stub", "flagged", "isp_risk", "smell", "ocp_risk", "violation",
    "lsp_risk", "srp_risk", "srp_risk_strict", "c3_suppressed", "is_new",
    "file_is_new", "parse_ok", "new_cycle", "new_cycle_xpkg", "mean_d_worse",
    "dip_risk", "srp_risk_authored",
)


def normalize_flags(df: pd.DataFrame, name: str) -> pd.DataFrame:
    if df is None or not len(df):
        return df
    df = df.copy()
    for col in FLAG_COLUMNS:
        if col not in df.columns:
            continue
        s = df[col]
        bad = s[s.notna() & ~s.isin([True, False, 0, 1, 0.0, 1.0])]
        if len(bad):
            raise RuntimeError(
                f"{name}.{col} holds non-boolean values {list(bad.unique()[:5])}; "
                f"refusing to coerce what may be a real bug")
        df[col] = s.map(lambda x: pd.NA if pd.isna(x) else bool(x)).astype("boolean")
    return df


def _check_schema(name: str, df: pd.DataFrame) -> None:
    if df.empty:
        return                      # nothing analysed yet; nothing to promise
    missing = _REQUIRED_COLS[name] - set(df.columns)
    if missing:
        raise RuntimeError(
            f"{name} parquet is missing {sorted(missing)}, which labels/ and "
            f"04_report/ read by name. Fix the row builder in analyse_pr rather "
            f"than the reader: the V1 guard chain is reused unchanged and a "
            f"rename here surfaces six phases later.")


def write_all(prs: list[dict], buckets: dict[str, list]) -> None:
    W = gitsnap.write_parquet_atomic
    pr_df = normalize_flags(pd.DataFrame(prs), "pr")
    _check_schema("pr", pr_df)
    _check_schema("dip", pr_df)
    W(pr_df, OUT["pr"], index=False)
    W(pr_df, OUT["dip"], index=False)
    for k in ("srp", "ocp", "lsp"):
        d = normalize_flags(pd.DataFrame(buckets[k]), k)
        _check_schema(k, d)
        W(d, OUT[k], index=False)
    isp = pd.DataFrame(buckets["isp"]).assign(row_type="interface")
    imp = pd.DataFrame(buckets["impl"]).assign(row_type="implementer")
    both = normalize_flags(pd.concat([isp, imp], ignore_index=True), "isp")
    _check_schema("isp", both)
    W(both, OUT["isp"], index=False)


# During calibration the rows must still be EMITTED so their distribution can be
# measured; only the flags are suppressed. These placeholders are used for
# scoping only and never reach a published number.
CAL_DEFAULTS = {
    "srp": {"class_loc": 10 ** 9, "class_wmc": 10 ** 9, "class_methods": 10 ** 9,
            "class_lcom": 2.0, "file_loc": 10 ** 9, "func_ccn": 10 ** 9,
            "func_params": 10 ** 9},
    "ocp": {"dispatch_min_branches": 10 ** 9, "value_switch_min_branches": 10 ** 9},
    "isp": {"iface_abstract_methods": 10 ** 9, "impl_stub_ratio": 2.0,
            "impl_stub_min": 10 ** 9, "iface_mean_coverage": -1.0,
            "iface_min_implementers": 10 ** 9},
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=getattr(C, "ANALYZE_WORKERS", 6))
    ap.add_argument("--calibrate", action="store_true",
                    help="force calibration mode regardless of config")
    ap.add_argument("--fresh", action="store_true", help="ignore existing results")
    ap.add_argument("--pilot", action="store_true",
                    help="end-to-end rehearsal: write to *.PILOT.parquet so the "
                         "real results are untouched. Combine with --cells.")
    ap.add_argument("--cells", type=int, default=0,
                    help="pilot: take N PRs from EVERY (agent x outcome) cell, so "
                         "the rehearsal exercises every cell rather than whichever "
                         "one happens to sort first")
    ap.add_argument("--shard", default="",
                    help="i/N: analyse only the repositories assigned to shard i "
                         "of N, writing *.shard{i}of{N}.parquet. See sharding note.")
    ap.add_argument("--no-sweep", action="store_true",
                    help="do not sweep stale git locks (implied by --shard)")
    a = ap.parse_args()
    if a.pilot:
        use_pilot_paths()
        log("PILOT MODE: writing to *.PILOT.parquet -- real results untouched")
    shard_i = shard_n = None
    if a.shard:
        try:
            shard_i, shard_n = (int(x) for x in a.shard.split("/"))
            assert shard_n >= 1 and 0 <= shard_i < shard_n
        except (ValueError, AssertionError):
            sys.exit(f"--shard must be i/N with 0 <= i < N, got {a.shard!r}")
        use_shard_paths(shard_i, shard_n)
        log(f"SHARD {shard_i}/{shard_n}: repository-disjoint, writing "
            f"{OUT['pr'].name}")

    th = {"srp": C.SRP_THRESHOLDS, "ocp": C.OCP_THRESHOLDS,
          "isp": C.ISP_THRESHOLDS, "lsp": C.LSP_THRESHOLDS,
          "dip": C.DIP_THRESHOLDS}
    calibrate = {k: (a.calibrate or th.get(k) is None) for k in ("srp", "ocp", "isp")}
    for k in ("srp", "ocp", "isp"):
        if calibrate[k]:
            th[k] = CAL_DEFAULTS[k]
    if any(calibrate.values()):
        log(f"CALIBRATION MODE for: {[k for k, v in calibrate.items() if v]} "
            f"-- flags left null, distributions printed at the end")

    log("free disk: %.1f GB" % safeio.check_disk())
    # Locks left by a run killed hard (reboot, power cut) would make every git
    # call in those repos fail instantly. Swept before any worker starts.
    if a.shard or a.no_sweep:
        log("not sweeping git locks (sharded: another shard may hold a live one; "
            "the orchestrator sweeps once before any shard starts)")
    else:
        swept = gitsnap.sweep_stale_locks()
        if swept:
            log("swept %d stale git lock(s) from a previous hard stop" % swept)

    gold = pd.read_parquet(C.GOLD_SET)
    if a.shard:
        assign = shard_assignment(gold, shard_n)
        gold = gold[gold.full_name.map(assign) == shard_i]
        log(f"shard {shard_i}/{shard_n}: {len(gold):,} PRs over "
            f"{gold.full_name.nunique():,} repositories")
    done: set = set()
    prs: list[dict] = []
    buckets: dict[str, list] = {k: [] for k in ("srp", "ocp", "lsp", "isp", "impl")}
    if OUT["pr"].exists() and not a.fresh:
        old = pd.read_parquet(OUT["pr"])
        keep = old[old.status.isin(PERMANENT)]
        done = set(keep.pr_id)
        prs = keep.to_dict("records")
        for k in ("srp", "ocp", "lsp"):
            if OUT[k].exists():
                dfp = pd.read_parquet(OUT[k])
                buckets[k] = dfp[dfp.pr_id.isin(done)].to_dict("records")
        if OUT["isp"].exists():
            dfp = pd.read_parquet(OUT["isp"])
            dfp = dfp[dfp.pr_id.isin(done)]
            buckets["isp"] = (dfp[dfp.row_type == "interface"]
                              .drop(columns=["row_type"]).to_dict("records"))
            buckets["impl"] = (dfp[dfp.row_type == "implementer"]
                               .drop(columns=["row_type"]).to_dict("records"))

        # DO NOT RESUME CALIBRATION ROWS INTO A FLAGGED RUN.
        #
        # Calibration emits every row with its flag left null so the
        # distribution can be measured. Those rows carry status "ok", which is
        # permanent, so a later run WITH thresholds would resume them untouched
        # and analyse only whatever was left, producing one parquet holding both
        # null-flag and flagged rows with nothing anywhere saying so. The report
        # and the label sets would both be built on a corpus that is mostly
        # unflagged. Demonstrated on a 12-PR Java smoke run: 112 null rows and 6
        # flagged ones in the same file.
        #
        # CLAUDE.md tells the operator to delete the parquets after a threshold
        # change, which is correct and is also the kind of instruction that gets
        # missed once. Detect it instead.
        flag_cols = {"srp": ("srp", "srp_risk"), "ocp": ("ocp", "smell"),
                     "isp": ("isp", "flagged")}
        stale: set = set()
        for principle, (bucket, col) in flag_cols.items():
            if calibrate.get(principle):
                continue                      # still calibrating: nulls expected
            stale |= {r["pr_id"] for r in (buckets.get(bucket) or [])
                      if col in r and pd.isna(r[col])}
        for pr_col in ("srp_risk", "ocp_risk", "isp_risk"):
            principle = pr_col.split("_")[0]
            if pr_col in keep.columns and not calibrate.get(principle, False):
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
    # SHUFFLE, SEEDED. Not cosmetic: all git work for one repository is
    # serialised by gitsnap.repo_lock, and the gold set is repo-stratified from
    # curation, so in its natural order consecutive PRs largely share a
    # repository and every worker queues on the same lock. The seed is fixed so
    # run order is reproducible; order affects only scheduling, never any
    # computed value, since each PR is analysed independently and keyed by pr_id.
    random.Random(C.RANDOM_SEED).shuffle(todo)
    if a.cells:
        # N PRs from EVERY (agent x outcome) cell. `--limit` takes the head of a
        # shuffled frame, which would rehearse whichever cells happen to land
        # first, and that is precisely the coverage a rehearsal must not have.
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

    from concurrent.futures import ThreadPoolExecutor, as_completed
    t0, n = time.time(), 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(analyse_pr, pr, th, calibrate): pr for pr in todo}
        for fut in as_completed(futs):
            pr = futs[fut]
            try:
                res = fut.result()
            except Exception as e:                      # noqa: BLE001
                res = {"pr": {"pr_id": pr.pr_id, "agent": pr.agent,
                              "full_name": pr.full_name,
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
                log(f"  [{n}/{len(todo)}] {res['pr']['status']:<18} "
                    f"{el/60:.1f} min elapsed, ~{el/n*(len(todo)-n)/60:.1f} min left")

    write_all(prs, buckets)
    df = pd.DataFrame(prs)
    log(f"\ndone in {(time.time()-t0)/60:.1f} min")
    log(f"status: {df.status.value_counts().to_dict()}")
    ok = df[df.status == "ok"]
    if len(ok):
        log(f"analysable PRs: {len(ok):,}")

    # THE COMPLETION MARKER. Written only when every PR in scope has exactly one
    # row, and after the final write. A merge that finds a parquet but no marker
    # is looking at a checkpoint, not a finished pass, and must refuse. It also
    # records the thresholds, so a merge can prove every shard ran under the
    # same bars rather than trusting that config did not change between them.
    import json
    have = set(df.pr_id) if len(df) else set()
    complete = (not a.limit and not a.cells and have == set(gold.pr_id)
                and len(df) == len(have))
    if complete:
        safeio.write_json(done_marker(), {
            "shard": shard_i, "n_shards": shard_n,
            "expected_prs": int(len(gold)), "written_prs": int(len(have)),
            "calibrate": bool(any(calibrate.values())),
            "thresholds": {k: th[k] for k in ("srp", "ocp", "isp")},
            "status": {k: int(v) for k, v in df.status.value_counts().items()},
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        log(f"completion marker written: {done_marker().name}")
    else:
        log(f"NO completion marker: {len(have):,} of {len(gold):,} PRs have a "
            f"row (limit/cells rehearsals never get one)")
    _distributions(buckets, calibrate)


def _distributions(buckets: dict, calibrate: dict) -> None:
    """The non-circular calibration step: print THIS corpus's own percentiles.

    THE POPULATION MATTERS AS MUCH AS THE PERCENTILE. Two corrections are baked
    in here because the Java version got both wrong at first and the write-up
    describes the corrected populations:

      * PRODUCTION ONLY. Test code is 28% of the Python corpus and is
        systematically smaller, so calibrating on all classes drags every
        threshold DOWN and makes production code cross it more readily, while
        the production-only rate is what the report leads with.
      * ENTITY-DEDUPLICATED. A class appears once per PR that touched it, so the
        raw row list over-weights whatever the agents edited most.

    Both populations are printed, with the one to use marked, so any discrepancy
    is visible instead of silent.
    """
    qs = [.5, .75, .9, .95, .99]

    def _pct(frame: pd.DataFrame, cols) -> None:
        for c in cols:
            if c in frame and frame[c].notna().any():
                log(f"    {c:<20} " + "  ".join(
                    f"p{int(q*100)}={frame[c].quantile(q):.2f}" for q in qs))

    if calibrate["srp"] and buckets["srp"]:
        d = pd.DataFrame(buckets["srp"])
        cols = ("class_loc", "wmc", "method_count", "max_method_ccn",
                "max_params", "lcom", "c3", "file_loc")
        d["_test"] = d["file_path"].map(lambda p: C.is_test_path(str(p), "py"))
        prod = d[~d["_test"]].copy()
        prod["_entity"] = prod["full_name"] + "::" + prod["file_path"] + "::" + prod["class_name"]
        uniq = prod.sort_values("class_loc").drop_duplicates("_entity", keep="last")
        log(f"\nSRP percentiles -- ALL {len(d):,} ROWS "
            f"({d['_test'].mean():.1%} test code, duplicated). "
            f"DO NOT CALIBRATE ON THIS:")
        _pct(d, cols)
        log(f"\nSRP percentiles -- PRODUCTION, ENTITY-DEDUPLICATED "
            f"(n={len(uniq):,}, duplication {len(prod)/max(len(uniq),1):.2f}x). "
            f"*** SET SRP_THRESHOLDS FROM ~p90 OF THIS ONE ***")
        _pct(uniq, cols)

    if calibrate["ocp"] and buckets["ocp"]:
        d = pd.DataFrame(buckets["ocp"])
        u = d.drop_duplicates(["full_name", "module", "qualname", "subject"])
        log(f"\nOCP branch-count percentiles BY KIND, DISTINCT chains "
            f"(n={len(u):,} of {len(d):,} rows). Type-based and tag-based bars "
            f"differ and are set separately:")
        for k, g in u.groupby("kind"):
            log(f"    {k:<14} n={len(g):<6} " + "  ".join(
                f"p{int(q*100)}={g.n_branches.quantile(q):.0f}" for q in qs))
        log("    dispatch_min_branches stays 3 (Martin's own minimum for a "
            "switch, used unchanged")
        log("    in every language so type-based bars stay comparable); "
            "value_switch_min_branches")
        log("    is the one to set from ~p90 of the value/tag kinds above.")

    if calibrate["isp"] and buckets["isp"]:
        d = pd.DataFrame(buckets["isp"])
        u = d.drop_duplicates(["full_name", "iface_id"])
        log(f"\nISP contract-width percentiles, DISTINCT contracts "
            f"(n={len(u):,} of {len(d):,} rows):")
        _pct(u, ("n_abstract_methods", "n_implementers", "mean_coverage"))
        log("    CAVEAT for the write-up: interface WIDTH is the weaker half of "
            "ISP, and Java")
        log("    proved it -- the fitted >= 30-member rule fell from 0.862 to "
            "0.704 on a holdout.")
        log("    The semantically meaningful signal is `forced_stub`, which is "
            "scale-free and")
        log("    needs no threshold. A categorical rule survived being fitted; "
            "a numeric one did not.")

    # LSP and DIP carry no numeric threshold to calibrate (LSP is a set of
    # categorical signals, DIP's bar is structural at 0 new cycles), but their
    # distributions still decide whether the corpus can support a model at all.
    # Java could not fit LSP: 16 flagged PRs cannot support four parameters.
    if buckets["lsp"]:
        d = pd.DataFrame(buckets["lsp"])
        sig: Counter = Counter()
        for s in d.signals.fillna(""):
            sig.update(x for x in str(s).split("|") if x)
        log(f"\nLSP signal frequencies over {len(d):,} in-scope override pairs "
            f"(FIVE signals here, not Java's three: `javac` already rejects the "
            f"other two faults, Python has no compiler):")
        for k, v in sig.most_common():
            log(f"    {k:<26} {v:,}")


# ---------------------------------------------------------------- self-check

def _self_check() -> None:
    """Offline assertions on snapshot() and the delta. No network, no repos.

    These pin the parts that are easy to get wrong and impossible to notice:
    scoping, novelty against the FULL before table, and the authored-vs-edited
    split. Each one corresponds to a defect this project has actually paid for.
    """
    th = {"srp": C.SRP_THRESHOLDS, "ocp": C.OCP_THRESHOLDS,
          "isp": C.ISP_THRESHOLDS, "lsp": C.LSP_THRESHOLDS,
          "dip": C.DIP_THRESHOLDS}
    cal = {"srp": False, "ocp": False, "isp": False}

    # ---- scoping: an entity outside the touched set is built but not in scope.
    files = {
        "a.py": "class Base:\n    def go(self, x):\n        return x\n",
        "b.py": ("from a import Base\n"
                 "class Sub(Base):\n"
                 "    def go(self, x, y):\n        return x\n"),
    }
    s_all = snapshot(files, {"a.py", "b.py"}, {"a", "b"}, th, cal)
    s_none = snapshot(files, set(), set(), th, cal)
    assert s_all["pairs"], "an override pair must be found at all"
    key = next(iter(s_all["pairs"]))
    assert s_all["pairs"][key]["in_scope"] is True
    assert s_none["pairs"][key]["in_scope"] is False, (
        "an untouched pair must still be BUILT, so novelty can be decided "
        "against the full before table, but must not be in scope")
    print("  [ok  ] entities are built repo-wide and scoped by touch")

    # ---- RIPPLE: touching only the BASE must still put the pair in scope.
    s_base = snapshot(files, {"a.py"}, {"a"}, th, cal)
    assert s_base["pairs"][key]["in_scope"] is True, (
        "RIPPLE-AWARE ATTRIBUTION: editing a base class breaks subclasses in "
        "files the agent never opened, and those are untouched by construction")
    print("  [ok  ] LSP attribution is ripple-aware (either side counts)")

    # ---- ISP ripple, the same rule on the other relation.
    ifiles = {
        "i.py": ("import abc\n"
                 "class I(abc.ABC):\n"
                 "    @abc.abstractmethod\n    def a(self): ...\n"
                 "    @abc.abstractmethod\n    def b(self): ...\n"),
        "j.py": ("from i import I\n"
                 "class J(I):\n"
                 "    def a(self):\n        return 1\n"
                 "    def b(self):\n        raise NotImplementedError\n"),
    }
    s_i = snapshot(ifiles, {"i.py"}, {"i"}, th, cal)
    assert s_i["impls"], "an implementer relationship must be found"
    ikey = next(iter(s_i["impls"]))
    assert s_i["impls"][ikey]["in_scope"] is True, (
        "widening a touched interface forces stubs into implementers the agent "
        "never opened, which is exactly what new_forced_stub reports")
    print("  [ok  ] ISP attribution is ripple-aware (either side counts)")

    # ---- OCP novelty: widening is measured against the before table, and a
    # pre-existing smell is never re-debited.
    # The types must be USER-DEFINED. A chain over builtins is a CLOSED type
    # set and OCP_CLOSED_SET_SUPPRESS deliberately refuses to flag it, that
    # being the deterministic fix for the isinstance-over-closed-set false
    # positive. Using builtins here would test the suppressor, not the delta.
    before = {"m.py": ("def f(x):\n"
                       "    if isinstance(x, Circle): return 1\n"
                       "    elif isinstance(x, Square): return 2\n"
                       "    elif isinstance(x, Hexagon): return 3\n"
                       "    return 0\n")}
    after = {"m.py": ("def f(x):\n"
                      "    if isinstance(x, Circle): return 1\n"
                      "    elif isinstance(x, Square): return 2\n"
                      "    elif isinstance(x, Hexagon): return 3\n"
                      "    elif isinstance(x, Octagon): return 4\n"
                      "    return 0\n")}
    bs = snapshot(before, {"m.py"}, {"m"}, th, cal)
    as_ = snapshot(after, {"m.py"}, {"m"}, th, cal)
    ck = next(iter(as_["chains"]))
    assert bs["chains"][ck]["smell"] is True, "3 branches is already a smell"
    assert as_["chains"][ck]["n_branches"] > bs["chains"][ck]["n_branches"]
    # the delta rule in analyse_pr: new smell requires it to be absent or
    # unflagged before, so this one widens rather than newly offends.
    assert not (as_["chains"][ck]["smell"] and not bs["chains"][ck]["smell"]), (
        "a PRE-EXISTING smell must never be re-debited as new_dispatch_smell")
    print("  [ok  ] OCP novelty is decided against the full before table")

    # ---- SRP authored vs edited.
    b_srp = snapshot({"s.py": "class Old:\n    pass\n"}, {"s.py"}, {"s"}, th, cal)
    a_srp = snapshot({"s.py": "class Old:\n    pass\n\nclass New:\n    pass\n"},
                     {"s.py"}, {"s"}, th, cal)
    assert ("s.py", "Old") in b_srp["srp"] and ("s.py", "New") not in b_srp["srp"]
    assert {("s.py", "Old"), ("s.py", "New")} <= set(a_srp["srp"]), a_srp["srp"].keys()
    print("  [ok  ] SRP separates authored from edited classes")

    # ---- DIP pair-level novelty, not SCC-level.
    b_dip = snapshot({"p.py": "import q\n", "q.py": "x = 1\n"}, {"p.py"}, {"p"}, th, cal)
    a_dip = snapshot({"p.py": "import q\n", "q.py": "import p\n"}, {"p.py"}, {"p"}, th, cal)
    new = a_dip["cyclic_pairs"] - b_dip["cyclic_pairs"]
    assert new, "p <-> q became mutually reachable and must read as new"
    assert not (b_dip["cyclic_pairs"] - set()), "before tree has no cycle"
    print("  [ok  ] DIP novelty is pair-level (a removed module cannot read as new)")

    # ---- calibration mode must suppress flags, not rows.
    calon = {"srp": True, "ocp": True, "isp": True}
    thc = dict(th); thc.update({k: CAL_DEFAULTS[k] for k in ("srp", "ocp", "isp")})
    sc = snapshot(after, {"m.py"}, {"m"}, thc, calon)
    assert sc["chains"], "calibration must still EMIT rows, or nothing can be measured"
    assert sc["chains"][ck]["smell"] is None, "calibration must leave the flag null"
    print("  [ok  ] calibration suppresses flags but still emits rows")

    # ---- the descriptive columns must never reach a flag.
    src = Path(__file__).read_text(encoding="utf-8")
    hits_body = src.split("def srp_hits", 1)[1].split("\ndef ", 1)[0]
    for col in ("fan_in", "fan_out", "abstract_dep_ratio", "maintainability_index"):
        assert col not in hits_body, f"{col} must not reach the SRP flag"
    print("  [ok  ] coupling columns are descriptive only, no flag reads them")

    # ---- sharding: disjoint, complete, balanced, deterministic, on the REAL gold set
    gold = pd.read_parquet(C.GOLD_SET)
    for n in (1, 2, 5, 6):
        sa = shard_assignment(gold, n)
        assert set(sa) == set(gold.full_name), "every repository gets a shard"
        assert shard_assignment(gold, n) == sa, "assignment must be deterministic"
        parts = [gold[gold.full_name.map(sa) == i] for i in range(n)]
        assert sum(len(p) for p in parts) == len(gold), "every PR in exactly one shard"
        repos = [set(p.full_name) for p in parts]
        for x in range(n):
            for y in range(x + 1, n):
                assert not (repos[x] & repos[y]), "shards must be REPOSITORY-DISJOINT"
        sizes = [len(p) for p in parts]
        assert max(sizes) - min(sizes) <= gold.groupby("full_name").size().max(), (
            f"greedy balance off: {sizes}")
    print(f"  [ok  ] shards are repo-disjoint, complete, deterministic, balanced "
          f"(n=5 sizes {[int((gold.full_name.map(shard_assignment(gold, 5)) == i).sum()) for i in range(5)]})")

    global OUT
    saved = dict(OUT)
    use_shard_paths(2, 5)
    assert OUT["pr"].name == "py_pr_results.shard2of5.parquet", OUT["pr"].name
    assert OUT["isp"].name == "isp_results.shard2of5.parquet", OUT["isp"].name
    assert done_marker().name == "py_pr_results.shard2of5.DONE.json"
    assert len(set(OUT.values())) == len(OUT), "no two outputs may share a file"
    assert not (set(OUT.values()) & set(_OUT_PATHS.values())), (
        "a shard must never write a canonical path")
    OUT = saved
    print("  [ok  ] shard output paths never collide with real or other-shard files")

    print("\nrun_python.py self-check ALL PASS")


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        _self_check()
    else:
        main()
