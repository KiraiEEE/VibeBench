"""
!! SUPERSEDED AND VOID -- DO NOT RUN, DO NOT QUOTE ITS OUTPUT (2026-08-03) !!

This module is part of the tree-sitter Java layer that produced the VOID Java
results. Kept only as a record of what was tried and why it failed; see
JAVA_STATUS.md sections 2 and 6 for the measured defects.

The replacement is:
  03_principles/java_sidecar/Facts.java   -- JavaParser + SymbolSolver extractor
  03_principles/java_bridge.py            -- Python bridge + facts cache
  03_principles/java_lang/detectors.py    -- all five principles, self-checked
  03_principles/java_lang/run_java.py     -- one-pass delta harness

Why this one had to go, in one line: tree-sitter parses Java but cannot RESOLVE
symbols, so two classes named `Node` in different packages collapsed into one
and every type hierarchy silently corrupted -- which is why ISP found nothing
and LSP found seven override pairs in 867 pull requests.
"""

"""
Step 3-SRP-JAVA - Single Responsibility evaluator over the Java corpus.

Scope note: this is a WITHIN-AGENT study. Only OpenAI_Codex has usable Java in
AIDev (the other four agents have 1, 2, 2 and 0 eligible PRs), so no agent
comparison is made or implied. The question is "does Codex respect SRP when it
writes Java?", and Java is the fairest place to ask it -- classes are mandatory,
so a class-scoped proxy has full surface, unlike TypeScript where 87% of agent
files contain no class at all.

Facts come from `java_lang/javafacts.py` (tree-sitter). Every METRIC is the
already-validated Python code: `metrics.py::c3_from_token_bags` and
`metrics.py::lcom_from_access_sets`, plus the same 7-metric rubric shape.

CALIBRATION MODE when `config.SRP_JAVA_THRESHOLDS is None`: emits every class
with null srp_risk and prints the metric percentiles, so thresholds are chosen
from Java's own distribution BEFORE any labelling. This is the non-circular
method used for OCP and for the TypeScript port; Python's numbers are NOT
copied, because Java classes have different size characteristics.

Run:  python vibebench/03_principles/srp_java/run_srp_java.py [--limit N]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))                  # 03_principles
sys.path.insert(0, str(_HERE.parents[1] / "srp"))          # validated cores
sys.path.insert(0, str(_HERE.parents[1] / "java_lang"))    # fact extractor
sys.path.insert(0, str(_HERE.parents[2]))                  # vibebench
import config as C  # noqa: E402
import javafacts  # noqa: E402
from metrics import c3_from_token_bags, lcom_from_access_sets  # noqa: E402


def class_rows(pr, path: str, source: str) -> list[dict]:
    """One row per type declaration, plus one file-level row."""
    f = javafacts.analyze_source(path, source)
    meta = {"pr_id": pr.pr_id, "agent": pr.agent, "full_name": pr.full_name,
            "pr_number": pr.pr_number, "file_path": path}
    file_loc = source.count("\n") + 1
    all_ccn = [m.ccn for t in f.types for m in t.methods]
    all_par = [m.params for t in f.types for m in t.methods]
    rows = [{**meta, "class_name": None, "parse_ok": f.parse_ok,
             "parse_error": f.parse_error, "file_loc": file_loc,
             "max_func_ccn": max(all_ccn) if all_ccn else 0,
             "max_func_params": max(all_par) if all_par else 0,
             "n_types": len(f.types), "package": f.package}]

    for t in f.types:
        # Constructors are excluded from cohesion exactly as in Python and TS:
        # they touch every field by construction and would flatten LCOM.
        ms = [m for m in t.methods if not m.is_constructor]
        rows.append({
            **meta,
            "class_name": t.name,
            "parse_ok": f.parse_ok,
            "parse_error": f.parse_error,
            "package": f.package,
            "kind": t.kind,
            "class_lineno": t.lineno,
            "class_loc": t.loc,
            "is_abstract": t.is_abstract,
            "method_count": len(ms),
            "wmc": sum(m.ccn for m in ms),
            "max_method_ccn": max((m.ccn for m in ms), default=0),
            "max_class_params": max((m.params for m in ms), default=0),
            "n_fields": len(t.fields),
            "lcom": lcom_from_access_sets(
                [m.name for m in ms],
                {m.name: m.fields for m in ms},
                {m.name: m.calls for m in ms}),
            "c3": c3_from_token_bags([m.tokens for m in ms]),
            "file_loc": file_loc,
        })
    return rows


def srp_hits(row: dict, thr: dict) -> list[str]:
    """The rubric, class-scoped. `file_loc` is deliberately NOT in class scope --
    that was a real defect found in the Python run (a big file made every class
    inside it look guilty) and the fix is preserved here."""
    hits = []
    for key, col in (("class_loc", "class_loc"), ("class_wmc", "wmc"),
                     ("class_methods", "method_count"),
                     ("func_ccn", "max_method_ccn"),
                     ("func_params", "max_class_params")):
        v = row.get(col)
        if v is not None and thr.get(key) is not None and v >= thr[key]:
            hits.append(key)
    lcom = row.get("lcom")
    if (lcom is not None and row.get("method_count", 0) >= thr["lcom_min_methods"]
            and lcom >= thr["class_lcom"]):
        hits.append("class_lcom")
    return hits


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    thr = getattr(C, "SRP_JAVA_THRESHOLDS", None)

    gold = pd.read_parquet(C.GOLD_SET_JAVA)
    man = pd.read_parquet(C.CLONE_MANIFEST_JAVA)
    man = man[man.extract_status == "ok"]
    if args.limit:
        gold = gold.head(args.limit)
        man = man[man.pr_id.isin(set(gold.pr_id))]

    by_pr = {pr.pr_id: pr for pr in gold.itertuples(index=False)}
    rows, n_files, n_err = [], 0, 0
    for i, m in enumerate(man.itertuples(index=False), 1):
        pr = by_pr.get(m.pr_id)
        if pr is None:
            continue
        try:
            src = Path(m.local_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        got = class_rows(pr, m.file_path, src)
        n_err += 0 if got[0]["parse_ok"] else 1
        rows.extend(got)
        n_files += 1
        if i % 200 == 0:
            print(f"  [{i}/{len(man)}]", flush=True)

    df = pd.DataFrame(rows)
    cls = df[df.class_name.notna()].copy()
    if thr is not None:
        hits = cls.apply(lambda r: srp_hits(r, thr), axis=1)
        cls["hits"] = hits.apply(lambda h: "|".join(h))
        cls["srp_risk_hits"] = hits.apply(len)
        suppressed = (cls["c3"].notna()) & (cls["c3"] >= thr["c3_suppress"])
        cls["c3_suppressed"] = suppressed & (cls.srp_risk_hits >= thr["min_hits"])
        cls["srp_risk"] = (cls.srp_risk_hits >= thr["min_hits"]) & ~suppressed
        cls["srp_risk_strict"] = (cls.srp_risk_hits >= thr["strict_hits"]) & ~suppressed
        df = pd.concat([df[df.class_name.isna()], cls], ignore_index=True)

    df.to_parquet(C.SRP_RESULTS_JAVA, index=False)
    print(f"\n[srp-java] wrote {C.SRP_RESULTS_JAVA}")
    print(f"[srp-java] files: {n_files}  parse errors: {n_err}  "
          f"types: {len(cls)}  rows: {len(df)}")
    print(f"[srp-java] type kinds: {cls['kind'].value_counts().to_dict()}")

    if thr is None:
        print("\n[srp-java] CALIBRATION -- percentiles of the Java distribution:")
        for col in ("class_loc", "wmc", "method_count", "max_method_ccn",
                    "max_class_params", "lcom", "c3"):
            s = cls[col].dropna()
            if not len(s):
                continue
            qs = " ".join(f"p{int(p*100)}={s.quantile(p):.2f}"
                          for p in (0.5, 0.75, 0.9, 0.95, 0.99))
            print(f"  {col:18} {qs}  max={s.max():.2f}  n={len(s)}")
        print("\n  -> set config.SRP_JAVA_THRESHOLDS from these (~p90), then re-run.")
    else:
        print(f"\n[srp-java] flagged: {int(cls.srp_risk.sum())} of {len(cls)} "
              f"({cls.srp_risk.mean():.1%})   strict: {int(cls.srp_risk_strict.sum())}")
        print(f"[srp-java] C3-suppressed: {int(cls.c3_suppressed.sum())}")


if __name__ == "__main__":
    main()
