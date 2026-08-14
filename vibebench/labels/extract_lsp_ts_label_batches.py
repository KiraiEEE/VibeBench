"""
Step 4a-LSP-TS helper - dump each sampled override pair, OVERRIDE AND BASE side
by side.

LSP is a RELATION between two methods; it cannot be judged from one side. The
Python extractor learned this the hard way, so this twin does the same: for each
pair it prints the subclass method's window and then the base class's declaration
of that method, resolved out of the base module's file.

The head tree is taken from the PR's after-state via gitsnap.delta_pair, not from
merge_sha alone -- a sizeable minority of PRs have no merge event recorded and
would otherwise render as "source unavailable".

Run:  python vibebench/labels/extract_lsp_ts_label_batches.py \
          --file lsp_ts_labeling_sample.csv --out-dir vibebench/labels/lsp_ts_batches
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
    """The minimal duck-type gitsnap.delta_pair expects."""

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


def base_method_src(tree, base_id, method, span=28) -> str:
    """The base class's declaration of `method`, located by text scan.

    A structural search is enough here: we only need to SHOW the judge the base
    signature, and a regex for `class <Name>` followed by the method name inside
    the class body is both simple and language-correct for the shapes ts-morph
    already told us exist.
    """
    if not isinstance(base_id, str) or ":" not in base_id:
        return "    (base not resolvable)\n"
    bmod, bname = base_id.rsplit(":", 1)
    path = file_for_module(tree, bmod)
    if path is None:
        return f"    (base module {bmod} not in tree)\n"
    src = tree[path]
    m = re.search(rf"\b(?:abstract\s+)?class\s+{re.escape(bname)}\b", src)
    if m is None:
        m = re.search(rf"\binterface\s+{re.escape(bname)}\b", src)
    if m is None:
        return f"    (class {bname} not found in {path})\n"
    lines = src.splitlines()
    cls_line = src[: m.start()].count("\n")
    for i in range(cls_line, min(len(lines), cls_line + 800)):
        if re.search(rf"(^|[\s;{{(])(?:public|private|protected|static|abstract|async|\*|get|set|override|readonly|\s)*"
                     rf"{re.escape(str(method))}\s*[<(:=]", lines[i]):
            s, e = max(cls_line, i - 2), min(len(lines), i + span)
            return f"```ts\n// {path} lines {s+1}-{e}\n" + "\n".join(lines[s:e]) + "\n```\n"
    s, e = cls_line, min(len(lines), cls_line + span)
    return (f"```ts\n// {path} lines {s+1}-{e} (method not located; class shown)\n"
            + "\n".join(lines[s:e]) + "\n```\n")


def block(row, tree) -> str:
    head = (f"### {row.qualname}.{row.method}  overrides  {row.base_id}\n"
            f"    {row.agent}  {row.full_name}#{row.pr_number}  {row.file_path}\n"
            f"    signals: {row.signals}   rubric_violation={row.violation}\n")
    if tree is None:
        return head + "    (source unavailable)\n"
    src = tree.get(row.file_path)
    if not src:
        return head + f"    (file {row.file_path} not in tree)\n"
    lines = src.splitlines()
    s = max(0, int(row.lineno) - 3)
    e = min(len(lines), int(row.lineno) + 34)
    out = head + "\n-- OVERRIDE --\n"
    out += f"```ts\n// lines {s+1}-{e}\n" + "\n".join(lines[s:e]) + "\n```\n"
    out += "\n-- BASE --\n" + base_method_src(tree, row.base_id, row.method)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="lsp_ts_labeling_sample.csv")
    ap.add_argument("--out-dir", default=str(HERE / "lsp_ts_batches"))
    ap.add_argument("--per-batch", type=int, default=10)
    a = ap.parse_args()

    df = pd.read_csv(HERE / a.file)
    gold = pd.read_parquet(C.GOLD_SET_TS)[["pr_id", "commit_shas"]]
    df = df.merge(gold, on="pr_id", how="left")

    outdir = Path(a.out_dir)
    outdir.mkdir(parents=True, exist_ok=True)

    trees: dict = {}
    blocks = []
    for i, row in enumerate(df.itertuples(index=False), 1):
        print(f"[{i}/{len(df)}] {row.full_name}#{row.pr_number} {row.qualname}.{row.method}",
              flush=True)
        key = (row.full_name, row.pr_id)
        if key not in trees:
            try:
                trees[key] = head_tree(row.full_name, row.merge_sha, row.commit_shas)
            except Exception as e:  # noqa: BLE001
                print(f"   !! {e}", flush=True)
                trees[key] = None
        blocks.append(block(row, trees[key]))

    for i in range(0, len(blocks), a.per_batch):
        p = outdir / f"lsp_ts_label_batch_{i // a.per_batch:02d}.txt"
        p.write_text("\n\n".join(blocks[i:i + a.per_batch]), encoding="utf-8")
        print("wrote", p)


if __name__ == "__main__":
    main()
