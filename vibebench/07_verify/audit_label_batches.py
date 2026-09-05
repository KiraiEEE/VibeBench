"""
Prove that every judged row had real material in front of the judge.

WHY THIS EXISTS. On 2026-09-05 an audit found that 294 of 400 ISP labels were
written for rows whose batch file DID NOT EXIST. The extraction had been killed
partway; it left a directory holding 106 files, which looks exactly like a
directory holding 400 files unless you count them. Nothing in the pipeline
noticed, and the resulting precision figure was a threshold detector graded by
re-applying a threshold.

The rule this enforces: A LABEL IS ONLY ADMISSIBLE IF THE JUDGE COULD SEE THE
CODE. Everything below is a mechanical check of that one sentence, so the claim
rests on the files rather than on anyone's account of what they did.

Checks, per principle:
  A  every CSV row index has a batch file            (the ISP defect)
  B  no batch is a tree-unavailable stub
  C  every batch carries real source, not just a header
  D  the batch names the same PR as its CSV row      (off-by-one / stale dir)
  E  the detector's own verdict is absent            (anchoring leak)
  G  the extraction run finished                     (manifest present)
  F  every judged row is covered by A-E              (the admissibility rule)

Exit 1 on any FAIL, so it can gate a chain.

Run:  python vibebench/07_verify/audit_label_batches.py
      python vibebench/07_verify/audit_label_batches.py --principle isp
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

PRINCIPLES = ["srp", "ocp", "lsp", "isp", "dip"]

# A header plus a couple of blank lines is not source. The smallest legitimate
# batch seen in practice is a DIP cycle listing at 18 lines, so the floor sits
# below that and only catches genuinely empty output.
MIN_LINES = 8

# Column names that carry the detector's answer. If one of these values shows
# up in the batch text the judge was anchored, which inflates agreement in the
# tool's direction and makes the precision estimate meaningless.
VERDICT_TOKENS = [
    r"\bsrp_risk\s*=", r"\bsmell\s*=\s*(True|False)", r"\bviolation\s*=\s*(True|False)",
    r"\bflagged\s*=\s*(True|False)", r"\bdip_risk\s*=", r"\bhits\s*=",
]

fails: list[str] = []
warns: list[str] = []


def fail(msg: str) -> None:
    fails.append(msg)
    print(f"  FAIL  {msg}")


def warn(msg: str) -> None:
    warns.append(msg)
    print(f"  warn  {msg}")


def ok(msg: str) -> None:
    print(f"  ok    {msg}")


def audit(p: str) -> None:
    csv = C.LABELS_DIR / f"java_{p}_labeling_sample.csv"
    bdir = C.LABELS_DIR / f"java_{p}_batches"
    print(f"\n=== {p.upper()} ===")
    if not csv.exists():
        warn(f"no sample CSV at {csv.name}, nothing to audit")
        return
    df = pd.read_csv(csv)
    judge = f"llm_{p}_risk"
    n_judged = int(df[judge].notna().sum()) if judge in df.columns else 0
    print(f"  {len(df)} rows, {n_judged} judged")

    if not bdir.exists():
        fail(f"{p}: batch directory does not exist, yet {n_judged} rows are judged")
        return

    # -- G: did the extraction run actually finish? ------------------------
    man = bdir / "_manifest.json"
    if not man.exists():
        fail(f"{p}: NO _manifest.json -- the extraction did not finish, so this "
             f"batch directory is partial no matter how many files it holds")
    else:
        import json
        m = json.loads(man.read_text())
        if int(m.get("rows", -1)) != len(df):
            fail(f"{p}: manifest says {m.get('rows')} rows, CSV has {len(df)} "
                 f"-- the batches belong to a DIFFERENT sample")
        else:
            ok(f"G  extraction finished ({m.get('written')} written, "
               f"{m.get('unjudgeable')} unjudgeable)")

    # -- A: index coverage -------------------------------------------------
    files: dict[int, Path] = {}
    for f in bdir.iterdir():
        m = re.match(r"^(\d+)_", f.name)
        if m:
            files[int(m.group(1))] = f
    missing = [i for i in range(len(df)) if i not in files]
    if missing:
        fail(f"{p}: {len(missing)} of {len(df)} rows have NO batch file "
             f"(first missing index {missing[0]})")
    else:
        ok(f"A  all {len(df)} rows have a batch file")

    # -- F: the admissibility rule ----------------------------------------
    if judge in df.columns:
        judged_idx = set(df.index[df[judge].notna()])
        blind = sorted(judged_idx & set(missing))
        if blind:
            fail(f"{p}: {len(blind)} rows were JUDGED WITH NO SOURCE AVAILABLE "
                 f"(indices {blind[0]}..{blind[-1]}). These labels are inadmissible.")
        else:
            ok(f"F  every judged row had material on disk")

    # -- B, C, D, E over the files that do exist ---------------------------
    unavailable, tiny, mismatched, leaked = [], [], [], []
    for i, f in sorted(files.items()):
        if "UNAVAILABLE" in f.name or "UNJUDGEABLE" in f.name:
            unavailable.append(i)
            continue
        try:
            txt = f.read_text(encoding="utf-8", errors="replace")
        except Exception:                                   # noqa: BLE001
            tiny.append(i)
            continue
        if txt.count("\n") < MIN_LINES:
            tiny.append(i)
        if i < len(df):
            want = str(df.iloc[i].get("full_name", ""))
            if want and want not in txt:
                mismatched.append(i)
        for pat in VERDICT_TOKENS:
            # the header legitimately prints size metrics; only a printed
            # BOOLEAN verdict is a leak
            if re.search(pat, txt):
                leaked.append((i, pat))
                break

    for name, bad, msg in [
        ("B", unavailable, "batches are tree-unavailable stubs"),
        ("C", tiny, f"batches are under {MIN_LINES} lines, i.e. header only"),
        ("D", mismatched, "batches name a different PR than their CSV row"),
    ]:
        if bad:
            fail(f"{p}: {len(bad)} {msg} (e.g. index {bad[0]})")
        else:
            ok(f"{name}  no {msg}")
    if leaked:
        fail(f"{p}: {len(leaked)} batches LEAK the detector's verdict "
             f"(e.g. index {leaked[0][0]}, pattern {leaked[0][1]})")
    else:
        ok("E  no batch leaks the detector's verdict")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--principle", choices=PRINCIPLES)
    a = ap.parse_args()
    print("=" * 70)
    print("LABEL BATCH AUDIT -- can every label be traced to source the judge saw?")
    print("=" * 70)
    for p in ([a.principle] if a.principle else PRINCIPLES):
        audit(p)
    print("\n" + "=" * 70)
    print(f"{len(fails)} FAIL, {len(warns)} warn")
    print("=" * 70)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
