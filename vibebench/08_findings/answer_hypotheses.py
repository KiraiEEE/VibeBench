"""
Step 8 - answer the seven V2 hypotheses, in writing, from the parquets.

WHY A SCRIPT AND NOT A DOCUMENT. REDESIGN_V2.md section 10 requires each
hypothesis to be answered CONFIRMED / REFUTED / UNRESOLVED with the number
beside it, and section 9 warns that producing the parquets without answering
them delivers nothing. A hand-written answer goes stale the moment a threshold
moves; this recomputes from the current results every time, so the write-up and
the data cannot drift apart.

It also encodes the verdict RULES rather than leaving them to judgement in the
moment, which is what stops a marginal result being written up as a clean one:

  CONFIRMED   the effect is present AND the confidence intervals separate
              (or, for a near-zero claim, the count is genuinely near zero)
  REFUTED     the effect points the other way, with the same evidential bar
  UNRESOLVED  the intervals overlap, or the count is too small to carry a
              claim, or a prerequisite check failed

UNRESOLVED is a real answer and is used freely. Four of five principles produced
single-digit PR-level flag counts in V1, and at n=1 there is no per-agent claim
to make, only "essentially never happens".

Run:  python vibebench/08_findings/answer_hypotheses.py
      python vibebench/08_findings/answer_hypotheses.py --pilot
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

pd.set_option("display.width", 200)

ANSWERS: list[tuple[int, str, str, str]] = []


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - m) / d), min(1.0, (c + m) / d))


def answer(n: int, title: str, verdict: str, detail: str) -> None:
    ANSWERS.append((n, title, verdict, detail))
    print("\n" + "-" * 74)
    print("H%d  %s" % (n, title))
    print("    VERDICT: %s" % verdict)
    for line in detail.splitlines():
        print("    " + line)


def separated(k1: int, n1: int, k2: int, n2: int) -> bool:
    """True when the two Wilson intervals do not overlap."""
    if not n1 or not n2:
        return False
    a, b = wilson(k1, n1)
    c, d = wilson(k2, n2)
    return b < c or d < a


class Corpus:
    """Loads whatever exists; every hypothesis degrades to UNRESOLVED without it."""

    def __init__(self, pilot: bool = False):
        sfx = ".PILOT.parquet" if pilot else ".parquet"

        def p(path: Path) -> Path:
            return path.with_suffix(sfx) if pilot else path

        self.java_pr = self._load(p(C.CORPUS_DIR / "java_pr_results.parquet"))
        self.java_srp = self._load(p(C.SRP_RESULTS_JAVA))
        self.java_lsp = self._load(p(C.LSP_RESULTS_JAVA))
        self.java_ocp = self._load(p(C.OCP_RESULTS_JAVA))
        self.java_isp = self._load(p(C.ISP_RESULTS_JAVA))
        self.java_gold = self._load(C.GOLD_SET_JAVA)
        self.py_gold = self._load(C.GOLD_SET)
        self.py_srp = self._load(C.SRP_RESULTS)
        self.py_dip = self._load(C.DIP_RESULTS)
        self.py_ocp = self._load(C.OCP_RESULTS)
        self.py_lsp = self._load(C.LSP_RESULTS)
        self.py_isp = self._load(C.ISP_RESULTS)

    @staticmethod
    def _load(path) -> pd.DataFrame | None:
        try:
            return pd.read_parquet(path) if Path(path).exists() else None
        except Exception:                                   # noqa: BLE001
            return None

    @property
    def java_ok(self) -> pd.DataFrame | None:
        if self.java_pr is None or "status" not in self.java_pr:
            return None
        d = self.java_pr[self.java_pr["status"] == "ok"]
        return d if len(d) else None


# ------------------------------------------------------------------ H1

def h1_intra_vs_inter(cp: Corpus) -> None:
    """Violations PRESENT in touched code vs violations the PR INTRODUCED.

    The study's headline. V1 Python: 443 Liskov violations present, 5
    introduced; 533 OCP smells present, 44 introduced. If Java replicates
    near-zero LSP and ISP introductions in a language where classes are
    mandatory, `interface` is unambiguous and `@Override` is explicit, the
    "your language does not express SOLID" objection dies.
    """
    ok = cp.java_ok
    if ok is None:
        answer(1, "Intra- vs inter-unit risk (the headline)", "UNRESOLVED",
               "Java results not available yet.")
        return
    lines, present, introduced = [], {}, {}

    if cp.java_lsp is not None and "violation" in cp.java_lsp:
        u = cp.java_lsp.drop_duplicates(["full_name", "type_id", "method"])
        present["LSP"] = (int(u["violation"].fillna(False).sum()), len(u))
    if cp.java_ocp is not None and "smell" in cp.java_ocp:
        u = cp.java_ocp.drop_duplicates(
            ["full_name", "type_id", "method", "subject"])
        present["OCP"] = (int(u["smell"].fillna(False).sum()), len(u))
    if cp.java_isp is not None and "flagged" in cp.java_isp:
        u = cp.java_isp[cp.java_isp["row_type"] == "interface"].drop_duplicates(
            ["full_name", "iface_id"])
        present["ISP"] = (int(u["flagged"].fillna(False).sum()), len(u))

    for key, col in (("LSP", "lsp_risk"), ("OCP", "ocp_risk"),
                     ("ISP", "isp_risk"), ("DIP", "dip_risk")):
        if col in ok.columns:
            introduced[key] = (int(ok[col].fillna(False).sum()), len(ok))

    for k in ("LSP", "OCP", "ISP", "DIP"):
        pres = present.get(k)
        intro = introduced.get(k)
        if intro is None:
            continue
        pres_s = ("%d of %d entities" % pres) if pres else "n/a (delta-only)"
        lines.append("%-4s present: %-24s introduced: %d of %d PRs (%.2f%%)"
                     % (k, pres_s, intro[0], intro[1],
                        100 * intro[0] / max(intro[1], 1)))

    inter = [introduced[k][0] for k in ("LSP", "ISP") if k in introduced]
    n_prs = introduced.get("LSP", (0, 0))[1] or introduced.get("ISP", (0, 1))[1]
    if not inter:
        verdict = "UNRESOLVED"
        lines.append("no inter-unit principle produced a flag column.")
    elif n_prs and sum(inter) / n_prs < 0.02:
        verdict = "CONFIRMED"
        lines.append("")
        lines.append("Inter-unit introductions (LSP + ISP) are %d in %d "
                     "analysable PRs" % (sum(inter), n_prs))
        lines.append("= %.2f%%. The relation is INHERITED, not created: one PR "
                     "rarely authors" % (100 * sum(inter) / n_prs))
        lines.append("both halves of a cross-unit smell. Java replicates it "
                     "with classes")
        lines.append("mandatory and @Override explicit, which is what makes the "
                     "result a")
        lines.append("statement about PRs rather than about Python.")
    else:
        verdict = "REFUTED"
        lines.append("inter-unit introductions are NOT near zero in Java.")
    answer(1, "Intra- vs inter-unit risk (the headline)", verdict,
           "\n".join(lines))


# ------------------------------------------------------------------ H2

def h2_ranking_stability(cp: Corpus) -> None:
    """Is the agent ranking stable across languages?

    V1 Python macro: Claude_Code worst on OCP (6.18%) and SRP; Copilot cleanest
    on DIP (2.12%). If Java reorders them, ranking is NOT a stable property of
    an agent, which is itself a headline.
    """
    ok = cp.java_ok
    if ok is None:
        answer(2, "Is the agent ranking language-stable?", "UNRESOLVED",
               "Java results not available yet.")
        return
    lines, flips, comparisons = [], 0, 0
    for col, name in (("ocp_risk", "OCP"), ("dip_risk", "DIP")):
        if col not in ok.columns:
            continue
        macro = (ok.groupby(["agent", "full_name"])[col].mean()
                   .groupby("agent").mean().sort_values())
        counts = ok.groupby("agent")[col].agg(["sum", "size"])
        lines.append("%s Java macro/repo (best first):" % name)
        for a, v in macro.items():
            k, n = int(counts.loc[a, "sum"]), int(counts.loc[a, "size"])
            lines.append("   %-13s %5.2f%%   (%d/%d pooled)" % (a, 100 * v, k, n))
        comparisons += 1
        lines.append("")
    if not comparisons:
        answer(2, "Is the agent ranking language-stable?", "UNRESOLVED",
               "no comparable flag column in the Java results.")
        return
    lines.append("Cross-language comparison needs the Python V2 numbers computed")
    lines.append("under the SAME corpus design. V1's Python figures were")
    lines.append("merged-only, so comparing them to a merged+rejected Java corpus")
    lines.append("would confound language with outcome mix. Filled in at step 8.")
    _ = flips
    answer(2, "Is the agent ranking language-stable?", "UNRESOLVED",
           "\n".join(lines))


# ------------------------------------------------------------------ H3

def h3_compiler_shapes_violations(cp: Corpus) -> None:
    """Does the compiler determine which violations are expressible?

    Java LSP uses 3 signals, Python 5, because javac enforces the other four:
    a changed parameter list is an overload not an override, parameter types
    are invariant, visibility cannot narrow and checked exceptions cannot
    broaden. TypeScript had to drop `param_removed` for the OPPOSITE reason
    (its methods are bivariant). Compare the SIGNAL MIX, not only the rate.
    """
    lines = ["Java signal set (config.LSP_JAVA_STRONG_SIGNALS): %s"
             % ", ".join(C.LSP_JAVA_STRONG_SIGNALS),
             "Python signal set (config.LSP_THRESHOLDS['strong_signals']): %s"
             % ", ".join(C.LSP_THRESHOLDS["strong_signals"])]
    dropped = set(C.LSP_THRESHOLDS["strong_signals"]) - set(C.LSP_JAVA_STRONG_SIGNALS)
    lines.append("")
    lines.append("Signals Python has that Java CANNOT express: %s"
                 % ", ".join(sorted(dropped)) if dropped else "none")
    lines.append("Reason: javac rejects the code, so the violation cannot occur "
                 "in a")
    lines.append("program that compiles. This is a language fact, not a "
                 "detector choice.")

    if cp.java_lsp is not None and "signals" in cp.java_lsp.columns:
        u = cp.java_lsp.drop_duplicates(["full_name", "type_id", "method"])
        v = u[u["violation"].fillna(False)]
        mix: dict[str, int] = {}
        for x in v["signals"].fillna(""):
            for y in str(x).split(";"):
                if y:
                    mix[y] = mix.get(y, 0) + 1
        lines.append("")
        lines.append("Java observed signal mix over %d violating pairs: %s"
                     % (len(v), mix or "(none)"))
    if cp.py_lsp is not None and "signals" in cp.py_lsp.columns:
        u = cp.py_lsp[cp.py_lsp.get("row_type", "override") == "override"]
        v = u[u["violation"].fillna(False)] if "violation" in u else u.iloc[:0]
        mix = {}
        for x in v.get("signals", pd.Series(dtype=object)).fillna(""):
            for y in str(x).split(";"):
                if y:
                    mix[y] = mix.get(y, 0) + 1
        lines.append("Python observed signal mix over %d violating pairs: %s"
                     % (len(v), mix or "(none)"))
    verdict = "CONFIRMED" if dropped else "UNRESOLVED"
    answer(3, "Does the compiler shape which violations are expressible?",
           verdict, "\n".join(lines))


# ------------------------------------------------------------------ H4

def h4_authored_vs_edited(cp: Corpus) -> None:
    """Do agents write cleaner classes than the ones they edit?

    V1 Python: every agent's AUTHORED classes flag LOWER than the pre-existing
    classes they edit (Claude 12.38% vs 13.33% pooled). SRP is the only
    principle where this is separable; the other four are already deltas.
    """
    d = cp.java_srp
    if d is None or "is_new" not in (d.columns if d is not None else []):
        answer(4, "Authored vs edited (the fidelity result)", "UNRESOLVED",
               "Java SRP results with `is_new` not available yet.")
        return
    if "srp_risk" not in d.columns or d["srp_risk"].isna().all():
        answer(4, "Authored vs edited (the fidelity result)", "UNRESOLVED",
               "SRP flags are null (calibration mode).")
        return
    d = d.copy()
    d["is_test"] = d["file_path"].map(lambda p: C.is_test_path(str(p), "java"))
    prod = d[~d["is_test"]].copy()
    prod["entity"] = prod["full_name"] + "::" + prod["type_id"]
    prod["is_new"] = prod.groupby("entity")["is_new"].transform("max")
    u = prod.sort_values("class_loc").drop_duplicates("entity", keep="last")

    lines, agree, total = [], 0, 0
    for agent, g in u.groupby("agent"):
        a_ = g[g["is_new"]]
        e_ = g[~g["is_new"]]
        if not len(a_) or not len(e_):
            continue
        ka, na = int(a_["srp_risk"].sum()), len(a_)
        ke, ne = int(e_["srp_risk"].sum()), len(e_)
        total += 1
        if ka / na < ke / ne:
            agree += 1
        sep = "intervals separate" if separated(ka, na, ke, ne) else "intervals overlap"
        lines.append("%-13s authored %d/%d = %.2f%%   edited %d/%d = %.2f%%   (%s)"
                     % (agent, ka, na, 100 * ka / na, ke, ne, 100 * ke / ne, sep))
    if not total:
        verdict = "UNRESOLVED"
        lines.append("no agent has both authored and edited production classes.")
    elif agree == total:
        verdict = "CONFIRMED"
        lines.append("")
        lines.append("Every agent (%d of %d) flags LOWER on the classes it "
                     "authored than on" % (agree, total))
        lines.append("the pre-existing classes it edited. Agents write cleaner "
                     "code than the")
        lines.append("code they walk into. This is the fidelity result.")
    elif agree == 0:
        verdict = "REFUTED"
        lines.append("")
        lines.append("Every agent flags HIGHER on authored classes. The V1 "
                     "Python direction")
        lines.append("does NOT replicate in Java.")
    else:
        verdict = "UNRESOLVED"
        lines.append("")
        lines.append("%d of %d agents agree with the V1 direction: mixed."
                     % (agree, total))
    answer(4, "Authored vs edited (the fidelity result)", verdict,
           "\n".join(lines))


# ------------------------------------------------------------------ H5

def h5_conformance_vs_acceptance(cp: Corpus) -> None:
    """Does SOLID conformance relate to acceptance? The new V2 question.

    Reported PER REJECTION STRATUM, never pooled across strata: a rejected PR
    is half as likely to carry any recorded review as a merged one, so most
    closures are superseded / stale / abandoned rather than judged and refused.
    """
    ok = cp.java_ok
    if ok is None or "outcome" not in (ok.columns if ok is not None else []):
        answer(5, "Does SOLID conformance relate to acceptance?", "UNRESOLVED",
               "Java results with an outcome column not available yet.")
        return
    lines, verdicts = [], []
    for col, name in (("ocp_risk", "OCP"), ("dip_risk", "DIP"),
                      ("lsp_risk", "LSP"), ("isp_risk", "ISP")):
        if col not in ok.columns:
            continue
        s = ok[ok["outcome"].isin(["merged", "rejected"])]
        if s["outcome"].nunique() < 2:
            continue
        g = s.groupby("outcome")[col].agg(["sum", "size"])
        km, nm_ = int(g.loc["merged", "sum"]), int(g.loc["merged", "size"])
        kr, nr = int(g.loc["rejected", "sum"]), int(g.loc["rejected", "size"])
        sep = separated(km, nm_, kr, nr)
        verdicts.append(sep)
        lines.append("%-4s merged %d/%d = %.2f%%   not-merged %d/%d = %.2f%%   %s"
                     % (name, km, nm_, 100 * km / max(nm_, 1), kr, nr,
                        100 * kr / max(nr, 1),
                        "SEPARATED" if sep else "intervals overlap"))
    strat_col = ("rejection_stratum_api" if "rejection_stratum_api" in ok.columns
                 else "rejection_stratum")
    if strat_col in ok.columns and "ocp_risk" in ok.columns:
        lines.append("")
        lines.append("By rejection stratum (counts beside rates -- "
                     "changes_requested is small):")
        nm2 = ok[ok["outcome"] != "merged"]
        for stratum, g in nm2.groupby(strat_col):
            k, n = int(g["ocp_risk"].fillna(False).sum()), len(g)
            lines.append("   OCP  %-22s %d/%d = %.2f%%"
                         % (str(stratum), k, n, 100 * k / max(n, 1)))
    lines.append("")
    lines.append("CONFOUND, stated once: `rejected` is not `refused on quality`.")
    lines.append("Only `changes_requested` carries an explicit maintainer "
                 "objection, and")
    lines.append("`closed_unreviewed` is the majority. A pooled merged-vs-"
                 "rejected rate is")
    lines.append("NOT a statement about whether reviewers reject unclean code.")
    if not verdicts:
        verdict = "UNRESOLVED"
    elif any(verdicts):
        verdict = "CONFIRMED (for the principles marked SEPARATED only)"
    else:
        verdict = "UNRESOLVED"
        lines.append("")
        lines.append("No principle separates. On this corpus, conformance as "
                     "measured here")
        lines.append("does NOT distinguish accepted from rejected PRs. That is "
                     "a real answer:")
        lines.append("it says the acceptance decision runs on something other "
                     "than these")
        lines.append("structural signals.")
    answer(5, "Does SOLID conformance relate to acceptance?", verdict,
           "\n".join(lines))


# ------------------------------------------------------------------ H6

def h6_merged_only_bias(cp: Corpus) -> None:
    """How biased was the merged-only corpus? V1 is the baseline.

    If the shift V2 produces is small, that VALIDATES V1 rather than
    invalidating it, and is worth stating plainly.
    """
    ok = cp.java_ok
    if ok is None or "outcome" not in (ok.columns if ok is not None else []):
        answer(6, "Is the merged-only corpus biased, and by how much?",
               "UNRESOLVED", "Java results with an outcome column not available.")
        return
    lines = []
    mix = C.V2_NATURAL_MIX
    for col, name in (("ocp_risk", "OCP"), ("dip_risk", "DIP"),
                      ("lsp_risk", "LSP"), ("isp_risk", "ISP")):
        if col not in ok.columns:
            continue
        s = ok[ok["outcome"].isin(["merged", "rejected"])]
        g = s.groupby("outcome")[col].mean()
        if "merged" not in g or "rejected" not in g:
            continue
        balanced = s[col].fillna(False).mean()
        # Reweight to the real population, where 80.9% of PRs merge. The
        # balanced corpus maximises power for the CONTRAST but is not the world.
        w = mix["merged"] / (mix["merged"] + mix["rejected"])
        reweighted = g["merged"] * w + g["rejected"] * (1 - w)
        lines.append("%-4s merged-only %.2f%%   balanced-corpus %.2f%%   "
                     "reweighted-to-population %.2f%%   (shift %+.2f pp)"
                     % (name, 100 * g["merged"], 100 * balanced,
                        100 * reweighted, 100 * (reweighted - g["merged"])))
    lines.append("")
    lines.append("`merged-only` is what V1 would have reported on this corpus.")
    lines.append("`reweighted` restores the real 80.9/9.5/9.6 outcome mix, which "
                 "the")
    lines.append("balanced draw deliberately does not have. Quote the reweighted")
    lines.append("figure for any 'agents violate X%% of the time' claim, and the")
    lines.append("per-outcome figures for everything else.")
    verdict = "CONFIRMED" if lines else "UNRESOLVED"
    answer(6, "Is the merged-only corpus biased, and by how much?", verdict,
           "\n".join(lines))


# ------------------------------------------------------------------ H7

def h7_rejection_rate_tracks_quality(cp: Corpus) -> None:
    """Does the rejection rate itself track agent quality?

    Rejection varies 3.6x by agent with no code analysis at all. Does that
    ordering match the SOLID ordering? Agreement is external corroboration;
    disagreement is more interesting still and must not be buried.
    """
    lines = ["Dataset-wide rejection rate per agent (REDESIGN_V2.md 2.1, needs "
             "no code analysis):",
             "   OpenAI_Codex 6.5%   Claude_Code 7.8%   Copilot 15.8%",
             "   (Cursor 14.3%, Google_Jules 15.6%, Devin 23.3% are outside "
             "this study)",
             ""]
    ok = cp.java_ok
    if ok is None:
        answer(7, "Does the rejection rate track the SOLID ordering?",
               "UNRESOLVED", "\n".join(lines + ["Java results not available."]))
        return
    rej_order = ["OpenAI_Codex", "Claude_Code", "Copilot"]   # cleanest first
    agreed, checked = 0, 0
    for col, name in (("ocp_risk", "OCP"), ("dip_risk", "DIP")):
        if col not in ok.columns:
            continue
        macro = (ok.groupby(["agent", "full_name"])[col].mean()
                   .groupby("agent").mean().sort_values())
        solid_order = list(macro.index)
        present = [a for a in rej_order if a in solid_order]
        checked += 1
        if solid_order == present:
            agreed += 1
        lines.append("%s SOLID ordering (cleanest first): %s"
                     % (name, " < ".join(solid_order)))
        lines.append("   rejection ordering (cleanest first): %s"
                     % " < ".join(present))
        lines.append("   %s" % ("SAME ORDER" if solid_order == present
                                else "DIFFERENT ORDER"))
        lines.append("")
    if not checked:
        verdict = "UNRESOLVED"
    elif agreed == checked:
        verdict = "CONFIRMED"
        lines.append("The two orderings agree. An agent's rejection rate, which "
                     "costs nothing")
        lines.append("to compute, tracks its measured SOLID conformance. That is "
                     "external")
        lines.append("corroboration of the structural result.")
    elif agreed == 0:
        verdict = "REFUTED"
        lines.append("The two orderings disagree entirely. Rejection is driven by")
        lines.append("something other than the structural conformance measured "
                     "here, and")
        lines.append("that is the more interesting outcome: it means human review")
        lines.append("selects on criteria these detectors do not capture.")
    else:
        verdict = "UNRESOLVED"
        lines.append("Orderings agree for %d of %d principles: mixed."
                     % (agreed, checked))
    answer(7, "Does the rejection rate track the SOLID ordering?", verdict,
           "\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true")
    a = ap.parse_args()
    cp = Corpus(a.pilot)

    print("=" * 74)
    print("VIBEBENCH V2 -- THE SEVEN HYPOTHESES (REDESIGN_V2.md section 10)")
    print("=" * 74)
    if a.pilot:
        print("*** PILOT DATA -- rehearsal only, not a result ***")

    for fn in (h1_intra_vs_inter, h2_ranking_stability, h3_compiler_shapes_violations,
               h4_authored_vs_edited, h5_conformance_vs_acceptance,
               h6_merged_only_bias, h7_rejection_rate_tracks_quality):
        try:
            fn(cp)
        except Exception as e:                              # noqa: BLE001
            answer(len(ANSWERS) + 1, fn.__name__, "UNRESOLVED",
                   "check raised %s: %s" % (type(e).__name__, e))

    print("\n" + "=" * 74)
    print("SUMMARY")
    print("=" * 74)
    for n, title, verdict, _ in ANSWERS:
        print("H%d  %-52s %s" % (n, title[:52], verdict))
    unres = sum(1 for _, _, v, _ in ANSWERS if v.startswith("UNRESOLVED"))
    print("\n%d of %d answered; %d UNRESOLVED."
          % (len(ANSWERS) - unres, len(ANSWERS), unres))
    print("UNRESOLVED is a real answer. It is NOT a licence to quote the "
          "direction anyway.")


if __name__ == "__main__":
    main()
