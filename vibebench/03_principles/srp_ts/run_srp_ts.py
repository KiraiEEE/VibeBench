"""
Step 3-TS - SRP evaluator over the TypeScript corpus.

Consumes corpus/clone_manifest_ts.parquet (Step 2 --lang ts), batches each PR's
extracted files through the ts-morph sidecar (one Node process per PR tree),
and computes the SAME per-class SRP metrics as the validated Python evaluator:
LCOM4 and C3 via the factored cores in srp/metrics.py, WMC/CCN/params/LOC from
the sidecar's AST facts. Output: corpus/srp_results_ts.parquet, one row per
class + one per class-less file (mirrors srp_results.parquet's shape).

Two-phase thresholds (the OCP lesson -- calibrate BEFORE labeling):
  - config.SRP_TS_THRESHOLDS is None  -> CALIBRATION MODE: metrics only,
    srp_risk left null, and the run prints the observed metric percentiles so
    thresholds can be chosen from the TS distribution (TS idioms -- React
    components, decorators -- shift distributions; Python numbers don't port).
  - after thresholds are set in config  -> re-run flags srp_risk (>=2 hits,
    C3 suppressor) and srp_risk_strict (>=3), with the per-class scope rule
    (class's OWN max method CCN/params -- the famous Python scope-bug fix).

Parallel: config.ANALYZE_WORKERS processes, one PR per task (the sidecar call
dominates, so process-per-PR is the right grain). Checkpoints every 25 PRs.

Run:  python vibebench/03_principles/srp_ts/run_srp_ts.py
      python vibebench/03_principles/srp_ts/run_srp_ts.py --limit 10 --workers 1
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))          # 03_principles (ts_bridge)
sys.path.insert(0, str(_HERE.parents[1] / "srp"))  # srp (metrics cores)
sys.path.insert(0, str(_HERE.parents[2]))          # vibebench (config)
import config as C  # noqa: E402


def srp_ts_hits(row: dict, thresholds: dict) -> list[str]:
    """Rubric hits for one CLASS row, class-scoped (the Python scope-bug fix:
    func_ccn/func_params come from the class's OWN methods, never file-wide).
    `file_loc` intentionally absent from class scope (validated Python fix #2).
    An LCOM hit requires >= LCOM_MIN_METHODS methods (saturation guard)."""
    hits = []
    if row["class_loc"] >= thresholds["class_loc"]:
        hits.append("class_loc")
    if row["wmc"] >= thresholds["class_wmc"]:
        hits.append("class_wmc")
    if row["method_count"] >= thresholds["class_methods"]:
        hits.append("class_methods")
    if (row["lcom"] is not None and row["method_count"] >= thresholds["lcom_min_methods"]
            and row["lcom"] >= thresholds["class_lcom"]):
        hits.append("class_lcom")
    if row["max_method_ccn"] >= thresholds["func_ccn"]:
        hits.append("func_ccn")
    if row["max_class_params"] >= thresholds["func_params"]:
        hits.append("func_params")
    return hits


def analyze_pr(task: dict) -> list[dict]:
    """Worker: one PR -> rows. Runs in a subprocess; imports must be local-safe."""
    import ts_bridge  # noqa: E402  (path set up by parent via sys.path inheritance)
    from metrics import c3_from_token_bags, lcom_from_access_sets  # noqa: E402

    meta = {k: task[k] for k in ("pr_id", "agent", "full_name", "pr_number")}
    files: dict[str, str] = {}
    for rel, local in task["files"]:
        try:
            files[rel] = Path(local).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return [{**meta, "file_path": rel, "parse_ok": False,
                     "parse_error": f"read: {e}", "class_name": None}]

    facts = ts_bridge.analyze_tree(files)
    if not facts.get("ok"):
        return [{**meta, "file_path": rel, "parse_ok": False,
                 "parse_error": str(facts.get("error"))[:300], "class_name": None}
                for rel in files]

    rows: list[dict] = []
    for f in facts["files"]:
        base = {**meta, "file_path": f["path"], "parse_ok": f["parse_ok"],
                "parse_error": (f.get("error") or None),
                "file_loc": f.get("file_loc", 0),
                "max_func_ccn": f.get("max_func_ccn", 0),
                "max_func_params": f.get("max_func_params", 0)}
        if not f["parse_ok"] or not f["classes"]:
            rows.append({**base, "class_name": None})
            continue
        for cls in f["classes"]:
            methods = cls["methods"]
            noncon = [m for m in methods if not m["is_constructor"]]
            lcom = lcom_from_access_sets(
                [m["name"] for m in noncon],
                {m["name"]: set(m["props"]) for m in noncon},
                {m["name"]: set(m["calls"]) for m in noncon},
            )
            c3 = c3_from_token_bags([m["tokens"] for m in noncon])
            rows.append({
                **base,
                "class_name": cls["name"],
                "class_lineno": cls["lineno"],
                "class_loc": cls["loc"],
                "is_abstract": cls.get("is_abstract", False),
                "method_count": len(methods),
                "wmc": sum(m["ccn"] for m in methods),
                "max_method_ccn": max((m["ccn"] for m in methods), default=0),
                "max_class_params": max((m["params"] for m in methods), default=0),
                "lcom": lcom,
                "c3": c3,
            })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=C.ANALYZE_WORKERS)
    args = ap.parse_args()

    man = pd.read_parquet(C.CLONE_MANIFEST_TS)
    ok = man[man["extract_status"] == "ok"].copy()
    tasks = []
    for pr_id, grp in ok.groupby("pr_id", sort=False):
        first = grp.iloc[0]
        tasks.append({
            "pr_id": pr_id, "agent": first["agent"],
            "full_name": first["full_name"], "pr_number": first["pr_number"],
            "files": list(zip(grp["file_path"], grp["local_path"])),
        })
    if args.limit:
        tasks = tasks[: args.limit]
    print(f"[srp-ts] {len(tasks)} PRs, {len(ok)} files, "
          f"{args.workers} workers", flush=True)

    rows: list[dict] = []
    done = 0
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = {ex.submit(analyze_pr, t): t for t in tasks}
        for fut in as_completed(futs):
            t = futs[fut]
            try:
                rows.extend(fut.result())
            except Exception as e:  # noqa: BLE001 -- one PR must not abort the batch
                print(f"  !! {t['full_name']}#{t['pr_number']}: {e}", flush=True)
                rows.append({"pr_id": t["pr_id"], "agent": t["agent"],
                             "full_name": t["full_name"],
                             "pr_number": t["pr_number"], "file_path": "",
                             "parse_ok": False, "parse_error": str(e)[:300],
                             "class_name": None})
            done += 1
            if done % 25 == 0:
                pd.DataFrame(rows).to_parquet(C.SRP_RESULTS_TS, index=False)
                print(f"[srp-ts] progress {done}/{len(tasks)}", flush=True)

    df = pd.DataFrame(rows)

    # Flags: calibration mode (None) leaves srp_risk null on purpose.
    thr = C.SRP_TS_THRESHOLDS
    if thr is None:
        df["srp_risk_hits"] = None
        df["srp_risk"] = None
        df["srp_risk_strict"] = None
        df["c3_suppressed"] = None
    else:
        hits_col, risk_col, strict_col, supp_col = [], [], [], []
        for _, r in df.iterrows():
            if r["class_name"] is None or not r.get("parse_ok", False):
                hits_col.append(None); risk_col.append(None)
                strict_col.append(None); supp_col.append(None)
                continue
            hits = srp_ts_hits(r, thr)
            risk = len(hits) >= thr["risk_min_hits"]
            suppressed = bool(
                risk and thr.get("c3_suppress") is not None
                and r["c3"] is not None and r["c3"] >= thr["c3_suppress"]
            )
            hits_col.append(";".join(hits))
            risk_col.append(bool(risk and not suppressed))
            strict_col.append(len(hits) >= thr["strict_min_hits"])
            supp_col.append(suppressed)
        df["srp_risk_hits"] = hits_col
        df["srp_risk"] = risk_col
        df["srp_risk_strict"] = strict_col
        df["c3_suppressed"] = supp_col

    df.to_parquet(C.SRP_RESULTS_TS, index=False)
    cls = df[df["class_name"].notna()]
    print(f"\n[srp-ts] wrote {C.SRP_RESULTS_TS}")
    print(f"[srp-ts] rows: {len(df)}  classes: {len(cls)}  "
          f"parse errors: {int((~df['parse_ok'].fillna(False)).sum())}")

    if thr is None and len(cls):
        print("\n[srp-ts] CALIBRATION -- observed class-metric percentiles "
              "(choose thresholds from these BEFORE labeling):")
        pcts = [0.5, 0.75, 0.9, 0.95, 0.99]
        for col in ("class_loc", "wmc", "method_count", "max_method_ccn",
                    "max_class_params", "lcom", "c3"):
            s = pd.to_numeric(cls[col], errors="coerce").dropna()
            if not len(s):
                continue
            q = " ".join(f"p{int(p*100)}={s.quantile(p):.2f}" for p in pcts)
            print(f"  {col:>16}: n={len(s)} {q} max={s.max():.2f}")
    elif len(cls):
        print("\n[srp-ts] risk rate by agent (pooled):")
        print(cls.groupby("agent")["srp_risk"].mean().round(4).to_string())


if __name__ == "__main__":
    main()
