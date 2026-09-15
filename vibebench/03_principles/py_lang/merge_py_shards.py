"""
Merge the repository-disjoint phase 3 shards into the canonical parquets.

A merge is where a partial result most easily turns into one that looks
complete: concatenate whatever files happen to be on disk and every downstream
table is computed on it without complaint. So this script REFUSES unless every
one of these holds, and writes nothing until all of them do:

  1. every shard has its completion marker, which run_python.py writes LAST and
     only when every PR in the shard has a row. A parquet without a marker is a
     checkpoint, not a finished pass.
  2. every marker agrees on the shard count, on calibration mode, and on the
     THRESHOLDS. Shards launched under different config would each be internally
     consistent and jointly meaningless.
  3. the shards are repository-disjoint in the data, not only by construction.
  4. no pull request appears twice, and the merged set equals the gold set
     exactly: 7,050 of 7,050, no more, no fewer.
  5. every entity row belongs to a merged PR, and each frame passes the same
     schema contract run_python.py enforces for the label chain.
  6. no canonical parquet already exists. The chain deletes them before the
     flagged pass; finding one means a leftover (calibration rows most likely)
     that must not be silently overwritten or, worse, mixed in.

Canonical files are written entity frames first and the PR frame LAST, then a
canonical completion marker, so a crash mid-write leaves no PR frame claiming a
finished corpus.

Run:  python vibebench/03_principles/py_lang/merge_py_shards.py --n 5
      python vibebench/03_principles/py_lang/merge_py_shards.py --n 5 --pilot
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))
import run_python as RP  # noqa: E402  (brings config, gitsnap, safeio on path)

C, gitsnap, safeio = RP.C, RP.gitsnap, RP.safeio


def fail(msg: str) -> None:
    sys.exit(f"\nMERGE REFUSED: {msg}\nNothing was written.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, required=True, help="number of shards")
    ap.add_argument("--pilot", action="store_true")
    a = ap.parse_args()

    base = dict(RP._OUT_PATHS)
    if a.pilot:
        base = {k: v.with_suffix(".PILOT.parquet") for k, v in base.items()}

    # ---- 6. canonical outputs must not already exist -----------------------
    existing = [p.name for p in base.values() if p.exists()]
    if existing:
        fail(f"canonical file(s) already present: {existing}. Delete them "
             f"deliberately (they are probably calibration leftovers) and re-run.")

    # ---- 1 + 2. markers present and consistent ------------------------------
    markers, frames = [], {k: [] for k in ("pr", "srp", "ocp", "lsp", "isp")}
    for i in range(a.n):
        RP.OUT = dict(base)
        RP.use_shard_paths(i, a.n)
        m = RP.done_marker()
        if not m.exists():
            fail(f"shard {i}/{a.n} has no completion marker ({m.name}). It did "
                 f"not finish; re-run it, it resumes.")
        meta = json.loads(m.read_text(encoding="utf-8"))
        if meta.get("n_shards") != a.n or meta.get("shard") != i:
            fail(f"{m.name} says shard {meta.get('shard')}/{meta.get('n_shards')}, "
                 f"expected {i}/{a.n}")
        markers.append(meta)
        for k in frames:
            p = RP.OUT[k]
            if not p.exists():
                fail(f"shard {i} has a marker but no {p.name}")
            frames[k].append(pd.read_parquet(p))

    ref = markers[0]
    for meta in markers[1:]:
        if meta["calibrate"] != ref["calibrate"]:
            fail("shards disagree on calibration mode")
        if meta["thresholds"] != ref["thresholds"]:
            fail(f"shards ran under DIFFERENT thresholds:\n  {ref['thresholds']}\n"
                 f"  {meta['thresholds']}")
    print(f"markers: {a.n} shards, calibrate={ref['calibrate']}, thresholds agree")

    # ---- 3. repository-disjoint in the data ---------------------------------
    seen: dict[str, int] = {}
    for i, df in enumerate(frames["pr"]):
        for fn in set(df.full_name):
            if fn in seen:
                fail(f"repository {fn} appears in shards {seen[fn]} and {i}")
            seen[fn] = i
    print(f"repository-disjoint: {len(seen):,} repositories, no overlap")

    # ---- 4. exactly the gold set, once each ---------------------------------
    pr = RP.normalize_flags(pd.concat(frames["pr"], ignore_index=True), "pr")
    dups = pr.pr_id[pr.pr_id.duplicated()]
    if len(dups):
        fail(f"{dups.nunique()} pull request(s) appear more than once, e.g. "
             f"{list(dups.unique()[:3])}")
    gold_ids = set(pd.read_parquet(C.GOLD_SET, columns=["pr_id"]).pr_id)
    have = set(pr.pr_id)
    if not a.pilot and have != gold_ids:
        fail(f"merged PRs {len(have):,} != gold set {len(gold_ids):,} "
             f"(missing {len(gold_ids - have):,}, unexpected {len(have - gold_ids):,})")
    print(f"pull requests: {len(have):,} unique"
          + ("" if a.pilot else f", equal to the gold set of {len(gold_ids):,}"))

    # ---- 5. entity rows belong to merged PRs, and schema holds -------------
    merged = {}
    for k in ("srp", "ocp", "lsp", "isp"):
        nonempty = [f for f in frames[k] if len(f)]
        d = pd.concat(nonempty, ignore_index=True) if nonempty else pd.DataFrame()
        # Flags normalised to nullable boolean: one shard's resume can leave a
        # flag stored as 0.0/1.0 while the others are bool (see
        # run_python.normalize_flags). Refuses any value that is not boolean.
        d = RP.normalize_flags(d, k)
        if len(d):
            orphan = set(d.pr_id) - have
            if orphan:
                fail(f"{k}: {len(orphan)} row pr_id(s) not in the merged PR set")
            RP._check_schema(k, d)
        merged[k] = d
        print(f"{k}: {len(d):,} rows")
    RP._check_schema("pr", pr)
    RP._check_schema("dip", pr)

    # ---- write: entities first, PR frame LAST, then the marker --------------
    W = gitsnap.write_parquet_atomic
    for k in ("srp", "ocp", "lsp", "isp"):
        W(merged[k], base[k], index=False)
    W(pr, base["dip"], index=False)
    W(pr, base["pr"], index=False)
    safeio.write_json(base["pr"].with_name(base["pr"].stem + ".DONE.json"), {
        "merged_from_shards": a.n, "prs": len(have),
        "calibrate": ref["calibrate"], "thresholds": ref["thresholds"],
        "status": {k: int(v) for k, v in pr.status.value_counts().items()},
        "merged_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    print(f"\nMERGED into canonical parquets. status: "
          f"{dict(pr.status.value_counts())}")


if __name__ == "__main__":
    main()
