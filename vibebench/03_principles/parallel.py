"""
Repo-partitioned parallel dispatch, for the analysis harnesses.

WHY THIS EXISTS. The harnesses use a ThreadPoolExecutor, and the work they do
after the parse -- `build_index`, `snapshot`, the five detectors -- is pure
Python bytecode. The GIL serialises that, so extra threads buy nothing once the
tree cache is warm. Measured on the V2 Java flagged pass, 2026-09-01:

    | workers | throughput   |
    |---------|--------------|
    |       4 | 35.7 PRs/min |
    |      10 | 40.0 PRs/min |

2.5x the workers for 11% more throughput, with `python` pinned at 91-100% of ONE
core while `java` and `git` sat idle. The interpreter was the limit, not the
machine.

WHY PROCESSES NEED A PARTITION, NOT JUST A SWAP. `gitsnap.repo_lock` is a
`threading.Lock` keyed by repository: it stops two workers issuing git commands
in the same clone at once, which a git directory does not tolerate. Under
processes that lock is per-interpreter and therefore a NO-OP, so a naive swap
lets two processes collide in one repository. Git protects its own index, so the
result is not corruption -- it is FAILED COMMANDS, which this pipeline records as
ordinary fetch failures. That loss would be correlated with repository, and
repository correlates with agent, which makes it the same silent agent-biased
loss this project keeps having to hunt down.

THE FIX IS STRUCTURAL: partition the work so that ONE REPOSITORY IS OWNED BY
EXACTLY ONE WORKER for the whole run. Then no cross-process lock is needed,
because the collision it prevents cannot occur. That also preserves the clone
cache's locality -- a repo is fetched once and reused by every PR of that repo.

LOAD BALANCING. Repository sizes are wildly uneven (one repo in this corpus holds
229 PRs while most hold 1-3), so groups are dispatched LARGEST FIRST. With a
long tail of small groups behind them, the big ones start early and the workers
finish together instead of one straggler holding the run open.

Self-check: `python vibebench/03_principles/parallel.py`
"""
from __future__ import annotations

import os
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from typing import Callable, Iterable, Iterator, Sequence


def partition_by_repo(rows: Sequence, key: str = "full_name") -> list[list]:
    """Group rows by repository, largest group first.

    The ordering is what makes the pool finish evenly: a 229-PR repository
    dispatched last would still be running long after everything else is done.
    """
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        groups[getattr(r, key)].append(r)
    return sorted(groups.values(), key=len, reverse=True)


def _run_group(group: Sequence, fn: Callable, args: tuple) -> list:
    """Execute `fn` over one repository's rows, in order.

    Runs inside the worker. Returns a LIST so the parent gets one pickled
    payload per repository rather than one per PR.
    """
    out = []
    for row in group:
        try:
            out.append((row, fn(row, *args), None))
        except Exception as e:                       # noqa: BLE001
            out.append((row, None, f"{type(e).__name__}: {e}"[:200]))
    return out


def imap_by_repo(rows: Sequence,
                 fn: Callable,
                 args: tuple = (),
                 workers: int = 4,
                 mode: str = "process") -> Iterator[tuple]:
    """Yield `(row, result, error)` as repositories complete.

    `mode="process"` gives true parallelism (separate interpreters, no GIL);
    `mode="thread"` reproduces the old behaviour and is kept so the two can be
    compared on identical inputs, which is the only honest way to claim a
    speed-up.

    `fn` and every element of `args` must be picklable under "process" -- i.e.
    module-level functions and plain data, never closures or open handles.
    """
    if not rows:
        return
    groups = partition_by_repo(rows)
    Pool = ProcessPoolExecutor if mode == "process" else ThreadPoolExecutor
    with Pool(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(_run_group, g, fn, args): g for g in groups}
        for fut in as_completed(futs):
            try:
                for item in fut.result():
                    yield item
            except Exception as e:                   # noqa: BLE001
                # An entire group died (worker killed, OOM). Surface every row
                # in it as an error rather than losing the repository silently:
                # a vanished repository is exactly the correlated loss this
                # module exists to prevent.
                for row in futs[fut]:
                    yield (row, None, f"group failed: {type(e).__name__}: {e}"[:200])


def recommended_workers(mem_gb_free: float,
                        mem_gb_per_worker: float = 2.0,
                        cpu_count: int | None = None,
                        reserve_gb: float = 2.0) -> int:
    """Worker count from MEASURED free memory, not from the core count.

    Every worker-count mistake in this project came from sizing on cores while
    memory was the binding constraint: 8 workers x 3 GB heap on a 15.4 GB
    machine, then 5, then 4, each discovered by watching throughput collapse.
    `reserve_gb` is the headroom the desktop needs -- free RAM measured while
    idle is not free RAM under load.
    """
    cpu = cpu_count or (os.cpu_count() or 4)
    by_mem = int(max(0.0, mem_gb_free - reserve_gb) // max(mem_gb_per_worker, 0.1))
    return max(1, min(cpu, by_mem))


if __name__ == "__main__":
    from collections import namedtuple

    Row = namedtuple("Row", "pr_id full_name")

    rows = ([Row(i, "big/repo") for i in range(10)]
            + [Row(100 + i, "mid/repo") for i in range(4)]
            + [Row(200 + i, f"small/r{i}") for i in range(3)])

    g = partition_by_repo(rows)
    assert [len(x) for x in g] == [10, 4, 1, 1, 1], [len(x) for x in g]
    print("  [ok  ] partition groups by repo, largest first")

    seen = {tuple(sorted(r.full_name for r in grp)) for grp in g}
    assert all(len(set(x)) == 1 for x in seen), "a repo must live in ONE group"
    print("  [ok  ] every repository is owned by exactly one group (no cross-process git)")

    # thread mode is enough to prove the contract without spawning interpreters
    out = list(imap_by_repo(rows, str, (), workers=3, mode="thread"))
    assert len(out) == len(rows), (len(out), len(rows))
    assert {r.pr_id for r, _, _ in out} == {r.pr_id for r in rows}
    assert all(err is None for _, _, err in out)
    print("  [ok  ] every row comes back exactly once, in any completion order")

    def boom(row):
        if row.pr_id == 0:
            raise ValueError("synthetic")
        return "fine"

    out = list(imap_by_repo(rows, boom, (), workers=2, mode="thread"))
    errs = [(r.pr_id, e) for r, _, e in out if e]
    assert len(out) == len(rows), "a failing row must not lose its siblings"
    assert len(errs) == 1 and errs[0][0] == 0, errs
    print("  [ok  ] one failing row is isolated, the rest of its repo still returns")

    assert recommended_workers(15.4, 2.0, 12) == 6, recommended_workers(15.4, 2.0, 12)
    assert recommended_workers(4.0, 2.0, 12) == 1, recommended_workers(4.0, 2.0, 12)
    assert recommended_workers(64.0, 2.0, 4) == 4, "cpu count still caps it"
    assert recommended_workers(0.5, 2.0, 12) == 1, "never returns zero"
    print("  [ok  ] worker count sized from free MEMORY, capped by cores, never zero")

    print("\nparallel.py self-check ALL PASS")
