"""
Where a labelling sample is allowed to be written, and when it may be replaced.

WHY THIS EXISTS. On 2026-09-08 the two label GUARDS (`record_labels.py` and
`audit_label_batches.py`) were made language-aware, because both hardcoded a
`java_` prefix and a Python V2 judging session would have written its labels into
the finished Java study's CSVs. The SAMPLERS were not fixed at the same time, and
they are the other half of the same path.

The V1 Python samplers write BARE names: `make_labeling_sample.py` writes
`srp_labeling_sample.csv` with a plain `to_csv` and no guard of any kind. That
file currently holds 240 JUDGED V1 labels. Running the sampler for Python V2
without a prefix would destroy them silently, and the guards downstream would
then look for `py2_srp_labeling_sample.csv`, find nothing, and report an empty
round rather than a clobbered one.

So two rules, both enforced here rather than remembered:

  1. A LANGUAGE PREFIX IS PART OF THE PATH. `lang="py2"` writes
     `py2_srp_labeling_sample.csv`, matching exactly what `record_labels.use_lang`
     and `audit_label_batches --lang` expect. A bare name stays reachable, since
     the archived V1 rounds use it, but it is never the default for new work.

  2. A FILE THAT ALREADY CARRIES JUDGEMENTS IS NOT OVERWRITTEN. Re-running a
     sampler is normal and cheap; re-running it over a round somebody has already
     judged throws away work that costs hours and cannot be regenerated, because
     the judgements are opinions, not derivations. `--force` is available and
     says so out loud.

Rule 2 deliberately checks for JUDGED rows rather than for existence: an
unjudged sample is a draw, and redrawing a draw is exactly what the sampler is
for.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402


def sample_path(stem: str, lang: str = "", holdout: bool = False,
                round_suffix: str = "") -> Path:
    """LABELS_DIR path for one principle's sample.

    `lang` is the same prefix the guards use, so "py2" here and
    `record_labels.use_lang('py2')` there name one file rather than two.
    """
    prefix = f"{lang}_" if lang else ""
    kind = "holdout_sample" if holdout else "labeling_sample"
    return C.LABELS_DIR / f"{prefix}{stem}_{kind}{round_suffix}.csv"


def n_judged(path: Path) -> int:
    """How many rows already carry a judgement, 0 if the file is absent."""
    if not path.exists():
        return 0
    try:
        d = pd.read_csv(path)
    except Exception:                                        # noqa: BLE001
        return 0
    cols = [c for c in d.columns if c.startswith("llm_") or c.startswith("human_")]
    if not cols:
        return 0
    judged = pd.Series(False, index=d.index)
    for c in cols:
        col = d[c]
        judged |= col.notna() & (col.astype(str).str.strip() != "")
    return int(judged.sum())


def guard_overwrite(path: Path, force: bool = False) -> None:
    """Refuse to replace a sample that somebody has already judged.

    Raises rather than prompting: these scripts run unattended in a chain, and a
    prompt nobody sees is the same as no guard at all.
    """
    n = n_judged(path)
    if n and not force:
        raise SystemExit(
            f"\nREFUSING TO OVERWRITE {path.name}: it already holds {n} judged "
            f"rows.\n"
            f"Judgements are opinions, not derivations, so they cannot be "
            f"regenerated.\n"
            f"  * writing a DIFFERENT language? pass --lang py2 (the guards "
            f"expect that prefix)\n"
            f"  * genuinely want to redraw and discard {n} judgements? pass "
            f"--force\n")
    if n:
        print(f"[labels] --force given: replacing {path.name} and DISCARDING "
              f"{n} judged rows")


def resolve(stem: str, lang: str = "", holdout: bool = False,
            round_suffix: str = "", force: bool = False) -> Path:
    """The one call a sampler needs: build the path and check it may be written."""
    p = sample_path(stem, lang, holdout, round_suffix)
    guard_overwrite(p, force)
    return p


def add_args(ap) -> None:
    """Wire `--lang` and `--force` onto a sampler's argument parser."""
    ap.add_argument("--lang", default="",
                    help="language prefix on the output file, e.g. py2. Must "
                         "match record_labels.use_lang() and "
                         "audit_label_batches --lang. Default is the bare V1 "
                         "name.")
    ap.add_argument("--force", action="store_true",
                    help="overwrite a sample that already holds judged rows, "
                         "discarding those judgements")


def _self_check() -> None:
    import tempfile
    print("sample_paths self-check")
    assert sample_path("srp").name == "srp_labeling_sample.csv"
    assert sample_path("srp", "py2").name == "py2_srp_labeling_sample.csv"
    assert sample_path("srp", "java").name == "java_srp_labeling_sample.csv"
    assert sample_path("isp", "py2", holdout=True).name == "py2_isp_holdout_sample.csv"
    assert sample_path("dip", "py2", round_suffix="_census").name == (
        "py2_dip_labeling_sample_census.csv")
    print("  [ok  ] prefixes match what the guards expect")

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        unjudged = d / "a.csv"
        pd.DataFrame({"pr_id": [1, 2], "llm_srp_risk": ["", ""]}).to_csv(unjudged, index=False)
        assert n_judged(unjudged) == 0
        guard_overwrite(unjudged)                       # must NOT raise
        print("  [ok  ] an unjudged draw may be redrawn freely")

        judged = d / "b.csv"
        pd.DataFrame({"pr_id": [1, 2], "llm_srp_risk": [1, ""]}).to_csv(judged, index=False)
        assert n_judged(judged) == 1, n_judged(judged)
        try:
            guard_overwrite(judged)
            raise AssertionError("must have refused")
        except SystemExit:
            pass
        guard_overwrite(judged, force=True)             # --force is the escape
        print("  [ok  ] a judged sample is refused, and --force overrides")

    # The real file this guard exists to protect.
    live = sample_path("srp")
    if live.exists():
        print(f"  [ok  ] live check: {live.name} holds {n_judged(live)} judged "
              f"rows and is now protected")
    print("\nsample_paths.py self-check ALL PASS")


if __name__ == "__main__":
    _self_check()
