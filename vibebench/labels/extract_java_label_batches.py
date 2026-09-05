"""
Dump the source a judge needs to label a Java sample -- one batch file per row.

The rule the earlier ports established the hard way: you cannot judge a
principle from the flagged entity alone.
  * LSP is a RELATION -- show the override AND its base method side by side.
  * OCP is open-vs-closed -- a branch COUNT says nothing; show the chain inside
    its enclosing method so the judge can see whether the variant set is open.
  * ISP needs the contract AND its implementers, to see who is forced to stub.
  * DIP needs the NEW import edges with each target marked abstract or CONCRETE.
  * SRP needs the whole class body.

Run:
  python vibebench/labels/extract_java_label_batches.py --principle lsp \
      --file java_lsp_labeling_sample.csv [--out-dir DIR]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd
from collections import OrderedDict

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))
sys.path.insert(0, str(_HERE.parents[1] / "03_principles"))
import config as C            # noqa: E402
import gitsnap                # noqa: E402
from java_bridge import analyze_tree  # noqa: E402
sys.path.insert(0, str(_HERE.parents[1] / "03_principles" / "java_lang"))
import detectors as D         # noqa: E402

PR_RESULTS = C.CORPUS_DIR / "java_pr_results.parquet"


_FRAMES: dict[str, pd.DataFrame] = {}


def _frame(kind: str) -> pd.DataFrame:
    """Load `java_pr_results` / the gold set ONCE per process.

    These were re-read from parquet on every call. The caller caches trees per
    PR, so this cost scaled with distinct PRs rather than rows -- tolerable for a
    44-row sample, wasteful for a census where the label set spans well over a
    thousand PRs and the gold set is half a megabyte.
    """
    if kind not in _FRAMES:
        _FRAMES[kind] = pd.read_parquet(
            PR_RESULTS if kind == "prs" else C.GOLD_SET_JAVA)
    return _FRAMES[kind]


def tree_for(full_name: str, pr_number: int) -> dict | None:
    prs = _frame("prs")
    row = prs[(prs.full_name == full_name) & (prs.pr_number == pr_number)]
    if not len(row):
        return None
    gold = _frame("gold")
    g = gold[(gold.full_name == full_name) & (gold.pr_number == pr_number)]
    if not len(g):
        return None
    pr = g.iloc[0]
    d = gitsnap.ensure_repo(full_name)
    with gitsnap.repo_lock(full_name):
        pair = gitsnap.delta_pair(d, pr)
        if pair is None:
            return None
        return gitsnap.src_tree(d, pair[1], C.JAVA_SOURCE_EXTENSIONS,
                                C.JAVA_EXCLUDED_PATH_PARTS, full_name)


def before_tree_for(full_name: str, pr_number: int) -> dict | None:
    """The BEFORE tree, so the DIP section can show what the PR actually ADDED."""
    gold = _frame("gold")
    g = gold[(gold.full_name == full_name) & (gold.pr_number == pr_number)]
    if not len(g):
        return None
    pr = g.iloc[0]
    d = gitsnap.ensure_repo(full_name)
    with gitsnap.repo_lock(full_name):
        pair = gitsnap.delta_pair(d, pr)
        if pair is None:
            return None
        return gitsnap.src_tree(d, pair[0], C.JAVA_SOURCE_EXTENSIONS,
                                C.JAVA_EXCLUDED_PATH_PARTS, full_name)


def span(files: dict, path: str, lineno: int, loc: int, pad: int = 0,
         mark: int | None = None, cap: int = 400) -> str:
    """Print [lineno, lineno+loc) with 1-based numbering.

    `mark` gets a ">>>" gutter -- for OCP the judge must find the dispatch chain
    inside a method that can run to hundreds of lines, and hunting for it by eye
    is how a batch gets judged on the header alone.

    A sanity check guards the window: if `lineno` is past the end of the file, or
    the marked line falls outside the window, the batch says so loudly instead of
    printing plausible-looking but WRONG code. That is exactly how the CRLF
    line-doubling bug hid -- the extractor happily printed a different method.
    """
    src = files.get(path)
    if src is None:
        return f"<file not in tree: {path}>"
    lines = src.splitlines()
    if lineno < 1 or lineno > len(lines):
        return (f"<BAD WINDOW: lineno {lineno} outside {path} "
                f"({len(lines)} lines) -- do not judge this row>")
    a = max(0, lineno - 1 - pad)
    b = min(len(lines), lineno - 1 + max(loc, 1) + pad)
    if mark is not None and not (a < mark <= b):
        # widen rather than silently show a window without the thing being judged
        a, b = max(0, mark - 1 - 40), min(len(lines), mark + 40)
    out = []
    for i in range(a, b):
        gutter = ">>>" if mark is not None and i + 1 == mark else "   "
        out.append(f"{gutter}{i+1:>6}  {lines[i]}")
        if len(out) >= cap:
            out.append(f"       ... truncated at {cap} lines ...")
            break
    return "\n".join(out)


def find_method(ix, type_id: str, sig: str):
    t = ix.types.get(type_id)
    if not t:
        return None, None
    for m in t["methods"]:
        if m["signature"] == sig:
            return t, m
    return t, None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--principle", required=True, choices=["srp", "ocp", "lsp", "isp", "dip"])
    ap.add_argument("--file", required=True)
    ap.add_argument("--out-dir")
    ap.add_argument("--only-missing", action="store_true",
                    help="skip rows whose batch file already exists. Extraction has been "
                         "killed mid-run before, leaving a PARTIAL batch directory that "
                         "looks complete; this makes the repair cheap instead of a full "
                         "re-extract.")
    ap.add_argument("--show-verdict", action="store_true",
                    help="print the rubric's own flag in each batch header. OFF BY DEFAULT: "
                         "showing the tool's answer before the judge reads the code is an "
                         "anchoring bias, and it inflates agreement in the tool's direction.")
    a = ap.parse_args()

    f = C.LABELS_DIR / a.file if not Path(a.file).exists() else Path(a.file)
    df = pd.read_csv(f)
    out = Path(a.out_dir) if a.out_dir else (C.LABELS_DIR / f"java_{a.principle}_batches")
    out.mkdir(parents=True, exist_ok=True)

    # BOUNDED cache. This used to be an unbounded dict and it held the FULL
    # SOURCE TREE plus the parsed index for EVERY PR in the sample: measured
    # 2026-09-04 at 10.8 GB resident on a 400-row OCP sample, which drove the
    # machine to 0.6 GB free and 7,388 hard faults/s -- thrashing, and unusable
    # for anything else.
    #
    # A tiny cache loses almost nothing: rows are emitted in sample order and
    # consecutive rows very often share a PR, so the hit rate that matters is
    # the immediate one. Keeping the last few trees captures that while bounding
    # residency to a few hundred MB.
    CACHE_MAX = 4
    cache: "OrderedDict[tuple, tuple]" = OrderedDict()
    n_written = n_empty = 0
    have = set()
    if a.only_missing:
        for fn in out.iterdir():
            m = re.match(r"^(\d+)_", fn.name)
            if m:
                have.add(int(m.group(1)))
        print(f"[extract] {len(have)} batches already on disk, extracting "
              f"{len(df) - len(have)} missing", flush=True)

    for i, r in df.iterrows():
        if i in have:
            continue
        key = (r.full_name, int(r.pr_number))
        if key in cache:
            cache.move_to_end(key)
        else:
            tr = tree_for(*key)
            ix = D.build_index(analyze_tree(tr)) if tr else None
            cache[key] = (tr, ix)
            while len(cache) > CACHE_MAX:
                cache.popitem(last=False)
        tree, ix = cache[key]
        if tree is None or ix is None:
            (out / f"{i:03d}_UNAVAILABLE.txt").write_text(
                f"tree unavailable for {r.full_name}#{r.pr_number}", encoding="utf-8")
            continue

        head = [f"row {i}   {r.full_name}#{int(r.pr_number)}", "=" * 76]
        body: list[str] = []

        if a.principle == "srp":
            t = ix.types.get(r.type_id)
            head.append(f"CLASS {r.type_id}   loc={r.get('class_loc')} "
                        f"methods={r.get('method_count')} wmc={r.get('wmc')} "
                        f"lcom={r.get('lcom')} c3={r.get('c3')}"
                        + (f" hits={r.get('hits')}" if a.show_verdict else ""))
            head.append("QUESTION: does this class have MORE THAN ONE reason to change?")
            if t:
                body.append(span(tree, t["file"], t["lineno"], t["loc"]))

        elif a.principle == "ocp":
            t, m = find_method(ix, r.type_id, r.method)
            head.append(f"CHAIN in {r.type_id}.{r.method}   kind={r.kind} "
                        f"subject={r.subject!r} branches={r.n_branches} "
                        f"closed_set={r.get('closed_set')}")
            head.append(f"cases: {r.get('type_names')}")
            head.append("QUESTION: is the set of variants OPEN (a new one is expected, and")
            head.append("          would force editing this chain) or CLOSED by nature?")
            head.append("SHOWING THE WHOLE ENCLOSING METHOD -- a branch count alone cannot answer this.")
            head.append(">>> marks the dispatch chain itself.")
            if m:
                chain_line = int(r.lineno) if pd.notna(r.get("lineno")) else None
                body.append(span(tree, t["file"], m["lineno"], m["loc"],
                                 mark=chain_line))
            else:
                body.append(f"<method {r.method} not found in {r.type_id} "
                            f"-- do not judge this row>")

        elif a.principle == "lsp":
            t, m = find_method(ix, r.type_id, r.method)
            bt, bm = find_method(ix, r.base_id, r.method)
            head.append(f"OVERRIDE  {r.type_id}.{r.method}")
            head.append(f"BASE      {r.base_id}.{r.method}")
            if a.show_verdict:
                head.append(f"signals   {r.signals}")
            head.append("QUESTION: can an instance of the SUBCLASS be used anywhere the BASE")
            head.append("          is expected, without breaking the caller?")
            if bm:
                body.append("---------------- BASE METHOD ----------------")
                body.append(span(tree, bt["file"], bm["lineno"], bm["loc"]))
            body.append("\n---------------- OVERRIDE ----------------")
            if m:
                body.append(span(tree, t["file"], m["lineno"], m["loc"]))

        elif a.principle == "isp":
            t = ix.types.get(r.iface_id)
            head.append(f"CONTRACT {r.iface_id}   members={r.n_members} "
                        f"implementers={r.get('n_implementers')}")
            head.append("QUESTION: are clients FORCED to depend on members they do not use?")
            if t:
                body.append(span(tree, t["file"], t["lineno"], t["loc"]))
            _, irows = D.contracts(ix, {"fat_members": 6, "min_stubs": 2, "stub_ratio": 0.5})
            for im in [x for x in irows if x["iface_id"] == r.iface_id][:6]:
                it = ix.types.get(im["type_id"])
                body.append(f"\n---------- IMPLEMENTER {im['type_id']}  "
                            f"stubs={im['n_stubs']}/{im['n_implemented']} ----------")
                if it:
                    body.append(span(tree, it["file"], it["lineno"], min(it["loc"], 160)))

        elif a.principle == "dip":
            head.append(f"PR {r.full_name}#{int(r.pr_number)}"
                        + (f"   hits={r.get('dip_risk_hits')}" if a.show_verdict else ""))
            # BLINDING LEAK, FIXED 2026-08-25. `new_cycle` IS the DIP flag
            # (config.DIP_FLAG_INCLUDES_MEAN_D=False makes it the whole flag), and
            # it was printed unconditionally while every other principle's verdict
            # sits behind --show-verdict. A judge that can read the verdict is
            # not judging, it is agreeing, and the resulting precision is an
            # upper bound. The cycle COUNTS stay visible because they are
            # evidence the judge needs; only the conclusion is hidden.
            head.append(f"cycles before={r.get('before_touched_cycles')} "
                        f"after={r.get('after_touched_cycles')}  "
                        + (f"new_cycle={r.get('new_cycle')}  "
                           if a.show_verdict else "")
                        + f"mean_D {r.get('before_mean_d')} -> {r.get('after_mean_d')}")
            head.append("QUESTION: does this PR make a high-level module depend on a CONCRETE")
            head.append("          low-level one (or close an import cycle)?")
            mods = D.dip_modules(ix)
            import graph as dipgraph  # noqa
            # DIFF AGAINST THE BEFORE GRAPH (fixed 2026-09-04). This section
            # used to print a heading that said "NEW IMPORT EDGES" over a loop
            # across EVERY module and EVERY import in the after-state. It never
            # computed a difference, so the judge was shown the whole repository
            # graph as though the PR had created it: 53,473 "new" edges for a PR
            # touching a handful of files. Judging that is judging the wrong
            # question, and it would have made DIP precision meaningless.
            btree = before_tree_for(*key)
            bmods = D.dip_modules(D.build_index(analyze_tree(btree))) if btree else {}
            bcycles = {frozenset(c) for c in dipgraph.find_cycles(bmods)} if bmods else set()
            acycles = list(dipgraph.find_cycles(mods))
            # SHOW THE SAME UNIT THE DETECTOR USES: cyclic PAIRS, not SCCs.
            # (fixed 2026-09-05) This block compared strongly-connected
            # components by exact set identity, so a pre-existing tangle that
            # absorbed one class printed as a brand new cycle -- 32 of the
            # first 60 batches announced a NEW CYCLE while the PR's cycle count
            # had not risen, and one announced one while the count FELL from 13
            # to 12. The judge was therefore answering a different question
            # from the one the detector asks. dip/graph.py::cyclic_pairs
            # already documents why the pair is the stable unit: shrinking an
            # SCC produces no new pair, and growing one produces pairs only for
            # the module that joined. run_java.py charges a pair only when one
            # of its ends is a module this PR touched, so this does the same.
            bpairs = dipgraph.cyclic_pairs(dipgraph.find_cycles(bmods)) if bmods else set()
            apairs = dipgraph.cyclic_pairs(acycles)
            # "touched" = the module's FILE TEXT differs between the two
            # trees. ModuleInfo carries no hash, so an attribute-based guess
            # would silently degrade to "modules the after-tree has and the
            # before-tree does not" and miss every MODIFIED file -- which is
            # most of them. ix.modules carries `file`, and both trees are
            # {path: source} dicts, so the exact comparison is available.
            _btree = btree or {}
            touched_mods = {m["name"] for m in ix.modules
                            if tree.get(m["file"]) != _btree.get(m["file"])}
            charged = sorted(q for q in (apairs - bpairs)
                             if q[0] in touched_mods or q[1] in touched_mods)
            uncharged = sorted(q for q in (apairs - bpairs)
                               if q[0] not in touched_mods and q[1] not in touched_mods)
            if charged:
                body.append(f"NEWLY CYCLIC PAIRS involving a module this PR "
                            f"changed ({len(charged)}):")
                for u, v in charged[:20]:
                    body.append(f"    {u}  <->  {v}")
                if len(charged) > 20:
                    body.append(f"    ... and {len(charged) - 20} more")
            else:
                body.append("NO newly cyclic pair involves a module this PR "
                            "changed.")
            if uncharged:
                body.append(f"({len(uncharged)} other pairs became cyclic, but "
                            f"in modules this PR did not change.)")
            body.append(f"pre-existing cyclic pairs (NOT this PR): {len(bpairs)}")
            bedges = {(mn, t) for mn, mi in bmods.items() for t in mi.imports}
            body.append("\nNEW IMPORT EDGES (absent from the before-tree; "
                        "target marked abstract or CONCRETE):")
            n_new = 0
            for mn, mi in sorted(mods.items()):
                for tgt in sorted(mi.imports):
                    if (mn, tgt) in bedges:
                        continue
                    tm = mods.get(tgt)
                    if tm is None:
                        continue
                    kind = "abstract" if tm.n_abstract_classes else "CONCRETE"
                    body.append(f"  {mn} -> {tgt}   [{kind}]")
                    n_new += 1
            if not n_new:
                body.append("  (none -- this PR added no import edge between "
                            "resolvable internal modules)")

        # The slug must be filename-safe AND unique. DIP rows have no `type_id`,
        # so it falls back to `full_name` -- which contains "/" and silently
        # created a nested path that does not exist. The row index keeps names
        # unique even when two rows share a type (which OCP rows do).
        raw = str(r.get("type_id", r.full_name) or r.full_name)
        slug = re.sub(r"[^0-9A-Za-z_.-]", "_", raw).replace(".", "_")[:60]

        # A HEADER IS NOT MATERIAL. When the entity cannot be located in the
        # index (renamed, moved, or resolved to a different type) `body` comes
        # out empty and this used to write a file containing nothing but the
        # question. On disk that is indistinguishable from a real batch, so the
        # row looks judgeable and gets a label -- 7 SRP rows and 2 ISP rows were
        # labelled this way. Naming it UNJUDGEABLE makes the audit catch it.
        text = "\n".join(body).strip()
        if not text:
            (out / f"{i:03d}_UNJUDGEABLE_{slug}.txt").write_text(
                "\n".join(head)
                + "\n\n<entity not found in the parsed tree -- "
                  "DO NOT JUDGE THIS ROW>\n",
                encoding="utf-8")
            n_empty += 1
            continue
        (out / f"{i:03d}_{slug}.txt").write_text(
            "\n".join(head + [""] + body), encoding="utf-8")
        n_written += 1

    # COMPLETION MARKER. Extraction has been killed partway at least twice, and
    # a directory holding 106 of 400 files looks exactly like a finished one
    # unless somebody counts. The manifest is written LAST and only on a clean
    # exit, so its absence proves the run did not finish.
    (out / "_manifest.json").write_text(json.dumps({
        "sample_file": f.name, "rows": int(len(df)),
        "written": n_written, "unjudgeable": n_empty, "complete": True,
    }, indent=2), encoding="utf-8")

    print(f"wrote {n_written} batches ({n_empty} unjudgeable) to {out}")
    print(f"fill the judge column in {f}, then:")
    print(f"  python vibebench/labels/score_labels.py --file {f.name} "
          f"--pred-col <pred> --label-col llm_{a.principle}_risk")


if __name__ == "__main__":
    main()
