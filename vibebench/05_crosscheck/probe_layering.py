"""Layering-feasibility probe (READ-ONLY; writes nothing but a CSV report).

Question: can layers be inferred from directory structure across this corpus,
or do too few repositories use a recognisable layered layout for a
layering-violation detector to measure anything but a biased subset?

Method: reconstruct each repo's FULL head tree from the gitsnap tree cache
(key = sha1("full_name@sha@ext_tag"), so no fetching), then look at every
directory segment and match it against a four-tier layer vocabulary. A repo
"has layering" only if >= 2 DISTINCT tiers appear -- a lone `core/` or `app/`
directory is not a layered architecture.

Run: python vibebench/05_crosscheck/probe_layering.py
"""
from __future__ import annotations

import gzip
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

# Four tiers, outermost to innermost. A directory segment must match EXACTLY
# (lowercased) -- substring matching would make "coreutils" a core layer.
TIERS: dict[str, set[str]] = {
    "presentation": {
        "api", "web", "ui", "view", "views", "controller", "controllers",
        "rest", "http", "handler", "handlers", "endpoint", "endpoints",
        "cli", "graphql", "routes", "router", "resources", "servlet",
    },
    "application": {
        "service", "services", "application", "usecase", "usecases",
        "usecase", "interactor", "interactors", "workflow", "workflows",
        "manager", "managers", "facade",
    },
    "domain": {
        "domain", "core", "model", "models", "entity", "entities",
        "aggregate", "aggregates", "valueobject",
    },
    "infrastructure": {
        "infra", "infrastructure", "repository", "repositories", "dao",
        "persistence", "storage", "store", "db", "database", "adapter",
        "adapters", "gateway", "gateways", "datasource", "provider",
        "providers", "client", "clients",
    },
}
TIER_OF = {name: tier for tier, names in TIERS.items() for name in names}

# Directories that are never architecture (vendored, generated, tests).
IGNORE_SEGMENTS = {
    "node_modules", "dist", "build", "target", "vendor", "third_party",
    "site-packages", ".git", "__pycache__", "docs", "doc", "examples",
    "example", "sample", "samples", "test", "tests", "testing", "spec",
    "specs", "fixtures", "testdata", "benchmark", "benchmarks",
}


def cache_key(full_name: str, sha: str, extensions) -> str:
    tag = ",".join(sorted(extensions))
    return hashlib.sha1(f"{full_name}@{sha}@{tag}".encode()).hexdigest()


def load_tree(full_name: str, sha: str, extensions) -> dict | None:
    key = cache_key(full_name, sha, extensions)
    p = C.TREE_CACHE_DIR / key[:2] / f"{key}.json.gz"
    if not p.exists():
        return None
    try:
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def tiers_in_tree(paths) -> tuple[set[str], Counter]:
    """Distinct layer tiers present, and the raw segment hits behind them."""
    found: set[str] = set()
    hits: Counter = Counter()
    for path in paths:
        segs = [s.lower() for s in str(path).replace("\\", "/").split("/")[:-1]]
        if any(s in IGNORE_SEGMENTS for s in segs):
            continue
        for s in segs:
            tier = TIER_OF.get(s)
            if tier:
                found.add(tier)
                hits[f"{tier}:{s}"] += 1
    return found, hits


def probe(gold_path: Path, extensions, label: str) -> pd.DataFrame:
    gold = pd.read_parquet(gold_path)
    # one representative commit per repo -- the layout does not move per PR
    reps = (gold.dropna(subset=["merge_sha"])
                .groupby("full_name", as_index=False).first())
    rows = []
    all_hits: Counter = Counter()
    for _, r in reps.iterrows():
        tree = load_tree(r.full_name, r.merge_sha, extensions)
        if tree is None:
            rows.append({"repo": r.full_name, "status": "no_cached_tree",
                         "n_files": 0, "n_tiers": 0, "tiers": ""})
            continue
        tiers, hits = tiers_in_tree(tree.keys())
        all_hits.update(hits)
        rows.append({"repo": r.full_name, "status": "ok", "n_files": len(tree),
                     "n_tiers": len(tiers), "tiers": "|".join(sorted(tiers))})
    df = pd.DataFrame(rows)

    ok = df[df.status == "ok"]
    print(f"\n{'='*72}\n{label}\n{'='*72}")
    print(f"  repos in gold set          {len(df)}")
    print(f"  with a cached head tree    {len(ok)}"
          f"   ({len(df) - len(ok)} not cached)")
    if len(ok):
        for k in (0, 1, 2, 3, 4):
            n = (ok.n_tiers == k).sum() if k < 4 else (ok.n_tiers >= 4).sum()
            lbl = f"{k} tiers" if k < 4 else "4 tiers"
            print(f"    {lbl:<10} {n:>4}  ({n/len(ok)*100:>5.1f}%)")
        viable = ok[ok.n_tiers >= 2]
        print(f"\n  >>> LAYERED (>=2 distinct tiers): {len(viable)} of {len(ok)}"
              f"  ({len(viable)/len(ok)*100:.1f}%)")
        if all_hits:
            print("\n  most common layer directories:")
            for name, c in all_hits.most_common(10):
                print(f"    {name:<32} {c:>6} files")
    return df


if __name__ == "__main__":
    out = C.REPORT_DIR if hasattr(C, "REPORT_DIR") else Path(".")
    frames = []
    for gold, exts, label in [
        (C.GOLD_SET, C.SOURCE_EXTENSIONS, "PYTHON"),
        (getattr(C, "GOLD_SET_JAVA", None), C.JAVA_SOURCE_EXTENSIONS, "JAVA"),
    ]:
        if gold is None or not Path(gold).exists():
            print(f"skip {label}: no gold set")
            continue
        df = probe(Path(gold), exts, label)
        df["lang"] = label.lower()
        frames.append(df)
    if frames:
        allf = pd.concat(frames, ignore_index=True)
        dest = Path(out) / "layering_probe.csv"
        allf.to_csv(dest, index=False)
        print(f"\nwrote {dest}")
