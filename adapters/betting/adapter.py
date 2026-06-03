"""Betting adapter — implements the Adapter contract for sports value betting.

This is the only place that knows what a bet is. It wires together:
  * ingestion (The Odds API) → raw events
  * stats provider + Poisson model → independent probabilities
  * value logic → signals with confidence and EV
  * resolution → binary outcome (won/lost) + CLV
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

import requests

from adapters.base import (
    Adapter,
    OutcomeData,
    RawEventData,
    ResolvableSignal,
    SignalData,
)
from adapters.betting import model as M
from adapters.betting import stats as S
from adapters.betting.ingestion import SPORT_KEYS, OddsAPIClient, best_h2h_odds


class BettingAdapter(Adapter):
    domain_slug = "betting"
    resolution_rule = "binary"

    def __init__(
        self,
        api_key: str,
        season: int,
        min_ev: float = 0.05,
        min_confidence: float = 0.50,
        events_override: list[RawEventData] | None = None,
        mlb_runs_override: dict[str, float] | None = None,
    ):
        self._client = OddsAPIClient(api_key) if api_key else None
        self._mlb = S.MLBStatsProvider(season, runs_override=mlb_runs_override)
        self._min_ev = min_ev
        self._min_confidence = min_confidence
        # Lets the paper-trade script inject sample data (mock mode).
        self._events_override = events_override

    @property
    def model_version(self) -> str:
        return "poisson_v0.1.0"

    def hyperparams(self) -> dict[str, Any]:
        return {
            "min_ev": self._min_ev,
            "min_confidence": self._min_confidence,
            "sports": list(SPORT_KEYS.keys()),
            "home_advantage": S.HOME_ADVANTAGE,
        }

    # --- ingestion -------------------------------------------------------- #
    def fetch_raw_events(self) -> list[RawEventData]:
        if self._events_override is not None:
            return self._events_override
        if self._client is None:
            raise RuntimeError("No API key configured and no events override given.")
        return self._client.fetch_all()

    # --- modeling --------------------------------------------------------- #
    def generate_signals(self, events: list[RawEventData]) -> list[SignalData]:
        signals: list[SignalData] = []
        for ev in events:
            sport_key = ev.event_key.split("::", 1)[0]
            odds_map = best_h2h_odds(ev.payload)
            if not odds_map:
                continue
            home = ev.payload.get("home_team")
            away = ev.payload.get("away_team")
            if not home or not away:
                continue

            probs = self._model_probs(sport_key, home, away, odds_map)
            if probs is None:
                continue

            # Only keep selections that survived the odds-sanity filter in
            # best_h2h_odds().  Extreme odds (e.g. Curaçao @ 80) are dropped
            # there, so probs may contain keys not present in odds_map.
            valid_selections = [s for s in probs if s in odds_map]
            if len(valid_selections) < 2:
                continue  # not enough valid outcomes to compute value

            odds = [odds_map[s] for s in valid_selections]
            raw_probs = [probs[s] for s in valid_selections]

            # Re-normalise so the subset still sums to 1.0 before devig.
            total = sum(raw_probs)
            if total <= 0:
                continue
            model_probs = [p / total for p in raw_probs]

            bets = M.find_value_bets(
                valid_selections, odds, model_probs, self._min_ev, self._min_confidence
            )
            for bet in bets:
                signals.append(
                    SignalData(
                        raw_event_key=ev.event_key,
                        signal_type="value_bet",
                        confidence=bet.model_prob,
                        expected_value=bet.ev,
                        valid_for_date=ev.event_at.date(),
                        valid_until=ev.event_at,
                        features={
                            "match": f"{home} vs {away}",
                            "sport": sport_key,
                            "market": "h2h",
                            "pick": bet.selection,
                            "best_odd": bet.decimal_odd,
                            "model_probability": round(bet.model_prob, 4),
                            "market_probability": round(bet.market_prob, 4),
                            "edge": round(bet.edge, 4),
                            "home_team": home,
                            "away_team": away,
                            "kelly_units": M.kelly_units(bet.model_prob, bet.decimal_odd),
                            "star_rating": M.star_rating(
                                M.kelly_units(bet.model_prob, bet.decimal_odd)
                            ),
                        },
                    )
                )
        return signals

    def _model_probs(
        self, sport_key: str, home: str, away: str, odds_map: dict[str, float]
    ) -> dict[str, float] | None:
        """Map each market selection to the model's independent probability."""
        if sport_key == SPORT_KEYS["world_cup"]:
            lam_h, lam_a = S.soccer_lambdas(home, away)
            p_home, p_draw, p_away = M.soccer_match_probs(lam_h, lam_a)
            out = {home: p_home, away: p_away}
            if "Draw" in odds_map:
                out["Draw"] = p_draw
            return out
        if sport_key == SPORT_KEYS["mlb"]:
            lam_h, lam_a = S.mlb_lambdas(self._mlb, home, away)
            p_home, p_away = M.baseball_match_probs(lam_h, lam_a)
            return {home: p_home, away: p_away}
        return None

    def evaluate_events(self, events: list[RawEventData]) -> list[dict[str, Any]]:
        """Return per-event model evaluation for diagnostics (not stored to DB).

        Each entry contains the match, all selections with their model/market
        probabilities and EV, and whether they cleared the configured thresholds.
        """
        results: list[dict[str, Any]] = []
        for ev in events:
            sport_key = ev.event_key.split("::", 1)[0]
            home = ev.payload.get("home_team", "?")
            away = ev.payload.get("away_team", "?")
            odds_map = best_h2h_odds(ev.payload)

            entry: dict[str, Any] = {
                "match": f"{home} vs {away}",
                "sport": sport_key,
                "event_key": ev.event_key,
                "game_time": ev.event_at.isoformat(),
                "has_odds": bool(odds_map),
                "supported": False,
                "skip_reason": None,
                "selections": [],
            }

            if not odds_map:
                entry["skip_reason"] = "no bookmaker odds in payload"
                results.append(entry)
                continue

            probs = self._model_probs(sport_key, home, away, odds_map)
            if probs is None:
                entry["skip_reason"] = f"sport '{sport_key}' not modelled yet"
                results.append(entry)
                continue

            entry["supported"] = True
            fair_probs = M.devig(list(odds_map.values()))
            fair_map = dict(zip(odds_map.keys(), fair_probs))

            for sel, model_p in probs.items():
                odd = odds_map.get(sel)
                if odd is None:
                    # Selection was filtered out by odds-sanity guard — skip
                    # rather than showing a misleading odd=0.00 / EV=-100%.
                    continue
                mkt_p = fair_map.get(sel, 0.0)
                ev_val = M.expected_value(model_p, odd)
                fails: list[str] = []
                if model_p < self._min_confidence:
                    fails.append(f"confidence {model_p:.1%} < {self._min_confidence:.1%}")
                if ev_val < self._min_ev:
                    fails.append(f"EV {ev_val:+.1%} < {self._min_ev:+.1%}")
                if model_p <= mkt_p:
                    fails.append(f"model {model_p:.1%} <= market {mkt_p:.1%}")
                entry["selections"].append({
                    "selection": sel,
                    "odd": round(odd, 3),
                    "model_prob": round(model_p, 4),
                    "market_prob": round(mkt_p, 4),
                    "ev": round(ev_val, 4),
                    "passes": len(fails) == 0,
                    "fail_reasons": fails,
                })

            results.append(entry)
        return results

    # --- resolution ------------------------------------------------------- #
    def resolve(self, signal: ResolvableSignal) -> OutcomeData | None:
        """Binary resolution: did the pick win? Returns None until the game ends.

        Returns OutcomeData with metadata['void']=True for suspended/cancelled games.
        Only MLB is implemented; soccer returns None (no stats feed yet).
        """
        if signal.valid_for_date > datetime.now(timezone.utc).date():
            return None  # event is scheduled for a future date (strict future)

        sport = signal.features.get("sport", "")
        if sport != SPORT_KEYS["mlb"]:
            return None  # soccer resolution not implemented in Phase 0

        home_team: str = signal.features["home_team"]
        away_team: str = signal.features["away_team"]
        pick: str = signal.features["pick"]
        best_odd: float = float(signal.features.get("best_odd", 1.0))

        result = self._fetch_mlb_result(signal.valid_for_date, home_team, away_team)
        if result is None:
            return None

        detail_state, home_score, away_score = result

        _VOID_KEYWORDS = ("Postponed", "Cancelled", "Suspended")
        if any(kw in detail_state for kw in _VOID_KEYWORDS):
            return OutcomeData(
                was_correct=False,
                actual_value=0.0,
                metadata={"void": True, "void_reason": detail_state},
            )

        if detail_state not in ("Final", "Game Over"):
            return None  # still in progress or not yet started

        winner = home_team if home_score > away_score else away_team
        was_correct = pick == winner
        meta: dict[str, Any] = {
            "home_score": home_score,
            "away_score": away_score,
            "winner": winner,
        }

        # Best-effort CLV — silently skipped if unavailable.
        closing = self._fetch_closing_line(sport, signal.event_key, signal.valid_for_date, pick)
        if closing is not None:
            meta["closing_line"] = closing
            meta["clv"] = round(best_odd / closing - 1, 4)

        return OutcomeData(
            was_correct=was_correct,
            actual_value=best_odd,
            metadata=meta,
        )

    def _fetch_mlb_result(
        self,
        game_date: date,
        home_team: str,
        away_team: str,
    ) -> tuple[str, int, int] | None:
        """Return (detailedState, home_score, away_score) for the matching game, or None."""
        url = f"{S.MLBStatsProvider._BASE}/schedule"
        params = {
            "sportId": 1,
            "date": game_date.isoformat(),
            "hydrate": "linescore",
        }
        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
        except Exception as exc:
            logging.warning("MLB Stats API unavailable: %s", exc)
            return None

        for date_block in resp.json().get("dates", []):
            for game in date_block.get("games", []):
                g_home = game["teams"]["home"]["team"]["name"]
                g_away = game["teams"]["away"]["team"]["name"]
                if g_home.lower() != home_team.lower() or g_away.lower() != away_team.lower():
                    continue
                detail = game["status"].get("detailedState", "")
                h_score = int(game["teams"]["home"].get("score") or 0)
                a_score = int(game["teams"]["away"].get("score") or 0)
                return detail, h_score, a_score

        return None  # game not found for this date

    def _fetch_closing_line(
        self,
        sport_key: str,
        event_key: str,
        game_date: date,
        pick: str,
    ) -> float | None:
        """Return the closing decimal odd for `pick`, or None if unavailable.

        Uses The Odds API historical endpoint. Snapshot at 16:00 UTC on game_date —
        before the earliest MLB first pitch (approx. noon ET / 9 AM PT).
        """
        if self._client is None:
            return None

        parts = event_key.split("::")
        if len(parts) < 2:
            return None
        event_id = parts[1]

        snapshot = datetime(game_date.year, game_date.month, game_date.day, 16, 0, 0,
                            tzinfo=timezone.utc)
        url = f"https://api.the-odds-api.com/v4/historical/sports/{sport_key}/odds"
        params = {
            "apiKey": self._client.api_key,
            "regions": self._client.regions,
            "markets": "h2h",
            "oddsFormat": "decimal",
            "date": snapshot.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "eventIds": event_id,
        }
        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            events = data.get("data", []) if isinstance(data, dict) else data
            if not events:
                return None
            best = best_h2h_odds(events[0])
            return best.get(pick) or None
        except Exception as exc:
            logging.debug("CLV fetch skipped: %s", exc)
            return None
