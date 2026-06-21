"""Investigation 1: why does the 60-70% confidence bin miscalibrate so badly?

Read-only. Pulls resolved poisson_v0.3.0 signals, splits into the 50-60/
60-70/70-80% model_probability bins, and compares every feature available
from stored data (era_diff is computed from the stored pitcher ERAs --
no reconstruction needed, unlike ha_resim.py). Recent-form RPG is NOT
stored for these picks and is intentionally not reconstructed here -- that
network-heavy reconstruction is reserved for scripts/era_weight_sensitivity.py
where it's load-bearing; reconstructing it again here would just duplicate
that work for a feature this script can characterize without it.

Usage:
    python -m scripts.bin_analysis
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scipy.stats import binomtest, mannwhitneyu
from sqlalchemy import select

from core.db import SessionLocal, init_db
from core.models import ModelRun, Signal, SignalOutcome

MODEL_VERSION = "poisson_v0.3.0"
LEAGUE_AVG_ERA = 4.20

BINS = [
    ("0.50-0.60", 0.50, 0.60),
    ("0.60-0.70", 0.60, 0.70),
    ("0.70-0.80", 0.70, 0.80),
]


def _era_diff(f: dict) -> float | None:
    pick = f.get("pick")
    home_team = f.get("home_team")
    away_team = f.get("away_team")
    home_era = f.get("home_pitcher_era")
    away_era = f.get("away_pitcher_era")
    if home_era is None or away_era is None:
        return None
    if pick == home_team:
        return home_era - away_era
    if pick == away_team:
        return away_era - home_era
    return None


def main() -> None:
    init_db()
    with SessionLocal() as session:
        rows = session.execute(
            select(Signal, SignalOutcome)
            .join(SignalOutcome, SignalOutcome.signal_id == Signal.id)
            .join(ModelRun, Signal.model_run_id == ModelRun.id)
            .where(
                ModelRun.model_version == MODEL_VERSION,
                SignalOutcome.was_correct.is_not(None),
            )
        ).all()

    print(f"Resolved {MODEL_VERSION} picks: {len(rows)}\n")

    records = []
    for signal, outcome in rows:
        f = signal.features or {}
        mp = float(f.get("model_probability", signal.confidence))
        records.append({
            "model_prob": mp,
            "was_correct": bool(outcome.was_correct),
            "era_diff": _era_diff(f),
            "is_home_pick": f.get("pick") == f.get("home_team"),
            "best_odd": f.get("best_odd"),
            "market_probability": f.get("market_probability"),
            "edge": f.get("edge"),
        })

    binned = {}
    for label, lo, hi in BINS:
        binned[label] = [r for r in records if lo <= r["model_prob"] < hi]

    print(f"{'bin':<10} {'n':>4} {'win_rate':>9} {'declared':>9} {'gap':>7} "
          f"{'home%':>6} {'avg_era_diff':>13} {'avg_odd':>8} {'avg_mkt_p':>10} {'avg_edge':>9}")
    for label, recs in binned.items():
        n = len(recs)
        if n == 0:
            print(f"{label:<10}    0  (no resolved picks in this bin)")
            continue
        win_rate = sum(r["was_correct"] for r in recs) / n
        declared = sum(r["model_prob"] for r in recs) / n
        home_pct = sum(r["is_home_pick"] for r in recs) / n
        era_diffs = [r["era_diff"] for r in recs if r["era_diff"] is not None]
        avg_era = statistics.mean(era_diffs) if era_diffs else float("nan")
        odds = [r["best_odd"] for r in recs if r["best_odd"] is not None]
        avg_odd = statistics.mean(odds) if odds else float("nan")
        mkts = [r["market_probability"] for r in recs if r["market_probability"] is not None]
        avg_mkt = statistics.mean(mkts) if mkts else float("nan")
        edges = [r["edge"] for r in recs if r["edge"] is not None]
        avg_edge = statistics.mean(edges) if edges else float("nan")
        gap = win_rate - declared
        print(f"{label:<10} {n:>4} {win_rate:>9.1%} {declared:>9.1%} {gap:>+7.1%} "
              f"{home_pct:>6.1%} {avg_era:>13.3f} {avg_odd:>8.2f} {avg_mkt:>10.3f} {avg_edge:>9.3f}")

    # --- Is the 60-70% gap explainable by sample noise alone? ----------- #
    mid = binned["0.60-0.70"]
    n_mid = len(mid)
    wins_mid = sum(r["was_correct"] for r in mid)
    declared_mid = sum(r["model_prob"] for r in mid) / n_mid if n_mid else float("nan")

    print(f"\n{'='*72}")
    print("  Is 37.5% (60-70% bin) consistent with the declared ~65% confidence?")
    print(f"{'='*72}")
    if n_mid >= 5:
        test = binomtest(wins_mid, n_mid, declared_mid, alternative="two-sided")
        print(f"  n={n_mid}, wins={wins_mid}, observed win rate={wins_mid/n_mid:.1%}, "
              f"declared={declared_mid:.1%}")
        print(f"  Binomial test (H0: true win rate = declared confidence): p={test.pvalue:.4f}")
        if test.pvalue < 0.05:
            print("  -> Statistically significant deviation at alpha=0.05. This is NOT just")
            print("     noise at this sample size -- the model is genuinely overconfident in")
            print("     this bin, beyond what chance alone would produce.")
        else:
            print("  -> Not statistically significant at alpha=0.05 -- consistent with sample")
            print("     noise; n is still small enough that this could resolve itself with")
            print("     more data.")
    else:
        print(f"  n={n_mid} too small for a meaningful test.")

    # --- era_diff: is the 60-70% bin distinctly different? -------------- #
    print(f"\n{'='*72}")
    print("  era_diff distribution: 60-70% bin vs neighbors")
    print(f"{'='*72}")
    era_60_70 = [r["era_diff"] for r in mid if r["era_diff"] is not None]
    for label in ("0.50-0.60", "0.70-0.80"):
        neighbor = [r["era_diff"] for r in binned[label] if r["era_diff"] is not None]
        if len(era_60_70) >= 3 and len(neighbor) >= 3:
            u_stat, p_val = mannwhitneyu(era_60_70, neighbor, alternative="two-sided")
            print(f"  60-70% (n={len(era_60_70)}, mean={statistics.mean(era_60_70):+.3f}) vs "
                  f"{label} (n={len(neighbor)}, mean={statistics.mean(neighbor):+.3f}): "
                  f"Mann-Whitney p={p_val:.3f}")
        else:
            print(f"  60-70% vs {label}: not enough non-null era_diff values for a test")

    # --- home/away split: is 60-70% disproportionately away? ----------- #
    print(f"\n{'='*72}")
    print("  home/away split by bin")
    print(f"{'='*72}")
    for label, recs in binned.items():
        n = len(recs)
        if n == 0:
            continue
        home_n = sum(r["is_home_pick"] for r in recs)
        print(f"  {label}: {home_n}/{n} home ({home_n/n:.1%}), "
              f"{n - home_n}/{n} away ({(n - home_n)/n:.1%})")

    # --- Summary -------------------------------------------------------- #
    print(f"\n{'='*72}")
    print("  SUMMARY")
    print(f"{'='*72}")
    print(f"  60-70% bin sample size: n={n_mid} -- ", end="")
    if n_mid < 15:
        print("small; treat any pattern below cautiously.")
    else:
        print("moderate; large enough to look for real patterns, not just noise.")
    print(
        "  See the binomial test above for whether the miscalibration itself is real.\n"
        "  See the era_diff / home-away breakdowns above for whether any single feature\n"
        "  distinguishes this bin from its neighbors -- if no test came back significant,\n"
        "  there's no single distinguishing feature found in the available data, and the\n"
        "  miscalibration (if real) is better explained as a property of this probability\n"
        "  range itself (consistent with why isotonic *calibration* -- which directly\n"
        "  targets exactly this non-monotonic pattern -- is the right fix, not a new\n"
        "  feature or filter)."
    )


if __name__ == "__main__":
    main()
