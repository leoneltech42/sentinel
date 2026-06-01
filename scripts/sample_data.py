"""Sample fixtures shaped like The Odds API v4 responses.

Lets you run the full pipeline end-to-end with `--mock`, with no network and no
API quota spent — useful for development and for anyone cloning the repo.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from adapters.base import RawEventData
from adapters.betting.ingestion import SPORT_KEYS


def _future(hours: int) -> str:
    dt = datetime.now(timezone.utc) + timedelta(hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def sample_events() -> list[RawEventData]:
    soccer_key = SPORT_KEYS["world_cup"]
    mlb_key = SPORT_KEYS["mlb"]
    raw = [
        (
            soccer_key,
            {
                "id": "wc001",
                "sport_key": soccer_key,
                "commence_time": _future(6),
                "home_team": "Argentina",
                "away_team": "Netherlands",
                "bookmakers": [
                    {
                        "key": "bet365",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Argentina", "price": 2.10},
                                    {"name": "Netherlands", "price": 3.60},
                                    {"name": "Draw", "price": 3.30},
                                ],
                            }
                        ],
                    }
                ],
            },
        ),
        (
            mlb_key,
            {
                "id": "mlb001",
                "sport_key": mlb_key,
                "commence_time": _future(4),
                "home_team": "Los Angeles Dodgers",
                "away_team": "Colorado Rockies",
                "bookmakers": [
                    {
                        "key": "draftkings",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Los Angeles Dodgers", "price": 1.55},
                                    {"name": "Colorado Rockies", "price": 2.65},
                                ],
                            }
                        ],
                    }
                ],
            },
        ),
    ]
    events = []
    for sport_key, payload in raw:
        commence = payload["commence_time"]
        event_at = datetime.fromisoformat(commence.replace("Z", "+00:00"))
        events.append(
            RawEventData(
                event_key=f"{sport_key}::{payload['id']}::{event_at.date().isoformat()}",
                payload=payload,
                event_at=event_at,
                source="mock",
            )
        )
    return events


def sample_mlb_runs() -> dict[str, float]:
    """Sample runs/game per team so MLB mock runs need no network."""
    return {
        "Los Angeles Dodgers": 5.4,  # strong offense
        "Colorado Rockies": 4.0,  # weaker
    }
