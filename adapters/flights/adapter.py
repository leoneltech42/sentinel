"""Flights adapter — implements the Adapter contract for flight price monitoring.

This is the second domain on the Sentinel engine, proving the core is genuinely
generic.  No changes to core/ were required: this file + its siblings in
adapters/flights/ are the complete addition.

Single-source design (SerpAPI):
  SerpAPIFlightsClient makes one request per departure date.  Each request
  returns the best available price plus Google's price_insights block, which
  gives an independent "low / typical / high" assessment.

  Free tier: 100 requests/month.  Default: 5 dates per route per run.
  Quota used is exposed in hyperparams() so it is recorded in the model_run row.

  Source is encoded in the event_key (parts[3] == "serpapi") — no schema change.
    SerpAPI : flights::ROUTE::DEP_DATE::serpapi::DEP_DATE_PRICE

Pipeline:
  fetch_raw_events()  -> SerpAPIFlightsClient.fetch_for_routes()
  generate_signals()  -> price_drop | monthly_minimum
                         price_drop has a fast-path via price_insights.price_level
  resolve()           -> after 7 days, re-fetch the date; was price higher?
"""

from __future__ import annotations

import logging
import statistics as _stats
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from adapters.base import (
    Adapter,
    OutcomeData,
    RawEventData,
    ResolvableSignal,
    SignalData,
)
from adapters.flights.ingestion import SerpAPIFlightsClient, next_n_dates
from adapters.flights.model import (
    check_monthly_minimum,
    check_price_drop,
    normalize_price,
    source_from_event_key,
)
from adapters.flights.routes import RouteConfig, load_routes

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# --------------------------------------------------------------------------- #
# Constants                                                                    #
# --------------------------------------------------------------------------- #
_PRICE_DROP_THRESHOLD = 0.10   # 10% below rolling average (rolling-avg path)
_MIN_OBSERVATIONS = 3          # prior observations needed for rolling-avg path
_RESOLUTION_DAYS = 7           # days before resolving a signal


class FlightsAdapter(Adapter):
    """Monitors configured flight routes for price signals via SerpAPI.

    Args:
        serpapi_key: SerpAPI key (https://serpapi.com -- 100 free searches/month).
        session:     SQLAlchemy session for historical price queries in
                     generate_signals. Pass None in mock/test mode.
        events_override: Pre-built events list; bypasses fetch_raw_events.
        routes_override: Override the route list (ignores domain config).
    """

    domain_slug = "flights"
    resolution_rule = "threshold"

    def __init__(
        self,
        serpapi_key: str = "",
        session: "Session | None" = None,
        events_override: list[RawEventData] | None = None,
        routes_override: list[RouteConfig] | None = None,
    ) -> None:
        self._session = session
        self._events_override = events_override
        self._routes = routes_override if routes_override is not None else load_routes({})

        # Build client only when a key is present and no events_override is set.
        if events_override is None and serpapi_key:
            self._client: SerpAPIFlightsClient | None = SerpAPIFlightsClient(serpapi_key)
        else:
            self._client = None
            if events_override is None and not serpapi_key:
                logging.warning(
                    "FlightsAdapter: SERPAPI_KEY not set -- "
                    "fetch_raw_events will return an empty list."
                )

    # ------------------------------------------------------------------ #
    # Adapter contract                                                     #
    # ------------------------------------------------------------------ #

    @property
    def model_version(self) -> str:
        return "price_series_v0.2.0"

    def hyperparams(self) -> dict[str, Any]:
        # Compute quota this run: one request per monitored date per route.
        quota_used = sum(
            len(r.monitored_dates) if r.monitored_dates else 5
            for r in self._routes
        )
        return {
            "price_drop_threshold": _PRICE_DROP_THRESHOLD,
            "min_observations": _MIN_OBSERVATIONS,
            "resolution_days": _RESOLUTION_DAYS,
            "source": "serpapi",
            "serpapi_quota_used": quota_used,
            "routes": [
                {
                    "origin": r.origin,
                    "destination": r.destination,
                    "monitored_dates": r.monitored_dates or next_n_dates(5),
                    "max_stops": r.max_stops,
                }
                for r in self._routes
            ],
        }

    # ------------------------------------------------------------------ #
    # Ingestion                                                            #
    # ------------------------------------------------------------------ #

    def fetch_raw_events(self) -> list[RawEventData]:
        """Fetch current flight prices from SerpAPI for all monitored dates.

        Returns events_override immediately if set (mock/test mode).
        Delegates to SerpAPIFlightsClient.fetch_for_routes() for live runs.
        Returns empty list if no key is configured.
        """
        if self._events_override is not None:
            return self._events_override

        if self._client is None:
            logging.warning("FlightsAdapter: no SerpAPI client; no events fetched.")
            return []

        return self._client.fetch_for_routes(self._routes, date.today())

    # ------------------------------------------------------------------ #
    # Signal generation                                                    #
    # ------------------------------------------------------------------ #

    def generate_signals(self, events: list[RawEventData]) -> list[SignalData]:
        """Run both price signal checks on each event.

        For SerpAPI events, price_drop has a fast-path via price_insights:
        if Google rates the price "low" and it is at/below the typical range
        floor, the signal fires immediately without needing prior observations.

        Historical prices are pulled from raw_events via self._session.
        In mock mode (session=None), only the fast-path can fire.
        """
        today = date.today()
        signals: list[SignalData] = []

        for ev in events:
            src = source_from_event_key(ev.event_key)
            try:
                price = normalize_price(ev.payload, src)
            except ValueError:
                continue

            parts = ev.event_key.split("::")
            if len(parts) < 3:
                continue
            route_key = parts[1]        # e.g. "EZE-MAD"
            dep_date_str = parts[2]     # e.g. "2026-07-15"
            origin, _, destination = route_key.partition("-")

            try:
                dep_date = date.fromisoformat(dep_date_str)
            except ValueError:
                continue

            # Pull cross-source history from DB (empty in mock mode)
            history = self._query_departure_history(
                route_key, dep_date_str, exclude_key=ev.event_key
            )
            month_prices = self._query_month_prices(
                route_key, dep_date.year, dep_date.month
            )

            # Extract price_insights for SerpAPI events (enables fast-path)
            price_insights: dict | None = None
            if src == "serpapi":
                insights_raw = ev.payload.get("price_insights")
                if isinstance(insights_raw, dict) and insights_raw:
                    price_insights = insights_raw

            # Shared feature metadata
            meta = _extract_flight_meta(ev.payload, src)
            avg_price = round(_stats.mean(history), 2) if history else None

            base_features: dict[str, Any] = {
                "origin": origin,
                "destination": destination,
                "departure_date": dep_date_str,
                "price_usd": price,
                "airline": meta["airline"],
                "stops": meta["stops"],
                "duration_hours": meta["duration_hours"],
                "rolling_avg_price": avg_price,
                "source": src,
            }

            # -- price_drop (fast-path if price_insights available, else rolling-avg) --
            drop = check_price_drop(
                price, history, _PRICE_DROP_THRESHOLD, _MIN_OBSERVATIONS,
                price_insights=price_insights,
            )
            if drop is not None:
                conf, ev_val = drop
                drop_features: dict[str, Any] = {
                    **base_features,
                    "observations_count": len(history),
                    "signal_subtype": "price_drop",
                }
                # Annotate with Google's assessment when insights are available
                if price_insights:
                    drop_features["price_level"] = price_insights.get("price_level")
                    drop_features["typical_range"] = price_insights.get("typical_price_range")
                    drop_features["google_assessment"] = (
                        f"Google rates this as {price_insights.get('price_level', '?')}"
                    )
                signals.append(
                    SignalData(
                        raw_event_key=ev.event_key,
                        signal_type="price_drop",
                        confidence=conf,
                        expected_value=ev_val,
                        features=drop_features,
                        valid_for_date=today,
                        valid_until=datetime.now(timezone.utc)
                        + timedelta(days=_RESOLUTION_DAYS),
                    )
                )

            # -- monthly_minimum --
            monthly = check_monthly_minimum(price, month_prices)
            if monthly is not None:
                conf2, ev_val2 = monthly
                signals.append(
                    SignalData(
                        raw_event_key=ev.event_key,
                        signal_type="monthly_minimum",
                        confidence=conf2,
                        expected_value=ev_val2,
                        features={
                            **base_features,
                            "observations_count": len(month_prices),
                            "signal_subtype": "monthly_minimum",
                        },
                        valid_for_date=today,
                        valid_until=datetime.now(timezone.utc)
                        + timedelta(days=_RESOLUTION_DAYS),
                    )
                )

        return signals

    # ------------------------------------------------------------------ #
    # Resolution                                                           #
    # ------------------------------------------------------------------ #

    def resolve(self, signal: ResolvableSignal) -> OutcomeData | None:
        """Resolve after RESOLUTION_DAYS by re-fetching the current price.

        was_correct = True if price rose (buying at signal time was right).
        actual_value = current_price / signal_price - 1 (positive = up).
        """
        if date.today() < signal.valid_for_date + timedelta(days=_RESOLUTION_DAYS):
            return None

        features = signal.features
        origin: str = features.get("origin", "")
        destination: str = features.get("destination", "")
        dep_date_str: str = features.get("departure_date", "")
        signal_price: float = float(features.get("price_usd", 0))

        if not origin or not destination or not dep_date_str or not signal_price:
            return None

        try:
            dep_date = date.fromisoformat(dep_date_str)
        except ValueError:
            return None

        if dep_date < date.today():
            return OutcomeData(
                was_correct=False,
                actual_value=0.0,
                metadata={"void": True, "void_reason": "departure date has passed"},
            )

        if self._client is None:
            return None

        result = self._client.search_specific_date(origin, destination, dep_date_str)
        if result is None:
            return None

        try:
            current_price = normalize_price(result, "serpapi")
        except ValueError:
            return None

        if current_price <= 0:
            return None

        price_ratio = current_price / signal_price - 1.0
        return OutcomeData(
            was_correct=current_price > signal_price,
            actual_value=round(price_ratio, 4),
            metadata={
                "price_at_signal": signal_price,
                "price_at_resolution": current_price,
                "price_change_pct": round(price_ratio * 100, 2),
                "departure_date": dep_date_str,
                "route": f"{origin}-{destination}",
            },
        )

    # ------------------------------------------------------------------ #
    # DB price history lookups (cross-source via LIKE prefix)             #
    # ------------------------------------------------------------------ #

    def _query_departure_history(
        self, route_key: str, dep_date_str: str, exclude_key: str
    ) -> list[float]:
        """All prior prices for this route + departure date, any source.

        LIKE prefix 'flights::{route}::{dep_date}::' matches all key formats,
        so prices from any source are aggregated.
        Current event (exclude_key) is excluded from the history.
        """
        if self._session is None:
            return []
        from sqlalchemy import select
        from core.models import RawEvent

        prefix = f"flights::{route_key}::{dep_date_str}::"
        rows = self._session.scalars(
            select(RawEvent).where(RawEvent.event_key.like(f"{prefix}%"))
        ).all()

        prices: list[float] = []
        for row in rows:
            if row.event_key == exclude_key:
                continue
            src = source_from_event_key(row.event_key)
            try:
                prices.append(normalize_price(row.payload, src))
            except ValueError:
                pass
        return prices

    def _query_month_prices(
        self, route_key: str, year: int, month: int
    ) -> list[float]:
        """All prices for this route across all departure dates this month, any source.

        LIKE prefix 'flights::{route}::{YYYY-MM}-' matches all key formats.
        Includes the current observation (already in DB when called).
        """
        if self._session is None:
            return []
        from sqlalchemy import select
        from core.models import RawEvent

        prefix = f"flights::{route_key}::{year}-{month:02d}-"
        rows = self._session.scalars(
            select(RawEvent).where(RawEvent.event_key.like(f"{prefix}%"))
        ).all()

        prices: list[float] = []
        for row in rows:
            src = source_from_event_key(row.event_key)
            try:
                prices.append(normalize_price(row.payload, src))
            except ValueError:
                pass
        return prices


# --------------------------------------------------------------------------- #
# Source-aware payload extraction                                              #
# --------------------------------------------------------------------------- #

def _extract_flight_meta(payload: dict, source: str) -> dict[str, Any]:
    """Extract airline, stops, and duration from a raw payload.

    Dispatches to the correct extractor based on source format.
    """
    if source == "serpapi":
        return {
            "airline": _serpapi_airline(payload),
            "stops": _serpapi_stops(payload),
            "duration_hours": _serpapi_duration_hours(payload),
        }
    elif source == "amadeus":
        return {
            "airline": _amadeus_airline(payload),
            "stops": _amadeus_stops(payload),
            "duration_hours": _amadeus_duration_hours(payload),
        }
    else:
        return {
            "airline": _tequila_airline(payload),
            "stops": _tequila_stops(payload),
            "duration_hours": _tequila_duration_hours(payload),
        }


# -- SerpAPI-specific extractors ------------------------------------------- #

def _serpapi_airline(payload: dict) -> str:
    best = payload.get("best_flights") or []
    if not best:
        return "?"
    # best_flights[].airline is a top-level string in the Google Flights offer
    airline = best[0].get("airline")
    if airline:
        return str(airline)
    # Fallback: first segment's airline field
    flights = best[0].get("flights") or []
    if flights:
        return str(flights[0].get("airline", "?"))
    return "?"


def _serpapi_stops(payload: dict) -> int:
    best = payload.get("best_flights") or []
    if not best:
        return 0
    flights = best[0].get("flights") or []
    return max(0, len(flights) - 1)


def _serpapi_duration_hours(payload: dict) -> float:
    best = payload.get("best_flights") or []
    if not best:
        return 0.0
    minutes = best[0].get("total_duration") or 0
    return round(minutes / 60, 1) if minutes else 0.0


# -- Tequila-specific extractors ------------------------------------------- #

def _tequila_airline(payload: dict) -> str:
    airlines = payload.get("airlines") or []
    return str(airlines[0]) if airlines else "?"


def _tequila_stops(payload: dict) -> int:
    route = payload.get("route") or []
    return max(0, len(route) - 1)


def _tequila_duration_hours(payload: dict) -> float:
    duration = payload.get("duration") or {}
    secs = duration.get("departure") or 0
    return round(secs / 3600, 1) if secs else 0.0


# -- Amadeus-specific extractors ------------------------------------------- #

def _amadeus_airline(offer: dict) -> str:
    codes = offer.get("validatingAirlineCodes", [])
    return str(codes[0]) if codes else "?"


def _amadeus_stops(offer: dict) -> int:
    try:
        segs = offer["itineraries"][0]["segments"]
        return max(0, len(segs) - 1)
    except (KeyError, IndexError, TypeError):
        return 0


def _amadeus_duration_hours(offer: dict) -> float:
    try:
        import re
        dur = offer["itineraries"][0]["duration"]  # e.g. "PT13H30M"
        m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?", dur or "")
        if not m:
            return 0.0
        hours = int(m.group(1) or 0)
        mins = int(m.group(2) or 0)
        return round(hours + mins / 60, 1)
    except (KeyError, IndexError, TypeError):
        return 0.0
