"""SerpAPI Google Flights ingestion client.

One HTTP request per departure date keeps quota consumption predictable.
Free tier: 100 requests/month.  Default: 5 dates per route per run.

SerpAPI endpoint: https://serpapi.com/search
  engine=google_flights, type=2 (one-way)

Response fields used:
  best_flights[].price                         -- best available price
  best_flights[].airline                       -- operating carrier name
  best_flights[].total_duration                -- minutes
  best_flights[].flights[].departure_airport   -- {time, ...}
  best_flights[].flights[].arrival_airport     -- {time, ...}
  price_insights.lowest_price                  -- Google's lowest tracked price
  price_insights.price_level                   -- "low" | "typical" | "high"
  price_insights.typical_price_range           -- [min, max]

Event-key format (source encoded at parts[3] for schema-free inference):
  flights::{route}::{departure_date}::serpapi::{outbound_date}_{price_int}

  Example: flights::EZE-MAD::2026-06-30::serpapi::2026-06-30_680
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

import requests

from adapters.base import RawEventData
from adapters.flights.routes import RouteConfig, next_n_dates

_DEFAULT_TIMEOUT = 30
_SERPAPI_BASE = "https://serpapi.com/search"


class SerpAPIFlightsClient:
    """Google Flights data via SerpAPI.

    Authentication: ``api_key`` query parameter (no OAuth required).
    One HTTP GET per departure date; pass a list of dates to ``fetch_for_routes``.

    The raw JSON response is stored untransformed in ``raw_events.payload`` so
    every field — including price_insights — is available to the model and to
    future retrain passes.
    """

    source_name = "serpapi"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    # ------------------------------------------------------------------ #
    # High-level: produce RawEventData for a set of routes                #
    # ------------------------------------------------------------------ #

    def fetch_for_routes(
        self, routes: list[RouteConfig], search_date: date
    ) -> list[RawEventData]:
        """Fetch all monitored departure dates for each route.

        For each route, the dates to search come from ``route.monitored_dates``
        (if non-empty) or from ``next_n_dates(5)`` (5 weekly dates from today+7).
        One API call is made per date.  Errors on individual dates are logged and
        skipped so one failed request never aborts the run.

        Args:
            routes:      Configured routes (origin, destination, monitored_dates).
            search_date: Date of this search run (used only for logging).
        """
        events: list[RawEventData] = []
        for route in routes:
            dates_to_check = route.monitored_dates if route.monitored_dates else next_n_dates(5)
            fetched = 0
            for dep_date_str in dates_to_check:
                result = self.search_specific_date(
                    route.origin, route.destination, dep_date_str, route.currency
                )
                if result is not None:
                    event = self._to_raw_event(result, route, dep_date_str)
                    if event is not None:
                        events.append(event)
                        fetched += 1
            logging.info(
                "SerpAPI: fetched %d/%d date(s) for %s-%s",
                fetched, len(dates_to_check), route.origin, route.destination,
            )
        return events

    def _to_raw_event(
        self, result: dict, route: RouteConfig, dep_date_str: str
    ) -> RawEventData | None:
        """Convert a raw SerpAPI JSON response to a RawEventData.

        Returns None when no price is present in the response (e.g. no flights
        available for that date).  The full response is stored as payload so
        price_insights and flight details are preserved for the model.

        Event-key encodes source at parts[3] (``serpapi``) so ``source_from_event_key``
        can infer the payload format without a DB schema change.
        """
        best = result.get("best_flights") or []
        insights = result.get("price_insights") or {}

        # Prefer the best-offer price; fall back to price_insights.lowest_price.
        price: float | None = None
        if best:
            raw = best[0].get("price")
            if raw is not None:
                try:
                    price = float(raw)
                except (TypeError, ValueError):
                    pass
        if price is None:
            raw = insights.get("lowest_price")
            if raw is not None:
                try:
                    price = float(raw)
                except (TypeError, ValueError):
                    pass

        if price is None:
            logging.warning(
                "SerpAPI: no price in response for %s->%s %s — skipping",
                route.origin, route.destination, dep_date_str,
            )
            return None

        route_key = f"{route.origin}-{route.destination}"
        # Include dep_date + price int in the suffix for reasonable uniqueness.
        event_key = (
            f"flights::{route_key}::{dep_date_str}::serpapi"
            f"::{dep_date_str}_{int(price)}"
        )

        try:
            dep_dt = datetime.fromisoformat(dep_date_str).replace(tzinfo=timezone.utc)
        except ValueError:
            dep_dt = datetime.now(timezone.utc)

        return RawEventData(
            event_key=event_key,
            payload=result,
            event_at=dep_dt,
            source=self.source_name,
        )

    # ------------------------------------------------------------------ #
    # Low-level search                                                     #
    # ------------------------------------------------------------------ #

    def search_specific_date(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        currency: str = "USD",
    ) -> dict | None:
        """Call SerpAPI for a single one-way departure date.

        Returns the raw JSON response dict, or None on any HTTP / network error.
        Errors are logged at WARNING level so the caller can skip and continue.

        Args:
            origin:         IATA departure airport code, e.g. "EZE".
            destination:    IATA arrival airport code, e.g. "MAD".
            departure_date: ISO date string, e.g. "2026-06-30".
            currency:       ISO 4217 currency code (default "USD").
        """
        params: dict = {
            "engine": "google_flights",
            "departure_id": origin,
            "arrival_id": destination,
            "outbound_date": departure_date,
            "currency": currency,
            "type": "2",   # one-way
            "hl": "en",
            "api_key": self._api_key,
        }
        try:
            resp = requests.get(_SERPAPI_BASE, params=params, timeout=_DEFAULT_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as exc:
            logging.warning(
                "SerpAPI HTTP error (%s) for %s->%s %s: %s",
                exc.response.status_code, origin, destination, departure_date, exc,
            )
            return None
        except Exception as exc:
            logging.warning(
                "SerpAPI request failed (%s->%s %s): %s",
                origin, destination, departure_date, exc,
            )
            return None
