"""Offline re-simulation: recompute model_probability for all resolved
poisson_v0.3.0 picks under HOME_ADVANTAGE=1.05 instead of the production
value (1.04), holding pitcher ERA adjustment, RPG blend, and tie
redistribution constant. Read-only: no DB writes, no production constants
touched (adapters/betting/stats.py is never imported for its HOME_ADVANTAGE
constant -- 1.04 and 1.05 are both passed explicitly here).

Limitation -- read before trusting the numbers:
  The season/recent-RPG blend that fed each pick's lambda was NOT persisted
  in `features` for these 103 picks (the gap scripts/calibrate.py's analysis
  flagged; closed for *new* signals by era_diff/raw_model_probability, but
  these predate that). RPG is reconstructed here via the same point-in-time
  methodology scripts/backtest.py uses for validation:
    - season-average RPG with endDate = game_date - 1 (StatsCache, with
      prior-season fallback for thin early-season samples)
    - last-15-games RPG via a live schedule lookback (also point-in-time by
      construction -- it only ever looks at games before game_date)
  Pitcher ERA is NOT reconstructed -- it IS stored in features and used
  as-is. Because RPG is reconstructed rather than read back exactly, the
  HA=1.04 reconstruction is checked against the value actually stored at
  generation time; signals where it doesn't match within tolerance are
  flagged in the output (this is reconstruction noise, not the HA effect).

Usage:
    python -m scripts.ha_resim
    python -m scripts.ha_resim --tolerance 0.02
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select

from adapters.betting.model import baseball_match_probs
from adapters.betting.stats import MLBStatsProvider
from core.db import SessionLocal, init_db
from core.models import ModelRun, Signal, SignalOutcome
from scripts.backtest import StatsCache

MODEL_VERSION = "poisson_v0.3.0"
PROD_HA = 1.04
PROPOSED_HA = 1.05
RECENT_WEIGHT = 0.30
LEAGUE_AVG_ERA = 4.20
OUT_PATH = Path(__file__).parent / "ha_resim_results.csv"


def _fetch_signals(session) -> list[tuple]:
    """Resolved poisson_v0.3.0 signals with their outcome. Void signals have
    no SignalOutcome row, so the join already excludes them."""
    rows = session.execute(
        select(Signal, SignalOutcome)
        .join(SignalOutcome, SignalOutcome.signal_id == Signal.id)
        .join(ModelRun, Signal.model_run_id == ModelRun.id)
        .where(
            ModelRun.model_version == MODEL_VERSION,
            SignalOutcome.was_correct.is_not(None),
        )
        .order_by(Signal.valid_for_date)
    ).all()
    return rows


def _rpg_blend(stats_cache: StatsCache, provider: MLBStatsProvider, team: str, game_date) -> float:
    season_rpg = stats_cache.runs_per_game(team, game_date)
    recent_rpg = provider.team_recent_rpg(team, game_date.isoformat())
    if recent_rpg is not None:
        return (1 - RECENT_WEIGHT) * season_rpg + RECENT_WEIGHT * recent_rpg
    return season_rpg


def _lambdas(rpg_home: float, rpg_away: float, home_era, away_era, home_advantage: float) -> tuple[float, float]:
    lam_home = rpg_home * home_advantage
    lam_away = rpg_away
    if away_era and away_era > 0:
        lam_home *= away_era / LEAGUE_AVG_ERA
    if home_era and home_era > 0:
        lam_away *= home_era / LEAGUE_AVG_ERA
    return lam_home, lam_away


def _brier(rows: list[dict], key: str) -> float:
    n = len(rows)
    if n == 0:
        return float("nan")
    return sum((r[key] - int(r["was_correct"])) ** 2 for r in rows) / n


def _win_rate(rows: list[dict]) -> float:
    n = len(rows)
    return sum(r["was_correct"] for r in rows) / n if n else float("nan")


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-simulate resolved picks under a proposed HOME_ADVANTAGE")
    parser.add_argument("--tolerance", type=float, default=0.02,
                         help="flag signals where reconstructed HA=1.04 prob deviates from the "
                              "stored value by more than this (default 0.02)")
    args = parser.parse_args()

    init_db()
    with SessionLocal() as session:
        signal_rows = _fetch_signals(session)

    print(f"Resolved {MODEL_VERSION} picks: {len(signal_rows)}")
    if not signal_rows:
        return

    mlb_signals = [
        (s, o) for s, o in signal_rows
        if s.features and s.features.get("sport", "").endswith("_mlb")
    ]
    skipped_non_mlb = len(signal_rows) - len(mlb_signals)
    if skipped_non_mlb:
        print(f"Skipping {skipped_non_mlb} non-MLB signal(s) (HOME_ADVANTAGE only applies to the MLB model).")

    stats_cache = StatsCache()
    provider = MLBStatsProvider(season=2026)  # season unused by team_recent_rpg; kept for API shape

    results: list[dict] = []
    excluded: list[dict] = []  # missing data / fetch failure -- not in results at all
    noisy: list[dict] = []     # reconstruction mismatch -- still in results, just flagged
    n = len(mlb_signals)

    for i, (signal, outcome) in enumerate(mlb_signals, 1):
        if i % 10 == 0 or i == n:
            print(f"  Re-simulating {i}/{n} ...", end="\r")

        f = signal.features
        home_team = f.get("home_team")
        away_team = f.get("away_team")
        pick = f.get("pick")
        home_era = f.get("home_pitcher_era")
        away_era = f.get("away_pitcher_era")
        game_date = signal.valid_for_date
        stored_prob = float(f.get("model_probability", signal.confidence))

        if not home_team or not away_team or not pick:
            excluded.append({"signal_id": str(signal.id), "reason": "missing home/away/pick in features"})
            continue

        try:
            rpg_home = _rpg_blend(stats_cache, provider, home_team, game_date)
            rpg_away = _rpg_blend(stats_cache, provider, away_team, game_date)
        except Exception as exc:
            excluded.append({"signal_id": str(signal.id), "reason": f"RPG fetch failed: {exc}"})
            continue
        time.sleep(0.1)  # polite pacing against the free MLB Stats API

        lam_h_104, lam_a_104 = _lambdas(rpg_home, rpg_away, home_era, away_era, PROD_HA)
        lam_h_105, lam_a_105 = _lambdas(rpg_home, rpg_away, home_era, away_era, PROPOSED_HA)
        p_home_104, p_away_104 = baseball_match_probs(lam_h_104, lam_a_104)
        p_home_105, p_away_105 = baseball_match_probs(lam_h_105, lam_a_105)

        pick_is_home = pick == home_team
        recon_104 = p_home_104 if pick_is_home else p_away_104
        recon_105 = p_home_105 if pick_is_home else p_away_105

        # Did the model's preferred side change under the proposed HA?
        preferred_side_105 = home_team if p_home_105 >= p_away_105 else away_team
        flipped = preferred_side_105 != pick

        reconstruction_error = abs(recon_104 - stored_prob)
        if reconstruction_error > args.tolerance:
            noisy.append({
                "signal_id": str(signal.id),
                "reason": f"reconstructed HA=1.04 prob ({recon_104:.4f}) deviates from stored "
                          f"({stored_prob:.4f}) by {reconstruction_error:.4f} > tolerance "
                          f"{args.tolerance} -- RPG inputs likely drifted since generation time",
            })

        results.append({
            "signal_id": str(signal.id),
            "pick": pick,
            "side": "home" if pick_is_home else "away",
            "was_correct": int(outcome.was_correct),
            "model_prob_ha104_stored": round(stored_prob, 4),
            "model_prob_ha104_recon": round(recon_104, 4),
            "model_prob_ha105_recon": round(recon_105, 4),
            "prob_delta": round(recon_105 - stored_prob, 4),
            "flipped_side": flipped,
            "noisy_reconstruction": reconstruction_error > args.tolerance,
        })

    print(f"\nRe-simulated: {len(results)}  |  Excluded: {len(excluded)}  |  Noisy reconstruction: {len(noisy)}")

    if excluded:
        print(f"\n{len(excluded)} signal(s) EXCLUDED from all stats below (could not be re-simulated):")
        for fl in excluded:
            print(f"  {fl['signal_id']}: {fl['reason']}")

    if noisy:
        print(f"\n{len(noisy)} signal(s) flagged for noisy reconstruction "
              f"(still INCLUDED in the stats below -- this is RPG drift between generation "
              f"time and today, not the HA effect):")
        for fl in noisy[:20]:
            print(f"  {fl['signal_id']}: {fl['reason']}")
        if len(noisy) > 20:
            print(f"  ... and {len(noisy) - 20} more")

    if not results:
        print("Nothing left to compare after exclusions.")
        return

    # --- Overall Brier / win rate under both HA values ---------------- #
    # "Old" = the value actually stored in production (HA=1.04, ground truth).
    # "New" = reconstructed HA=1.05 probability for the same pick.
    home_rows = [r for r in results if r["side"] == "home"]
    away_rows = [r for r in results if r["side"] == "away"]

    def _brier_for(rows, key):
        return _brier(rows, key)

    print(f"\n{'='*70}")
    print(f"  Brier score comparison ({len(results)} re-simulated picks)")
    print(f"{'='*70}")
    print(f"  HA=1.04 (stored, production):     {_brier_for(results, 'model_prob_ha104_stored'):.4f}")
    print(f"  HA=1.05 (reconstructed):           {_brier_for(results, 'model_prob_ha105_recon'):.4f}")
    print(f"  [check] HA=1.04 reconstructed:     {_brier_for(results, 'model_prob_ha104_recon'):.4f}"
          "  (compare to the stored row above -- gap is reconstruction noise)")

    print(f"\n  By side:")
    print(f"  {'side':<6} {'n':>4} {'win_rate':>9} {'brier_1.04':>11} {'brier_1.05':>11}")
    for label, rows in (("home", home_rows), ("away", away_rows)):
        if not rows:
            continue
        print(
            f"  {label:<6} {len(rows):>4} {_win_rate(rows):>9.1%} "
            f"{_brier_for(rows, 'model_prob_ha104_stored'):>11.4f} "
            f"{_brier_for(rows, 'model_prob_ha105_recon'):>11.4f}"
        )

    flips = [r for r in results if r["flipped_side"]]
    print(f"\n  Picks that would flip sides under HA=1.05: {len(flips)} / {len(results)}")
    for r in flips:
        print(f"    {r['signal_id']}  pick={r['pick']} ({r['side']})  "
              f"ha1.04={r['model_prob_ha104_stored']:.3f}  ha1.05_other_side_preferred")

    avg_delta_home = sum(r["prob_delta"] for r in home_rows) / len(home_rows) if home_rows else float("nan")
    avg_delta_away = sum(r["prob_delta"] for r in away_rows) / len(away_rows) if away_rows else float("nan")
    print(f"\n  Average prob_delta (HA1.05 recon - HA1.04 stored):")
    print(f"    home picks: {avg_delta_home:+.4f}")
    print(f"    away picks: {avg_delta_away:+.4f}")

    # --- CSV output ----------------------------------------------------- #
    with OUT_PATH.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)
    print(f"\nFull comparison table saved: {OUT_PATH}")
    print("No signals, model_runs, or production constants were modified by this script.")


if __name__ == "__main__":
    main()
