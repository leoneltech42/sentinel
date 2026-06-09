"""The Odds API client — pulls pre-match decimal odds for the betting domain.

Free tier: 500 requests/month. We cache every response into raw_events, so we
never re-fetch the same slate and stay well within quota. See:
https://the-odds-api.com/

NOTE: written against the documented v4 shape but not executed here (no network
in this environment). Verify field names against a live response on first run.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import requests

from adapters.base import RawEventData

_BASE = "https://api.the-odds-api.com/v4"

# Full registry of sports the adapter knows how to model. Not all of them are
# necessarily *active* in a given run — see BettingAdapter's `active_sports`.
# World Cup stays registered (re-enable via domain config) even though it's
# off by default: the static WORLD_CUP_RATINGS placeholder finds no genuine
# edge without a real stats feed (see CLAUDE.md decision log).
ALL_SPORT_KEYS = {
    "mlb": "baseball_mlb",
    "world_cup": "soccer_fifa_world_cup",
}

# Back-compat alias — existing lookups like SPORT_KEYS["mlb"] keep working.
SPORT_KEYS = ALL_SPORT_KEYS


class OddsAPIClient:
    def __init__(
        self,
        api_key: str,
        regions: str = "eu",
        market: str = "h2h",
        sport_keys: list[str] | None = None,
    ):
        self.api_key = api_key
        self.regions = regions  # eu gives decimal odds
        self.market = market  # h2h = moneyline / match winner
        # Provider sport_key strings (e.g. "baseball_mlb") to fetch. Defaults
        # to every registered sport when not given.
        self._sport_keys = sport_keys if sport_keys is not None else list(ALL_SPORT_KEYS.values())
        # Populated after each successful request; read via .last_quota.
        self._last_quota: dict[str, str] = {}

    @property
    def last_quota(self) -> dict[str, str]:
        """Headers x-requests-used and x-requests-remaining from the last call."""
        return self._last_quota

    def fetch_sport(self, sport_key: str) -> list[RawEventData]:
        """Fetch the current odds slate for one sport, normalized to RawEventData."""
        url = f"{_BASE}/sports/{sport_key}/odds"
        params = {
            "apiKey": self.api_key,
            "regions": self.regions,
            "markets": self.market,
            "oddsFormat": "decimal",
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        # Capture quota headers for every successful call; last one wins on fetch_all.
        self._last_quota = {
            "x-requests-used": resp.headers.get("x-requests-used", "?"),
            "x-requests-remaining": resp.headers.get("x-requests-remaining", "?"),
        }
        # Discard events that have already started — in-play odds are volatile
        # and meaningless for pre-match value betting.
        now = datetime.now(timezone.utc)
        today = now.date()
        all_events: list[dict[str, Any]] = resp.json()
        pre_match: list[dict[str, Any]] = [
            ev for ev in all_events
            if datetime.fromisoformat(
                ev["commence_time"].replace("Z", "+00:00")
            ) > now
        ]
        skipped_inplay = len(all_events) - len(pre_match)
        if skipped_inplay:
            logging.info("Skipped %d in-play event(s) for %s", skipped_inplay, sport_key)

        # Build RawEventData first so we can filter on the already-parsed event_at.
        events = [self._to_event(sport_key, ev) for ev in pre_match]

        # Restrict to games starting today (UTC). Future-dated lines (tomorrow+)
        # vary wildly between runs as bookmakers open/close their markets, causing
        # spurious signal churn. We only model games we can track and resolve same-day.
        today_events = [e for e in events if e.event_at.date() == today]
        n_future = len(events) - len(today_events)
        if n_future:
            logging.info(
                "Filtered %d future-date event(s) (tomorrow or later) for %s",
                n_future, sport_key,
            )

        return today_events

    def fetch_all(self) -> list[RawEventData]:
        events: list[RawEventData] = []
        for sport_key in self._sport_keys:
            try:
                events.extend(self.fetch_sport(sport_key))
            except Exception as exc:
                # Log and continue — one sport failing shouldn't abort the whole run.
                print(f"  [warn] Could not fetch {sport_key}: {exc}")
        return events

    @staticmethod
    def _to_event(sport_key: str, raw: dict) -> RawEventData:
        commence = raw["commence_time"]  # ISO 8601, e.g. "2026-06-15T18:00:00Z"
        event_at = datetime.fromisoformat(commence.replace("Z", "+00:00"))
        # Stable key: sport + provider event id + date. Prevents duplicates.
        event_key = f"{sport_key}::{raw['id']}::{event_at.date().isoformat()}"
        return RawEventData(
            event_key=event_key,
            payload=raw,  # stored untransformed; the model reads it later
            event_at=event_at,
            source="the-odds-api",
        )


_ODD_MIN = 1.05   # below this is effectively a certainty — suspicious for MLB
_ODD_MAX = 15.0   # above this is not a real moneyline offering


def best_h2h_odds(payload: dict) -> dict[str, float]:
    """Extract the best available decimal odd per outcome across bookmakers.

    Returns a mapping like {"Argentina": 2.40, "Brasil": 2.95, "Draw": 3.10}.
    Best odds = most favorable to the bettor, which is what we'd actually take.

    Odds outside [_ODD_MIN, _ODD_MAX] are filtered out and logged — they are
    either data errors or not genuine betting opportunities.
    """
    best: dict[str, float] = {}
    for bookmaker in payload.get("bookmakers", []):
        for mkt in bookmaker.get("markets", []):
            if mkt.get("key") != "h2h":
                continue
            for outcome in mkt.get("outcomes", []):
                name, price = outcome["name"], float(outcome["price"])
                if price > _ODD_MAX or price < _ODD_MIN:
                    logging.warning(
                        "Filtered suspicious odd %.2f for %s (bookmaker: %s)",
                        price, name, bookmaker.get("key", "?"),
                    )
                    continue
                if name not in best or price > best[name]:
                    best[name] = price
    return best
