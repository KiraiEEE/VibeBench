"""
Validate every analysed PR's touched-file list against GitHub's own count.

THE IMPOSSIBILITY THIS EXPLOITS. Our `n_touched_files` counts only `.java`
files that are neither test nor machine-generated. GitHub's `changed_files`
counts EVERY path the PR changed, in every language, including deletions. Ours
is therefore a strict subset of theirs, and

    n_touched_files > changed_files

is not "a discrepancy to investigate". It is arithmetically impossible, and it
can only mean the before/after pair we diffed does not describe that PR.

WHY IT HAPPENS (measured 2026-09-02). Tier 2 reconstructs a PR from its own
commit SHAs and diffs each commit against its first parent. When a commit's
parent lies outside the PR (a merge from the base branch, a rebase, a shallow
graft), that diff sweeps in unrelated work. Observed:

    graphql-java/graphql-java#4218                  ours 123  github  20  tier 2
    open-telemetry/opentelemetry-java-instrument..  ours 240  github  37  tier 2

Those PRs were credited with files nobody on that PR wrote, so any violation
found in the surplus belongs to someone else's code and inflates the agent's
rate. 4 of 9 sampled PRs over-counted, and both severe cases were tier 2, which
is 1,280 of 3,855 analysed PRs.

WHAT THIS SCRIPT DOES. One API call per PR, records ours vs GitHub's, and
writes `corpus/file_count_audit.parquet`. It CHANGES NOTHING -- repair is a
separate, deliberate step, because rewriting file lists is exactly the kind of
action that should never be a side effect of an audit.

Run:  python vibebench/07_verify/validate_file_counts.py [--workers N] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "03_principles"))
import config as C          # noqa: E402
import gitsnap              # noqa: E402

OUT = C.CORPUS_DIR / "file_count_audit.parquet"
_lock = threading.Lock()


def changed_files_batch(batch) -> dict:
    """{pr_id: changed_files} for up to ~80 PRs in ONE GraphQL request.

    WHY BATCHING, not more workers (measured 2026-09-02). The REST endpoint
    needs one request per PR, so auditing 3,855 PRs meant 3,855 round trips.
    On this machine's link that is fatal rather than merely slow: with ZERO load
    from us, github.com failed a plain TCP probe and api.github.com answered 5
    of 6, and the audit spent 8-minute stretches paused waiting for the link.
    More workers made it worse, not better -- the failure mode is connection
    COUNT, not bandwidth.

    GraphQL aliases let one request carry many PRs, so 3,855 round trips become
    ~50. Measured: 4 PRs in a single 1.4s request. Fewer, larger requests is the
    right shape for an unreliable connection, and it is also gentler on the API
    than 20 parallel REST callers.

    Rate limiting is not the constraint either way: `gh` is authenticated
    (5,000/h), and this uses ~50 requests instead of 3,855.
    """
    parts, keymap = [], {}
    for i, r in enumerate(batch):
        owner, _, name = str(r.full_name).partition("/")
        if not owner or not name:
            continue
        alias = f"p{i}"
        keymap[alias] = r.pr_id
        parts.append(
            f'{alias}: repository(owner:"{owner}", name:"{name}") '
            f'{{ pullRequest(number:{int(r.pr_number)}) {{ changedFiles }} }}')
    if not parts:
        return {}
    query = "query {\n" + "\n".join(parts) + "\n}"
    for attempt in (1, 2):
        try:
            r = subprocess.run(["gh", "api", "graphql", "-f", f"query={query}"],
                               capture_output=True, text=True, timeout=180,
                               **gitsnap.NO_WINDOW)
        except FileNotFoundError:
            sys.exit("gh is not on PATH")
        except (subprocess.TimeoutExpired, OSError):
            r = None
        if r is not None and r.stdout.strip():
            try:
                payload = json.loads(r.stdout)
            except json.JSONDecodeError:
                payload = None
            if payload and "data" in payload:
                out = {}
                for alias, node in (payload["data"] or {}).items():
                    pid = keymap.get(alias)
                    # A deleted repo/PR yields null -- record it as unknown
                    # rather than guessing, so it is never silently counted OK.
                    if pid is None or not node or not node.get("pullRequest"):
                        continue
                    out[pid] = node["pullRequest"].get("changedFiles")
                return out
        if attempt == 1 and not gitsnap.network_is_up():
            gitsnap.wait_for_network()
            continue
        break
    return {}


def changed_files(full_name: str, pr_number: int) -> int | None:
    for attempt in (1, 2):
        try:
            r = subprocess.run(
                ["gh", "api", f"repos/{full_name}/pulls/{pr_number}",
                 "-q", ".changed_files"],
                capture_output=True, text=True, timeout=60,
                **gitsnap.NO_WINDOW)
        except FileNotFoundError:
            sys.exit("gh is not on PATH")
        except (subprocess.TimeoutExpired, OSError):
            r = None
        if r is not None and r.returncode == 0 and r.stdout.strip():
            try:
                return int(r.stdout.strip())
            except ValueError:
                return None
        if attempt == 1 and not gitsnap.network_is_up():
            gitsnap.wait_for_network()
            continue
        break
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    pr = pd.read_parquet(C.CORPUS_DIR / "java_pr_results.parquet")
    ok = pr[(pr.status == "ok") & pr.n_touched_files.notna()].copy()

    done = {}
    if OUT.exists():                      # resumable: an outage must not restart it
        prev = pd.read_parquet(OUT)
        done = dict(zip(prev.pr_id, prev.github_changed))
        print(f"[audit] resuming, {len(done):,} PRs already checked", flush=True)

    todo = [r for r in ok.itertuples() if r.pr_id not in done]
    if a.limit:
        todo = todo[:a.limit]
    print(f"[audit] {len(ok):,} analysed PRs, {len(todo):,} to check", flush=True)

    rows, t0 = [], time.time()
    BATCH = 60
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    print(f"[audit] {len(batches)} GraphQL requests instead of {len(todo):,} REST calls",
          flush=True)
    seen = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(changed_files_batch, b): b for b in batches}
        for fut in as_completed(futs):
            b = futs[fut]
            try:
                got = fut.result()
            except Exception:             # noqa: BLE001
                got = {}
            with _lock:
                for r in b:
                    gh = got.get(r.pr_id)
                    rows.append({"pr_id": r.pr_id, "agent": r.agent,
                                 "full_name": r.full_name,
                                 "pr_number": r.pr_number,
                                 "pair_tier": r.pair_tier,
                                 "ours": int(r.n_touched_files),
                                 "github_changed": gh,
                                 "impossible": bool(gh is not None
                                                    and int(r.n_touched_files) > gh)})
                seen += len(b)
            el = time.time() - t0
            bad = sum(1 for x in rows if x["impossible"])
            print(f"[audit] {seen}/{len(todo)}  impossible={bad}  "
                  f"{seen/max(el,1e-9)*3600:,.0f}/h  "
                  f"eta {(len(todo)-seen)/max(seen/max(el,1e-9),1e-9)/60:.0f} min",
                  flush=True)
            _save(rows, done, ok)

    _save(rows, done, ok)
    df = pd.read_parquet(OUT)
    chk = df[df.github_changed.notna()]
    bad = chk[chk.impossible]
    print(f"\n[audit] checked {len(chk):,} PRs")
    print(f"[audit] IMPOSSIBLE (ours > github): {len(bad):,} "
          f"({100*len(bad)/max(len(chk),1):.1f}%)")
    if len(bad):
        print("\n  by reconstruction tier:")
        t = chk.groupby("pair_tier").impossible.agg(["size", "sum", "mean"])
        t.columns = ["prs", "impossible", "rate"]
        print(t.round(3).to_string())
        print("\n  by agent:")
        t2 = chk.groupby("agent").impossible.agg(["size", "sum", "mean"])
        t2.columns = ["prs", "impossible", "rate"]
        print(t2.round(3).to_string())
        print("\n  worst offenders:")
        print(bad.nlargest(8, "ours")[
            ["full_name", "pr_number", "ours", "github_changed", "pair_tier"]
        ].to_string(index=False))
    print(f"\n[audit] written to {OUT}")


def _save(rows, done, ok) -> None:
    if not rows:
        return
    new = pd.DataFrame(rows)
    if OUT.exists():
        old = pd.read_parquet(OUT)
        new = pd.concat([old[~old.pr_id.isin(new.pr_id)], new], ignore_index=True)
    gitsnap.write_parquet_atomic(new, OUT, index=False)


if __name__ == "__main__":
    main()
