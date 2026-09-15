"""
Derive Python V2's SRP / OCP / ISP thresholds from THIS corpus's own percentiles.

THE METHOD, and why it is mechanical rather than a judgement call. A threshold
chosen after looking at labels is circular: it is fitted to the answer it is
later validated against. So the bars are anchored at ~p90 of this corpus's own
distribution BEFORE any label exists, exactly as Java and the V1 ports did. That
makes "roughly the worst tenth" the definition of the bar, which is a stated
convention rather than a tuned parameter.

TWO POPULATION RULES, both of which the Java version got wrong at first and both
of which are baked in here:

  PRODUCTION ONLY. Test code is ~28% of the Python corpus and is systematically
  smaller, so calibrating on all classes drags every bar DOWN and makes
  production code cross it more readily, while the production rate is what the
  report leads with.

  ENTITY-DEDUPLICATED. A class appears once per PR that touched it, so the raw
  row list over-weights whatever the agents edited most. Deduplicating to
  (repo, file, class) makes the calibration population, the reported population
  and the judged population one population rather than three.

WHAT IS NOT CALIBRATED, deliberately:
  * `dispatch_min_branches` stays 3. That is Martin's own minimum for a switch,
    used unchanged in every language so type-based bars stay comparable.
  * `class_lcom` stays 0.8. On the population it applies to, LCOM reads
    p75 = p90 = p95 = 1.000; a metric pinned at its own maximum cannot be
    calibrated by percentile, so the validated value is kept.
  * LSP has no numeric threshold at all (it is a set of categorical signals) and
    DIP's bar is structural at 0 new cycles.
  * `impl_stub_ratio` / `impl_stub_min` stay as validated: `forced_stub` is
    scale-free and needs no corpus anchor. Java proved the danger of the
    alternative, its fitted >= 30-member width rule fell from 0.862 to 0.704 on
    a holdout while the categorical rule held at 1.000.

Run:  python vibebench/03_principles/py_lang/propose_py_thresholds.py
      python vibebench/03_principles/py_lang/propose_py_thresholds.py --apply
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))
sys.path.insert(0, str(_HERE.parents[1] / "ocp"))
import config as C  # noqa: E402
# The detector's OWN set, imported rather than copied. A copy that drifted from
# dispatch.py would calibrate the value-switch bar on a population the detector
# does not apply it to, and nothing would say so.
from dispatch import _TYPE_KINDS as TYPE_KINDS  # noqa: E402

QS = [.5, .75, .9, .95, .99]


def pct(s: pd.Series) -> str:
    return "  ".join(f"p{int(q * 100)}={s.quantile(q):.2f}" for q in QS)


def production_dedup(d: pd.DataFrame) -> pd.DataFrame:
    """The population every SRP bar is anchored on. See the module docstring."""
    d = d[~d["file_path"].map(lambda p: C.is_test_path(str(p), "py"))].copy()
    d["_entity"] = d["full_name"] + "::" + d["file_path"] + "::" + d["class_name"]
    return d.sort_values("class_loc").drop_duplicates("_entity", keep="last")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write the proposal into config.py (a timestamped "
                         "backup is made first)")
    a = ap.parse_args()

    for p in (C.SRP_RESULTS, C.OCP_RESULTS, C.ISP_RESULTS):
        if not p.exists():
            sys.exit(f"missing {p.name}. Run the calibration pass first:\n"
                     f"  python vibebench/03_principles/py_lang/run_python.py "
                     f"--calibrate")

    # ---- SRP ---------------------------------------------------------------
    srp = pd.read_parquet(C.SRP_RESULTS)
    prod = production_dedup(srp)
    print(f"SRP population: {len(srp):,} rows -> {len(prod):,} production, "
          f"entity-deduplicated (duplication "
          f"{len(srp) / max(len(prod), 1):.2f}x)")
    for c in ("class_loc", "wmc", "method_count", "max_method_ccn",
              "max_class_params", "file_loc"):
        if c in prod:
            print(f"    {c:<18} {pct(prod[c])}")

    def p90(c: str) -> float:
        return float(prod[c].quantile(.9))

    # FILE_LOC IS A PROPERTY OF A FILE, so it is anchored on DISTINCT production
    # files. Taken over class rows, a file holding 200 classes is counted 200
    # times, which drags the percentile toward the largest files: measured
    # p90 2,938 over class rows against 1,135 over distinct files.
    files = srp[~srp["file_path"].map(lambda p: C.is_test_path(str(p), "py"))]
    files = files.drop_duplicates(["full_name", "file_path"])
    file_loc_p90 = float(files["file_loc"].quantile(.9))
    print(f"    file_loc over {len(files):,} DISTINCT production files: "
          f"{pct(files['file_loc'])}")

    srp_new = {
        "class_loc": int(round(p90("class_loc"), -1)),
        "class_wmc": int(round(p90("wmc"))),
        "class_methods": int(round(p90("method_count"))),
        "class_lcom": 0.8,          # NOT percentile-derived, see docstring
        "file_loc": int(round(file_loc_p90, -1)),
        "func_ccn": int(round(p90("max_method_ccn"))),
        "func_params": int(round(p90("max_class_params"))),
    }
    print("\n  sanity, share of production classes each bar alone would fire on:")
    for k, col in (("class_loc", "class_loc"), ("class_wmc", "wmc"),
                   ("class_methods", "method_count"),
                   ("func_ccn", "max_method_ccn"),
                   ("func_params", "max_class_params")):
        print(f"    {k:<15} {(prod[col] > srp_new[k]).mean():.1%}")
    print("  (each should land near 10%: that is what a p90 anchor means. A bar "
          "far above 10%\n   means the metric is discrete and piles up on one "
          "value, which is not an error.)")

    # ---- OCP ---------------------------------------------------------------
    ocp = pd.read_parquet(C.OCP_RESULTS)
    u = ocp.drop_duplicates(["full_name", "module", "qualname", "subject"])
    val = u[~u.kind.isin(TYPE_KINDS)]
    print(f"\nOCP population: {len(ocp):,} rows -> {len(u):,} distinct chains")
    for k, g in u.groupby("kind"):
        print(f"    {k:<14} n={len(g):<6} " + "  ".join(
            f"p{int(q * 100)}={g.n_branches.quantile(q):.0f}" for q in QS))
    ocp_new = {
        "dispatch_min_branches": 3,          # fixed by convention, see docstring
        "value_switch_min_branches": (
            int(round(val.n_branches.quantile(.9))) if len(val) else 6),
    }

    # ---- ISP ---------------------------------------------------------------
    isp = pd.read_parquet(C.ISP_RESULTS)
    ifs = isp[isp.row_type == "interface"].drop_duplicates(["full_name", "iface_id"])
    print(f"\nISP population: {len(ifs):,} distinct contracts")
    if len(ifs):
        print(f"    n_abstract_methods {pct(ifs.n_abstract_methods)}")
    isp_new = dict(C.ISP_THRESHOLDS)
    isp_new["iface_abstract_methods"] = (
        int(round(ifs.n_abstract_methods.quantile(.9))) if len(ifs) else 10)

    # ---- the proposal ------------------------------------------------------
    print("\n" + "=" * 66)
    print("PROPOSED, from this corpus's own percentiles")
    print("=" * 66)
    print(f"SRP_THRESHOLDS = {srp_new}")
    print(f"OCP_THRESHOLDS = {ocp_new}")
    print(f"ISP iface_abstract_methods = {isp_new['iface_abstract_methods']}")
    print("\nwas (V1, calibrated on 546 PRs and meaningless here):")
    print(f"SRP {dict((k, C.SRP_THRESHOLDS[k]) for k in srp_new)}")
    print(f"OCP {C.OCP_THRESHOLDS}")
    print(f"ISP iface_abstract_methods = {C.ISP_THRESHOLDS['iface_abstract_methods']}")

    # ---- SANITY GUARDS, checked before anything is written ------------------
    #
    # A percentile of a tiny population is not a calibration, it is an accident.
    # Rehearsed on 22 pilot classes this script proposed class_loc 1060 and
    # value_switch_min_branches 2, the second of which would flag nearly every
    # two-branch conditional in the corpus. Running unattended in a chain, that
    # would have been applied, the parquets deleted, and hours spent producing a
    # corpus flagged by a meaningless bar.
    #
    # So: refuse on a population too small to carry a p90, and refuse on a value
    # outside the range any plausible Python corpus can produce. These are
    # deliberately WIDE, they exist to catch degeneracy, not to encode an
    # expected answer.
    problems: list[str] = []
    if len(prod) < 2000:
        problems.append(
            f"SRP population is {len(prod):,} production classes; a p90 needs a "
            f"real corpus. Expect tens of thousands at full coverage.")
    if len(val) < 200:
        problems.append(
            f"only {len(val):,} distinct value/tag dispatch chains; too few to "
            f"anchor value_switch_min_branches.")
    if len(ifs) < 100:
        problems.append(
            f"only {len(ifs):,} distinct contracts; too few to anchor the fat "
            f"interface bar.")
    BOUNDS = {"class_loc": (80, 2000), "class_wmc": (10, 400),
              "class_methods": (5, 100), "file_loc": (200, 5000),
              "func_ccn": (4, 60), "func_params": (3, 30)}
    for k, (lo, hi) in BOUNDS.items():
        if not lo <= srp_new[k] <= hi:
            problems.append(f"SRP {k}={srp_new[k]} is outside the sane range "
                            f"[{lo}, {hi}]")
    if not 3 <= ocp_new["value_switch_min_branches"] <= 30:
        problems.append(f"OCP value_switch_min_branches="
                        f"{ocp_new['value_switch_min_branches']} is outside "
                        f"[3, 30]; below 3 it would flag ordinary conditionals")
    if not 3 <= isp_new["iface_abstract_methods"] <= 80:
        problems.append(f"ISP iface_abstract_methods="
                        f"{isp_new['iface_abstract_methods']} is outside [3, 80]")

    if problems:
        print("\n" + "!" * 66)
        print("REFUSING TO APPLY, the calibration population is not sound:")
        for p_ in problems:
            print(f"  * {p_}")
        print("!" * 66)
        print("This usually means the calibration pass ran on a partial corpus. "
              "Check\ncoverage, finish phase 2, and re-run the calibration pass.")
        sys.exit(2)

    if not a.apply:
        print("\nall sanity guards passed.")
        print("(dry run. Pass --apply to write these into config.py.)")
        return

    cfg = Path(C.__file__)
    backup = cfg.with_name(f"config.py.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(cfg, backup)
    src = cfg.read_text(encoding="utf-8")

    def replace_block(text: str, name: str, body: str) -> str:
        """Replace `NAME = { ... }` up to its closing brace at column 0."""
        pat = re.compile(rf"^{name} = \{{.*?^\}}\n", re.S | re.M)
        if not pat.search(text):
            sys.exit(f"could not locate {name} in config.py; nothing written")
        return pat.sub(body, text, count=1)

    stamp = time.strftime("%Y-%m-%d")
    srp_body = (
        f"SRP_THRESHOLDS = {{\n"
        f"    # RECALIBRATED {stamp} on the Python V2 corpus (7,050 PRs), anchored\n"
        f"    # at ~p90 of PRODUCTION, ENTITY-DEDUPLICATED classes (n={len(prod):,}).\n"
        f"    # The previous values were percentiles of the 546-PR V1 corpus.\n"
        f"    # Derived mechanically by py_lang/propose_py_thresholds.py, BEFORE any\n"
        f"    # label existed, so the bar is not fitted to the answer it is\n"
        f"    # validated against. The V1 values (260 / 36 / 11 / 871 / 11 / 6) and\n"
        f"    # their rationale remain in git history.\n"
        f"    #\n"
        f"    # MEASURED, production entity-deduplicated classes (n={len(prod):,}):\n"
        f"    #   class_loc   p90={prod['class_loc'].quantile(.9):.0f}  p95={prod['class_loc'].quantile(.95):.0f}\n"
        f"    #   wmc         p90={prod['wmc'].quantile(.9):.0f}  p95={prod['wmc'].quantile(.95):.0f}\n"
        f"    #   methods     p90={prod['method_count'].quantile(.9):.0f}  p95={prod['method_count'].quantile(.95):.0f}\n"
        f"    #   method ccn  p90={prod['max_method_ccn'].quantile(.9):.0f}  p95={prod['max_method_ccn'].quantile(.95):.0f}\n"
        f"    #   params      p90={prod['max_class_params'].quantile(.9):.0f}  p95={prod['max_class_params'].quantile(.95):.0f}\n"
        f"    #   file_loc    p90={file_loc_p90:.0f}  (n={len(files):,} distinct production files)\n"
        f"    \"class_loc\": {srp_new['class_loc']},\n"
        f"    \"class_wmc\": {srp_new['class_wmc']},\n"
        f"    \"class_methods\": {srp_new['class_methods']},\n"
        f"    \"class_lcom\": {srp_new['class_lcom']},   # NOT percentile-derived:\n"
        f"                            # LCOM reads p75=p90=p95=1.000 on the\n"
        f"                            # population it applies to, so the\n"
        f"                            # validated value is kept.\n"
        f"    \"file_loc\": {srp_new['file_loc']},   # p90 of DISTINCT production files.\n"
        f"                            # INERT in V2: run_python.py scores classes\n"
        f"                            # only (as Java does), and srp_hits reads\n"
        f"                            # file_loc for file-level rows alone.\n"
        f"    \"func_ccn\": {srp_new['func_ccn']},\n"
        f"    \"func_params\": {srp_new['func_params']},\n"
        f"}}\n")
    ocp_body = (
        f"OCP_THRESHOLDS = {{\n"
        f"    # RECALIBRATED {stamp} on the Python V2 corpus.\n"
        f"    # dispatch_min_branches stays 3: Martin's own minimum for a switch,\n"
        f"    # held constant across languages so type-based bars stay comparable.\n"
        f"    \"dispatch_min_branches\": {ocp_new['dispatch_min_branches']},\n"
        f"    \"value_switch_min_branches\": {ocp_new['value_switch_min_branches']},"
        f"   # ~p90 of value/tag chains\n"
        f"}}\n")
    src = replace_block(src, "SRP_THRESHOLDS", srp_body)
    src = replace_block(src, "OCP_THRESHOLDS", ocp_body)
    # ANCHORED TO THE ISP_THRESHOLDS BLOCK. A bare `"iface_abstract_methods"`
    # substitution edits whichever block contains that key FIRST in the file;
    # ISP_TS_THRESHOLDS sits above ISP_THRESHOLDS, so the day it gains the key the
    # wrong language's threshold would change, and the re-import check below would
    # then fail only because ISP_THRESHOLDS did not move. `(?!^\})` stops the
    # match from running past the block's own closing brace.
    isp_pat = re.compile(
        r'(^ISP_THRESHOLDS = \{(?:(?!^\}).)*?"iface_abstract_methods":\s*)\d+',
        re.S | re.M)
    if not isp_pat.search(src):
        sys.exit("could not locate iface_abstract_methods inside ISP_THRESHOLDS; "
                 "config.py is unchanged")
    src = isp_pat.sub(rf'\g<1>{isp_new["iface_abstract_methods"]}', src, count=1)
    cfg.write_text(src, encoding="utf-8")

    # VALIDATE THE WRITE, AND ROLL BACK IF IT BROKE ANYTHING.
    #
    # This step edits config.py unattended, in the middle of a chain that then
    # runs for hours on its output. A regex that produced syntactically valid but
    # WRONG Python, or that clipped a neighbouring block, would not surface here;
    # it would surface as a strange number in the report, or not at all. So the
    # rewritten file is re-imported in a fresh interpreter and the values are
    # read back and compared against what was intended. Anything short of an
    # exact match restores the backup and fails loudly, because a silently wrong
    # threshold is the worst outcome available: every downstream flag, label and
    # published rate inherits it.
    import subprocess
    check = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, r'%s'); import config as C; "
         "print(C.SRP_THRESHOLDS['class_loc'], C.SRP_THRESHOLDS['class_wmc'], "
         "C.SRP_THRESHOLDS['class_methods'], C.SRP_THRESHOLDS['file_loc'], "
         "C.SRP_THRESHOLDS['func_ccn'], C.SRP_THRESHOLDS['func_params'], "
         "C.OCP_THRESHOLDS['dispatch_min_branches'], "
         "C.OCP_THRESHOLDS['value_switch_min_branches'], "
         "C.ISP_THRESHOLDS['iface_abstract_methods'])" % cfg.parent],
        capture_output=True, text=True, timeout=120)
    want = [srp_new["class_loc"], srp_new["class_wmc"], srp_new["class_methods"],
            srp_new["file_loc"], srp_new["func_ccn"], srp_new["func_params"],
            ocp_new["dispatch_min_branches"], ocp_new["value_switch_min_branches"],
            isp_new["iface_abstract_methods"]]
    got = check.stdout.split()
    if check.returncode != 0 or [str(x) for x in want] != got:
        shutil.copy2(backup, cfg)
        sys.exit(f"\nCONFIG WRITE FAILED VALIDATION, backup restored.\n"
                 f"  wanted : {want}\n  got    : {got or '(import failed)'}\n"
                 f"  stderr : {check.stderr[-600:]}\n"
                 f"Nothing downstream has run. config.py is byte-identical to "
                 f"before.")

    print(f"\nwritten to config.py and VERIFIED by re-import (backup: {backup.name})")
    print("NOW DELETE THE PARQUETS AND RE-RUN, so no flag predates its threshold.")


if __name__ == "__main__":
    main()
