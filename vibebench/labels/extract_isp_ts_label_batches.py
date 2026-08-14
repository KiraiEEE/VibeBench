"""
Step 4a-ISP-TS helper - dump each sampled contract WITH its implementers (or the
implementer WITH the contract it implements).

ISP, like LSP, is a RELATION: a wide interface is only a violation if somebody is
forced to supply members they do not use. So each block prints the contract
declaration AND every class in the same tree that implements it, with the
stub-vs-real body visible.

Run:  python vibebench/labels/extract_isp_ts_label_batches.py \
          --file isp_ts_labeling_sample.csv --out-dir vibebench/labels/isp_ts_batches
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0]))
sys.path.insert(0, str(HERE.parents[0] / "03_principles"))
import config as C  # noqa: E402
import gitsnap  # noqa: E402

EXTS, EXCL = C.TS_SOURCE_EXTENSIONS, C.TS_EXCLUDED_PATH_PARTS


class _PR:
    def __init__(self, merge_sha, commit_shas):
        self.merge_sha = merge_sha
        self.commit_shas = commit_shas


def head_tree(full_name, merge_sha, commit_shas):
    d = gitsnap.repo_dir(full_name)
    with gitsnap.repo_lock(full_name):
        pair = gitsnap.delta_pair(d, _PR(merge_sha, commit_shas))
        if pair is None:
            return None
        return gitsnap.src_tree(d, pair[1], EXTS, EXCL, full_name)


def file_for_module(tree, module):
    for ext in (".ts", ".tsx"):
        if module + ext in tree:
            return module + ext
    return None


def decl_src(tree, module, name, span=26) -> str:
    """The declaration of `name` in `module`, class or interface."""
    path = file_for_module(tree, module)
    if path is None:
        return f"    (module {module} not in tree)\n"
    src = tree[path]
    m = re.search(rf"\b(?:export\s+)?(?:abstract\s+)?(?:class|interface)\s+{re.escape(name)}\b", src)
    if m is None:
        return f"    ({name} not found in {path})\n"
    lines = src.splitlines()
    s = src[: m.start()].count("\n")
    e = min(len(lines), s + span)
    return f"```ts\n// {path} lines {s+1}-{e}\n" + "\n".join(lines[s:e]) + "\n```\n"


def implementers_of(tree, iface_name, limit=2, span=26) -> str:
    """Classes in the tree that declare `implements <iface>` or extend it."""
    out, found = [], 0
    pat = re.compile(rf"\bclass\s+(\w+)[^{{]*\b(?:implements|extends)\b[^{{]*\b"
                     rf"{re.escape(iface_name)}\b")
    for path, src in tree.items():
        for m in pat.finditer(src):
            lines = src.splitlines()
            s = src[: m.start()].count("\n")
            e = min(len(lines), s + span)
            out.append(f"```ts\n// IMPLEMENTER {m.group(1)} -- {path} lines {s+1}-{e}\n"
                       + "\n".join(lines[s:e]) + "\n```\n")
            found += 1
            if found >= limit:
                return "".join(out)
    return "".join(out) or "    (no implementer found by text scan)\n"


def block(row, tree) -> str:
    is_iface = row.row_type == "interface"
    name = row.qualname
    iface_name = (row.iface_id.rsplit(":", 1)[-1]
                  if isinstance(row.iface_id, str) else name)
    head = (f"### [{row.row_type}] {name}   contract={row.iface_id}\n"
            f"    {row.agent}  {row.full_name}#{row.pr_number}\n"
            f"    width={row.n_members if is_iface else row.iface_width}  "
            f"implemented={row.n_implemented}  stubs={row.n_stubs}  "
            f"stub_ratio={row.stub_ratio}\n"
            f"    rubric_flag={row.pred_isp_risk}\n")
    if tree is None:
        return head + "    (source unavailable)\n"

    out = head + "\n-- CONTRACT --\n"
    imod = (row.iface_id.rsplit(":", 1)[0]
            if isinstance(row.iface_id, str) and ":" in row.iface_id else row.module)
    out += decl_src(tree, imod, iface_name)
    out += "\n-- IMPLEMENTERS --\n"
    if is_iface:
        out += implementers_of(tree, iface_name)
    else:
        out += decl_src(tree, row.module, name)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="isp_ts_labeling_sample.csv")
    ap.add_argument("--out-dir", default=str(HERE / "isp_ts_batches"))
    ap.add_argument("--per-batch", type=int, default=12)
    a = ap.parse_args()

    df = pd.read_csv(HERE / a.file)
    gold = pd.read_parquet(C.GOLD_SET_TS)[["pr_id", "commit_shas"]]
    df = df.merge(gold, on="pr_id", how="left")

    outdir = Path(a.out_dir)
    outdir.mkdir(parents=True, exist_ok=True)

    trees, blocks = {}, []
    for i, row in enumerate(df.itertuples(index=False), 1):
        print(f"[{i}/{len(df)}] {row.full_name}#{row.pr_number} {row.qualname}", flush=True)
        key = (row.full_name, row.pr_id)
        if key not in trees:
            try:
                trees[key] = head_tree(row.full_name, row.merge_sha, row.commit_shas)
            except Exception as e:  # noqa: BLE001
                print(f"   !! {e}", flush=True)
                trees[key] = None
        blocks.append(block(row, trees[key]))

    for i in range(0, len(blocks), a.per_batch):
        p = outdir / f"isp_ts_label_batch_{i // a.per_batch:02d}.txt"
        p.write_text("\n\n".join(blocks[i:i + a.per_batch]), encoding="utf-8")
        print("wrote", p)


if __name__ == "__main__":
    main()
