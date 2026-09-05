"""
Crash-safe writes for every stage of the pipeline.

WHY THIS EXISTS. This machine loses mains power and its internet connection
without warning, and both have already happened mid-run. Neither is exotic: any
long-running pipeline on real hardware has to assume it will be killed at an
arbitrary instant.

THE FAILURE THAT MATTERS IS NOT THE CRASH, IT IS THE SURVIVING HALF-FILE.
A process killed during `to_parquet` / `to_csv` / `write_text` leaves a
TRUNCATED file that later readers accept without complaint:

  * parquet keeps its metadata FOOTER at the end of the file, so a truncated
    parquet is not "partly readable", it is unreadable, and one lost checkpoint
    can invalidate thousands of correctly extracted files that no longer have a
    record saying which PR they belong to;
  * a truncated CSV of judged labels reads back fine and silently drops rows,
    which changes a precision figure with nothing anywhere reporting an error.
    Labels are the most expensive artifact this project produces;
  * a truncated JSON cache raises on read, which is loud, but the entry is then
    lost rather than reused.

THE FIX is the standard one: write to a sibling temp file, flush it to the
platter, then `os.replace`, which is atomic on NTFS and POSIX alike. A reader
sees either the previous complete version or the new complete version, never a
half-written one. The temp file sits in the SAME directory so the rename cannot
cross a filesystem boundary and degrade to a copy.

Self-check: `python vibebench/safeio.py` (offline, uses a temp directory).
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def _fsync_path(p: Path) -> None:
    """Force the temp file's bytes out before the rename.

    Without this the rename can land in the directory while the file's contents
    are still in the OS cache, so a power cut immediately after `os.replace`
    leaves a correctly-named file full of nothing.
    """
    try:
        fd = os.open(p, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass                    # best effort; the rename is still atomic


def _tmp_for(path) -> tuple[Path, Path]:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path, path.with_name(path.name + ".tmp")


def write_bytes(path, data: bytes) -> None:
    """Atomically write bytes (extracted source files, blobs)."""
    path, tmp = _tmp_for(path)
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def write_text(path, text: str, encoding: str = "utf-8") -> None:
    """Atomically write text."""
    write_bytes(path, text.encode(encoding, "replace"))


def write_json(path, obj) -> None:
    """Atomically write JSON (caches, derived file lists)."""
    write_text(path, json.dumps(obj))


def to_parquet(df, path, **kw) -> None:
    """Atomically write a DataFrame to parquet.

    The most important one. See the module docstring: a truncated parquet is
    unreadable, not degraded, so a power cut during a checkpoint destroys the
    whole run's bookkeeping rather than the last few rows of it.
    """
    path, tmp = _tmp_for(path)
    df.to_parquet(tmp, **kw)
    _fsync_path(tmp)
    os.replace(tmp, path)


def to_csv(df, path, **kw) -> None:
    """Atomically write a DataFrame to CSV.

    Used for LABEL files above all. A truncated label CSV reads back without
    error and silently loses rows, which moves a precision figure with nothing
    reporting a problem -- the single worst failure mode in this project,
    because the number still looks reasonable.
    """
    path, tmp = _tmp_for(path)
    df.to_csv(tmp, **kw)
    _fsync_path(tmp)
    os.replace(tmp, path)

# --------------------------------------------------------------------------
# DISK-SPACE GUARD
# --------------------------------------------------------------------------
# A full disk does NOT stop this pipeline, it makes every write fail while the
# run keeps going and records the failures as data problems. On a corpus that
# grows to ~110 GB of clones that is a realistic ending, and the symptom looks
# identical to a network fault, so it would be diagnosed wrong.
#
# Checked BEFORE each expensive stage and periodically during it, so the run
# stops with a clear message while the results on disk are still complete and
# consistent, rather than degrading into a long tail of silent write errors.
MIN_FREE_GB = 15.0


def free_gb(path=None) -> float:
    """Free space in GB on the volume holding `path` (default: the corpus)."""
    import shutil
    target = Path(path) if path else Path(__file__).resolve().parent / "corpus"
    while not target.exists() and target != target.parent:
        target = target.parent
    return shutil.disk_usage(target).free / (1024 ** 3)


def check_disk(min_gb: float = MIN_FREE_GB, path=None, fatal: bool = True) -> float:
    """Return free GB; warn, or raise when below `min_gb` and `fatal`.

    Raising is deliberate. A pipeline that continues on a full disk produces a
    corpus that is silently incomplete, and incompleteness in this project has
    repeatedly turned out to be AGENT-BIASED, which is a wrong result rather
    than a missing one.
    """
    gb = free_gb(path)
    if gb < min_gb:
        msg = ("DISK LOW: %.1f GB free, below the %.1f GB floor. Stopping so the "
               "results on disk stay complete. Free space (superseded datasets, "
               "clones of repositories no longer in the gold set) and re-run; "
               "everything is resumable." % (gb, min_gb))
        if fatal:
            raise RuntimeError(msg)
        print("[disk] " + msg, flush=True)
    return gb


if __name__ == "__main__":
    import tempfile
    import shutil

    import pandas as pd

    d = Path(tempfile.mkdtemp(prefix="vb_safeio_"))
    try:
        p = d / "sub" / "a.txt"
        write_text(p, "hello")
        assert p.read_text(encoding="utf-8") == "hello"
        assert not (p.parent / "a.txt.tmp").exists(), "temp file must be gone"
        print("  [ok  ] write_text creates parents and leaves no temp behind")

        write_json(d / "x.json", {"a": [1, 2]})
        assert json.loads((d / "x.json").read_text(encoding="utf-8")) == {"a": [1, 2]}
        print("  [ok  ] write_json round-trip")

        df = pd.DataFrame({"a": [1, 2, 3], "b": list("xyz")})
        to_parquet(df, d / "t.parquet", index=False)
        assert len(pd.read_parquet(d / "t.parquet")) == 3
        to_csv(df, d / "t.csv", index=False)
        assert len(pd.read_csv(d / "t.csv")) == 3
        print("  [ok  ] to_parquet / to_csv round-trip")

        # Overwriting must never destroy the previous version until the new one
        # is complete: the old file has to stay readable right up to the rename.
        to_parquet(pd.DataFrame({"a": [9]}), d / "t.parquet", index=False)
        assert pd.read_parquet(d / "t.parquet")["a"].tolist() == [9]
        print("  [ok  ] overwrite replaces atomically")

        # A crash BETWEEN the temp write and the rename must leave the ORIGINAL
        # intact -- that is the whole guarantee.
        _, tmp = _tmp_for(d / "t.parquet")
        pd.DataFrame({"a": [999]}).to_parquet(tmp, index=False)   # no rename
        assert pd.read_parquet(d / "t.parquet")["a"].tolist() == [9], (
            "a stranded temp file must not affect the real one")
        os.remove(tmp)
        print("  [ok  ] a stranded temp file leaves the original untouched")

        print("\nsafeio.py self-check ALL PASS")
    finally:
        shutil.rmtree(d, ignore_errors=True)
