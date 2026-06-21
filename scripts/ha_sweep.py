"""Sweep HOME_ADVANTAGE against the backtest dataset to see which value best
matches observed home/away win rates, without re-introducing the v0.1.0 bias
(100% home picks at HA=1.10).

Background: live analysis of 103 resolved poisson_v0.3.0 picks found home
picks win 66% vs 48% away, while the model assigns them nearly equal
probability (0.732 vs 0.718) -- suggesting the current HOME_ADVANTAGE=1.04
understates the real home-field effect. The report flags this as needing
more live data (n>=200-250) before a firm parameter change, so this script
cross-validates candidate values against the 2026-05 backtest dataset
instead of acting on the live sample alone.

This script does NOT change adapters/betting/stats.py. It only prints a
table for manual review.

Usage:
    python -m scripts.ha_sweep
    python -m scripts.ha_sweep --start 2026-05-01 --end 2026-05-31 --min 1.00 --max 1.15 --step 0.01
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.betting.model import baseball_match_probs
from scripts.backtest import Game, StatsCache, fetch_games_from_mlb_api, load_games_retrosheet


def _load_games(start: date, end: date) -> list[Game]:
    if start.year <= 2025:
        return load_games_retrosheet(start, end)
    return fetch_games_from_mlb_api(start, end)


def _evaluate_at(games: list[Game], rpg_by_game: list[tuple[float, float]], home_advantage: float) -> dict:
    n = len(games)
    correct = 0
    brier_sum = 0.0
    home_picks = 0
    home_picks_correct = 0
    away_picks = 0
    away_picks_correct = 0

    for game, (rpg_home, rpg_away) in zip(games, rpg_by_game):
        lam_home = rpg_home * home_advantage
        lam_away = rpg_away
        p_home, p_away = baseball_match_probs(lam_home, lam_away)

        if p_home >= p_away:
            pick, model_prob = game.home_team, p_home
        else:
            pick, model_prob = game.away_team, p_away

        is_correct = pick == game.actual_winner
        correct += is_correct
        brier_sum += (model_prob - int(is_correct)) ** 2

        if pick == game.home_team:
            home_picks += 1
            home_picks_correct += is_correct
        else:
            away_picks += 1
            away_picks_correct += is_correct

    return {
        "home_advantage": home_advantage,
        "accuracy": correct / n,
        "brier": brier_sum / n,
        "home_pick_pct": home_picks / n,
        "home_win_rate": (home_picks_correct / home_picks) if home_picks else None,
        "away_win_rate": (away_picks_correct / away_picks) if away_picks else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sweep HOME_ADVANTAGE against the backtest dataset")
    parser.add_argument("--start", default="2026-05-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default="2026-05-31", help="End date YYYY-MM-DD")
    parser.add_argument("--min", type=float, default=1.00, dest="ha_min")
    parser.add_argument("--max", type=float, default=1.15, dest="ha_max")
    parser.add_argument("--step", type=float, default=0.01)
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    print(f"Loading games {start} to {end} ...")
    games = _load_games(start, end)
    print(f"Games loaded: {len(games)}")
    if not games:
        print("No games found. Check date range.")
        return

    # Point-in-time RPG is independent of HOME_ADVANTAGE -- fetch it once per
    # game, then sweep HA values cheaply against the cached stats (avoids
    # re-hitting the MLB Stats API once per sweep step).
    cache = StatsCache()
    rpg_by_game: list[tuple[float, float]] = []
    for i, game in enumerate(games, 1):
        if i % 50 == 0 or i == len(games):
            print(f"  Fetching stats {i}/{len(games)} ...", end="\r")
        rpg_by_game.append((
            cache.runs_per_game(game.home_team, game.game_date),
            cache.runs_per_game(game.away_team, game.game_date),
        ))
    print(f"\nStats API calls made: {cache.api_calls}")

    ha_values = []
    v = args.ha_min
    while v <= args.ha_max + 1e-9:
        ha_values.append(round(v, 2))
        v += args.step

    results = [_evaluate_at(games, rpg_by_game, ha) for ha in ha_values]

    print(f"\n{'HA':>5}  {'accuracy':>9}  {'brier':>7}  {'home_pick%':>10}  {'home_win%':>9}  {'away_win%':>9}")
    for r in results:
        home_win = f"{r['home_win_rate']:.1%}" if r["home_win_rate"] is not None else "n/a"
        away_win = f"{r['away_win_rate']:.1%}" if r["away_win_rate"] is not None else "n/a"
        print(
            f"{r['home_advantage']:>5.2f}  {r['accuracy']:>9.1%}  {r['brier']:>7.4f}  "
            f"{r['home_pick_pct']:>9.1%}  {home_win:>9}  {away_win:>9}"
        )

    # Recommend the value with the lowest Brier among candidates that don't
    # reintroduce the v0.1.0 bias (home_pick_pct > 85% was the symptom at
    # HA=1.10 in the original incident -- see CLAUDE.md decision log).
    sane = [r for r in results if r["home_pick_pct"] <= 0.85]
    pool = sane if sane else results
    best = min(pool, key=lambda r: r["brier"])

    print(f"\nLowest-Brier HOME_ADVANTAGE within a sane home-pick-rate band: {best['home_advantage']:.2f}")
    print(f"  accuracy={best['accuracy']:.1%}  brier={best['brier']:.4f}  "
          f"home_pick%={best['home_pick_pct']:.1%}  "
          f"home_win%={best['home_win_rate']:.1%}  away_win%={best['away_win_rate']:.1%}")
    print(
        "\nNOTE: live data (n=103) shows a 66%/48% home/away win-rate split that this\n"
        "backtest dataset may not fully reproduce -- the report explicitly flags needing\n"
        "n>=200-250 live resolved picks before changing HOME_ADVANTAGE in production.\n"
        "adapters/betting/stats.py is UNCHANGED by this script. Review this table and\n"
        "confirm before any production change."
    )


if __name__ == "__main__":
    main()
