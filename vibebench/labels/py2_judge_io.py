"""
The two operations a Python V2 judging session needs, and nothing that reveals a
verdict.

  show    print batch files by row index. Reads ONLY the batch directory, never
          the sample CSV, which carries the detector's prediction (rubric rule 2).
  record  write labels from a JSON file through record_labels.py, after
          use_lang('py2'). A JSON file, not shell arguments, because the written
          reasons contain quotes and parentheses that shell quoting mangles.
  status  judged counts per principle.

JSON format: {"<row index>": [true|false, "reason, at least 12 characters"]}

Run:  python vibebench/labels/py2_judge_io.py show ocp 0 9
      python vibebench/labels/py2_judge_io.py record ocp labels.json
      python vibebench/labels/py2_judge_io.py status
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))
sys.path.insert(0, str(_HERE.parent))
import config as C          # noqa: E402
import record_labels as R   # noqa: E402

R.use_lang("py2")


# A judge must see every line of a batch before labelling it. The tool that
# relays this output truncates past ~30,000 characters, which on the first run
# silently hid the middle of rows 3 to 6. So output stops BEFORE a row would
# overflow the budget and names the row to resume from; a row is never cut.
BUDGET = 24000


def show(p: str, a: int, b: int) -> None:
    bdir = C.LABELS_DIR / f"py2_{p}_batches"
    files = {int(m.group(1)): f for f in bdir.iterdir()
             if (m := re.match(r"^(\d+)_", f.name))}
    used = 0
    for i in range(a, b + 1):
        f = files.get(i)
        if f is None:
            text = "<no batch file>"
        elif "UNJUDGEABLE" in f.name or "UNAVAILABLE" in f.name:
            text = f"<{f.name}: do not judge>"
        else:
            text = f.read_text(encoding="utf-8", errors="replace")
        block = f"\n{'#' * 30} {p} row {i} {'#' * 30}\n{text}"
        if used and used + len(block) > BUDGET:
            print(f"\n>>> BUDGET REACHED: rows {a}..{i - 1} shown IN FULL. "
                  f"CONTINUE FROM row {i}")
            return
        if len(block) > BUDGET:
            block = block[:BUDGET] + (f"\n>>> ROW {i} EXCEEDS ONE SCREEN: printed "
                                      f"{BUDGET} of {len(block)} chars. Use "
                                      f"`showrow {p} {i}` to page it.")
        print(block)
        used += len(block)
    print(f"\n>>> rows {a}..{b} shown IN FULL")


def showrow(p: str, i: int, part: int) -> None:
    """Page one oversized batch in BUDGET-sized parts, so it is read whole."""
    bdir = C.LABELS_DIR / f"py2_{p}_batches"
    f = next(x for x in bdir.iterdir() if x.name.startswith(f"{i:03d}_"))
    text = f.read_text(encoding="utf-8", errors="replace")
    parts = (len(text) + BUDGET - 1) // BUDGET
    print(text[part * BUDGET:(part + 1) * BUDGET])
    print(f"\n>>> {p} row {i}: part {part + 1} of {parts}")


def record(p: str, path: str) -> None:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    labels = {int(k): (bool(v[0]), str(v[1])) for k, v in raw.items()}
    R.record(p, labels)


if __name__ == "__main__":
    # The Windows console is cp932 here; batches contain arbitrary Unicode.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "show":
        show(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]))
    elif cmd == "showrow":
        showrow(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]) if len(sys.argv) > 4 else 0)
    elif cmd == "record":
        record(sys.argv[2], sys.argv[3])
    else:
        R.status()
