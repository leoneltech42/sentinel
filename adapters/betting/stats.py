"""Stats provider — supplies the expected goals/runs (lambdas) the model needs.

This is the independent signal that lets us find *value*: if we only read the
bookmaker's odds back, we can never beat the market. The model needs its own
probability estimate, which comes from team strength derived here.

Phase 0 status:
  * MLB: structured to pull runs scored/allowed per game from the free, official
    MLB Stats API (no key required). Written but not executed here — verify the
    endpoint/field names on first live run.
  * Soccer (World Cup): uses a configurable team-ratings map, because reliable
    pre-tournament per-team stats are sparse. This is the explicit v0 input to be
    replaced by a real stats feed (e.g. football-data.org). Unknown teams fall
    back to a league-average baseline.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import requests

# League-average baselines (expected goals/runs for an average team per game).
SOCCER_BASELINE = 1.35  # avg goals per team in a World Cup match (approx.)
# 1.05 (poisson_v0.3.1, 2026-06-20): bumped from 1.04. The 103-pick live
# sample showed home picks winning 66% vs 48% away while the model assigned
# them nearly equal probability -- 1.04 was understating the real home-field
# effect. Confirmed via two independent checks before shipping: the 419-game
# May-2026 backtest sweep (1.05 minimizes Brier within a sane home-pick-rate
# band, well short of the v0.1.0 100%-home bias seen at 1.10) and a
# re-simulation of all 103 live picks at 1.05 (Brier 0.2722 -> 0.2684, zero
# picks flip sides). See scripts/ha_sweep.py and scripts/ha_resim.py.
# Still flagged as needing more live data (n>=200-250) for a fully confident
# read -- re-evaluate alongside the next calibration refresh.
#
# History: 1.04 matched the empirical MLB home win rate (~53%) and replaced
# 1.10, which generated 72%+ home picks and inflated confidence in the 70%+
# band. Applied at the 30-pick gate, 2026-06-04.
HOME_ADVANTAGE = 1.05

# MLB team name -> MLB Stats API team ID. Required for the schedule endpoint
# (recent-form game logs and probable-pitcher lookups are keyed by teamId).
# Source: https://statsapi.mlb.com/api/v1/teams?sportId=1&season=2026
MLB_TEAM_IDS: dict[str, int] = {
    "Arizona Diamondbacks": 109,
    "Athletics": 133,
    "Atlanta Braves": 144,
    "Baltimore Orioles": 110,
    "Boston Red Sox": 111,
    "Chicago Cubs": 112,
    "Chicago White Sox": 145,
    "Cincinnati Reds": 113,
    "Cleveland Guardians": 114,
    "Colorado Rockies": 115,
    "Detroit Tigers": 116,
    "Houston Astros": 117,
    "Kansas City Royals": 118,
    "Los Angeles Angels": 108,
    "Los Angeles Dodgers": 119,
    "Miami Marlins": 146,
    "Milwaukee Brewers": 158,
    "Minnesota Twins": 142,
    "New York Mets": 121,
    "New York Yankees": 147,
    "Philadelphia Phillies": 143,
    "Pittsburgh Pirates": 134,
    "San Diego Padres": 135,
    "San Francisco Giants": 137,
    "Seattle Mariners": 136,
    "St. Louis Cardinals": 138,
    "Tampa Bay Rays": 139,
    "Texas Rangers": 140,
    "Toronto Blue Jays": 141,
    "Washington Nationals": 120,
}

# v0 soccer ratings: relative attacking strength, 1.0 = average. Replace with feed.
WORLD_CUP_RATINGS: dict[str, float] = {
    "Argentina": 1.45,
    "France": 1.45,
    "Brazil": 1.40,
    "England": 1.35,
    "Spain": 1.35,
    "Portugal": 1.30,
    "Germany": 1.25,
    "Netherlands": 1.25,
}


def soccer_lambdas(home_team: str, away_team: str) -> tuple[float, float]:
    """Expected goals for each side, from ratings. v0 — see module docstring."""
    home_rating = WORLD_CUP_RATINGS.get(home_team, 1.0)
    away_rating = WORLD_CUP_RATINGS.get(away_team, 1.0)
    lam_home = SOCCER_BASELINE * home_rating * HOME_ADVANTAGE
    lam_away = SOCCER_BASELINE * away_rating
    return lam_home, lam_away


class MLBStatsProvider:
    """Pulls team run-scoring rates, recent form, and probable-pitcher data
    from the free MLB Stats API.

    v0.3.0 additions:
      * probable_pitchers() — starting-pitcher season ERA for both sides of a
        game, used to adjust the opposing team's expected runs.
      * team_recent_rpg() — last-N-games runs/game, blended with the season
        average to capture recent form (CLAUDE.md: 70% season / 30% last-15).

    Both new lookups are cached and degrade gracefully to None when data is
    unavailable (e.g. mock mode, API hiccups, lineups not yet announced) —
    the model simply falls back to season-RPG-only behavior.
    """

    _BASE = "https://statsapi.mlb.com/api/v1"

    def __init__(
        self,
        season: int,
        runs_override: dict[str, float] | None = None,
        pitchers_override: dict[str, dict] | None = None,
    ):
        self.season = season
        # Pre-seeded runs/game by team. Lets mock mode run with no network.
        self._cache: dict[str, float] = dict(runs_override or {})
        self._loaded = runs_override is not None

        # Mock-mode pitcher data, keyed by "{home_team}_{away_team}".
        # When set, probable_pitchers() returns from here instead of the API.
        self._pitchers_override = pitchers_override

        # Caches for the new per-game lookups (avoid duplicate API calls
        # within a single pipeline run).
        self._pitcher_cache: dict[tuple[str, str, str], dict | None] = {}
        self._recent_rpg_cache: dict[tuple[str, str], float | None] = {}
        self._schedule_cache: dict[str, list[dict]] = {}

    def runs_per_game(self, team_name: str) -> float:
        if not self._loaded:
            self._load()
            self._loaded = True
        return self._cache.get(team_name, 4.5)  # league-avg fallback

    def _load(self) -> None:
        url = f"{self._BASE}/teams/stats"
        params = {"season": self.season, "group": "hitting", "stats": "season",
                  "sportIds": 1}
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        for split in resp.json().get("stats", [{}])[0].get("splits", []):
            team = split.get("team", {}).get("name")
            stat = split.get("stat", {})
            games = float(stat.get("gamesPlayed", 0) or 0)
            runs = float(stat.get("runs", 0) or 0)
            if team and games:
                self._cache[team] = runs / games

    # ------------------------------------------------------------------ #
    # Starting pitcher data (v0.3.0)                                      #
    # ------------------------------------------------------------------ #
    def probable_pitchers(
        self, game_date: str, home_team: str, away_team: str
    ) -> dict | None:
        """Return probable-starter info for a specific game, or None.

        Returns:
          {"home_pitcher_era": float | None, "away_pitcher_era": float | None,
           "home_pitcher_name": str | None, "away_pitcher_name": str | None}
        or None when probable pitchers aren't announced for either side yet —
        the caller then proceeds without a pitcher adjustment.
        """
        # Mock mode: serve canned fixtures, no network at all.
        if self._pitchers_override is not None:
            return self._pitchers_override.get(f"{home_team}_{away_team}")

        cache_key = (game_date, home_team, away_team)
        if cache_key in self._pitcher_cache:
            return self._pitcher_cache[cache_key]

        result = self._fetch_probable_pitchers(game_date, home_team, away_team)
        self._pitcher_cache[cache_key] = result
        return result

    def _fetch_probable_pitchers(
        self, game_date: str, home_team: str, away_team: str
    ) -> dict | None:
        try:
            games = self._schedule_for_date(game_date)
        except (requests.RequestException, ValueError):
            return None

        game = self._match_game(games, home_team, away_team)
        if game is None:
            return None

        teams = game.get("teams", {})
        home_p = teams.get("home", {}).get("probablePitcher")
        away_p = teams.get("away", {}).get("probablePitcher")
        if not home_p and not away_p:
            return None

        return {
            "home_pitcher_era": self._pitcher_season_era(home_p),
            "away_pitcher_era": self._pitcher_season_era(away_p),
            "home_pitcher_name": home_p.get("fullName") if home_p else None,
            "away_pitcher_name": away_p.get("fullName") if away_p else None,
        }

    def _schedule_for_date(self, game_date: str) -> list[dict]:
        if game_date in self._schedule_cache:
            return self._schedule_cache[game_date]
        url = f"{self._BASE}/schedule"
        params = {
            "sportId": 1,
            "date": game_date,
            "hydrate": "probablePitcher,linescore",
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        games = [g for d in resp.json().get("dates", []) for g in d.get("games", [])]
        self._schedule_cache[game_date] = games
        return games

    @staticmethod
    def _match_game(games: list[dict], home_team: str, away_team: str) -> dict | None:
        h, a = home_team.casefold(), away_team.casefold()
        for g in games:
            teams = g.get("teams", {})
            g_home = teams.get("home", {}).get("team", {}).get("name", "")
            g_away = teams.get("away", {}).get("team", {}).get("name", "")
            if g_home.casefold() == h and g_away.casefold() == a:
                return g
        return None

    def _pitcher_season_era(self, pitcher: dict | None) -> float | None:
        if not pitcher or not pitcher.get("id"):
            return None
        try:
            url = f"{self._BASE}/people/{pitcher['id']}/stats"
            params = {"stats": "season", "group": "pitching", "season": self.season}
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            splits = resp.json().get("stats", [{}])[0].get("splits", [])
            if not splits:
                return None
            era = splits[0].get("stat", {}).get("era")
            return float(era) if era is not None else None
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
            return None

    # ------------------------------------------------------------------ #
    # Recent form (v0.3.0)                                                #
    # ------------------------------------------------------------------ #
    def team_recent_rpg(
        self, team_name: str, game_date: str, n_games: int = 15
    ) -> float | None:
        """Last-N-games runs/game for a team, ending the day before game_date.

        Returns None when fewer than 5 finished games are found in the lookback
        window (too little signal — fall back to season RPG only).
        """
        cache_key = (team_name, game_date)
        if cache_key in self._recent_rpg_cache:
            return self._recent_rpg_cache[cache_key]

        result = self._fetch_recent_rpg(team_name, game_date, n_games)
        self._recent_rpg_cache[cache_key] = result
        return result

    def _fetch_recent_rpg(
        self, team_name: str, game_date: str, n_games: int
    ) -> float | None:
        team_id = MLB_TEAM_IDS.get(team_name)
        if team_id is None:
            return None
        try:
            end = datetime.strptime(game_date, "%Y-%m-%d").date() - timedelta(days=1)
            start = end - timedelta(days=30)
            url = f"{self._BASE}/schedule"
            params = {
                "sportId": 1,
                "teamId": team_id,
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "hydrate": "linescore",
            }
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            games = [g for d in resp.json().get("dates", []) for g in d.get("games", [])]
        except (requests.RequestException, ValueError):
            return None

        runs: list[float] = []
        for g in games:
            if g.get("status", {}).get("detailedState") != "Final":
                continue
            home_id = g.get("teams", {}).get("home", {}).get("team", {}).get("id")
            side = "home" if home_id == team_id else "away"
            scored = g.get("linescore", {}).get("teams", {}).get(side, {}).get("runs")
            if scored is not None:
                runs.append(float(scored))

        if len(runs) < 5:
            return None
        last_n = runs[-n_games:]
        return sum(last_n) / len(last_n)


def mlb_lambdas(
    provider: MLBStatsProvider,
    home_team: str,
    away_team: str,
    game_date: str,
    league_avg_era: float = 4.20,
    recent_weight: float = 0.30,
) -> tuple[float, float]:
    """Expected runs for each side.

    v0.3.0 pipeline:
      1. Start from season-average runs/game (stable baseline).
      2. Blend in recent form — 70% season average / 30% last-15-games RPG —
         when enough recent games are available (CLAUDE.md "Next model
         improvement" plan; evidence: several teams significantly under/over
         scored their season RPG in observed live losses).
      3. Apply HOME_ADVANTAGE to the home side.
      4. Adjust each side's expected runs for the *opposing* starting pitcher's
         quality: a sub-league-average ERA suppresses the facing offense's
         expected runs, and vice versa. Degrades gracefully to no adjustment
         when probable pitchers aren't announced yet.
    """
    rpg_home = provider.runs_per_game(home_team)
    rpg_away = provider.runs_per_game(away_team)

    recent_home = provider.team_recent_rpg(home_team, game_date)
    recent_away = provider.team_recent_rpg(away_team, game_date)
    if recent_home is not None:
        rpg_home = (1 - recent_weight) * rpg_home + recent_weight * recent_home
    if recent_away is not None:
        rpg_away = (1 - recent_weight) * rpg_away + recent_weight * recent_away

    lam_home = rpg_home * HOME_ADVANTAGE
    lam_away = rpg_away

    pitchers = provider.probable_pitchers(game_date, home_team, away_team)
    if pitchers is not None:
        home_era = pitchers.get("home_pitcher_era")
        away_era = pitchers.get("away_pitcher_era")
        # NOTE: ratio is (starter_era / league_avg_era), NOT the inverse.
        # ERA measures runs *allowed* — a low-ERA ace suppresses the facing
        # offense (ratio < 1 shrinks their lambda); a high-ERA arm inflates it
        # (ratio > 1). The inverted ratio would reward teams for facing aces
        # and punish them for facing weak starters — exactly backwards. (This
        # corrects a direction error in the original research proposal, caught
        # during implementation on 2026-06-08 before it reached the live model.)
        if away_era and away_era > 0:
            # The away pitcher faces the home lineup — adjusts home's lambda.
            lam_home *= (away_era / league_avg_era)
        if home_era and home_era > 0:
            # The home pitcher faces the away lineup — adjusts away's lambda.
            lam_away *= (home_era / league_avg_era)

    return lam_home, lam_away
