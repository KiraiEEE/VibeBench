"""
Convergent validity for the Java SRP instrument (JAVA_STATUS.md 8.1).

WHAT THIS IS FOR
----------------
The strongest attack on the Java chapter is "you invented your own metrics and
never checked them against an established Java tool". This script answers it by
running three independent, published, source-only Java analysers -- CK 0.7.0,
PMD 7.7.0 and DesigniteJava 1.1.2 -- over the SAME trees our own detector sees,
and reporting how far they agree.

WHAT THIS IS NOT
----------------
It is NOT a replacement for our metrics and it is NOT a ground truth. Ours stay
PRIMARY because the three languages must remain numerically commensurable:
`metrics.py::lcom_from_access_sets` (LCOM4) and `c3_from_token_bags` are shared
verbatim across Python / TypeScript / Java. A tool's number cannot be swapped in
for one language without breaking that.

Two consequences that must travel with every figure this prints:

  * CK's `lcom` is LCOM1 and its `lcom*` is Henderson-Sellers; ours is LCOM4
    (connected components of the method/field access graph). These are DIFFERENT
    FORMULAS with different ranges. Spearman rho is therefore a RANK CORRELATE
    ONLY -- it can show the instruments order classes similarly, and it can
    never show they agree on a value. Any "agreement" language about LCOM is
    wrong by construction.
  * PMD's `GodClass` is Lanza & Marinescu's detection strategy (ATFD + WMC +
    TCC), a different rule with different thresholds from our 6-knob rubric.
    Partial agreement is the DESIGNED outcome, not a defect. We report P/R of
    our flag against PMD's as triangulation between two imperfect screens,
    with neither one privileged.

EXCLUDED, and why (keep this line, it is a recurring question):
  * JDepend and ArchUnit both operate on compiled `.class` files. Our analysis
    runs on bare `git archive` trees with no build, no classpath and no JDK
    target -- so neither tool can run on our inputs at all. Hard blocker, not a
    preference.
  * DesigniteJava 1.1.2 IS source-only and IS used here, built from GitHub
    source (the binary download is form-gated). It is the only tool in this
    script that reaches past SRP: its `Insufficient Modularization` /
    `Multifaceted Abstraction` speak to SRP, `Rebellious Hierarchy` /
    `Broken Hierarchy` to LSP, and `Cyclic-Dependent Modularization` to DIP.
    It has NO smell for OCP or ISP, and no mapping is invented for them.

SCOPE
-----
One HEAD-STATE tree per corpus repo (the `after` tree of each repo's most
recently merged PR, 27 repos). Every tool sees exactly the same files. This is
an INSTRUMENT-level study: it asks whether our metric agrees with established
tools on real corpus code, not whether any particular PR was flagged correctly.

Materialisation goes to the system temp dir, never under the workspace -- the
VS Code Java extension re-indexes any .java it finds there.

Usage:
    python vibebench/05_crosscheck/run_crosscheck_java.py [--workers N] [--limit N]
                                                          [--repo NAME] [--skip-tools]
Self-check (no corpus, no network):
    python vibebench/05_crosscheck/run_crosscheck_java.py --self-check
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))
sys.path.insert(0, str(_HERE.parents[1] / "03_principles"))
sys.path.insert(0, str(_HERE.parents[1] / "03_principles" / "java_lang"))
import config as C  # noqa: E402
import safeio  # noqa: E402  -- crash-safe writes
import gitsnap  # noqa: E402
import detectors as D  # noqa: E402
from java_bridge import analyze_tree  # noqa: E402

TOOLS_DIR = _HERE.parents[2] / "tools"
CP_CK = TOOLS_DIR / "cp_ck.txt"
CP_PMD = TOOLS_DIR / "cp_pmd.txt"
PMD_RULESET = TOOLS_DIR / "pmd_srp_ruleset.xml"
OUT_DIR = _HERE.parents[1] / "04_report"

CK_MAIN = "com.github.mauricioaniche.ck.Runner"
PMD_MAIN = "net.sourceforge.pmd.cli.PmdCli"
TOOL_TIMEOUT_S = 2400
# Each worker holds ONE tool JVM at a time; keep the heap modest enough that
# `--workers 6` does not page the machine the user is still using.
TOOL_HEAP = os.environ.get("VB_TOOL_HEAP", "2g")
# DesigniteJava needs far more than CK or PMD: at 2g it dies on every tree over
# ~4,500 files (pinot, operaton, dd-trace-java all failed), and at 5g pinot
# finishes in 74s. A bigger heap for every worker would exceed the machine, so
# Designite runs under a SEMAPHORE -- at most `DESIGNITE_CONCURRENCY` of them at
# once, while CK and PMD stay parallel at the smaller heap.
DESIGNITE_HEAP = os.environ.get("VB_DESIGNITE_HEAP", "5g")
DESIGNITE_CONCURRENCY = int(os.environ.get("VB_DESIGNITE_CONCURRENCY", "1"))
_designite_slot = threading.Semaphore(DESIGNITE_CONCURRENCY)

_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


# --------------------------------------------------------------------------
# tool runners
# --------------------------------------------------------------------------

def _classpath(p: Path) -> str:
    return p.read_text(encoding="utf-8").strip()


CK_PARTITION_FALLBACK = 100


def _ck_once(src_dir: Path, out_prefix: Path, partition: int) -> tuple[pd.DataFrame | None, str]:
    """CK 0.7.0. Runner <dir> <useJars> <maxFilesPerPartition> <varsAndFields> <prefix>.

    `useJars=false` is the whole point: CK falls back to a partial-resolution
    mode that works without a classpath, which is what makes it usable on a bare
    tree at all.
    """
    for stale in (str(out_prefix) + "class.csv", str(out_prefix) + "method.csv"):
        Path(stale).unlink(missing_ok=True)
    cmd = ["java", f"-Xmx{TOOL_HEAP}", "-cp", _classpath(CP_CK), CK_MAIN,
           str(src_dir), "false", str(partition), "false", str(out_prefix)]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=TOOL_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None, f"timeout after {TOOL_TIMEOUT_S}s"
    f = Path(str(out_prefix) + "class.csv")
    if not f.exists() or f.stat().st_size == 0:
        return None, (f"exit {p.returncode}: "
                      + p.stderr.decode("utf-8", "replace").strip().splitlines()[-1][:200]
                      if p.stderr else f"exit {p.returncode}, empty class.csv")
    try:
        return pd.read_csv(f, on_bad_lines="skip", engine="python"), ""
    except Exception as e:  # noqa: BLE001
        return None, f"csv unreadable: {e}"


def run_ck(src_dir: Path, out_prefix: Path) -> tuple[pd.DataFrame | None, str, str]:
    """-> (rows, mode, error). Whole tree first; partitioned retry on failure.

    CK embeds the Eclipse JDT compiler, and on a tree with no classpath JDT can
    die outright: Chronicle-Core kills it with
    `NullPointerException: Cannot read field "declaringClass" because "mb" is null`
    inside `ReferenceExpression.generateCode` -- a method reference whose target
    type (`MethodHandle.invokeExact`) it cannot resolve. The crash is fatal to
    the whole run and leaves a 0-byte csv, so we retry with CK's own
    `maxFilesPerPartition`, which confines each JDT session to a slice.

    WHAT THE FALLBACK COSTS, stated because it must be: partitioning reduces
    CROSS-FILE resolution, so `cbo`, `fanin`, `fanout` and `rfc` are degraded for
    that repo. Everything we actually compare on -- `wmc`, `loc`,
    `totalMethodsQty`, `lcom`, `lcom*`, `tcc` -- is computed from the class's own
    AST and is partition-invariant. Repos that needed the fallback are marked
    `ck_partitioned` in the status CSV so the choice is visible, not hidden.
    """
    rows, err = _ck_once(src_dir, out_prefix, 0)
    if rows is not None:
        return rows, "whole_tree", ""
    log(f"    CK failed on whole tree ({err}); retrying partitioned at "
        f"{CK_PARTITION_FALLBACK} files")
    rows2, err2 = _ck_once(src_dir, out_prefix, CK_PARTITION_FALLBACK)
    if rows2 is not None:
        return rows2, "partitioned", err
    return None, "failed", f"{err} | partitioned: {err2}"


def run_pmd(src_dir: Path, out_xml: Path) -> tuple[list[dict], list[str]] | None:
    """PMD 7.7.0 design rules at their DEFAULT thresholds.

    Defaults are kept on purpose: re-tuning PMD until it agreed with us would
    destroy the only thing it is being asked for, an independent opinion.
    Exit code 4 means "violations found" and 5 "recoverable errors occurred";
    both are normal and neither is a reason to discard the run.

    -> (violations, files PMD FAILED to analyse).

    The second half matters and was nearly missed. PMD emits `<error>` nodes for
    files it could not process -- Chronicle-Wire has one
    (`Disambiguation pass should resolve everything except qualified...`). A
    class in such a file yields NO violation, which is indistinguishable from
    "PMD looked and found nothing" unless the errors are read. Ignoring them
    would quietly inflate our false-positive count against every PMD rule, so
    those files are excluded from the PMD comparison instead.
    """
    cmd = ["java", f"-Xmx{TOOL_HEAP}", "-cp", _classpath(CP_PMD), PMD_MAIN, "check",
           "-d", str(src_dir), "-R", str(PMD_RULESET), "-f", "xml",
           "-r", str(out_xml), "--no-cache", "--no-progress"]
    try:
        subprocess.run(cmd, capture_output=True, timeout=TOOL_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        log(f"    PMD timeout after {TOOL_TIMEOUT_S}s")
        return None
    if not out_xml.exists():
        log("    PMD produced no report")
        return None
    try:
        root = ET.parse(out_xml).getroot()
    except Exception as e:  # noqa: BLE001
        log(f"    PMD xml unreadable: {e}")
        return None
    ns = "{http://pmd.sourceforge.net/report/2.0.0}"
    out = []
    for fnode in root.findall(f"{ns}file"):
        fname = fnode.get("name") or ""
        for v in fnode.findall(f"{ns}violation"):
            out.append({
                "abs_file": fname,
                "rule": v.get("rule"),
                "package": v.get("package") or "",
                "class": v.get("class") or "",
                "beginline": int(v.get("beginline") or 0),
            })
    errs = [e.get("filename") or "" for e in root.findall(f"{ns}error")]
    if errs:
        log(f"    PMD failed to analyse {len(errs)} file(s); excluded from the comparison")
    return out, errs


DESIGNITE_JAR = TOOLS_DIR / "DesigniteJava-1.1.2.jar"

# DesigniteJava's smell vocabulary, mapped onto the principles it can speak to.
# Only these three are claimed; Designite has no OCP or ISP smell and inventing a
# mapping for them would be worse than admitting the gap.
DESIGNITE_SRP = ["Insufficient Modularization", "Multifaceted Abstraction"]
DESIGNITE_LSP = ["Rebellious Hierarchy", "Broken Hierarchy"]
DESIGNITE_DIP = ["Cyclic-Dependent Modularization"]


def run_designite(src_dir: Path, work_dir: Path) -> list[dict] | None:
    """DesigniteJava 1.1.2 Community -> design smells per type.

    REQUIRES THE PATCHED BUILD in `tools/` (see tools/README.md). Upstream master
    ships `Constants.DEBUG = true`, which routes `main()` to
    `RegularArgumentParser` -- that parser IGNORES `-i` and `-o` entirely and
    analyses `<cwd>/src/Designite`, i.e. Designite's own source. It fails loudly
    ("Input source folder path is not valid") from any directory that has no
    `src/Designite`, and SILENTLY produces a smell report for the wrong project
    from one that does. Both build patches are recorded in tools/.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["java", f"-Xmx{DESIGNITE_HEAP}", "-jar", str(DESIGNITE_JAR),
           "-i", str(src_dir), "-o", str(work_dir)]
    with _designite_slot:
        try:
            subprocess.run(cmd, capture_output=True, timeout=TOOL_TIMEOUT_S,
                           cwd=str(work_dir))
        except subprocess.TimeoutExpired:
            log(f"    Designite timeout after {TOOL_TIMEOUT_S}s")
            return None
    f = work_dir / "designCodeSmells.csv"
    if not f.exists() or f.stat().st_size == 0:
        log("    Designite produced no designCodeSmells.csv")
        return None
    out = []
    with f.open(newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh):
            pkg = (row.get("Package Name") or "").strip()
            typ = (row.get("Type Name") or "").strip()
            if not typ:
                continue
            out.append({"fqn": f"{pkg}.{typ}" if pkg else typ,
                        "smell": (row.get("Code Smell") or "").strip()})
    return out


# --------------------------------------------------------------------------
# joining
# --------------------------------------------------------------------------

def _rel(abs_path: str, root: Path) -> str:
    try:
        return Path(abs_path).resolve().relative_to(root.resolve()).as_posix()
    except Exception:  # noqa: BLE001
        return Path(abs_path).as_posix()


def ck_key(row, root: Path) -> tuple[str, str]:
    """CK reports the fully-qualified name in `class`, but nested types come
    through as `pkg.Outer$Inner` or `pkg.Outer.Inner` depending on construct, and
    anonymous classes as `pkg.Outer$1`. Key on (file, simple name) so the join is
    robust to both; the file already disambiguates same-named types in different
    packages, which is exactly the collision that voided the tree-sitter run."""
    fq = str(row.get("class", ""))
    simple = fq.replace("$", ".").split(".")[-1]
    return (_rel(str(row.get("file", "")), root), simple)


def our_key(t: dict) -> tuple[str, str]:
    return (t["file"].replace("\\", "/"), t["id"].split(".")[-1])


# --------------------------------------------------------------------------
# per-repo work
# --------------------------------------------------------------------------

def analyse_repo(pr, work_root: Path, skip_tools: bool) -> dict:
    full = pr.full_name
    slug = full.replace("/", "__")
    # `head_outcome` records WHICH KIND of PR supplied this repository's
    # head-state tree. It is merged wherever the repository contributed a merged
    # PR, and a not-merged PR head otherwise. That distinction matters for the
    # QUALITY PROFILE (PMD rule rates, Designite smell frequencies), which is
    # meant to describe real projects: a never-integrated branch tip is not the
    # project. It matters much less for the AGREEMENT figures (kappa, rho),
    # where all that is required is that our detector and the external tools see
    # THE SAME tree, which they do either way.
    res = {"full_name": full, "pr_id": pr.pr_id, "status": "ok",
           "head_outcome": getattr(pr, "outcome", "merged"),
           "our_rows": [], "ck_rows": [], "pmd_rows": [], "dj_rows": [],
           "pmd_error_files": []}
    try:
        d = gitsnap.ensure_repo(full)
        with gitsnap.repo_lock(full):
            pair = gitsnap.delta_pair(d, pr)
        if not pair:
            res["status"] = "no_delta_pair"
            return res
        _, after_sha = pair
        files = gitsnap.src_tree(d, after_sha, C.JAVA_SOURCE_EXTENSIONS,
                                 C.JAVA_EXCLUDED_PATH_PARTS, full_name=full)
        if files is None:
            res["status"] = "archive_failed"
            return res
        if gitsnap.TOO_LARGE in files:
            res["status"] = "repo_too_large"
            return res
        if not files:
            res["status"] = "no_java"
            return res
        res["sha"] = after_sha
        res["n_files"] = len(files)

        # --- our instrument -------------------------------------------------
        facts = analyze_tree(files)
        if not facts.get("ok"):
            res["status"] = "sidecar_failed"
            return res
        ix = D.build_index(facts)
        th = C.SRP_JAVA_THRESHOLDS

        # Our LSP and DIP verdicts on the SAME tree, lifted to TYPE level so they
        # can be compared with Designite, which reports smells per type.
        lsp_bad = {p["type_id"] for p in D.override_pairs(ix)
                   if D.is_lsp_violation(p, C.LSP_JAVA_STRONG_SIGNALS)}
        mods = D.dip_modules(ix)
        cyclic_mods = {m for scc in D.dipgraph.find_cycles(mods) for m in scc}
        mod_of_type: dict[str, str] = {}
        for m in ix.modules:
            for tid in ix.by_file.get(m["file"], []):
                mod_of_type[tid] = m["name"]

        # SAME Tarjan, aggregated to PACKAGES. Our DIP unit is the compilation
        # unit (one module per file, identical to Python's and TypeScript's, and
        # not to be changed here), but DesigniteJava's
        # `Cyclic-Dependent Modularization` is a PACKAGE-level smell. Comparing
        # file cycles against package cycles measures granularity, not agreement,
        # so both lenses are reported and the difference between them IS the
        # result.
        pkg_mods: dict[str, D.dipgraph.ModuleInfo] = {}
        for name, mi in mods.items():
            pkg = name.rsplit(".", 1)[0] if "." in name else name
            cur = pkg_mods.get(pkg)
            if cur is None:
                cur = pkg_mods[pkg] = D.dipgraph.ModuleInfo(
                    name=pkg, n_classes=0, n_abstract_classes=0,
                    imports=set(), parse_ok=True)
            cur.n_classes += mi.n_classes
            cur.n_abstract_classes += mi.n_abstract_classes
            cur.imports |= {t.rsplit(".", 1)[0] if "." in t else t for t in mi.imports}
        for pkg, mi in pkg_mods.items():
            mi.imports.discard(pkg)          # a package importing itself is not a cycle
        cyclic_pkgs = {p for scc in D.dipgraph.find_cycles(pkg_mods) for p in scc}

        for t in ix.types.values():
            mv = D.srp_metrics(t)
            risk, strict, hits, supp = D.srp_risk(mv, th)
            res["our_rows"].append({
                "full_name": full, "type_id": t["id"], "file_path": t["file"],
                "simple_name": t["id"].split(".")[-1], "kind": t["kind"],
                "is_nested": bool(t.get("isNested")), "is_abstract": bool(t["isAbstract"]),
                "is_test": C.is_test_path(t["file"], "java"),
                "class_loc": mv["class_loc"], "method_count": mv["method_count"],
                "wmc": mv["wmc"], "max_method_ccn": mv["max_method_ccn"],
                "max_params": mv["max_params"], "lcom": mv["lcom"], "c3": mv["c3"],
                "srp_risk": risk, "srp_risk_strict": strict, "hits": ";".join(hits),
                "lsp_violating": t["id"] in lsp_bad,
                "dip_in_cycle": mod_of_type.get(t["id"]) in cyclic_mods,
                "dip_in_pkg_cycle": (mod_of_type.get(t["id"], "").rsplit(".", 1)[0]
                                     in cyclic_pkgs),
            })
        if skip_tools:
            return res

        # --- materialise once, share the directory between CK and PMD --------
        tree_dir = work_root / slug / "src"
        out_dir = work_root / slug / "out"
        shutil.rmtree(work_root / slug, ignore_errors=True)
        tree_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        written = 0
        for rel, src in files.items():
            safe = Path(str(rel).replace("\\", "/").lstrip("/"))
            if ".." in safe.parts:
                continue
            try:
                f = tree_dir / safe
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(src, encoding="utf-8", errors="replace")
                written += 1
            except OSError:
                continue
        res["n_materialised"] = written

        ck, ck_mode, ck_err = run_ck(tree_dir, out_dir / "ck_")
        res["ck_mode"] = ck_mode
        res["ck_error"] = ck_err
        if ck is not None:
            for _, r in ck.iterrows():
                fp, simple = ck_key(r, tree_dir)
                res["ck_rows"].append({
                    "full_name": full, "file_path": fp, "simple_name": simple,
                    "ck_class": r.get("class"), "ck_type": r.get("type"),
                    "ck_wmc": r.get("wmc"), "ck_lcom": r.get("lcom"),
                    "ck_lcom_star": r.get("lcom*"), "ck_tcc": r.get("tcc"),
                    "ck_cbo": r.get("cbo"), "ck_rfc": r.get("rfc"),
                    "ck_loc": r.get("loc"), "ck_dit": r.get("dit"),
                    "ck_methods": r.get("totalMethodsQty"), "ck_fields": r.get("totalFieldsQty"),
                })
        else:
            res["status"] = "ck_failed"

        pmd_res = run_pmd(tree_dir, out_dir / "pmd.xml")
        if pmd_res is not None:
            pmd, pmd_errs = pmd_res
            res["pmd_error_files"] = sorted({_rel(e, tree_dir) for e in pmd_errs})
            res["n_pmd_errors"] = len(res["pmd_error_files"])
            for v in pmd:
                res["pmd_rows"].append({
                    "full_name": full,
                    "file_path": _rel(v["abs_file"], tree_dir),
                    "simple_name": v["class"].replace("$", ".").split(".")[-1],
                    "rule": v["rule"], "beginline": v["beginline"],
                })
        else:
            res["status"] = "pmd_failed" if res["status"] == "ok" else res["status"]
            res["pmd_error_files"] = []

        dj = run_designite(tree_dir, out_dir / "designite")
        if dj is not None:
            for v in dj:
                res["dj_rows"].append({"full_name": full, "fqn": v["fqn"],
                                       "smell": v["smell"]})
        else:
            res["status"] = "designite_failed" if res["status"] == "ok" else res["status"]
        shutil.rmtree(work_root / slug, ignore_errors=True)
    except Exception as e:  # noqa: BLE001
        res["status"] = f"error: {type(e).__name__}: {e}"
    return res


# --------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------

def spearman(a: pd.Series, b: pd.Series) -> tuple[float | None, int]:
    """Spearman rho via pandas' own rank+Pearson -- no scipy dependency, and the
    pipeline has none."""
    df = pd.DataFrame({"a": pd.to_numeric(a, errors="coerce"),
                       "b": pd.to_numeric(b, errors="coerce")}).dropna()
    if len(df) < 3 or df["a"].nunique() < 2 or df["b"].nunique() < 2:
        return None, len(df)
    return float(df["a"].corr(df["b"], method="spearman")), len(df)


def prf(pred: pd.Series, ref: pd.Series) -> dict:
    pred, ref = pred.astype(bool), ref.astype(bool)
    tp = int((pred & ref).sum())
    fp = int((pred & ~ref).sum())
    fn = int((~pred & ref).sum())
    tn = int((~pred & ~ref).sum())
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f1 = 2 * p * r / (p + r) if p and r else None
    n = tp + fp + fn + tn
    # Cohen's kappa: agreement corrected for what chance alone would produce.
    # Two rare-positive screens can look agreeable on raw accuracy alone.
    po = (tp + tn) / n if n else 0.0
    pe = (((tp + fp) * (tp + fn) + (fn + tn) * (fp + tn)) / (n * n)) if n else 0.0
    kappa = (po - pe) / (1 - pe) if pe < 1 else None
    return {"n": n, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": p, "recall": r, "f1": f1, "kappa": kappa}


def decile_overlap(df: pd.DataFrame, col: str, flag_col: str = "srp_risk") -> dict:
    """Top-decile overlap: does the tool's own top 10% by `col` contain the
    classes we flag? Reported as recall-of-our-flags inside the decile and as
    Jaccard, because the two sets are different sizes by construction."""
    s = pd.to_numeric(df[col], errors="coerce")
    sub = df[s.notna()]
    if len(sub) < 20:
        return {"n": len(sub), "overlap": None}
    s = pd.to_numeric(sub[col], errors="coerce")
    cut = s.quantile(0.90)
    top = s >= cut
    ours = sub[flag_col].astype(bool)
    inter = int((top & ours).sum())
    union = int((top | ours).sum())
    return {
        "n": int(len(sub)),
        "n_tool_decile": int(top.sum()),
        "n_ours": int(ours.sum()),
        "intersection": inter,
        "share_of_ours_in_tool_decile": inter / int(ours.sum()) if ours.sum() else None,
        "share_of_tool_decile_flagged_by_us": inter / int(top.sum()) if top.sum() else None,
        "jaccard": inter / union if union else None,
    }


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

CK_COLS = ["ck_class", "ck_type", "ck_wmc", "ck_lcom", "ck_lcom_star", "ck_tcc",
           "ck_cbo", "ck_rfc", "ck_loc", "ck_dit", "ck_methods", "ck_fields"]


def tri_tool(joined: pd.DataFrame) -> dict | None:
    """THE argument this whole script exists to make.

    Without ground truth, "our screen agrees with PMD at kappa 0.59" means
    nothing on its own -- 0.59 could be terrible. The interpretable quantity is
    how well the two ESTABLISHED tools agree WITH EACH OTHER on the same classes.
    If PMD and Designite agree with one another no better than either agrees with
    us, then our screen is not an outlier: it sits inside the spread that
    published Java god-class detectors already exhibit among themselves.

    Reported symmetrically, with no tool privileged as the reference.
    """
    need = {"GodClass", "Insufficient Modularization", "Multifaceted Abstraction",
            "srp_risk", "is_test"}
    if not need <= set(joined.columns):
        return None
    df = joined[~joined["is_test"].astype(bool)].copy()
    for c in ("GodClass", "Insufficient Modularization", "Multifaceted Abstraction"):
        df[c] = df[c].fillna(False).astype(bool)
    df["dj_srp"] = df["Insufficient Modularization"] | df["Multifaceted Abstraction"]
    fired = df[["srp_risk", "GodClass", "dj_srp"]].astype(int).sum(axis=1)
    corrob = df["srp_risk"] & (df["GodClass"] | df["dj_srp"])
    return {
        "population": "production",
        "n": int(len(df)),
        "flag_counts": {"ours": int(df["srp_risk"].sum()),
                        "pmd_godclass": int(df["GodClass"].sum()),
                        "designite_srp": int(df["dj_srp"].sum())},
        "flag_rates": {"ours": float(df["srp_risk"].mean()),
                       "pmd_godclass": float(df["GodClass"].mean()),
                       "designite_srp": float(df["dj_srp"].mean())},
        "pairwise": {
            "ours~pmd_godclass": prf(df["srp_risk"], df["GodClass"]),
            "ours~designite_srp": prf(df["srp_risk"], df["dj_srp"]),
            "pmd_godclass~designite_srp": prf(df["GodClass"], df["dj_srp"]),
        },
        "n_screens_firing": {str(k): int(v) for k, v in
                             fired.value_counts().sort_index().items()},
        "our_flags_corroborated_by_at_least_one": int(corrob.sum()),
        "our_flags_corroborated_by_both":
            int((df["srp_risk"] & df["GodClass"] & df["dj_srp"]).sum()),
    }


def build_report(our: pd.DataFrame, ck: pd.DataFrame, pmd: pd.DataFrame,
                 dj: pd.DataFrame | None = None,
                 pmd_bad: set | None = None) -> dict:
    if ck.empty:
        # A single-repo debug run can legitimately produce no CK rows; the report
        # must still come out rather than dying in a merge on missing columns.
        ck = pd.DataFrame(columns=["full_name", "file_path", "simple_name"] + CK_COLS)
    joined = our.merge(ck, on=["full_name", "file_path", "simple_name"],
                       how="left", suffixes=("", "_ck"))
    joined = joined.drop_duplicates(subset=["full_name", "file_path", "simple_name"])

    if len(pmd):
        wide = (pmd.assign(v=True)
                   .pivot_table(index=["full_name", "file_path", "simple_name"],
                                columns="rule", values="v", aggfunc="any")
                   .fillna(False).reset_index())
        joined = joined.merge(wide, on=["full_name", "file_path", "simple_name"], how="left")
    for rule in ("GodClass", "TooManyMethods", "NcssCount", "CouplingBetweenObjects",
                 "CyclomaticComplexity", "ExcessivePublicCount", "TooManyFields"):
        if rule not in joined.columns:
            joined[rule] = False
        joined[rule] = joined[rule].fillna(False).astype(bool)

    # A file PMD could not parse yields no violation, which is indistinguishable
    # from "clean" unless the <error> nodes are read. Those classes are dropped
    # from the PMD comparison rather than counted as PMD-says-no.
    bad = pmd_bad or set()
    joined["pmd_analysed"] = ~joined.apply(
        lambda r: (r["full_name"], r["file_path"]) in bad, axis=1) if bad else True

    rep = {"n_our_types": int(len(our)), "n_ck_rows": int(len(ck)),
           "n_pmd_unanalysed_files": len(bad),
           "n_types_dropped_from_pmd": int((~joined["pmd_analysed"]).sum()),
           "n_pmd_violations": int(len(pmd)),
           "n_joined_ck": int(joined["ck_wmc"].notna().sum()),
           "ck_join_rate": float(joined["ck_wmc"].notna().mean()) if len(joined) else None,
           "populations": {}}

    pops = {"all": joined, "production": joined[~joined["is_test"].astype(bool)]}
    for name, df in pops.items():
        if not len(df):
            continue
        block = {"n": int(len(df)), "n_our_flags": int(df["srp_risk"].sum())}
        pairs = [("wmc", "ck_wmc"), ("class_loc", "ck_loc"), ("method_count", "ck_methods"),
                 ("lcom", "ck_lcom"), ("lcom", "ck_lcom_star"), ("lcom", "ck_tcc"),
                 ("c3", "ck_tcc"), ("max_method_ccn", "ck_rfc")]
        block["spearman"] = {}
        for ours_col, ck_col in pairs:
            rho, n = spearman(df[ours_col], df[ck_col])
            block["spearman"][f"{ours_col}~{ck_col}"] = {"rho": rho, "n": n}

        # CONTROL: CK's OWN two LCOM variants against each other, and against its
        # own TCC. If LCOM1 and Henderson-Sellers disagree INSIDE one tool, then
        # our LCOM4 disagreeing with them is a property of the LCOM family, not
        # evidence that our implementation is wrong. This is the control that
        # turns "your LCOM does not match CK's" from an accusation into a
        # measured fact about the metric literature.
        block["ck_internal_control"] = {}
        for x, y in (("ck_lcom", "ck_lcom_star"), ("ck_lcom", "ck_tcc"),
                     ("ck_lcom_star", "ck_tcc"), ("ck_wmc", "ck_loc")):
            rho, n = spearman(df[x], df[y])
            block["ck_internal_control"][f"{x}~{y}"] = {"rho": rho, "n": n}
        block["decile_overlap"] = {c: decile_overlap(df, c)
                                   for c in ("ck_wmc", "ck_loc", "ck_lcom_star", "ck_cbo")}
        pdf = df[df["pmd_analysed"].astype(bool)]
        block["n_pmd_comparable"] = int(len(pdf))
        block["pmd"] = {rule: prf(pdf["srp_risk"], pdf[rule])
                        for rule in ("GodClass", "TooManyMethods", "NcssCount",
                                     "CouplingBetweenObjects")}
        any_two = (pdf[["GodClass", "TooManyMethods", "NcssCount",
                        "CouplingBetweenObjects", "CyclomaticComplexity",
                        "ExcessivePublicCount", "TooManyFields"]].sum(axis=1) >= 2)
        block["pmd"]["any_two_design_rules"] = prf(pdf["srp_risk"], any_two)
        rep["populations"][name] = block

    # ---- DesigniteJava: the only tool here that speaks to more than SRP ------
    # It is joined on the fully-qualified type name rather than (file, simple
    # name), because Designite reports package + type and never a path.
    if dj is not None and len(dj):
        smells = sorted(set(dj["smell"]))
        wide = (dj.assign(v=True)
                  .pivot_table(index=["full_name", "fqn"], columns="smell",
                               values="v", aggfunc="any")
                  .fillna(False).reset_index())
        wide["dj_seen"] = True
        j2 = joined.merge(wide, left_on=["full_name", "type_id"],
                          right_on=["full_name", "fqn"], how="left")
        for s in smells:
            j2[s] = j2[s].fillna(False).astype(bool)
        # A type Designite never names is a type it found NO smell in, but only
        # if Designite actually analysed that repo. Restrict to repos where it
        # returned rows, so a tool failure is never read as "clean".
        dj_repos = set(dj["full_name"])
        j2 = j2[j2["full_name"].isin(dj_repos)]
        rep["designite"] = {"n_smell_rows": int(len(dj)),
                            "n_repos": len(dj_repos),
                            "smells_seen": smells,
                            "n_types_compared": int(len(j2)),
                            "populations": {}}
        for name, mask in (("all", pd.Series(True, index=j2.index)),
                           ("production", ~j2["is_test"].astype(bool))):
            sub = j2[mask]
            if not len(sub):
                continue
            def _any(cols):
                cols = [c for c in cols if c in sub.columns]
                return sub[cols].any(axis=1) if cols else pd.Series(False, index=sub.index)
            rep["designite"]["populations"][name] = {
                "n": int(len(sub)),
                "SRP: ours vs Insufficient Modularization|Multifaceted Abstraction":
                    prf(sub["srp_risk"], _any(DESIGNITE_SRP)),
                "LSP: ours vs Rebellious|Broken Hierarchy":
                    prf(sub["lsp_violating"], _any(DESIGNITE_LSP)),
                "LSP: ours vs Rebellious Hierarchy only":
                    prf(sub["lsp_violating"], _any(["Rebellious Hierarchy"])),
                "DIP file-level cycles vs Cyclic-Dependent Modularization":
                    prf(sub["dip_in_cycle"], _any(DESIGNITE_DIP)),
                "DIP PACKAGE-level cycles vs Cyclic-Dependent Modularization":
                    prf(sub["dip_in_pkg_cycle"], _any(DESIGNITE_DIP)),
            }
        joined = j2

    rep["tri_tool"] = tri_tool(joined)

    # PER-REPO, so nobody can say the pooled rho is carried by one large repo.
    # Same reason the whole project leads with macro-per-repo rates.
    prod = pops["production"]
    per_repo = []
    for repo, g in prod.groupby("full_name"):
        rho_wmc, n = spearman(g["wmc"], g["ck_wmc"])
        rho_loc, _ = spearman(g["class_loc"], g["ck_loc"])
        rho_lcom, nl = spearman(g["lcom"], g["ck_lcom_star"])
        gc = prf(g["srp_risk"], g["GodClass"])
        per_repo.append({"full_name": repo, "n": int(len(g)), "n_joined": n,
                         "rho_wmc": rho_wmc, "rho_loc": rho_loc,
                         "rho_lcom_vs_lcomstar": rho_lcom, "n_lcom": nl,
                         "our_flags": int(g["srp_risk"].sum()),
                         "pmd_godclass": int(g["GodClass"].sum()),
                         "godclass_P": gc["precision"], "godclass_R": gc["recall"]})
    rep["per_repo_production"] = per_repo

    # ---------------------------------------------------------------- profile
    # CODE-QUALITY PROFILE (added 2026-08-19). Everything below was already
    # being COMPUTED by the same three tool invocations and then discarded:
    # only PMD's GodClass and the five SOLID-mapped Designite smells were ever
    # reported, out of 7 PMD design rules and Designite's full vocabulary.
    #
    # These are NOT SOLID measurements and are not presented as such -- no
    # published source-only Java tool implements an OCP or ISP check, and
    # inventing a mapping for one is exactly the error this file refuses to make
    # elsewhere. They are established, citable descriptors of the code agents
    # touched (coupling, inheritance depth, cohesion, size), which is worth
    # having beside the SOLID result at literally zero extra tool runtime.
    prod = joined[~joined["is_test"].astype(bool)]
    profile: dict = {"n_production_types": int(len(prod))}
    if len(prod):
        profile["pmd_design_rules"] = {
            rule: {"n": int(prod[rule].sum()),
                   "rate": round(float(prod[rule].mean()), 4)}
            for rule in ("GodClass", "TooManyMethods", "NcssCount",
                         "CouplingBetweenObjects", "CyclomaticComplexity",
                         "ExcessivePublicCount", "TooManyFields")
            if rule in prod.columns
        }
        ck_metrics = {}
        for col, label in (("ck_cbo", "coupling_between_objects"),
                           ("ck_rfc", "response_for_class"),
                           ("ck_dit", "depth_inheritance_tree"),
                           ("ck_tcc", "tight_class_cohesion"),
                           ("ck_lcom_star", "lcom_henderson_sellers"),
                           ("ck_wmc", "weighted_methods"),
                           ("ck_loc", "lines_of_code")):
            if col in prod.columns and prod[col].notna().any():
                s = prod[col].dropna()
                ck_metrics[label] = {"median": float(s.median()),
                                     "p90": float(s.quantile(.90)),
                                     "n": int(len(s))}
        profile["ck_metrics_production"] = ck_metrics
    # Designite's WHOLE smell vocabulary, counted from the raw per-type frame.
    # Reported as observed frequencies, with no principle attached to any smell
    # that Designite does not itself name.
    if dj is not None and len(dj) and "smell" in dj.columns:
        vc = dj["smell"].value_counts()
        profile["designite_all_smells"] = {str(k): int(v) for k, v in vc.items()}
        profile["designite_note"] = (
            "Frequencies only. Designite has no OCP or ISP smell; the five "
            "mapped to SRP/LSP/DIP are listed in DESIGNITE_SRP/LSP/DIP.")
    rep["quality_profile"] = profile
    return rep, joined


def print_report(rep: dict) -> None:
    def f(x, nd=3):
        return "n/a" if x is None else f"{x:.{nd}f}"

    print("\n" + "=" * 78)
    print("CONVERGENT VALIDITY -- Java SRP instrument vs CK 0.7.0 and PMD 7.7.0")
    print("=" * 78)
    print(f"our types            : {rep['n_our_types']}")
    print(f"CK class rows        : {rep['n_ck_rows']}")
    print(f"PMD violations       : {rep['n_pmd_violations']}")
    print(f"joined to CK         : {rep['n_joined_ck']} ({f(rep['ck_join_rate'])} of our types)")
    print(f"PMD unanalysed files : {rep['n_pmd_unanalysed_files']} "
          f"-> {rep['n_types_dropped_from_pmd']} types EXCLUDED from the PMD comparison")

    for name, b in rep["populations"].items():
        print(f"\n--- {name.upper()}  (n={b['n']}, our SRP flags={b['n_our_flags']}) ---")
        print("  Spearman rho (RANK correlation -- see the LCOM caveat):")
        for k, v in b["spearman"].items():
            print(f"    {k:28s} rho={f(v['rho'])}  n={v['n']}")
        print("  CONTROL -- CK's own metrics against each other:")
        for k, v in b.get("ck_internal_control", {}).items():
            print(f"    {k:28s} rho={f(v['rho'])}  n={v['n']}")
        print("  Top-decile overlap with our srp_risk:")
        for k, v in b["decile_overlap"].items():
            if v.get("overlap", 1) is None:
                print(f"    {k:16s} too few rows")
                continue
            print(f"    {k:16s} tool_decile={v['n_tool_decile']:5d} ours={v['n_ours']:5d} "
                  f"inter={v['intersection']:5d}  ours-in-decile={f(v['share_of_ours_in_tool_decile'])}"
                  f"  jaccard={f(v['jaccard'])}")
        print(f"  Our srp_risk scored AGAINST each PMD rule "
              f"(n={b.get('n_pmd_comparable')}; neither is ground truth):")
        for rule, m in b["pmd"].items():
            print(f"    {rule:24s} tp={m['tp']:4d} fp={m['fp']:5d} fn={m['fn']:4d} "
                  f"P={f(m['precision'])} R={f(m['recall'])} F1={f(m['f1'])} kappa={f(m['kappa'])}")

    if rep.get("tri_tool"):
        t = rep["tri_tool"]
        print("\n" + "=" * 78)
        print("TRIANGULATION -- how the three screens agree WITH EACH OTHER "
              f"(production, n={t['n']})")
        print("=" * 78)
        print("  flags:  " + "  ".join(
            f"{k}={v} ({t['flag_rates'][k] * 100:.1f}%)" for k, v in t["flag_counts"].items()))
        for k, m in t["pairwise"].items():
            print(f"    {k:30s} P={f(m['precision'])} R={f(m['recall'])} "
                  f"F1={f(m['f1'])} kappa={f(m['kappa'])}")
        print(f"  screens firing per class: {t['n_screens_firing']}")
        print(f"  our flags corroborated by >=1 other tool: "
              f"{t['our_flags_corroborated_by_at_least_one']} / {t['flag_counts']['ours']}"
              f"  (by BOTH: {t['our_flags_corroborated_by_both']})")

    if rep.get("designite"):
        d = rep["designite"]
        print("\n" + "=" * 78)
        print("DESIGNITEJAVA 1.1.2 -- the only cross-check that reaches beyond SRP")
        print("=" * 78)
        print(f"  {d['n_smell_rows']} smell rows over {d['n_repos']} repos; "
              f"{d['n_types_compared']} types compared")
        print(f"  smells seen: {', '.join(d['smells_seen'])}")
        print("  (Designite has NO OCP or ISP smell -- that gap is stated, not papered over)")
        for name, block in d["populations"].items():
            print(f"\n  --- {name.upper()} (n={block['n']}) ---")
            for k, m in block.items():
                if k == "n":
                    continue
                print(f"    {k}")
                print(f"      tp={m['tp']:5d} fp={m['fp']:6d} fn={m['fn']:5d} "
                      f"P={f(m['precision'])} R={f(m['recall'])} F1={f(m['f1'])} "
                      f"kappa={f(m['kappa'])}")

    if rep.get("per_repo_production"):
        print("\n--- PER REPO (production only) -- the pooled rho is not one big repo ---")
        pr_df = pd.DataFrame(rep["per_repo_production"]).sort_values("n", ascending=False)
        print(pr_df.to_string(index=False, float_format=lambda v: f"{v:.3f}"))


# --------------------------------------------------------------------------

def _self_check() -> None:
    print("crosscheck self-check")
    m = prf(pd.Series([True, True, False, False]), pd.Series([True, False, True, False]))
    assert m["tp"] == 1 and m["fp"] == 1 and m["fn"] == 1, m
    assert abs(m["precision"] - 0.5) < 1e-9 and abs(m["recall"] - 0.5) < 1e-9
    assert abs(m["kappa"]) < 1e-9, m["kappa"]
    print("  prf + kappa ok (kappa=0 on chance-level agreement)")

    rho, n = spearman(pd.Series([1, 2, 3, 4, 5]), pd.Series([2, 4, 6, 8, 10]))
    assert rho is not None and abs(rho - 1.0) < 1e-9, rho
    rho2, _ = spearman(pd.Series([1, 2, 3, 4, 5]), pd.Series([5, 4, 3, 2, 1]))
    assert abs(rho2 + 1.0) < 1e-9, rho2
    # monotone-but-nonlinear: Spearman must see it where Pearson would not
    rho3, _ = spearman(pd.Series([1, 2, 3, 4, 5]), pd.Series([1, 4, 9, 16, 25]))
    assert abs(rho3 - 1.0) < 1e-9, rho3
    print("  spearman ok (+1, -1, monotone-nonlinear = +1)")

    df = pd.DataFrame({"ck_wmc": list(range(100)),
                       "srp_risk": [i >= 95 for i in range(100)]})
    d = decile_overlap(df, "ck_wmc")
    assert d["n_tool_decile"] == 10 and d["intersection"] == 5, d
    assert abs(d["share_of_ours_in_tool_decile"] - 1.0) < 1e-9, d
    print("  decile_overlap ok (our 5 flags all inside the tool's top decile)")

    assert CP_CK.exists(), f"missing {CP_CK}"
    assert CP_PMD.exists(), f"missing {CP_PMD} -- run the mvn dependency:build-classpath step"
    assert PMD_RULESET.exists(), f"missing {PMD_RULESET}"
    assert DESIGNITE_JAR.exists(), f"missing {DESIGNITE_JAR} -- see tools/README.md"
    print("  CK classpath, PMD classpath, PMD ruleset and Designite jar all present")
    print("ALL PASS")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=None,
                    help="alphabetical head; biased, use --sample instead")
    ap.add_argument("--sample", type=int, default=None,
                    help="seeded random sample of N repositories (unbiased, unlike --limit)")
    ap.add_argument("--repo", default=None)
    ap.add_argument("--skip-tools", action="store_true",
                    help="compute our own metrics only (fast sanity pass)")
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--rebuild", action="store_true",
                    help="recompute the triangulation block from the saved "
                         "java_crosscheck_classes.csv, without re-running the tools")
    a = ap.parse_args()
    if a.self_check:
        _self_check()
        return
    if a.rebuild:
        joined = pd.read_csv(OUT_DIR / "java_crosscheck_classes.csv", low_memory=False)
        block = tri_tool(joined)
        if block is None:
            print("classes csv lacks the tool columns -- run the full pass first")
            return
        sj = OUT_DIR / "java_crosscheck_summary.json"
        rep = json.loads(sj.read_text(encoding="utf-8")) if sj.exists() else {}
        rep["tri_tool"] = block
        safeio.write_text(sj, json.dumps(rep, indent=2, default=str))
        print_report(rep)
        print(f"\nupdated {sj}")
        return

    gs = pd.read_parquet(C.GOLD_SET_JAVA)
    # HEAD-STATE TREE SELECTION, corrected for V2 (2026-08-25).
    #
    # This used to be `sort_values("merged_at").groupby("full_name").tail(1)`,
    # which was right for a merged-only corpus and is WRONG now. A not-merged PR
    # has `merged_at` NaN, pandas sorts NaN LAST, and `tail(1)` therefore picked
    # a REJECTED PR as the repository's "head state" wherever one existed. A
    # rejected PR's head is a branch that was never integrated, so the external
    # tools would have been run against code that is not in the project at all,
    # and every kappa would have been computed against the wrong tree.
    #
    # Correct order: prefer a MERGED PR (the closest available thing to the
    # repository's mainline), latest first; fall back to the most recent PR of
    # any outcome only for repositories that contributed no merged PR.
    gs = gs.copy()
    gs["_is_merged"] = gs["outcome"].eq("merged") if "outcome" in gs.columns \
        else gs["merged_at"].notna()
    gs["_when"] = pd.to_datetime(
        gs["merged_at"].fillna(gs.get("created_at")), errors="coerce", utc=True)
    head = (gs.sort_values(["_is_merged", "_when"], na_position="first")
              .groupby("full_name").tail(1))
    n_nonmerged = int((~head["_is_merged"]).sum())
    if n_nonmerged:
        print(f"note: {n_nonmerged} of {len(head)} repos contributed no merged PR; "
              f"their head-state tree comes from a not-merged PR head and is "
              f"marked as such", flush=True)
    if a.repo:
        head = head[head["full_name"] == a.repo]
    head = head.sort_values("full_name")
    if a.sample and a.sample < len(head):
        # Seeded so the drawn set is reproducible from the seed alone.
        head = head.sample(n=a.sample, random_state=C.RANDOM_SEED).sort_values("full_name")
        print(f"seeded random sample: {len(head)} of the corpus's repositories "
              f"(seed {C.RANDOM_SEED})", flush=True)
    elif a.limit:
        head = head.head(a.limit)
    print(f"head-state trees: {len(head)} repos", flush=True)

    # NEVER under the workspace: the VS Code Java extension indexes any .java it
    # finds there and the corpus trees cost ~1.6 GB of index.
    work_root = Path(tempfile.mkdtemp(prefix="vbxcheck_"))
    print(f"work dir: {work_root}", flush=True)
    our, ck, pmd, dj, statuses = [], [], [], [], []
    pmd_bad: set[tuple[str, str]] = set()
    try:
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            futs = {ex.submit(analyse_repo, pr, work_root, a.skip_tools): pr.full_name
                    for pr in head.itertuples()}
            done = 0
            for fu in as_completed(futs):
                r = fu.result()
                done += 1
                our += r["our_rows"]
                ck += r["ck_rows"]
                pmd += r["pmd_rows"]
                dj += r["dj_rows"]
                pmd_bad |= {(r["full_name"], f) for f in (r.get("pmd_error_files") or [])}
                statuses.append({"full_name": r["full_name"], "status": r["status"],
                                 "head_outcome": r.get("head_outcome"),
                                 "n_files": r.get("n_files"), "sha": r.get("sha"),
                                 "n_types": len(r["our_rows"]), "n_ck": len(r["ck_rows"]),
                                 "n_pmd": len(r["pmd_rows"]), "n_dj": len(r["dj_rows"]),
                                 "n_pmd_error_files": len(r.get("pmd_error_files") or []),
                                 "ck_mode": r.get("ck_mode"), "ck_error": r.get("ck_error")})
                log(f"[{done}/{len(futs)}] {r['full_name']:38s} {r['status']:16s} "
                    f"files={r.get('n_files')} types={len(r['our_rows'])} "
                    f"ck={len(r['ck_rows'])}({r.get('ck_mode')}) pmd={len(r['pmd_rows'])} "
                    f"dj={len(r['dj_rows'])}")
    finally:
        shutil.rmtree(work_root, ignore_errors=True)

    our_df = pd.DataFrame(our)
    ck_df = pd.DataFrame(ck)
    pmd_df = pd.DataFrame(pmd)
    dj_df = pd.DataFrame(dj)
    st_df = pd.DataFrame(statuses)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    safeio.to_csv(st_df, OUT_DIR / "java_crosscheck_status.csv", index=False)
    print("\nper-repo status:")
    print(st_df.sort_values("full_name").to_string(index=False))
    if our_df.empty:
        print("no rows -- nothing to report")
        return

    rep, joined = build_report(our_df, ck_df, pmd_df, dj_df, pmd_bad)
    safeio.to_csv(joined, OUT_DIR / "java_crosscheck_classes.csv", index=False)
    safeio.write_text(OUT_DIR / "java_crosscheck_summary.json",
                      json.dumps(rep, indent=2, default=str))
    print_report(rep)
    print(f"\nwrote {OUT_DIR / 'java_crosscheck_classes.csv'}")
    print(f"wrote {OUT_DIR / 'java_crosscheck_summary.json'}")


if __name__ == "__main__":
    main()
