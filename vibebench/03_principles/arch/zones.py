"""Martin component zones -- pure, descriptive classification of packages.

DESCRIPTIVE ONLY. This module classifies where a package sits on Robert
Martin's abstractness/instability plane. It deliberately does NOT produce a
before/after delta and does NOT emit a violation flag, for a measured reason:
the DIP validation in this project scored `mean_d_worse` at precision 0.000
(0 of 7 flagged PRs were real). Mean D moved when the graph was RECOMPOSED --
files added or moved -- not when a dependency actually inverted. Treating D as
a change signal is known-bad here. Treating the A/I position of a package as a
DESCRIPTION of that package is a different and defensible use.

The plane (Martin, "Agile Software Development", ch. 20):

      A=1  +-----------------------+
           | zone of uselessness   |   abstract AND unstable:
           |                       |   abstractions nobody depends on
           |     main sequence     |
           |        A + I = 1      |
           | zone of pain          |   concrete AND stable: rigid, everything
      A=0  +-----------------------+   depends on it and it cannot change
          I=0                     I=1

  I = Ce / (Ce + Ca)      instability   (0 = nothing depends out, maximally stable)
  A = Na / Nc             abstractness  (share of classes that are abstract)
  D = |A + I - 1|         distance from the main sequence

Self-check: run this file.
"""
from __future__ import annotations

import math

# Corner radius for the two zones. Martin draws them as the regions near
# (0,0) and (1,1); the radius is a presentation choice, not a measurement --
# reported alongside every result so a reader can reposition it.
ZONE_RADIUS = 0.5
# Band around A + I = 1 that counts as "on the main sequence".
MAIN_SEQUENCE_BAND = 0.15


def zone_of(abstractness: float | None,
            instability: float | None,
            radius: float = ZONE_RADIUS,
            band: float = MAIN_SEQUENCE_BAND) -> str:
    """One of: pain | uselessness | main_sequence | intermediate | unknown.

    `unknown` when either coordinate is undefined -- A is None for a package
    with no classes, I is None for a package with no internal couplings at
    all. Those are NOT zeros; collapsing them to zero would put every
    class-free utility package in the zone of pain.
    """
    if abstractness is None or instability is None:
        return "unknown"
    a, i = abstractness, instability
    if math.dist((a, i), (0.0, 0.0)) <= radius:
        return "pain"
    if math.dist((a, i), (1.0, 1.0)) <= radius:
        return "uselessness"
    if abs(a + i - 1.0) <= band:
        return "main_sequence"
    return "intermediate"


def classify_packages(martin: dict[str, dict[str, float]],
                      radius: float = ZONE_RADIUS,
                      band: float = MAIN_SEQUENCE_BAND) -> list[dict]:
    """martin_metrics() output -> one descriptive row per package."""
    rows = []
    for pkg, m in sorted(martin.items()):
        rows.append({
            "package": pkg,
            "Ce": m["Ce"], "Ca": m["Ca"],
            "I": m["I"], "A": m["A"], "D": m["D"],
            "zone": zone_of(m["A"], m["I"], radius, band),
        })
    return rows


def zone_summary(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[r["zone"]] = out.get(r["zone"], 0) + 1
    return out


# --------------------------------------------------------------------------
if __name__ == "__main__":
    assert zone_of(0.0, 0.0) == "pain"
    assert zone_of(0.1, 0.2) == "pain"
    assert zone_of(1.0, 1.0) == "uselessness"
    assert zone_of(0.9, 0.85) == "uselessness"
    assert zone_of(0.5, 0.5) == "main_sequence"
    assert zone_of(1.0, 0.0) == "main_sequence"   # A+I=1 exactly
    assert zone_of(0.0, 1.0) == "main_sequence"
    # None must never be read as zero -- a class-free package is not "pain"
    assert zone_of(None, 0.0) == "unknown"
    assert zone_of(0.0, None) == "unknown"
    # (0.75, 0.75) is 0.354 from (1,1) -- inside the uselessness corner
    assert zone_of(0.75, 0.75) == "uselessness", zone_of(0.75, 0.75)
    # genuinely intermediate: >radius from both corners, >band off the sequence
    assert zone_of(0.2, 0.5) == "intermediate", zone_of(0.2, 0.5)

    m = {"a": {"Ce": 3, "Ca": 0, "I": 1.0, "A": 0.0, "D": 0.0},
         "b": {"Ce": 0, "Ca": 4, "I": 0.0, "A": 0.0, "D": 1.0},
         "c": {"Ce": 1, "Ca": 1, "I": 0.5, "A": None, "D": None}}
    rows = classify_packages(m)
    assert [r["zone"] for r in rows] == ["main_sequence", "pain", "unknown"]
    assert zone_summary(rows) == {"main_sequence": 1, "pain": 1, "unknown": 1}
    print("zones.py self-check OK:", zone_summary(rows))
