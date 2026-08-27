"""Martin component zones -- DESCRIPTIVE report.

Reads `corpus/arch_results.parquet` only. Writes `arch_zones.csv` and
`arch_packages.csv`. Touches no SOLID result.

READ THIS BEFORE QUOTING ANYTHING FROM HERE
-------------------------------------------
These are DESCRIPTORS of where packages sit on Martin's abstractness /
instability plane. They are NOT violations and NOT validated against the
reading judge. Three rules follow:

  1. Never call a zone-of-pain package "a violation" or "badly designed".
     The zone says a package is concrete and widely depended upon. That is a
     normal and often correct state for a utility or model package.
  2. Never rank agents on these numbers. A package's position is dominated by
     the host repository, not by the pull request that touched it.
  3. Always print the unplaceable share beside any zone distribution. A
     package with no classes has no abstractness; one with no couplings has
     no instability. Those are undefined, not zero.

Run: python vibebench/04_report/report_arch.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

SRC = C.CORPUS_DIR / "arch_results.parquet"
ZONE_ORDER = ["pain", "main_sequence", "intermediate", "uselessness", "unknown"]


def section(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


def report_lang(pk: pd.DataFrame, prs: pd.DataFrame, lang: str) -> pd.DataFrame:
    section(f"{lang.upper()} , Martin component zones (descriptive)")

    ok = prs[prs.status == "ok"]
    print(f"  PRs analysed              {len(ok)} of {len(prs)}")
    if len(prs) > len(ok):
        bad = prs[prs.status != "ok"].status.value_counts().to_dict()
        print(f"  not analysed              {bad}")

    # Deduplicate: the same package recurs across PRs of the same repo. The
    # package is the unit here, not the PR, so count each (repo, package) once.
    uniq = pk.drop_duplicates(subset=["full_name", "package"])
    print(f"  package rows              {len(pk)}")
    print(f"  distinct (repo, package)  {len(uniq)}   <- the unit below")

    n = len(uniq)
    unk = int((uniq.zone == "unknown").sum())
    print(f"\n  UNPLACEABLE               {unk} of {n}  ({unk / n * 100:.1f}%)")
    print(f"    no classes  (A undefined)   {int(uniq.A.isna().sum())}")
    print(f"    no coupling (I undefined)   {int(uniq.I.isna().sum())}")

    placed = uniq[uniq.zone != "unknown"]
    print(f"\n  PLACED ON THE PLANE       {len(placed)}")
    for z in ZONE_ORDER:
        if z == "unknown":
            continue
        k = int((placed.zone == z).sum())
        if len(placed):
            print(f"    {z:<16} {k:>6}  ({k / len(placed) * 100:>5.1f}% of placed)")

    if len(placed):
        print(f"\n  A (abstractness)  median {placed.A.median():.3f}")
        print(f"  I (instability)   median {placed.I.median():.3f}")
        print(f"  D (from main seq) median {placed.D.median():.3f}"
              f"   mean {placed.D.mean():.3f}")

    # touched vs untouched: are the packages agents reach different?
    # `touched` round-trips from parquet as OBJECT dtype (the pr rows carry no
    # value), and `~` on an object column is bitwise-not on ints, not a boolean
    # negation -- it silently yields -1/-2. Cast before using it.
    mask = uniq.touched.fillna(False).astype(bool)
    t, u = uniq[mask], uniq[~mask]
    if len(t) and len(u):
        print(f"\n  packages the PRs TOUCHED   {len(t)}"
              f"   pain {(t.zone == 'pain').mean() * 100:.1f}%")
        print(f"  packages they did not      {len(u)}"
              f"   pain {(u.zone == 'pain').mean() * 100:.1f}%")
        print("  (descriptive contrast only , not an agent attribution)")

    worst = placed.nlargest(8, "D")[
        ["full_name", "package", "Ce", "Ca", "I", "A", "D", "zone"]]
    if len(worst):
        print("\n  furthest from the main sequence:")
        for _, r in worst.iterrows():
            print(f"    D={r.D:.2f}  {r.zone:<14} {str(r.full_name)[:28]:<30}"
                  f" {str(r.package)[:34]}")
    return uniq


def main() -> None:
    if not SRC.exists():
        print(f"no {SRC} , run 03_principles/arch/run_arch.py first")
        return
    df = pd.read_parquet(SRC)
    pkgs = df[df.row_type == "package"]
    prs = df[df.row_type == "pr"]

    frames = []
    for lang in sorted(df.lang.unique()):
        u = report_lang(pkgs[pkgs.lang == lang], prs[prs.lang == lang], lang)
        u = u.copy()
        u["lang"] = lang
        frames.append(u)

    if not frames:
        return
    allu = pd.concat(frames, ignore_index=True)

    section("ZONE DISTRIBUTION , both languages, placed packages only")
    placed = allu[allu.zone != "unknown"]
    tab = (placed.groupby(["lang", "zone"]).size().unstack(fill_value=0)
           .reindex(columns=[z for z in ZONE_ORDER if z != "unknown"], fill_value=0))
    pct = tab.div(tab.sum(axis=1), axis=0) * 100
    print(tab.to_string())
    print("\nas % of placed packages:")
    print(pct.round(1).to_string())

    out_pkg = C.REPORT_DIR / "arch_packages.csv"
    out_zone = C.REPORT_DIR / "arch_zones.csv"
    allu.to_csv(out_pkg, index=False)
    tab.to_csv(out_zone)
    print(f"\nwrote {out_pkg}\nwrote {out_zone}")
    print("\nREMINDER: descriptive only. Not validated, not a violation, "
          "not an agent ranking.")


if __name__ == "__main__":
    main()
