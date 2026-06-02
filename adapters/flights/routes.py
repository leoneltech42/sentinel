"""Route configuration for the flights domain.

Routes are stored in the domain's config jsonb column so they can be updated
from Supabase without touching code. The default (EZE->MAD) is used when no
routes are configured in the domain row.

Two monitoring modes, combinable on the same route:

  Flexible (default):
    Auto-generates n dates starting a configurable number of days from today
    at a configurable interval. Zero configuration needed — works out of the box.

  Range:
    Samples n dates distributed uniformly across a specific calendar range
    (e.g. "monitor August for a trip I'm planning"). Activate by setting
    range_date_from + range_date_to in the domain config or via --range CLI flag.

Both modes may be active simultaneously on the same route. get_dates_to_monitor()
returns the union (deduped, sorted), so the two modes complement each other.

Range mode example (add to domains.config jsonb in Supabase):
  {
    "routes": [{
      "origin": "EZE",
      "destination": "MAD",
      "monitor_flexible": false,
      "range_date_from": "2026-08-01",
      "range_date_to": "2026-08-31",
      "range_dates_count": 5
    }]
  }

Both modes example:
  {
    "routes": [{
      "origin": "EZE",
      "destination": "MAD",
      "monitor_flexible": true,
      "flexible_dates_count": 3,
      "range_date_from": "2026-08-01",
      "range_date_to": "2026-08-31",
      "range_dates_count": 5
    }]
  }
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass
class RouteConfig:
    """Configuration for one monitored flight route.

    Fields without defaults (origin, destination) must be supplied; all other
    fields fall back to sensible defaults so a minimal config works immediately.
    """

    # ---- Required -------------------------------------------------------- #
    origin: str          # IATA departure airport, e.g. "EZE"
    destination: str     # IATA arrival airport, e.g. "MAD"

    # ---- Shared ---------------------------------------------------------- #
    max_stops: int = 2          # 0 = direct only
    currency: str = "USD"       # ISO 4217

    # ---- Flexible mode (default on) -------------------------------------- #
    # Generates n dates starting start_days_ahead from today, interval_days apart.
    # With defaults: 5 weekly dates beginning one week from now.
    monitor_flexible: bool = True
    flexible_dates_count: int = 5
    flexible_start_days_ahead: int = 7
    flexible_interval_days: int = 7

    # ---- Range mode (off by default) ------------------------------------- #
    # Activated when both date strings are provided.
    # Samples range_dates_count dates uniformly across the calendar range.
    range_date_from: str | None = None   # YYYY-MM-DD, inclusive
    range_date_to: str | None = None     # YYYY-MM-DD, inclusive
    range_dates_count: int = 5

    # ------------------------------------------------------------------ #
    # Date computation                                                     #
    # ------------------------------------------------------------------ #

    def get_dates_to_monitor(self) -> list[str]:
        """Return the full list of departure dates to fetch for this route.

        Combines flexible and range modes; deduplicates and sorts the result.
        Quota cost = len(result) SerpAPI requests.
        """
        dates: list[str] = []
        if self.monitor_flexible:
            dates += next_n_dates(
                self.flexible_dates_count,
                self.flexible_start_days_ahead,
                self.flexible_interval_days,
            )
        if self.range_date_from and self.range_date_to:
            dates += dates_for_range(
                self.range_date_from,
                self.range_date_to,
                self.range_dates_count,
            )
        return sorted(set(dates))

    def monitoring_mode(self) -> str:
        """Short label for this route's active monitoring mode(s).

        Returns "flexible", "range", or "both".
        """
        has_range = bool(self.range_date_from and self.range_date_to)
        if self.monitor_flexible and has_range:
            return "both"
        if has_range:
            return "range"
        return "flexible"


# --------------------------------------------------------------------------- #
# Date helpers                                                                 #
# --------------------------------------------------------------------------- #

def next_n_dates(
    n: int,
    start_days_ahead: int = 7,
    interval_days: int = 7,
) -> list[str]:
    """Generate n departure dates spaced interval_days apart.

    Starts start_days_ahead days from today.

    Example (today = 2026-06-02, defaults)::

        next_n_dates(5)
        # -> ['2026-06-09', '2026-06-16', '2026-06-23', '2026-06-30', '2026-07-07']

    Quota cost: n SerpAPI requests.
    """
    today = date.today()
    return [
        (today + timedelta(days=start_days_ahead + i * interval_days)).isoformat()
        for i in range(n)
    ]


def dates_for_range(date_from: str, date_to: str, n: int) -> list[str]:
    """Return n dates distributed uniformly across the closed range [date_from, date_to].

    Behaviour:
      - n == 1        : returns the midpoint of the range.
      - n >= total_days: returns every day in the range (range smaller than n).
      - otherwise     : distributes n dates with equal spacing across the range so
                        that the first date == date_from and the last == date_to.

    Examples::

        dates_for_range("2026-08-01", "2026-08-31", 5)
        # -> ['2026-08-01', '2026-08-09', '2026-08-16', '2026-08-23', '2026-08-31']

        dates_for_range("2026-08-01", "2026-08-05", 5)
        # -> ['2026-08-01', '2026-08-02', '2026-08-03', '2026-08-04', '2026-08-05']

    Quota cost: min(n, total_days_in_range) SerpAPI requests.
    """
    d_from = date.fromisoformat(date_from)
    d_to = date.fromisoformat(date_to)

    if d_to < d_from:
        raise ValueError(
            f"dates_for_range: date_from ({date_from}) must be <= date_to ({date_to})"
        )

    total_days = (d_to - d_from).days + 1  # inclusive count

    if n <= 0:
        return []

    if n == 1:
        # Single date: midpoint (rounds down for even spans)
        mid_offset = (d_to - d_from).days // 2
        return [(d_from + timedelta(days=mid_offset)).isoformat()]

    if total_days <= n:
        # Range is smaller than requested count — return every day
        return [(d_from + timedelta(days=i)).isoformat() for i in range(total_days)]

    # Distribute n points uniformly: step = (total_days - 1) / (n - 1)
    # so that index 0 maps to d_from and index n-1 maps to d_to exactly.
    step = (total_days - 1) / (n - 1)
    return [
        (d_from + timedelta(days=round(i * step))).isoformat()
        for i in range(n)
    ]


# --------------------------------------------------------------------------- #
# Default and factory                                                          #
# --------------------------------------------------------------------------- #

# Phase 0 default: Buenos Aires Ezeiza -> Madrid.
# Uses flexible mode only (5 weekly dates from today+7) — identical to
# the previous behaviour before range mode was added.
# Note: EZE (not BUE) — Google Flights uses specific airport IATA codes.
DEFAULT_ROUTE = RouteConfig(origin="EZE", destination="MAD")


def load_routes(domain_config: dict) -> list[RouteConfig]:
    """Build the list of routes from the domain's config jsonb.

    The domain row's ``config`` column may contain:
      {"routes": [{"origin": "EZE", "destination": "MAD", ...}, ...]}

    Falls back to DEFAULT_ROUTE when the key is absent or the list is empty.
    Unknown / legacy keys are silently ignored — forward-compatible.
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
                    max_stops=int(r.get("max_stops", 2)),
                    currency=str(r.get("currency", "USD")).upper(),
                    # Flexible mode
                    monitor_flexible=bool(r.get("monitor_flexible", True)),
                    flexible_dates_count=int(r.get("flexible_dates_count", 5)),
                    flexible_start_days_ahead=int(r.get("flexible_start_days_ahead", 7)),
                    flexible_interval_days=int(r.get("flexible_interval_days", 7)),
                    # Range mode
                    range_date_from=r.get("range_date_from") or None,
                    range_date_to=r.get("range_date_to") or None,
                    range_dates_count=int(r.get("range_dates_count", 5)),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            import logging
            logging.warning("Skipping malformed route config %r: %s", r, exc)

    return routes or [DEFAULT_ROUTE]
