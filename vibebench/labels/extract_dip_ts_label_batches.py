"""
Step 4a-DIP-TS helper - show WHY each sampled PR was (or was not) flagged.

A DIP delta cannot be judged from numbers alone: `delta_mean_d = 0.2` says
nothing about whether a high-level module started depending on a concrete
low-level one. So for each sampled PR this rebuilds the before/after module
graphs with the same sidecar the harness uses, and prints:

  * every NEW import edge whose source is a touched module,
  * whether each edge's TARGET is abstract (declares abstract classes /
    interfaces) or concrete -- the actual DIP question,
  * the members of any cycle that exists after but not before.

Run:  python vibebench/labels/extract_dip_ts_label_batches.py \
          --file dip_ts_labeling_sample.csv --out-dir vibebench/labels/dip_ts_batches
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0]))
sys.path.insert(0, str(HERE.parents[0] / "03_principles"))
sys.path.insert(0, str(HERE.parents[0] / "03_principles" / "dip"))
sys.path.insert(0, str(HERE.parents[0] / "03_principles" / "dip_ts"))
import config as C  # noqa: E402
import gitsnap  # noqa: E402
from graph import find_cycles  # noqa: E402
from run_dip_ts import build_modules  # noqa: E402

EXTS, EXCL = C.TS_SOURCE_EXTENSIONS, C.TS_EXCLUDED_PATH_PARTS


class _PR:
    def __init__(self, merge_sha, commit_shas):
        self.merge_sha = merge_sha
        self.commit_shas = commit_shas


def graphs(row):
    d = gitsnap.repo_dir(row.full_name)
    with gitsnap.repo_lock(row.full_name):
        pair = gitsnap.delta_pair(d, _PR(row.merge_sha, row.commit_shas))
        if pair is None:
            return None, None
        bsha, asha = pair
        bf = gitsnap.src_tree(d, bsha, EXTS, EXCL, row.full_name)
        af = gitsnap.src_tree(d, asha, EXTS, EXCL, row.full_name)
    if bf is None or af is None or gitsnap.TOO_LARGE in bf or gitsnap.TOO_LARGE in af:
        return None, None
    return build_modules(bf), build_modules(af)


def block(row, touched) -> str:
    head = (f"### {row.full_name}#{row.pr_number}  [{row.agent}]\n"
            f"    touched modules={row.n_touched_modules}  "
            f"cycles {row.before_touched_cycles} -> {row.after_touched_cycles}  "
            f"delta_mean_d={row.delta_mean_d}\n"
            f"    rubric hits: {row.dip_risk_hits}   rubric_flag={row.dip_risk}\n")
    try:
        bm, am = graphs(row)
    except Exception as e:  # noqa: BLE001
        return head + f"    (error rebuilding graphs: {e})\n"
    if bm is None or am is None:
        return head + "    (trees unavailable)\n"

    scope = {m for m in touched if m in am}
    lines = []
    for m in sorted(scope):
        before = bm[m].imports if m in bm else set()
        new = sorted(am[m].imports - before)
        if not new:
            continue
        lines.append(f"  {m}  ({'NEW MODULE' if m not in bm else 'modified'})")
        for t in new:
            info = am.get(t)
            kind = "?"
            if info is not None:
                kind = (f"abstract({info.n_abstract_classes}/{info.n_classes} classes)"
                        if info.n_abstract_classes else
                        f"CONCRETE({info.n_classes} classes)")
            lines.append(f"      -> {t}   [{kind}]")
    out = head + "\n-- NEW IMPORT EDGES FROM TOUCHED MODULES --\n"
    out += ("\n".join(lines) if lines else "  (none)") + "\n"

    bc = {frozenset(c) for c in find_cycles(bm)}
    ac = {frozenset(c) for c in find_cycles(am)}
    new_cycles = [c for c in ac - bc if c & scope]
    out += "\n-- NEW CYCLES TOUCHING THIS PR --\n"
    if new_cycles:
        for c in new_cycles:
            out += "  cycle: " + " <-> ".join(sorted(c)) + "\n"
    else:
        out += "  (none)\n"
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="dip_ts_labeling_sample.csv")
    ap.add_argument("--out-dir", default=str(HERE / "dip_ts_batches"))
    ap.add_argument("--per-batch", type=int, default=12)
    a = ap.parse_args()

    df = pd.read_csv(HERE / a.file)
    gold = pd.read_parquet(C.GOLD_SET_TS)[["pr_id", "commit_shas", "touched_files",
                                           "files_source"]]
    df = df.merge(gold, on="pr_id", how="left")

    outdir = Path(a.out_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    blocks = []
    for i, row in enumerate(df.itertuples(index=False), 1):
        print(f"[{i}/{len(df)}] {row.full_name}#{row.pr_number}", flush=True)
        touched = gitsnap.touched_files(row, EXTS, EXCL, extract_dir=C.EXTRACT_DIR_TS)
        tmods = {p.rsplit(".", 1)[0] for p in touched}
        blocks.append(block(row, tmods))

    for i in range(0, len(blocks), a.per_batch):
        p = outdir / f"dip_ts_label_batch_{i // a.per_batch:02d}.txt"
        p.write_text("\n\n".join(blocks[i:i + a.per_batch]), encoding="utf-8")
        print("wrote", p)


if __name__ == "__main__":
    main()
