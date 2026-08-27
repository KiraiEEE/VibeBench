"""
Verify every extracted file byte-for-byte against git, and repair what fails.

WHY. Step 2 writes each touched file to `corpus/ai_files*/<pr_id>/<path>`. Two
events can damage that copy, and BOTH are silent:

  * a process killed mid-write leaves a TRUNCATED file that every later reader
    accepts as complete (fixed going forward by `write_atomic`, but files
    written before that fix are not covered by it);
  * a re-run treats a PR as already done by checking file EXISTENCE only, so a
    damaged file is never re-extracted.

THE SIZE CHECK IS NOT ENOUGH, AND IS PARTLY CIRCULAR. When a file already
exists, `clone_and_extract` records `n_bytes` by reading the file's own size off
disk. Comparing disk size against that number therefore compares disk to disk,
and a truncated file passes. That was a real hole in an earlier verification of
this corpus.

THE NON-CIRCULAR TEST is git itself. Git stores every blob addressed by the
SHA-1 of its content and refuses to serve an object whose checksum does not
match, so `<sha>:<path>` regenerates the original bytes or errors. Comparing our
copy against that is a direct comparison with ground truth, not an inference.

`git cat-file --batch` is used rather than one `git show` per file: it answers
thousands of lookups down a single pipe, turning a ~20-minute process-spawn
storm into seconds per repository.

Exit code is 0 when every file verifies. `--repair` deletes the failures so the
next `clone_and_extract` run re-fetches exactly those, and nothing else.

Run:  python vibebench/07_verify/verify_extracted_files.py --lang java
      python vibebench/07_verify/verify_extracted_files.py --lang java --repair
      python vibebench/07_verify/verify_extracted_files.py --lang java --newer-than 90
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "03_principles"))
import config as C  # noqa: E402
import gitsnap  # noqa: E402

MANIFEST = {"java": C.CLONE_MANIFEST_JAVA, "py": C.CLONE_MANIFEST,
            "ts": C.CLONE_MANIFEST_TS}


def verify_repo(full_name: str, rows: pd.DataFrame) -> dict:
    """Check every extracted file of one repository against its git blob.

    Returns counts plus the list of local paths whose content does NOT match.
    A blob git cannot produce (history pruned, ref gone) is `unverifiable`, and
    is reported separately: it is not evidence of damage and must never be
    silently counted as either pass or fail.
    """
    d = gitsnap.repo_dir(full_name)
    res = {"repo": full_name, "ok": 0, "bad": 0, "unverifiable": 0, "bad_paths": []}
    if not (d / ".git").exists():
        res["unverifiable"] = len(rows)
        return res

    specs, locals_ = [], []
    for r in rows.itertuples():
        specs.append("%s:%s" % (r.head_sha, r.file_path))
        locals_.append(r.local_path)

    try:
        p = subprocess.run(["git", "cat-file", "--batch"], cwd=str(d),
                           input="\n".join(specs).encode() + b"\n",
                           capture_output=True, timeout=900)
    except (subprocess.TimeoutExpired, OSError):
        res["unverifiable"] = len(rows)
        return res
    if p.returncode != 0:
        res["unverifiable"] = len(rows)
        return res

    # `--batch` emits "<sha> <type> <size>\n<payload>\n" per hit, or
    # "<spec> missing\n" per miss. Parse positionally so a miss cannot shift
    # every later file onto the wrong blob.
    buf, pos = p.stdout, 0
    for local in locals_:
        nl = buf.find(b"\n", pos)
        if nl < 0:
            res["unverifiable"] += 1
            continue
        header = buf[pos:nl]
        pos = nl + 1
        if header.endswith(b"missing"):
            res["unverifiable"] += 1
            continue
        try:
            size = int(header.rsplit(b" ", 1)[1])
        except (IndexError, ValueError):
            res["unverifiable"] += 1
            continue
        payload = buf[pos:pos + size]
        pos += size + 1                      # trailing newline
        try:
            with open(local, "rb") as fh:
                disk = fh.read()
        except OSError:
            res["bad"] += 1
            res["bad_paths"].append(local)
            continue
        if hashlib.sha1(disk).digest() == hashlib.sha1(payload).digest():
            res["ok"] += 1
        else:
            res["bad"] += 1
            res["bad_paths"].append(local)
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", default="java", choices=sorted(MANIFEST))
    ap.add_argument("--repair", action="store_true",
                    help="delete files that fail, so the next extraction run "
                         "re-fetches exactly those")
    ap.add_argument("--newer-than", type=float, default=0,
                    help="only check files modified within N minutes")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()

    man = pd.read_parquet(MANIFEST[a.lang])
    rows = man[(man.extract_status == "ok") & (man.n_bytes > 0)
               & (man.head_sha.astype(str).str.len() == 40)].copy()
    assert len(rows), "no extracted files to verify"

    if a.newer_than:
        now = time.time()

        def age_min(p):
            try:
                return (now - os.path.getmtime(p)) / 60.0
            except OSError:
                return 1e9
        rows = rows[[age_min(p) < a.newer_than for p in rows.local_path.values]]
        print("[verify] restricted to files modified in the last %.0f min: %d"
              % (a.newer_than, len(rows)))

    groups = list(rows.groupby("full_name"))
    print("[verify] %d files across %d repositories, %d workers"
          % (len(rows), len(groups), a.workers), flush=True)

    ok = bad = unv = 0
    bad_paths: list[str] = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(verify_repo, n, g): n for n, g in groups}
        for i, fu in enumerate(as_completed(futs), 1):
            r = fu.result()
            ok += r["ok"]; bad += r["bad"]; unv += r["unverifiable"]
            bad_paths += r["bad_paths"]
            if r["bad"]:
                print("  !! %-45s %d file(s) DO NOT match git"
                      % (r["repo"], r["bad"]), flush=True)
            if i % 100 == 0:
                print("[verify] %d/%d repos, %.1f min elapsed"
                      % (i, len(groups), (time.time() - t0) / 60), flush=True)

    total = ok + bad + unv
    print("\n" + "=" * 66)
    print("byte-identical to git : %d  (%.3f%%)" % (ok, 100 * ok / max(total, 1)))
    print("DAMAGED               : %d" % bad)
    print("unverifiable          : %d  (blob no longer reachable; NOT damage)"
          % unv)
    print("=" * 66)

    if bad_paths:
        out = C.CORPUS_DIR / ("damaged_files_%s.txt" % a.lang)
        out.write_text("\n".join(bad_paths), encoding="utf-8")
        print("wrote the damaged list to %s" % out)
        if a.repair:
            gone = 0
            for p in bad_paths:
                try:
                    os.remove(p)
                    gone += 1
                except OSError:
                    pass
            print("[repair] deleted %d damaged files. Re-run "
                  "clone_and_extract.py --lang %s to re-fetch exactly those."
                  % (gone, a.lang))
        else:
            print("run again with --repair to delete them, then re-extract.")
        sys.exit(1)

    print("No damaged files. The extracted corpus matches its source exactly.")


if __name__ == "__main__":
    main()
