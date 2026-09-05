"""
Step 3 - SRP evaluator: run metrics.py over every extracted AI-touched file and
apply the SRP-risk rubric from config.py.

Reads corpus/clone_manifest.parquet (Step 2 output), analyzes each successfully
extracted file, and writes one row per class (plus one file-level row when a
file has zero classes) to corpus/srp_results.parquet.

Run:  python vibebench/03_principles/srp/run_srp.py
"""
from __future__ import annotations

import ast
import pathlib
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "srp"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import config as C  # noqa: E402
from metrics import analyze_file  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 03_principles
import gitsnap  # noqa: E402



def _atomic_parquet(df, path):
    """Write a checkpoint so a power cut can never leave a CORRUPT file.

    `to_parquet(path)` overwrites in place, so a crash mid-write truncates the
    file. Writing to a sibling temp and then `os.replace` (atomic on NTFS and
    POSIX) means a reader always sees either the previous complete checkpoint or
    the new one -- never a half-written one. Matters most for the long overnight
    delta runs, which checkpoint every 25 PRs and so have many more write windows
    than a single end-of-run write.
    """
    import os
    tmp = pathlib.Path(str(path) + ".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)

def srp_risk_hits(row: dict, *, func_ccn: int, func_params: int,
                  is_class_row: bool) -> list[str]:
    """Which rubric thresholds this row crosses. See config.SRP_THRESHOLDS.

    `func_ccn`/`func_params` are passed explicitly rather than read from `row`
    because the right scope differs by row type: for a class row they must be
    the MAX OVER THAT CLASS'S OWN METHODS (ClassMetrics.max_method_ccn /
    .max_params), not the file-wide max -- otherwise one huge unrelated
    function elsewhere in the file would flag every other class in that file.
    For a file-only row (no classes) the file-wide max is the only option.

    Label-validated adjustments (see config.py for the tuning evidence):
    - LCOM only counts for classes with >= LCOM_MIN_METHODS methods (the metric
      saturates to 0/1 below that, its biggest false-positive source).
    - file_loc only counts for file-level rows: it's context, not a property of
      any individual class in the file.
    """
    hits = []
    t = C.SRP_THRESHOLDS
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


def before_class_index(pr) -> set[tuple[str, str]] | None:
    """{(file_path, class_name)} present in the PR's BEFORE state, or None.

    WHY THIS EXISTS (added 2026-08-17). Python SRP had no authored-vs-edited flag
    while Java did, so a Python SRP finding could only ever be stated as
    "present in touched code", never "introduced by this PR". That asymmetry is
    what made Java's headline result ("0 of 17 flagged types were AUTHORED by the
    agent") impossible to state for Python -- the strongest fidelity claim in the
    project had no Python counterpart. Mirrors run_java.py's `is_new`
    (`tid not in before_ids`) so the two languages stay commensurable.

    Returns None when the before state cannot be resolved, so `is_new` is left
    NULL rather than silently defaulting to True (which would inflate "authored").
    """
    d = gitsnap.ensure_repo(pr.full_name)
    with gitsnap.repo_lock(pr.full_name):
        pair = gitsnap.delta_pair(d, pr)
        if pair is None:
            return None
        tree = gitsnap.src_tree(d, pair[0], C.SOURCE_EXTENSIONS, (), pr.full_name)
    if tree is None or gitsnap.TOO_LARGE in tree:
        return None
    out: set[tuple[str, str]] = set()
    for path, src in tree.items():
        try:
            t = ast.parse(src)
        except (SyntaxError, ValueError):
            continue
        for node in ast.walk(t):
            if isinstance(node, ast.ClassDef):
                out.add((path, node.name))
    return out


def main() -> None:
    manifest = pd.read_parquet(C.CLONE_MANIFEST)
    ok = manifest[manifest.extract_status == "ok"].copy()
    print(f"[srp] analyzing {len(ok)} extracted files")

    # Before-state class inventory per PR, for the is_new flag. Resolved once per
    # PR and cached; the trees come from gitsnap's shared per-(repo,sha) cache, so
    # the other four harnesses reuse the same fetches.
    gold = pd.read_parquet(C.GOLD_SET,
                           columns=["pr_id", "full_name", "pr_number",
                                    "merge_sha", "commit_shas"])
    gold_by_pr = {r.pr_id: r for r in gold.itertuples(index=False)}
    before_cache: dict = {}

    # C3 backend: "tfidf" (default) or "embed" (per-method code embeddings).
    # Fall back to tfidf if "embed" is requested but the encoder is unavailable.
    c3_mode = getattr(C, "SRP_C3_MODE", "tfidf")
    if c3_mode == "embed":
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 03_principles
        import embeddings as E  # noqa: E402
        if E.available():
            print(f"[srp] C3 backend = EMBED ({getattr(C, 'EMBED_MODEL', '?')})")
        else:
            print(f"[srp] SRP_C3_MODE=embed but encoder unavailable "
                  f"({E.unavailable_reason()}) -> falling back to tfidf")
            c3_mode = "tfidf"

    # RESUMABLE (added 2026-08-17). This stage previously wrote its parquet ONCE
    # at the very end, so any interruption -- power cut, pause, a pathological
    # file forcing a kill -- discarded the whole pass. The four delta harnesses
    # already checkpoint every 25 PRs; SRP was the only stage that did not, and
    # it is the longest single-file pass. Rows are keyed by (pr_id, file_path),
    # which is unique per emitted row.
    rows: list[dict] = []
    done_keys: set = set()
    if C.SRP_RESULTS.exists():
        try:
            prev = pd.read_parquet(C.SRP_RESULTS)
            rows = prev.to_dict("records")
            done_keys = {(x["pr_id"], x["file_path"]) for x in rows}
            print(f"[srp] resuming: {len(done_keys)} file rows already done")
        except Exception as e:  # noqa: BLE001 -- a corrupt checkpoint must not block a fresh run
            print(f"[srp] could not read checkpoint ({e}); starting fresh")
            rows, done_keys = [], set()

    errors = 0
    for i, r in enumerate(ok.itertuples(index=False), 1):
        # Progress AND checkpoint live at the TOP of the loop on purpose. There
        # are four `continue` paths below (read error, parse failure, class-less
        # file, skip-already-done); putting either at the bottom meant they were
        # silently skipped whenever the file hit one -- and since MOST Python
        # files contain no class, the checkpoint almost never fired. An
        # unreliable checkpoint is worse than none: it invites trusting a resume
        # that was never written.
        if i % 200 == 0:
            print(f"  [{i}/{len(ok)}]", flush=True)
        if i % 1000 == 0 and rows:
            _atomic_parquet(pd.DataFrame(rows), C.SRP_RESULTS)

        if (r.pr_id, r.file_path) in done_keys:
            continue
        local_path = Path(r.local_path)
        try:
            source = local_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            errors += 1
            continue

        if r.pr_id not in before_cache:
            g = gold_by_pr.get(r.pr_id)
            try:
                before_cache[r.pr_id] = before_class_index(g) if g is not None else None
            except Exception as e:  # noqa: BLE001 -- never let this kill the run
                print(f"  !! before-state failed for {r.full_name}#{r.pr_number}: {e}",
                      flush=True)
                before_cache[r.pr_id] = None
        before = before_cache[r.pr_id]

        fm = analyze_file(r.file_path, source, c3_mode=c3_mode)
        base = {
            "pr_id": r.pr_id, "agent": r.agent, "full_name": r.full_name,
            "pr_number": r.pr_number, "file_path": r.file_path,
            # V2 (REDESIGN_V2.md 3): outcome rides on every row so SRP can be
            # split merged vs not-merged without a join. Sourced from the clone
            # manifest, which carries it from the gold set; defaulted for a
            # pre-V2 manifest.
            "outcome": getattr(r, "outcome", "merged"),
            "rejection_stratum": getattr(r, "rejection_stratum", "merged"),
            "file_loc": fm.file_loc, "maintainability_index": fm.maintainability_index,
            "max_func_ccn": fm.max_func_ccn, "max_func_params": fm.max_func_params,
            "parse_ok": fm.parse_ok, "parse_error": fm.error,
            # file-level: is this file itself new to the repo?
            "file_is_new": (None if before is None
                            else not any(p == r.file_path for p, _ in before)),
        }

        if not fm.parse_ok:
            errors += 1
            rows.append({**base, "class_name": None, "class_loc": None,
                         "method_count": None, "wmc": None, "max_method_ccn": None,
                         "lcom": None, "c3": None, "max_class_params": None,
                         "srp_risk_hits": [], "srp_risk": False})
            continue

        if not fm.classes:
            # Module with no classes: no per-class scope exists, so the file-wide
            # max is the only option here (this row IS the whole file).
            hits = srp_risk_hits(base, func_ccn=fm.max_func_ccn,
                                 func_params=fm.max_func_params, is_class_row=False)
            rows.append({**base, "class_name": None, "class_loc": None,
                         "method_count": None, "wmc": None, "max_method_ccn": None,
                         "lcom": None, "c3": None, "max_class_params": None,
                         "srp_risk_hits": hits, "srp_risk": len(hits) >= C.SRP_RISK_MIN_HITS})
            continue

        for c in fm.classes:
            row = {
                **base,
                "class_name": c.class_name, "class_loc": c.loc,
                "method_count": c.method_count, "wmc": c.wmc,
                "max_method_ccn": c.max_method_ccn, "lcom": c.lcom,
                "c3": c.c3, "max_class_params": c.max_params,
                # AUTHORED vs EDITED (mirrors run_java.py's is_new). NULL, not
                # True, when the before state could not be resolved -- defaulting
                # to True would inflate the "agent authored this god class" claim,
                # which is exactly the claim this column exists to support.
                "is_new": (None if before is None
                           else (r.file_path, c.class_name) not in before),
            }
            # Class-scoped complexity/params, NOT the file-wide max (see srp_risk_hits docstring).
            hits = srp_risk_hits(row, func_ccn=c.max_method_ccn, func_params=c.max_params,
                                 is_class_row=True)
            row["srp_risk_hits"] = hits
            risk = len(hits) >= C.SRP_RISK_MIN_HITS
            # Semantic suppressor: high conceptual cohesion (C3) overrides a
            # size/structure-based flag -- see config.SRP_C3_SUPPRESS.
            suppressed = bool(
                risk and C.SRP_C3_SUPPRESS is not None
                and c.c3 is not None and c.c3 >= C.SRP_C3_SUPPRESS
            )
            row["c3_suppressed"] = suppressed
            row["srp_risk"] = risk and not suppressed
            # Stricter operating point (holdout: P=0.43/R=0.90 vs 0.33/1.00).
            row["srp_risk_strict"] = (len(hits) >= C.SRP_RISK_STRICT_MIN_HITS
                                      and not suppressed)
            rows.append(row)



    results = pd.DataFrame(rows)
    _atomic_parquet(results, C.SRP_RESULTS)

    print(f"\n[srp] results written to {C.SRP_RESULTS}")
    print(f"[srp] rows: {len(results)}  parse errors: {errors}")
    print("\nSRP-risk rate per agent (class-level rows only):")
    class_rows = results[results.class_name.notna()]
    summary = class_rows.groupby("agent").agg(
        classes=("class_name", "size"),
        srp_risk_rate=("srp_risk", "mean"),
        median_loc=("class_loc", "median"),
        median_wmc=("wmc", "median"),
        median_lcom=("lcom", "median"),
    ).round(3)
    print(summary.to_string())


if __name__ == "__main__":
    main()
