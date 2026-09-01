"""
Python <-> Java-sidecar bridge, the twin of `ts_bridge.py`.

The sidecar needs files ON DISK because a `JavaParserTypeSolver` maps package
`a.b.C` to `<root>/a/b/C.java`; that is also how source roots are inferred from
package declarations (the Phase 0b finding -- rooting at the repo root drops
supertype resolution from 93% to 17%). So a tree dict is materialised into a
temp directory, analysed, and deleted.

A per-(repo, sha) FACTS CACHE sits in front of that. It matters more here than
in TypeScript: the Java corpus is 867 PRs over only 27 repos, so the same
commit is analysed repeatedly as one PR's `after` tree and another's `before`.
Facts are gzipped JSON keyed by a hash of the tree content, so the cache is
correct even when the same sha is reached by different routes.

Never raises: returns {"ok": False, "error": ...} like the TS bridge, so a
single bad tree cannot kill a long run.

Self-check: python vibebench/03_principles/java_bridge.py
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C
import gitsnap  # noqa: E402  -- NO_WINDOW  # noqa: E402

SIDECAR_DIR = Path(__file__).resolve().parent / "java_sidecar"
SIDECAR_JAR = SIDECAR_DIR / "target" / "java-sidecar-1.0-jar-with-dependencies.jar"
FACTS_CACHE = C.CORPUS_DIR / "java_facts_cache"
TIMEOUT_S = getattr(C, "JAVA_SIDECAR_TIMEOUT_S", 900)
HEAP = getattr(C, "JAVA_SIDECAR_HEAP", "3g")

# JVM flags, all three measured on this machine rather than guessed.
#
# MaxHeapFreeRatio/MinHeapFreeRatio: -Xmx is a CEILING, but G1 does not hand
#   pages back to the OS once it has grown into them, so a worker that parses
#   one big tree keeps ~3 GB resident for the rest of its life even while
#   parsing tiny ones. Measured with 4 workers: system available RAM fell to
#   1.53 GB and stayed there. These ratios make the heap shrink after each
#   collection, so resident memory tracks the CURRENT tree, not the largest
#   one seen so far.
# ActiveProcessorCount: each JVM otherwise sizes its GC thread pool from all
#   12 logical cores, so N workers spawn ~12N GC threads that fight for the
#   same cores. Capping it keeps the machine responsive and, because the
#   contention is removed, does not cost throughput.
# ExitOnOutOfMemoryError: without it a JVM at its heap limit spends minutes in
#   back-to-back full GCs before failing, freezing the whole machine. With it
#   the worker dies at once with a non-zero exit, which analyze_tree already
#   turns into a recorded failure carrying its reason.
JVM_TUNING = [
    "-XX:MaxHeapFreeRatio=30",
    "-XX:MinHeapFreeRatio=10",
    "-XX:ActiveProcessorCount=2",
    "-XX:+ExitOnOutOfMemoryError",
]


# Bump when a change makes previously cached facts WRONG rather than merely
# stale. v2 = the CRLF line-doubling fix (2026-08-04): the tree CONTENT is
# identical, so without this every cached entry would be served with the old
# 2x-inflated line numbers and the fix would be invisible.
_CACHE_VERSION = "v2-crlf"


def _parser_fingerprint() -> str:
    """Hash of the things that decide WHAT the facts are: the sidecar jar and
    this bridge (which materialises the files the parser reads).

    The cache key covers the tree CONTENT, so it cannot notice a change to the
    parser itself -- same bytes in, different facts out, and the entry is served
    stale. That is precisely how the CRLF line-doubling defect survived: the
    materialisation changed, the tree hash did not, and only a hand-edited
    `_CACHE_VERSION` forced the re-parse.

    Recording this fingerprint INSIDE each entry makes the invalidation
    automatic. Entries written before this existed carry no fingerprint and are
    grandfathered: they were verified against a fresh parse on a random sample of
    8 corpus trees (8/8 byte-identical) before this check was introduced.
    """
    h = hashlib.sha1()
    for f in (SIDECAR_JAR, Path(__file__)):
        try:
            h.update(f.read_bytes() if f.suffix == ".py" else str(f.stat().st_mtime_ns).encode())
            h.update(str(f.stat().st_size).encode())
        except OSError:
            h.update(b"missing")
    return h.hexdigest()[:16]


def _key(files: dict[str, str]) -> str:
    h = hashlib.sha1()
    h.update(_CACHE_VERSION.encode())
    h.update(b"\0")
    for p in sorted(files):
        h.update(p.encode("utf-8", "replace"))
        h.update(b"\0")
        h.update(files[p].encode("utf-8", "replace"))
        h.update(b"\0")
    return h.hexdigest()


def analyze_tree(files: dict[str, str], use_cache: bool = True) -> dict:
    """{path: source} -> facts dict. Never raises."""
    if not files:
        return {"ok": True, "files": [], "types": [], "modules": [],
                "sourceRoots": [], "resolvedSupertypes": 0, "unresolvedSupertypes": 0}
    if not SIDECAR_JAR.exists():
        return {"ok": False, "error": f"sidecar jar missing: {SIDECAR_JAR}"}

    cache_file = None
    if use_cache:
        FACTS_CACHE.mkdir(parents=True, exist_ok=True)
        k = _key(files)
        cache_file = FACTS_CACHE / f"{k[:2]}" / f"{k}.json.gz"
        if cache_file.exists():
            try:
                with gzip.open(cache_file, "rt", encoding="utf-8") as fh:
                    hit = json.load(fh)
                fp = hit.get("_parser_fp")
                # No fingerprint = written before this guard existed; those were
                # verified byte-identical against a fresh parse and are trusted.
                # A DIFFERENT fingerprint means the parser changed under us:
                # recompute rather than serve a stale fact set.
                if fp is None or fp == _parser_fingerprint():
                    return hit
            except Exception:
                pass  # corrupt cache entry: fall through and recompute

    tmp = Path(tempfile.mkdtemp(prefix="vbjava_"))
    try:
        skipped = 0
        for rel, src in files.items():
            # defend against absolute paths / traversal in archive entries
            safe = Path(str(rel).replace("\\", "/").lstrip("/"))
            if ".." in safe.parts:
                continue
            # A path git accepts can still be illegal on Windows (trailing dots,
            # reserved device names like CON/NUL, over-long paths). Skip the one
            # file rather than losing the whole tree -- the alternative is an
            # exception that fails an entire PR, which is how mochilang/mochi
            # silently cost 9.7% of the corpus.
            try:
                f = tmp / safe
                f.parent.mkdir(parents=True, exist_ok=True)
                # WRITE BYTES, NEVER write_text. `write_text` opens in TEXT mode,
                # so Python translates every "\n" to os.linesep -- on Windows that
                # turns the "\r\n" these files already contain into "\r\r\n".
                # JavaParser then counts the stray "\r" as its own line break and
                # every line number and `loc` it reports comes back DOUBLED.
                # 100% of this corpus arrives CRLF (git applies autocrlf to
                # `git archive` too), so the whole Java line-number layer was
                # 2x inflated until 2026-08-04. Structure -- types, methods,
                # signatures, chains, override pairs -- was never affected;
                # positions and LOC were.
                f.write_bytes(src.encode("utf-8", "replace"))
            except OSError:
                skipped += 1
                continue
        if skipped:
            print(f"  [java_bridge] skipped {skipped} file(s) unwritable on this OS", flush=True)
        try:
            proc = subprocess.run(
                ["java", f"-Xmx{HEAP}", *JVM_TUNING,
                 "-cp", str(SIDECAR_JAR), "vb.Facts", str(tmp)],
                capture_output=True, timeout=TIMEOUT_S,
                **gitsnap.NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"sidecar timeout after {TIMEOUT_S}s"}
        except FileNotFoundError:
            return {"ok": False, "error": "java not on PATH"}
        if proc.returncode != 0:
            return {"ok": False,
                    "error": f"exit {proc.returncode}: {proc.stderr.decode('utf-8', 'replace')[-400:]}"}
        try:
            out = json.loads(proc.stdout.decode("utf-8", "replace"))
        except Exception as e:
            return {"ok": False, "error": f"bad JSON from sidecar: {e}"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if use_cache and cache_file is not None and out.get("ok"):
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_out = cache_file.with_suffix(".tmp")
            out["_parser_fp"] = _parser_fingerprint()
            with gzip.open(tmp_out, "wt", encoding="utf-8") as fh:
                json.dump(out, fh)
            os.replace(tmp_out, cache_file)
        except Exception:
            pass
    return out


def _self_check() -> None:
    src = {
        "src/main/java/app/Base.java":
            "package app;\npublic class Base { public String go(String s) { return s; } }\n",
        "src/main/java/app/Sub.java":
            "package app;\npublic class Sub extends Base {\n"
            "  @Override public String go(String s) { throw new UnsupportedOperationException(); }\n}\n",
    }
    print("java_bridge self-check")
    r = analyze_tree(src, use_cache=False)
    assert r.get("ok"), r
    ids = {t["id"] for t in r["types"]}
    assert ids == {"app.Base", "app.Sub"}, ids
    sub = next(t for t in r["types"] if t["id"] == "app.Sub")
    assert sub["supertypes"] == ["app.Base"], sub["supertypes"]
    go = next(m for m in sub["methods"] if m["name"] == "go")
    assert go["bodyKind"] == "throw", go
    assert go["lineno"] > 0
    print("  facts ok: FQN ids, resolved supertype, refused-bequest body kind")

    a = analyze_tree(src, use_cache=True)
    b = analyze_tree(src, use_cache=True)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    print("  cache round-trip identical")

    # REGRESSION GUARD: CRLF sources must not double line numbers. The corpus
    # is 100% CRLF (git applies autocrlf to `git archive` too), and `write_text`
    # in TEXT mode turned each CR-LF into CR-CR-LF, so JavaParser counted the
    # stray CR as its own break and reported every lineno and loc at 2x until
    # 2026-08-04. Structure was never affected; positions and LOC were.
    crlf = ("package app;\r\n"
            "public class A {\r\n"
            "  public int one(){ return 1; }\r\n"
            "\r\n"
            "  public int two(){ return 2; }\r\n"
            "}\r\n")
    rc = analyze_tree({"src/main/java/app/A.java": crlf}, use_cache=False)
    tc = rc["types"][0]
    assert tc["lineno"] == 2, "CRLF doubling is back: class A is not on line 2"
    two = next(m for m in tc["methods"] if m["name"] == "two")
    assert two["lineno"] == 5, "CRLF doubling is back: two() is not on line 5"
    assert tc["loc"] <= len(crlf.splitlines()), "loc exceeds real line count"
    print("  CRLF sources report TRUE line numbers (no CR-CR-LF doubling)")

    assert analyze_tree({}, use_cache=False)["ok"]
    bad = analyze_tree({"X.java": "class {{{ broken"}, use_cache=False)
    assert bad.get("ok"), "a broken file must NOT fail the whole tree"
    assert any(not f["parseOk"] for f in bad["files"]), "parse error must be surfaced"
    print("  empty tree ok; broken file surfaces parse_error without failing the run")
    print("ALL PASS")


if __name__ == "__main__":
    _self_check()
