"""
Write judged labels, and REFUSE to write one that cannot be traced to source.

WHY THIS EXISTS. Round 1 of the Java validation was voided on 2026-09-05: 294
of 400 ISP labels had been written for rows whose batch file did not exist, and
200 more were written at 3 seconds a row. Nothing stopped either. The labels
went into the CSV by a throwaway pandas assignment, which will happily set 400
values whether or not anybody read anything.

This module is the replacement for that assignment. It enforces three rules
mechanically, so the guarantee rests on the code rather than on the judge's
account of what they did:

  1. NO SOURCE, NO LABEL. A row whose batch file is missing, or is an
     UNJUDGEABLE/UNAVAILABLE stub, cannot be labelled. This is the exact defect
     that voided round 1, and it is now impossible rather than merely
     discouraged.

  2. EVERY LABEL CARRIES A REASON. `notes` was empty for all 1,995 round-1
     rows, which is why the rush was invisible on disk and had to be caught by
     the user instead of the pipeline. A label with no reason is rejected.

  3. WRITES ARE ATOMIC AND INCREMENTAL. Judging is the long phase and it will be
     interrupted. Labels are flushed per call, so progress is never held only in
     a conversation that can be lost.

Use from a small script:

    import record_labels as R
    R.record("isp", {
        12: (True,  "141 members, every implementer stubs 130 of them"),
        13: (False, "fluent builder, chained calls are the intended use"),
    })

Then check progress with:  python vibebench/labels/record_labels.py --status
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
MIN_NOTE = 12          # a note shorter than this is not a reason


def _paths(p: str) -> tuple[Path, Path]:
    return (C.LABELS_DIR / f"java_{p}_labeling_sample.csv",
            C.LABELS_DIR / f"java_{p}_batches")


def batch_index(p: str) -> dict[int, Path]:
    """Map row index -> batch file, EXCLUDING stubs that carry no source."""
    _, bdir = _paths(p)
    out: dict[int, Path] = {}
    if not bdir.exists():
        return out
    for f in bdir.iterdir():
        m = re.match(r"^(\d+)_", f.name)
        if not m:
            continue
        if "UNJUDGEABLE" in f.name or "UNAVAILABLE" in f.name:
            continue                      # a stub is not material; rule 1
        out[int(m.group(1))] = f
    return out


def record(principle: str, labels: dict[int, tuple[bool, str]]) -> None:
    """Write {row_index: (verdict, reason)}. Raises rather than write a bad row."""
    if principle not in PRINCIPLES:
        raise ValueError(f"unknown principle {principle}")
    csv, _ = _paths(principle)
    df = pd.read_csv(csv)
    col = f"llm_{principle}_risk"
    if col not in df.columns:
        df[col] = ""
    if "notes" not in df.columns:
        df["notes"] = ""
    df[col] = df[col].astype(object)
    df["notes"] = df["notes"].astype(object)

    have = batch_index(principle)
    problems = []
    for i, (verdict, note) in labels.items():
        if i not in range(len(df)):
            problems.append(f"row {i} is out of range (0-{len(df)-1})")
        elif i not in have:
            problems.append(f"row {i} has NO SOURCE on disk -- refusing to label it")
        elif not isinstance(verdict, bool):
            problems.append(f"row {i} verdict must be a bool, got {type(verdict).__name__}")
        elif not note or len(note.strip()) < MIN_NOTE:
            problems.append(f"row {i} has no reason (notes must be >= {MIN_NOTE} chars)")
    if problems:
        raise ValueError("refusing to write:\n  " + "\n  ".join(problems))

    for i, (verdict, note) in labels.items():
        df.at[i, col] = bool(verdict)
        df.at[i, "notes"] = note.strip()

    tmp = csv.with_suffix(".csv.tmp")
    df.to_csv(tmp, index=False)
    tmp.replace(csv)
    done = df[col].notna().sum()
    print(f"[{principle}] +{len(labels)} rows -> {done}/{len(df)} judged", flush=True)


def status() -> None:
    print(f"{'principle':10}{'rows':>6}{'judged':>8}{'with notes':>12}"
          f"{'batches':>9}{'unjudgeable':>13}")
    for p in PRINCIPLES:
        csv, bdir = _paths(p)
        if not csv.exists():
            print(f"{p:10}{'-- no sample --':>48}")
            continue
        df = pd.read_csv(csv)
        col = f"llm_{p}_risk"
        judged = int(df[col].notna().sum()) if col in df.columns else 0
        # fillna FIRST. `astype(str)` on an all-NaN float column does not
        # produce the string "nan" here, so the old check counted every empty
        # note as filled -- it reported 200/200 notes on a file with none.
        notes = int(df["notes"].fillna("").astype(str).str.strip().ne("").sum()) \
            if "notes" in df.columns else 0
        have = len(batch_index(p))
        stub = 0
        if bdir.exists():
            stub = sum(1 for f in bdir.iterdir()
                       if "UNJUDGEABLE" in f.name or "UNAVAILABLE" in f.name)
        print(f"{p:10}{len(df):>6}{judged:>8}{notes:>12}{have:>9}{stub:>13}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.status:
        status()
    else:
        # self-check: the refusal must actually fire
        try:
            record("isp", {999999: (True, "this row does not exist at all")})
            print("SELF-CHECK FAILED: an impossible row was accepted")
            sys.exit(1)
        except ValueError as e:
            assert "out of range" in str(e), e
            print("self-check ok: out-of-range row refused")
        status()
