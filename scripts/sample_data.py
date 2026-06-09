"""Sample fixtures shaped like live API responses.

Lets you run the full pipeline end-to-end with `--mock`, with no network and no
API quota spent — useful for development and for anyone cloning the repo.

betting:         shaped like The Odds API v4 responses.
flights serpapi: shaped like SerpAPI Google Flights responses (best_flights + price_insights).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from adapters.base import RawEventData
from adapters.betting.ingestion import SPORT_KEYS


def _future(hours: int) -> str:
    """Return an ISO timestamp that is `hours` ahead of now, but always on
    today's UTC date.

    Anchors to today at 14:00 UTC instead of wall-clock now + hours so that
    the generated commence_time stays on today's date regardless of when
    --mock is run (avoids crossing UTC midnight on late-evening runs).
    """
    today = datetime.now(timezone.utc).date()
    anchor = datetime(today.year, today.month, today.day, 14, 0, 0, tzinfo=timezone.utc)
    dt = anchor + timedelta(hours=hours)
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


def sample_mlb_pitchers() -> dict[str, dict]:
    """Sample probable-pitcher data, keyed by '{home_team}_{away_team}'.

    Lets --mock exercise the v0.3.0 pitcher-adjustment path with no network.
    """
    return {
        "Los Angeles Dodgers_Colorado Rockies": {
            "home_pitcher_era": 3.20,
            "away_pitcher_era": 5.10,
            "home_pitcher_name": "Mock Pitcher A",
            "away_pitcher_name": "Mock Pitcher B",
        }
    }


# --------------------------------------------------------------------------- #
# Flights domain fixtures                                                      #
# --------------------------------------------------------------------------- #

def _flight(
    price: float,
    dep_offset_days: int,
    airline: str = "IB",
    stops: int = 1,
    duration_secs: int = 49_800,
) -> dict:
    """Build a minimal Tequila/Kiwi flight dict for mock runs."""
    dep = datetime.now(timezone.utc) + timedelta(days=dep_offset_days)
    dep_str = dep.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {
        "price": price,
        "airlines": [airline],
        "route": [{"airline": airline}] * (stops + 1),
        "local_departure": dep_str,
        "utc_departure": dep_str,
        "duration": {"departure": duration_secs, "return": 0, "total": duration_secs},
    }


def sample_flights_events() -> list[RawEventData]:
    """Mock RawEventData (Tequila format) for BUE->MAD over several future dates.

    Prices simulate a mid-month dip that would trigger price_drop on subsequent
    runs (on first mock run no history exists, so signals won't fire yet).
    """
    today = datetime.now(timezone.utc).date()
    search_date = today.isoformat()

    raw_flights: list[tuple[int, float]] = [
        (30, 850.0),   # dep in 30 days — baseline price
        (37, 780.0),   # dep in 37 days
        (44, 720.0),   # dep in 44 days — cheaper
        (51, 680.0),   # dep in 51 days — cheapest (monthly minimum candidate)
        (58, 810.0),   # dep in 58 days
        (65, 870.0),   # dep in 65 days
    ]

    events: list[RawEventData] = []
    for dep_offset, price in raw_flights:
        flight = _flight(price, dep_offset)
        dep_str = flight["local_departure"][:10]
        event_key = f"flights::BUE-MAD::{dep_str}::{search_date}"
        dep_dt = datetime.fromisoformat(flight["utc_departure"].replace("Z", "+00:00"))
        events.append(
            RawEventData(
                event_key=event_key,
                payload=flight,
                event_at=dep_dt,
                source="tequila",
            )
        )
    return events


def _serpapi_response(
    price: float,
    dep_offset_days: int,
    airline: str = "Iberia",
    stops: int = 1,
    total_duration_minutes: int = 810,
    price_level: str = "low",
    typical_range: tuple[float, float] = (700.0, 1100.0),
    lowest_price: float | None = None,
) -> dict:
    """Build a minimal SerpAPI Google Flights response dict for mock runs.

    Includes best_flights[] and price_insights so the fast-path in
    check_price_drop can fire without needing prior DB history.
    """
    dep = datetime.now(timezone.utc) + timedelta(days=dep_offset_days)
    dep_time = dep.strftime("%H:%M")
    arr = dep + timedelta(minutes=total_duration_minutes)
    arr_time = arr.strftime("%H:%M")

    # Build segments list: 1 stop = 2 flights
    flights_list = [
        {
            "departure_airport": {"iataCode": "EZE", "time": dep_time},
            "arrival_airport": {"iataCode": "MAD", "time": arr_time},
            "airline": airline,
        }
    ]
    if stops >= 1:
        mid_time = (dep + timedelta(hours=6)).strftime("%H:%M")
        flights_list = [
            {
                "departure_airport": {"iataCode": "EZE", "time": dep_time},
                "arrival_airport": {"iataCode": "MIA", "time": mid_time},
                "airline": airline,
            },
            {
                "departure_airport": {"iataCode": "MIA", "time": mid_time},
                "arrival_airport": {"iataCode": "MAD", "time": arr_time},
                "airline": airline,
            },
        ]

    return {
        "best_flights": [
            {
                "price": price,
                "airline": airline,
                "total_duration": total_duration_minutes,
                "flights": flights_list,
            }
        ],
        "price_insights": {
            "lowest_price": lowest_price if lowest_price is not None else price,
            "price_level": price_level,
            "typical_price_range": list(typical_range),
        },
    }


def sample_flights_events_serpapi() -> list[RawEventData]:
    """Mock RawEventData (SerpAPI format) for EZE->MAD over 5 future departure dates.

    Each event is shaped like a real SerpAPI Google Flights response, with
    best_flights[] and price_insights included.  price_level="low" on the
    cheapest dates ensures the fast-path in check_price_drop fires on the
    first mock run (no DB history needed).

    Simulates a realistic mid-month price dip: dates 21 and 28 days ahead
    are priced below the typical_range floor, so they trigger signals.
    """
    today = datetime.now(timezone.utc).date()
    search_date = today.isoformat()

    raw_offers: list[tuple[int, float, str, tuple[float, float]]] = [
        (7,  890.0, "typical", (700.0, 1100.0)),   # 1 week ahead — typical
        (14, 820.0, "typical", (700.0, 1100.0)),   # 2 weeks ahead — typical
        (21, 640.0, "low",     (700.0, 1100.0)),   # 3 weeks ahead — LOW (fast-path)
        (28, 660.0, "low",     (700.0, 1100.0)),   # 4 weeks ahead — LOW (fast-path)
        (35, 870.0, "typical", (700.0, 1100.0)),   # 5 weeks ahead — typical
    ]

    events: list[RawEventData] = []
    for dep_offset, price, level, typical_range in raw_offers:
        payload = _serpapi_response(
            price=price,
            dep_offset_days=dep_offset,
            price_level=level,
            typical_range=typical_range,
            lowest_price=price,
        )
        dep = datetime.now(timezone.utc) + timedelta(days=dep_offset)
        dep_str = dep.date().isoformat()
        event_key = f"flights::EZE-MAD::{dep_str}::serpapi::{dep_str}_{int(price)}"
        dep_dt = dep.replace(hour=0, minute=0, second=0, microsecond=0)
        events.append(
            RawEventData(
                event_key=event_key,
                payload=payload,
                event_at=dep_dt,
                source="serpapi",
            )
        )
    return events
