"""Backtest the MLB Poisson model against historical game results.

Data sources (auto-selected by year):
  year <= 2025 → Retrosheet game logs (free, no key)
  year >= 2026 → MLB Stats API schedule endpoint (free, no key)

No DB writes. No look-ahead bias: stats are fetched with endDate = game_date - 1.
ROI uses estimated odds (model probs + Pinnacle vig) — NOT real market odds.

Usage:
    python -m scripts.backtest
    python -m scripts.backtest --start 2026-05-01 --end 2026-05-31
    python -m scripts.backtest --start 2025-04-01 --end 2025-05-31
"""

from __future__ import annotations

import argparse
import csv
import sys
import tempfile
import time
import urllib.request
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import NamedTuple

import requests

# Make project root importable regardless of cwd.
sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.betting.model import baseball_match_probs
from adapters.betting.stats import HOME_ADVANTAGE

# --------------------------------------------------------------------------- #
# Constants                                                                    #
# --------------------------------------------------------------------------- #
MLB_STATS_BASE = "https://statsapi.mlb.com/api/v1"
RETROSHEET_URL_TPL = "https://www.retrosheet.org/gamelogs/gl{year}.zip"
LEAGUE_AVG_RUNS = 4.5       # fallback when a team has no data
VIG = 0.045                 # Pinnacle's approximate MLB vig
MIN_GAMES_FOR_SEASON = 15   # below this, blend with prior-season stats

# --------------------------------------------------------------------------- #
# Retrosheet → MLB API full-name mapping (all 30 teams)                       #
# --------------------------------------------------------------------------- #
RETRO_TO_MLB: dict[str, str] = {
    "ARI": "Arizona Diamondbacks",
    "ATL": "Atlanta Braves",
    "BAL": "Baltimore Orioles",
    "BOS": "Boston Red Sox",
    "CHA": "Chicago White Sox",
    "CHN": "Chicago Cubs",
    "CIN": "Cincinnati Reds",
    "CLE": "Cleveland Guardians",
    "COL": "Colorado Rockies",
    "DET": "Detroit Tigers",
    "HOU": "Houston Astros",
    "KCA": "Kansas City Royals",
    "LAA": "Los Angeles Angels",
    "LAN": "Los Angeles Dodgers",
    "MIA": "Miami Marlins",
    "MIL": "Milwaukee Brewers",
    "MIN": "Minnesota Twins",
    "NYA": "New York Yankees",
    "NYN": "New York Mets",
    "OAK": "Oakland Athletics",
    "PHI": "Philadelphia Phillies",
    "PIT": "Pittsburgh Pirates",
    "SDN": "San Diego Padres",
    "SEA": "Seattle Mariners",
    "SFN": "San Francisco Giants",
    "SLN": "St. Louis Cardinals",
    "TBA": "Tampa Bay Rays",
    "TEX": "Texas Rangers",
    "TOR": "Toronto Blue Jays",
    "WAS": "Washington Nationals",
}


# --------------------------------------------------------------------------- #
# Data structures                                                              #
# --------------------------------------------------------------------------- #
class Game(NamedTuple):
    game_date: date
    home_team: str    # MLB full name
    away_team: str
    home_score: int
    away_score: int
    actual_winner: str


class GameResult(NamedTuple):
    game_date: date
    home_team: str
    away_team: str
    model_pick: str
    model_prob: float      # probability assigned to the model's pick
    actual_winner: str
    correct: bool
    estimated_odd: float   # decimal odd under Pinnacle vig
    profit: float          # +(odd-1) if correct, -1 if not


# --------------------------------------------------------------------------- #
# Results source A — Retrosheet (year <= 2025)                                #
# --------------------------------------------------------------------------- #
def _retrosheet_url(year: int) -> str:
    return RETROSHEET_URL_TPL.format(year=year)


def download_retrosheet(tmp_dir: str, year: int) -> Path:
    """Download gl{year}.zip and return path to extracted game log file."""
    url = _retrosheet_url(year)
    zip_path = Path(tmp_dir) / f"gl{year}.zip"
    print(f"Downloading Retrosheet {year} game log to {zip_path} ...")
    urllib.request.urlretrieve(url, zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        txt_names = [n for n in zf.namelist()
                     if n.upper().endswith(".TXT") or n.upper().endswith(".CSV")]
        if not txt_names:
            raise RuntimeError(
                f"No game log file found in zip. Contents: {zf.namelist()}"
            )
        zf.extractall(tmp_dir)
    for name in txt_names:
        candidate = Path(tmp_dir) / name
        if candidate.exists():
            return candidate
    raise RuntimeError("Extracted game log not found after extraction.")


def parse_retrosheet(csv_path: Path, start: date, end: date) -> list[Game]:
    """Parse Retrosheet game log CSV; filter to [start, end]."""
    games: list[Game] = []
    skipped_teams: set[str] = set()

    with open(csv_path, encoding="latin-1") as f:
        for row in csv.reader(f):
            if len(row) < 11:
                continue
            try:
                raw_date = row[0].strip()
                game_date = date(
                    int(raw_date[:4]), int(raw_date[4:6]), int(raw_date[6:8])
                )
            except (ValueError, IndexError):
                continue

            if not (start <= game_date <= end):
                continue

            retro_away = row[3].strip()
            retro_home = row[6].strip()
            try:
                # col 9 = visitor runs, col 10 = home runs (Retrosheet format)
                away_score = int(row[9].strip())
                home_score = int(row[10].strip())
            except (ValueError, IndexError):
                continue

            home_name = RETRO_TO_MLB.get(retro_home)
            away_name = RETRO_TO_MLB.get(retro_away)

            if not home_name:
                skipped_teams.add(retro_home)
                continue
            if not away_name:
                skipped_teams.add(retro_away)
                continue

            actual_winner = home_name if home_score > away_score else away_name
            games.append(
                Game(game_date, home_name, away_name, home_score, away_score, actual_winner)
            )

    if skipped_teams:
        print(f"  [warn] Unmapped Retrosheet codes (games skipped): {sorted(skipped_teams)}")

    return games


def load_games_retrosheet(start: date, end: date) -> list[Game]:
    """Top-level helper: download + parse Retrosheet for the given year."""
    year = start.year  # single-season assumption; end.year must match
    with tempfile.TemporaryDirectory() as tmp_dir:
        csv_path = download_retrosheet(tmp_dir, year)
        print(f"Parsing game log: {csv_path.name} ...")
        return parse_retrosheet(csv_path, start, end)


# --------------------------------------------------------------------------- #
# Results source B — MLB Stats API schedule (year >= 2026)                    #
# --------------------------------------------------------------------------- #
def fetch_games_from_mlb_api(start: date, end: date) -> list[Game]:
    """Fetch completed MLB games from the Stats API schedule endpoint.

    Only games with detailedState == "Final" are included (excludes postponed,
    in-progress, or future games).  The API is paged by the date range natively.
    """
    print(f"Fetching MLB schedule {start} to {end} from Stats API ...")
    params = {
        "sportId": 1,
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "hydrate": "linescore",
    }
    try:
        resp = requests.get(f"{MLB_STATS_BASE}/schedule", params=params, timeout=60)
        resp.raise_for_status()
    except Exception as exc:
        raise RuntimeError(f"MLB Stats API schedule fetch failed: {exc}") from exc

    games: list[Game] = []
    skipped = 0

    for date_block in resp.json().get("dates", []):
        raw_date_str = date_block.get("date", "")
        try:
            block_date = date.fromisoformat(raw_date_str)
        except ValueError:
            continue

        for game in date_block.get("games", []):
            state = game.get("status", {}).get("detailedState", "")
            if state != "Final":
                skipped += 1
                continue

            teams = game.get("teams", {})
            home_info = teams.get("home", {})
            away_info = teams.get("away", {})

            home_name: str = home_info.get("team", {}).get("name", "")
            away_name: str = away_info.get("team", {}).get("name", "")

            try:
                home_score = int(home_info.get("score") or 0)
                away_score = int(away_info.get("score") or 0)
            except (TypeError, ValueError):
                skipped += 1
                continue

            if not home_name or not away_name:
                skipped += 1
                continue

            actual_winner = home_name if home_score > away_score else away_name
            games.append(
                Game(block_date, home_name, away_name, home_score, away_score, actual_winner)
            )

    if skipped:
        print(f"  [info] Skipped {skipped} non-Final games (postponed / in-progress / future)")

    return games


# --------------------------------------------------------------------------- #
# Point-in-time stats cache (works for any season year)                       #
# --------------------------------------------------------------------------- #
def _fetch_runs_per_game(
    season: int, end_date: date | None = None
) -> dict[str, dict[str, float]]:
    """Fetch runs/game and gamesPlayed per team from MLB Stats API.

    end_date=None  → full-season stats (used as prior-season fallback).
    Returns {team_name: {"rpg": float, "games": float}}.
    """
    params: dict[str, str | int] = {
        "season": season,
        "group": "hitting",
        "stats": "season",
        "sportIds": 1,
    }
    if end_date is not None:
        params["endDate"] = end_date.isoformat()

    try:
        resp = requests.get(
            f"{MLB_STATS_BASE}/teams/stats", params=params, timeout=30
        )
        resp.raise_for_status()
    except Exception as exc:
        print(f"  [warn] MLB Stats API error (season={season} endDate={end_date}): {exc}")
        return {}

    result: dict[str, dict[str, float]] = {}
    for split in resp.json().get("stats", [{}])[0].get("splits", []):
        team = split.get("team", {}).get("name")
        stat = split.get("stat", {})
        games = float(stat.get("gamesPlayed", 0) or 0)
        runs = float(stat.get("runs", 0) or 0)
        if team and games:
            result[team] = {"rpg": runs / games, "games": games}
    return result


class StatsCache:
    """Lazy per-date stats cache.

    Season is derived from the game date, so the same instance works for any
    calendar year.  The fallback (prior season full stats) is fetched once and
    blended in for teams with fewer than MIN_GAMES_FOR_SEASON played.
    """

    def __init__(self) -> None:
        # {game_date: {team_name: rpg}}
        self._cache: dict[date, dict[str, float]] = {}
        # {season_year: {team_name: rpg}} — prior-season full stats per season
        self._fallbacks: dict[int, dict[str, float]] = {}
        self.api_calls = 0

    def runs_per_game(self, team: str, game_date: date) -> float:
        stats = self._get_or_fetch(game_date)
        return stats.get(team, LEAGUE_AVG_RUNS)

    def _get_or_fetch(self, game_date: date) -> dict[str, float]:
        if game_date in self._cache:
            return self._cache[game_date]

        season = game_date.year
        # endDate = day before the game (point-in-time, no look-ahead)
        end_date = game_date - timedelta(days=1)
        raw = _fetch_runs_per_game(season, end_date)
        self.api_calls += 1
        time.sleep(0.15)  # polite pacing

        fallback = self._get_fallback(season)
        merged: dict[str, float] = dict(fallback)  # start from prior-season baseline

        for team, data in raw.items():
            if data["games"] >= MIN_GAMES_FOR_SEASON:
                merged[team] = data["rpg"]
            elif data["games"] > 0:
                # Weighted blend toward the prior when sample is thin
                prior = fallback.get(team, LEAGUE_AVG_RUNS)
                w = data["games"] / MIN_GAMES_FOR_SEASON
                merged[team] = w * data["rpg"] + (1 - w) * prior

        self._cache[game_date] = merged
        return merged

    def _get_fallback(self, season: int) -> dict[str, float]:
        if season not in self._fallbacks:
            prior = season - 1
            print(f"  Fetching {prior} full-season stats for early-{season} fallback ...")
            raw = _fetch_runs_per_game(prior)
            self.api_calls += 1
            self._fallbacks[season] = {t: d["rpg"] for t, d in raw.items()}
        return self._fallbacks[season]


# --------------------------------------------------------------------------- #
# Model evaluation (same for any results source)                              #
# --------------------------------------------------------------------------- #
def evaluate_game(game: Game, cache: StatsCache) -> GameResult:
    rpg_home = cache.runs_per_game(game.home_team, game.game_date)
    rpg_away = cache.runs_per_game(game.away_team, game.game_date)

    lam_home = rpg_home * HOME_ADVANTAGE
    lam_away = rpg_away

    p_home, p_away = baseball_match_probs(lam_home, lam_away)

    if p_home >= p_away:
        pick = game.home_team
        model_prob = p_home
    else:
        pick = game.away_team
        model_prob = p_away

    correct = pick == game.actual_winner

    # Estimated market odds: apply Pinnacle vig to model probability
    implied_prob = model_prob * (1 + VIG)
    estimated_odd = 1.0 / implied_prob
    profit = (estimated_odd - 1.0) if correct else -1.0

    return GameResult(
        game_date=game.game_date,
        home_team=game.home_team,
        away_team=game.away_team,
        model_pick=pick,
        model_prob=model_prob,
        actual_winner=game.actual_winner,
        correct=correct,
        estimated_odd=estimated_odd,
        profit=profit,
    )


# --------------------------------------------------------------------------- #
# Summary output                                                               #
# --------------------------------------------------------------------------- #
def print_summary(results: list[GameResult], start: date, end: date) -> None:
    if not results:
        print("No results to display.")
        return

    total = len(results)
    correct_count = sum(r.correct for r in results)
    total_profit = sum(r.profit for r in results)
    roi = (total_profit / total) * 100

    # By month (preserves insertion order → chronological)
    months: dict[str, list[GameResult]] = {}
    for r in results:
        key = r.game_date.strftime("%B")
        months.setdefault(key, []).append(r)

    # Confidence bands
    bands = [
        ("50-60%", 0.50, 0.60),
        ("60-70%", 0.60, 0.70),
        ("70%+",   0.70, 1.01),
    ]

    bar = "=" * 64
    print(f"\n{bar}")
    print(f"  SENTINEL -- MLB Backtest  {start.strftime('%b')}-{end.strftime('%b %Y')}")
    print(bar)
    print(f"  Games evaluated:     {total}")
    print(f"  Model picks correct: {correct_count} / {total}"
          f"  ({correct_count / total * 100:.1f}%)")
    print()
    roi_sign = "+" if total_profit >= 0 else ""
    net_sign = "+" if total_profit >= 0 else ""
    print(f"  Estimated ROI*:      {roi_sign}{roi:.1f}%  (betting 1 unit/game)")
    print(f"  Total units staked:  {total}")
    print(f"  Net units:           {net_sign}{total_profit:.1f}")
    print()
    print("  By month:")
    for month, rs in months.items():
        n = len(rs)
        c = sum(r.correct for r in rs)
        print(f"    {month}:  {c / n * 100:.1f}% correct  ({c}/{n})")
    print()
    print("  By confidence band:")
    for label, lo, hi in bands:
        band_rs = [r for r in results if lo <= r.model_prob < hi]
        if not band_rs:
            continue
        n = len(band_rs)
        c = sum(r.correct for r in band_rs)
        print(f"    {label}: {c / n * 100:.1f}% correct  (n={n})")
    print()
    print("  * Estimated odds -- Pinnacle vig applied to model probs.")
    print("    Not real market odds. Actual ROI will differ.")
    print(bar)
    print()


# --------------------------------------------------------------------------- #
# CSV output                                                                   #
# --------------------------------------------------------------------------- #
def _csv_path(start: date, end: date) -> Path:
    """Generate a filename that avoids overwriting previous backtest runs."""
    scripts_dir = Path(__file__).parent
    if start.year == end.year and start.month == end.month:
        # Single-month run: backtest_results_2026_may.csv
        suffix = f"{start.year}_{start.strftime('%b').lower()}"
    else:
        # Multi-month run: backtest_results_2025_apr_may.csv
        parts = []
        d = start.replace(day=1)
        while d <= end:
            parts.append(d.strftime("%b").lower())
            # advance to next month
            if d.month == 12:
                d = d.replace(year=d.year + 1, month=1)
            else:
                d = d.replace(month=d.month + 1)
        suffix = f"{start.year}_{'_'.join(parts)}"
    return scripts_dir / f"backtest_results_{suffix}.csv"


def save_csv(results: list[GameResult], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "date", "home_team", "away_team", "model_pick",
            "model_prob", "actual_winner", "correct",
            "estimated_odd", "profit",
        ])
        for r in results:
            writer.writerow([
                r.game_date.isoformat(),
                r.home_team,
                r.away_team,
                r.model_pick,
                round(r.model_prob, 4),
                r.actual_winner,
                int(r.correct),
                round(r.estimated_odd, 4),
                round(r.profit, 4),
            ])
    print(f"Full results saved: {path}")


# --------------------------------------------------------------------------- #
# Entry point                                                                  #
# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(description="MLB Poisson model backtest")
    parser.add_argument("--start", default="2026-05-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end",   default="2026-05-31", help="End date YYYY-MM-DD")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end   = date.fromisoformat(args.end)

    if start > end:
        print("Error: --start must be before --end.")
        sys.exit(1)

    # ------------------------------------------------------------------ #
    # Step 1 — Load game results (source depends on year)                 #
    # ------------------------------------------------------------------ #
    if start.year <= 2025:
        print(f"Source: Retrosheet game logs (year={start.year})")
        games = load_games_retrosheet(start, end)
    else:
        print(f"Source: MLB Stats API schedule (year={start.year})")
        games = fetch_games_from_mlb_api(start, end)

    print(f"Completed games in range {start} to {end}: {len(games)}")

    if not games:
        print("No completed games found. Check date range or data source.")
        return

    unique_dates = sorted({g.game_date for g in games})
    print(
        f"Unique game dates: {len(unique_dates)}"
        f"  (~{len(unique_dates) + 1} stats API calls)"
    )

    # ------------------------------------------------------------------ #
    # Steps 2–4 — Run model with point-in-time stats                      #
    # ------------------------------------------------------------------ #
    cache = StatsCache()
    results: list[GameResult] = []

    for i, game in enumerate(games, 1):
        if i % 50 == 0 or i == len(games):
            print(f"  Evaluating game {i}/{len(games)} ...", end="\r")
        results.append(evaluate_game(game, cache))

    print(f"\nStats API calls made: {cache.api_calls}")

    # ------------------------------------------------------------------ #
    # Steps 5–6 — Report and persist                                      #
    # ------------------------------------------------------------------ #
    print_summary(results, start, end)
    csv_out = _csv_path(start, end)
    save_csv(results, csv_out)


if __name__ == "__main__":
    main()
