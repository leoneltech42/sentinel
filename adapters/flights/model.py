"""Price signal detection for the flights domain.

Two signal types:

  price_drop      -- current price is >= 10% below the rolling historical
                     average for this route + departure date. Requires at
                     least 3 prior observations so the average is meaningful.
                     Fast-path: if SerpAPI's price_insights rates the price
                     "low" and the price is at or below the typical range
                     floor, the signal fires immediately (no history needed).

  monthly_minimum -- current price is the cheapest we have ever tracked for
                     any departure date on this route in this calendar month.
                     Useful when comparing across alternative travel dates.

Both detection functions are pure: they take price lists and return a
(confidence, expected_value) pair when a signal fires, or None when it
does not. The adapter handles all DB access and result assembly.

EV proxy: for flight prices there is no bookmaker to de-vig, so we use
confidence directly as the expected-value proxy.
"""

from __future__ import annotations

import statistics


# --------------------------------------------------------------------------- #
# Multi-source price normalisation                                             #
# --------------------------------------------------------------------------- #

def source_from_event_key(event_key: str) -> str:
    """Infer the data source from an event key's structural format.

    Key formats (source is always at parts[3]):
      SerpAPI  : flights::ROUTE::DEP_DATE::serpapi::DEP_DATE_PRICE
      Amadeus  : flights::ROUTE::DEP_DATE::amadeus::OFFER_ID
      Tequila  : flights::ROUTE::DEP_DATE::SEARCH_DATE  (parts[3] is a date)
      unknown  : treated as tequila format (top-level numeric price field)
    """
    parts = event_key.split("::")
    if len(parts) >= 4:
        if parts[3] == "serpapi":
            return "serpapi"
        if parts[3] == "amadeus":
            return "amadeus"
    return "tequila"


def normalize_price(payload: dict, source: str) -> float:
    """Extract the total price (USD float) from a raw event payload.

    Dispatches on source because each API uses a different JSON structure:

    +---------+------------------------------------------+----------+
    | Source  | JSON path                                | Type     |
    +=========+==========================================+==========+
    | serpapi | best_flights[0].price                    | numeric  |
    |         | fallback: price_insights.lowest_price    | numeric  |
    +---------+------------------------------------------+----------+
    | amadeus | price.total                              | string   |
    +---------+------------------------------------------+----------+
    | tequila | price                                    | numeric  |
    | (other) |                                          |          |
    +---------+------------------------------------------+----------+

    Raises ValueError if the price cannot be extracted — callers should catch
    this and skip the event rather than crashing the whole batch.
    """
    try:
        if source == "serpapi":
            best = payload.get("best_flights") or []
            if best:
                raw = best[0].get("price")
                if raw is not None:
                    return float(raw)
            # Fallback: price_insights.lowest_price
            insights = payload.get("price_insights") or {}
            lowest = insights.get("lowest_price")
            if lowest is not None:
                return float(lowest)
            raise ValueError("SerpAPI payload missing best_flights[0].price and price_insights.lowest_price")

        elif source == "amadeus":
            price_info = payload.get("price", {})
            if not isinstance(price_info, dict):
                raise ValueError("Amadeus price field is not a dict")
            total = price_info.get("total")
            if total is None:
                raise ValueError("Amadeus payload missing price.total")
            return float(total)

        else:
            # Tequila, mock, or any future source that stores price at top level
            raw = payload.get("price")
            if raw is None:
                raise ValueError("Tequila payload missing price field")
            return float(raw)

    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Cannot extract price from {source!r} payload: {exc}"
        ) from exc


# --------------------------------------------------------------------------- #
# Signal detection                                                             #
# --------------------------------------------------------------------------- #

def check_price_drop(
    current_price: float,
    history_prices: list[float],
    threshold: float = 0.10,
    min_observations: int = 3,
    price_insights: dict | None = None,
) -> tuple[float, float] | None:
    """Signal if the current price represents a meaningful drop.

    Two paths are tried in order:

    **Fast-path (SerpAPI price_insights):**
      If ``price_insights`` is provided and Google rates the price as "low"
      AND the current price is at or below the typical range floor, the
      signal fires immediately — no prior observations needed.
      confidence = 1 - (current_price / typical_range[1])
      This is the highest-confidence path: Google's own model says it's cheap.

    **Rolling-average fallback:**
      If price_insights is absent or the fast-path condition is not met,
      falls back to the historical rolling average.  Requires at least
      ``min_observations`` prior observations.
      confidence = (avg - current) / avg

    Args:
        current_price:    The price found in today's search (USD).
        history_prices:   Prices from all *prior* searches for the same
                          route + departure date (current excluded).
        threshold:        Fractional drop required for rolling-avg path, e.g. 0.10.
        min_observations: Minimum prior observations for rolling-avg path.
        price_insights:   Optional dict from SerpAPI's price_insights block,
                          enabling the fast-path.  Pass None or omit when not
                          available (e.g. non-SerpAPI sources, mock data).

    Returns:
        (confidence, expected_value) if the signal fires, else None.
        confidence is in (0, 1]; expected_value == confidence (proxy).
    """
    # --- Fast-path: Google's price assessment ---
    # Bypasses min_observations entirely — Google's model has already seen
    # much more data than we have, so we trust its "low" rating immediately.
    if price_insights:
        level = price_insights.get("price_level", "")
        typical_range = price_insights.get("typical_price_range") or []
        if (
            level == "low"
            and len(typical_range) == 2
            and current_price <= typical_range[0]
        ):
            try:
                hi = float(typical_range[1])
                if hi > 0:
                    confidence = round(1.0 - (current_price / hi), 4)
                    return confidence, confidence
            except (TypeError, ValueError):
                pass  # malformed range — fall through to rolling-average

    # --- Rolling-average path ---
    # Hard requirement: we need at least min_observations prior data points to
    # compute a meaningful average.  This check is intentionally placed AFTER
    # the fast-path so Google's "low" assessment still fires on the first run
    # (n=0), but a single prior observation (n=1) is never enough to trigger
    # the rolling-average on its own.
    if len(history_prices) < min_observations:
        return None

    avg = statistics.mean(history_prices)
    if avg <= 0:
        return None

    if current_price < avg * (1.0 - threshold):
        confidence = round((avg - current_price) / avg, 4)
        return confidence, confidence

    return None


def check_monthly_minimum(
    current_price: float,
    all_month_prices: list[float],
) -> tuple[float, float] | None:
    """Signal if current_price is the lowest ever seen for this route this month.

    'This month' covers all departure dates within the same calendar month
    across all prior searches (the full pool stored in raw_events).

    The current price IS included in all_month_prices (the orchestrator
    inserts raw events before calling generate_signals, so the current
    observation is already in the DB).

    Args:
        current_price:     Price from the current search (USD).
        all_month_prices:  All prices tracked for this route + departure
                           month, including current.

    Returns:
        (confidence, expected_value) if the signal fires, else None.
        confidence = 1 - (current / max_month_price).
        expected_value = confidence (proxy).
    """
    if not all_month_prices:
        return None

    min_p = min(all_month_prices)
    max_p = max(all_month_prices)

    # Guard: need at least some price spread for confidence to be meaningful.
    if max_p <= min_p:
        return None

    if current_price <= min_p:
        confidence = 1.0 - (current_price / max_p)
        return round(confidence, 4), round(confidence, 4)

    return None
