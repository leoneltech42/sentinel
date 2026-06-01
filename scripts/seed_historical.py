"""Seed historical signals for 2026-05-31 to demonstrate resolution.

Creates three real 2026-05-31 MLB matchups as RawEventData objects, runs the
pipeline against them so signals are stored in Supabase, and prints what was
seeded. Run *before* `paper_trade.py --resolve --date 2026-05-31`.

Actual results (verified via MLB Stats API):
  Cardinals 5 - Cubs 1        → Cardinals won (pick should be CORRECT)
  Mariners  3 - Diamondbacks 2 → Mariners won  (pick should be CORRECT)
  Brewers   2 - Astros 0       → Brewers won   (pick Astros should be WRONG)

Usage:
    python -m scripts.seed_historical
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from core.db import SessionLocal, init_db
from core.orchestrator import run_pipeline
from adapters.base import RawEventData
from adapters.betting.adapter import BettingAdapter
from adapters.betting.ingestion import SPORT_KEYS


def _event(event_id: str, home: str, away: str, home_odd: float, away_odd: float) -> RawEventData:
    """Build a RawEventData shaped like a real Odds API response for 2026-05-31."""
    mlb_key = SPORT_KEYS["mlb"]
    # Game time: 18:10 UTC (typical afternoon East Coast start, 2:10 PM ET).
    event_at = datetime(2026, 5, 31, 18, 10, 0, tzinfo=timezone.utc)
    payload = {
        "id": event_id,
        "sport_key": mlb_key,
        "commence_time": "2026-05-31T18:10:00Z",
        "home_team": home,
        "away_team": away,
        "bookmakers": [
            {
                "key": "draftkings",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": home, "price": home_odd},
                            {"name": away, "price": away_odd},
                        ],
                    }
                ],
            }
        ],
    }
    return RawEventData(
        event_key=f"{mlb_key}::{event_id}::{event_at.date().isoformat()}",
        payload=payload,
        event_at=event_at,
        source="seed_historical",
    )


def main() -> None:
    # Run rates calibrated so each home team (Cardinals, Mariners, Astros)
    # shows a +EV signal through the Poisson model at the given odds.
    mlb_runs_override = {
        "St. Louis Cardinals": 5.20,   # above-average offense
        "Chicago Cubs":        4.00,   # below-average offense
        "Seattle Mariners":    4.80,   # solid offense, home edge
        "Arizona Diamondbacks": 4.30,
        "Houston Astros":      5.00,   # strong offense, home edge
        "Milwaukee Brewers":   4.10,
    }

    events_override = [
        # Cardinals (home) vs Cubs — odds give Cardinals a +EV edge
        _event("hist_531_cardinals", "St. Louis Cardinals", "Chicago Cubs",
               home_odd=1.80, away_odd=2.20),
        # Mariners (home) vs Diamondbacks — tight game, small edge
        _event("hist_531_mariners",  "Seattle Mariners", "Arizona Diamondbacks",
               home_odd=1.85, away_odd=2.10),
        # Astros (home) vs Brewers — model favors Astros (they lost in reality)
        _event("hist_531_astros",    "Houston Astros",   "Milwaukee Brewers",
               home_odd=1.72, away_odd=2.30),
    ]

    season = int(os.getenv("SEASON", datetime.now(timezone.utc).year))
    adapter = BettingAdapter(
        api_key="",               # no API calls needed for seeding
        season=season,
        events_override=events_override,
        mlb_runs_override=mlb_runs_override,
    )

    init_db()
    with SessionLocal() as session:
        run = run_pipeline(session, adapter)

    # Summarise what was seeded.
    print("\nSeeded 2026-05-31 historical signals.")
    print("Now run:")
    print("  python -m scripts.paper_trade --resolve --date 2026-05-31")
    print()
    print("Expected resolution:")
    print("  [W] St. Louis Cardinals  (won 5-1 vs Cubs)")
    print("  [W] Seattle Mariners     (won 3-2 vs Diamondbacks)")
    print("  [L] Houston Astros       (lost 0-2 to Brewers)")


if __name__ == "__main__":
    main()
