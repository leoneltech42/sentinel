"""Route configuration for the flights domain.

Routes are stored in the domain's config jsonb column so they can be updated
from Supabase without touching code. The default (EZE->MAD) is used when no
routes are configured in the domain row.

Each route drives:
  - monitored_dates   -> explicit YYYY-MM-DD departure dates to search
                         (empty = generate next_n_dates(5) at runtime)
  - specific_dates    -> legacy alias kept for backward-compat with DB configs
  - monitor_month     -> kept in schema for DB configs; ignored by SerpAPIFlightsClient
                         (SerpAPI charges per request; we use monitored_dates instead)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta


@dataclass
class RouteConfig:
    """Configuration for one monitored flight route."""

    origin: str              # IATA departure airport, e.g. "EZE"
    destination: str         # IATA arrival airport, e.g. "MAD"
    monitor_month: bool      # legacy flag; SerpAPIFlightsClient uses monitored_dates
    specific_dates: list[str]   # legacy; used by TequilaClient / backward compat
    monitored_dates: list[str]  # YYYY-MM-DD dates for SerpAPI (empty = auto-generate)
    max_stops: int           # 0 = direct only, 1 = up to 1 stop, 2 = up to 2 stops
    currency: str            # ISO 4217, e.g. "USD"


def next_n_dates(n: int, start_days_ahead: int = 7) -> list[str]:
    """Generate n departure dates, each 7 days apart, starting start_days_ahead from today.

    Example::

        next_n_dates(5, 7)
        # -> ['2026-06-09', '2026-06-16', '2026-06-23', '2026-06-30', '2026-07-07']
        # (if today is 2026-06-02)

    Default behaviour gives a weekly spread of 5 upcoming departures, starting
    one week from now.  Callers that want a different cadence can adjust n or
    start_days_ahead; the total quota cost equals n requests per route.
    """
    today = date.today()
    return [
        (today + timedelta(days=start_days_ahead + i * 7)).isoformat()
        for i in range(n)
    ]


# Phase 0 default: Buenos Aires Ezeiza -> Madrid.
# Note: EZE (not BUE) — Google Flights requires the specific airport IATA code.
# monitored_dates=[] means the client will call next_n_dates(5) at runtime,
# producing 5 departure dates starting 7 days from today.
DEFAULT_ROUTE = RouteConfig(
    origin="EZE",
    destination="MAD",
    monitor_month=False,
    specific_dates=[],
    monitored_dates=[],   # empty → next_n_dates(5) at runtime
    max_stops=2,
    currency="USD",
)


def load_routes(domain_config: dict) -> list[RouteConfig]:
    """Build the list of routes from the domain's config jsonb.

    The domain row's config column may contain:
      {"routes": [{"origin": "EZE", "destination": "MAD", ...}, ...]}

    Falls back to DEFAULT_ROUTE if the key is absent or the list is empty.
    Unknown keys in each route dict are silently ignored — forward-compatible.
    """
    raw_routes: list[dict] = domain_config.get("routes", [])
    if not raw_routes:
        return [DEFAULT_ROUTE]

    routes: list[RouteConfig] = []
    for r in raw_routes:
        try:
            routes.append(
                RouteConfig(
                    origin=str(r["origin"]).upper(),
                    destination=str(r["destination"]).upper(),
                    monitor_month=bool(r.get("monitor_month", False)),
                    specific_dates=list(r.get("specific_dates", [])),
                    monitored_dates=list(r.get("monitored_dates", [])),
                    max_stops=int(r.get("max_stops", 2)),
                    currency=str(r.get("currency", "USD")).upper(),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            # Malformed route entry — log and skip rather than crash the run.
            import logging
            logging.warning("Skipping malformed route config %r: %s", r, exc)

    return routes or [DEFAULT_ROUTE]
