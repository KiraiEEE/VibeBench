"""
Build the API-SURFACE digest the SRP judge reads (JAVA_STATUS.md 8.4).

WHY A DIGEST AND NOT THE SOURCE. SRP asks "does this class have more than one
reason to change?". That is answered from the class's API surface -- its fields
and its method signatures -- because responsibility grouping is visible there.
Reading full bodies is not feasible at sample scale when a single class runs to
10,000 lines, and it is not necessary: nobody judges `SystemThermo` by reading
it. `extract_java_label_batches.py` dumps full bodies when one specific row
needs a closer look.

This script existed only as inline code in a session log, which meant the
tuning digest could not be regenerated. It is a file now.

Run:
  python vibebench/labels/build_java_srp_digest.py --file java_srp_labeling_sample_holdout.csv
  -> writes <file>.digest.json next to it
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))
sys.path.insert(0, str(_HERE.parents[1] / "03_principles"))
sys.path.insert(0, str(_HERE.parents[1] / "03_principles" / "java_lang"))
import config as C  # noqa: E402
import gitsnap  # noqa: E402
import detectors as D  # noqa: E402
from java_bridge import analyze_tree  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    src = Path(a.file)
    if not src.exists():
        src = _HERE.parent / a.file
    df = pd.read_csv(src)
    gs = pd.read_parquet(C.GOLD_SET_JAVA).set_index("pr_id")

    # One tree fetch per (repo, pr) covering every sampled row in it: the facts
    # cache makes the repeat cost ~0, but grouping keeps the git work minimal.
    out, missing = [], 0
    for (full, pr_id), g in df.groupby(["full_name", "pr_id"], sort=False):
        try:
            d = gitsnap.ensure_repo(full)
            pair = gitsnap.delta_pair(d, gs.loc[pr_id])
            if not pair:
                missing += len(g)
                continue
            files = gitsnap.src_tree(d, pair[1], C.JAVA_SOURCE_EXTENSIONS,
                                     C.JAVA_EXCLUDED_PATH_PARTS, full_name=full)
            if not files or gitsnap.TOO_LARGE in files:
                missing += len(g)
                continue
            ix = D.build_index(analyze_tree(files))
        except Exception as e:  # noqa: BLE001
            print(f"  {full} #{pr_id}: {e}", flush=True)
            missing += len(g)
            continue
        for _, r in g.iterrows():
            t = ix.types.get(r["type_id"])
            if t is None:
                missing += 1
                continue
            out.append({
                "i": int(r.name),
                "id": r["type_id"],
                "kind": t["kind"],
                "repo": full,
                "file": t["file"],
                "is_test": bool(C.is_test_path(t["file"], "java")),
                "loc": int(t["loc"]),
                "n_methods": int(r["method_count"]),
                "wmc": int(r["wmc"]),
                "lcom": None if pd.isna(r["lcom"]) else round(float(r["lcom"]), 2),
                "c3": None if pd.isna(r["c3"]) else round(float(r["c3"]), 2),
                "hits": r["hits"] if isinstance(r["hits"], str) else "",
                "pred": bool(r["srp_risk"]),
                "fields": list(t.get("fields") or []),
                "methods": [m["signature"] for m in t["methods"] if not m["isCtor"]],
            })
        print(f"  {full} #{pr_id}: {len(g)} row(s)", flush=True)

    dest = Path(a.out) if a.out else src.with_suffix(".digest.json")
    dest.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nwrote {dest}  ({len(out)} digests, {missing} unresolved)")


if __name__ == "__main__":
    main()
