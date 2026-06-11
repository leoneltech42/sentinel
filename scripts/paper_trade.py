"""Phase 0 paper-trading runner.

Runs the full pipeline (ingest -> model -> signals) and prints today's picks.
Supports two domains via --domain: "betting" (default) and "flights".

Usage:
    python -m scripts.paper_trade --mock                               # sample data, no network
    python -m scripts.paper_trade                                      # live The Odds API (betting)
    python -m scripts.paper_trade --domain flights                     # live SerpAPI (flights)
    python -m scripts.paper_trade --domain flights --mock              # flights mock, no network
    python -m scripts.paper_trade --domain flights --range 2026-08-01 2026-08-31  # range mode
    python -m scripts.paper_trade --resolve                            # resolve today's picks
    python -m scripts.paper_trade --date 2026-05-30 --resolve         # resolve a past date
    python -m scripts.paper_trade --notify                             # picks + Telegram message
    python -m scripts.paper_trade --resolve --date X --notify         # results + Telegram message

Env (see .env.example):
    ODDS_API_KEY           your The Odds API key       (betting domain)
    SERPAPI_KEY            your SerpAPI key            (flights domain, 100 req/mo free)
    DATABASE_URL           Supabase/Postgres URL, or omit for local SQLite
    SEASON                 MLB season year (default: current year)
    TELEGRAM_BOT_TOKEN     optional -- enables Telegram notifications
    TELEGRAM_CHAT_ID       optional -- target chat for notifications
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any

# Ensure star/Unicode characters (★ ☆) render correctly on Windows terminals
# that default to cp1252.  errors='replace' prevents crashes on edge cases.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from core.db import SessionLocal, configure_mock_db, init_db
from core.models import Signal, SignalOutcome
from core.orchestrator import run_pipeline, run_resolution


def main() -> None:
    parser = argparse.ArgumentParser(description="Sentinel Phase 0 paper trader")
    parser.add_argument(
        "--domain",
        default="betting",
        choices=["betting", "flights"],
        help="which adapter to run (default: betting)",
    )
    parser.add_argument("--mock", action="store_true", help="use sample data, no network")
    parser.add_argument("--resolve", action="store_true", help="resolve past picks")
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        default=None,
        help="show/resolve picks for a specific past date instead of running the pipeline",
    )
    parser.add_argument("--verbose", action="store_true",
                        help="show upsert/insert log messages from the orchestrator")
    parser.add_argument("--notify", action="store_true",
                        help="send Telegram notification (requires TELEGRAM_* env vars; "
                             "ignored with --mock)")
    parser.add_argument("--refresh", action="store_true",
                        help="second daily run: show all today's picks with follow status "
                             "and deltas vs the morning run; combines with --notify")
    parser.add_argument(
        "--range",
        nargs=2,
        metavar=("DATE_FROM", "DATE_TO"),
        default=None,
        help="flights only: monitor a specific calendar range instead of (or in addition to) "
             "flexible weekly dates. Format: YYYY-MM-DD YYYY-MM-DD. "
             "Activates range mode on the default EZE->MAD route (flexible mode disabled). "
             "Example: --range 2026-08-01 2026-08-31",
    )
    parser.add_argument(
        "--route",
        nargs=2,
        metavar=("ORIGIN", "DESTINATION"),
        default=None,
        help="flights only: override default route using IATA codes. "
             "Example: --route JFK LHR. Combine with --range for date-range mode.",
    )
    args = parser.parse_args()

    if args.verbose or args.mock:
        logging.basicConfig(level=logging.INFO, format="  [orchestrator] %(message)s")

    today = datetime.now(timezone.utc).date()
    do_notify = args.notify and not args.mock

    if args.mock:
        # Redirect all DB writes to an in-memory SQLite instance so mock runs
        # never touch the production database.  Must happen before init_db()
        # so the schema is created in the ephemeral engine, not Supabase.
        configure_mock_db()

    init_db()

    # Re-import SessionLocal after a possible mock reconfiguration so the
    # context manager below uses the (possibly replaced) session factory.
    from core.db import SessionLocal  # noqa: PLC0415

    with SessionLocal() as session:
        # ------------------------------------------------------------------ #
        # Adapter selection                                                   #
        # ------------------------------------------------------------------ #
        if args.domain == "flights":
            adapter = _build_flights_adapter(args, session)
        else:
            adapter = _build_betting_adapter(args)

        # ------------------------------------------------------------------ #
        # Past-date mode: skip pipeline, show/resolve for target date         #
        # ------------------------------------------------------------------ #
        if args.date:
            target_date = date.fromisoformat(args.date)
            if args.resolve:
                n = run_resolution(session, adapter)
                print(f"Resolved {n} past signal(s).\n")
                if args.domain == "betting":
                    _verify_outcomes_supabase(session, target_date)
                if do_notify:
                    _send_results_notification(session, target_date, args.domain)
            _print_by_date(session, target_date, args.domain, show_outcomes=True)
            return

        # ------------------------------------------------------------------ #
        # Live / mock ingestion run                                           #
        # ------------------------------------------------------------------ #

        # Snapshot current signal values BEFORE the pipeline upserts them.
        # Used by --refresh to compute deltas vs the morning run.
        if args.refresh and args.domain == "betting":
            from core.orchestrator import snapshot_signals
            from core.models import Domain as _DomainSnap
            _domain_snap = session.scalar(
                select(_DomainSnap).where(_DomainSnap.slug == "betting")
            )
            prev_signals = (
                snapshot_signals(session, _domain_snap.id, today)
                if _domain_snap else {}
            )
        else:
            prev_signals = {}

        if not args.mock:
            print(f"\nFetching live data ({args.domain}) ...")
            raw_events = adapter.fetch_raw_events()
            # Cache fetched events so the pipeline reuses them.
            adapter._events_override = raw_events

            _print_ingestion_summary(raw_events, args.domain)

            if args.domain == "betting":
                evals = adapter.evaluate_events(raw_events)
                _print_model_diagnostic(evals)
                quota = adapter._client.last_quota if adapter._client else {}
                _print_quota(quota)
            else:
                _print_flights_price_summary(raw_events)

        run = run_pipeline(session, adapter)

        if args.resolve:
            n = run_resolution(session, adapter)
            print(f"Resolved {n} past signal(s).\n")

        if args.refresh and args.domain == "betting":
            followed_ids = _get_followed_ids(session, today)
            signals = _print_refresh(session, run, today, prev_signals, followed_ids)
            if do_notify:
                _send_refresh_notification(signals, today, prev_signals, followed_ids)
        else:
            signals = _print_by_run(session, run.id, args.domain, show_outcomes=args.resolve)
            if do_notify:
                _send_picks_notification(signals, today, args.domain)
                if args.resolve:
                    _send_results_notification(session, today, args.domain)

        if not args.mock:
            _verify_supabase(session, today, args.domain)


# --------------------------------------------------------------------------- #
# Adapter builders                                                             #
# --------------------------------------------------------------------------- #

def _build_betting_adapter(args):
    from adapters.betting.adapter import BettingAdapter
    today = datetime.now(timezone.utc).date()
    season = int(os.getenv("SEASON", today.year))
    events_override = None
    mlb_runs_override = None
    mlb_pitchers_override = None
    justifier = None
    if args.mock:
        from scripts.sample_data import sample_events, sample_mlb_runs, sample_mlb_pitchers
        events_override = sample_events()
        mlb_runs_override = sample_mlb_runs()
        mlb_pitchers_override = sample_mlb_pitchers()
        # Never call an LLM API in mock mode — justifier stays None.
    else:
        from adapters.betting.justification import LLMJustifier
        if api_key := os.getenv("LLM_JUSTIFIER_API_KEY"):
            justifier = LLMJustifier(
                api_key=api_key,
                base_url=os.getenv("LLM_JUSTIFIER_BASE_URL", "https://api.groq.com/openai/v1"),
                model=os.getenv("LLM_JUSTIFIER_MODEL", "llama-3.3-70b-versatile"),
            )
    return BettingAdapter(
        api_key=os.getenv("ODDS_API_KEY", ""),
        season=season,
        events_override=events_override,
        mlb_runs_override=mlb_runs_override,
        mlb_pitchers_override=mlb_pitchers_override,
        justifier=justifier,
    )


def _build_flights_adapter(args, session):
    """Build the FlightsAdapter from CLI args.

    --route ORIGIN DEST  overrides the default EZE->MAD route with any IATA pair.
    --range DATE_FROM DATE_TO  activates range mode (flexible disabled) on the
        active route (default or --route override).
    Without either flag, the adapter uses the default EZE->MAD flexible config.
    """
    import re

    from adapters.flights.adapter import FlightsAdapter
    events_override = None
    routes_override = None

    if args.mock:
        from scripts.sample_data import sample_flights_events_serpapi
        events_override = sample_flights_events_serpapi()

    # Resolve origin/destination: --route overrides default EZE->MAD.
    route_arg = getattr(args, "route", None)
    range_arg = getattr(args, "range", None)

    if route_arg is not None:
        origin, destination = route_arg[0].upper(), route_arg[1].upper()
        for code in (origin, destination):
            if not re.match(r"^[A-Z]{3}$", code):
                print(f"ERROR: '{code}' is not a valid IATA code (must be 3 letters, e.g. JFK).")
                sys.exit(1)
        from adapters.flights.routes import RouteConfig
        use_flexible = not bool(range_arg)
        routes_override = [
            RouteConfig(
                origin=origin,
                destination=destination,
                monitor_flexible=use_flexible,
                range_date_from=range_arg[0] if range_arg else None,
                range_date_to=range_arg[1] if range_arg else None,
            )
        ]
    elif range_arg:
        # --range without --route: apply to default EZE->MAD route.
        from adapters.flights.routes import RouteConfig
        date_from, date_to = range_arg
        routes_override = [
            RouteConfig(
                origin="EZE",
                destination="MAD",
                monitor_flexible=False,
                range_date_from=date_from,
                range_date_to=date_to,
            )
        ]

    return FlightsAdapter(
        serpapi_key=os.getenv("SERPAPI_KEY", ""),
        session=session,
        events_override=events_override,
        routes_override=routes_override,
    )


# --------------------------------------------------------------------------- #
# Ingestion diagnostic helpers                                                 #
# --------------------------------------------------------------------------- #

def _print_ingestion_summary(raw_events: list, domain: str) -> None:
    prefix_counter: Counter = Counter()
    for ev in raw_events:
        prefix = ev.event_key.split("::", 1)[0]
        prefix_counter[prefix] += 1
    print(f"\n  Ingested {len(raw_events)} raw event(s) [{domain}]:")
    for prefix, n in sorted(prefix_counter.items()):
        print(f"    {prefix}: {n}")


def _print_model_diagnostic(evals: list[dict[str, Any]]) -> None:
    """Betting-specific per-event model evaluation output."""
    print(f"\n{'='*64}")
    print(f"  MODEL DIAGNOSTIC -- {len(evals)} event(s) evaluated")
    print(f"{'='*64}")
    for ev in evals:
        tag = ev["sport"].split("_")[0]
        print(f"\n  {ev['match']}  ({tag})  {ev['game_time'][:10]}")
        if not ev["has_odds"]:
            print(f"    SKIP -- {ev['skip_reason']}")
            continue
        if not ev["supported"]:
            print(f"    SKIP -- {ev['skip_reason']}")
            continue
        for sel in ev["selections"]:
            status = "PASS" if sel["passes"] else "skip"
            reasons = "  |  " + ", ".join(sel["fail_reasons"]) if sel["fail_reasons"] else ""
            print(
                f"    [{status}]  {sel['selection']:<28} "
                f"odd {sel['odd']:.2f}  "
                f"model {sel['model_prob']:.1%}  "
                f"mkt {sel['market_prob']:.1%}  "
                f"EV {sel['ev']:+.1%}"
                f"{reasons}"
            )
    print(f"{'='*64}\n")


def _print_quota(quota: dict[str, str]) -> None:
    used = quota.get("x-requests-used", "?")
    remaining = quota.get("x-requests-remaining", "?")
    print(f"  API quota:  {used} used / {remaining} remaining this month\n")


def _print_flights_price_summary(raw_events: list) -> None:
    """Show a compact table of routes and prices found in this run."""
    from adapters.flights.model import normalize_price, source_from_event_key
    if not raw_events:
        print("  No flight data returned.")
        return
    print(f"\n  Flight prices fetched ({len(raw_events)} departure date(s)):")
    # Group by route key (parts[1] of event_key); departure date from parts[2]
    by_route: dict[str, list[tuple[str, float]]] = {}
    for ev in raw_events:
        parts = ev.event_key.split("::")
        if len(parts) < 3:
            continue
        route_key = parts[1]
        dep = parts[2]
        src = source_from_event_key(ev.event_key)
        try:
            price = normalize_price(ev.payload, src)
        except ValueError:
            continue
        by_route.setdefault(route_key, []).append((dep, price))
    for route, entries in sorted(by_route.items()):
        entries.sort()  # sort by departure date
        origin, _, dest = route.partition("-")
        prices = [p for _, p in entries]
        print(f"\n    {origin} -> {dest}  ({len(entries)} departure date(s))")
        print(f"    Price range: ${min(prices):.0f} -- ${max(prices):.0f} USD")
        for dep, price in entries[:5]:
            print(f"      {dep}  ${price:.0f}")
        if len(entries) > 5:
            print(f"      ... ({len(entries) - 5} more)")
    print(f"\n  SerpAPI quota this run: {len(raw_events)} request(s) of 100/month (free tier)")


# --------------------------------------------------------------------------- #
# Supabase verification helpers                                                #
# --------------------------------------------------------------------------- #

def _verify_outcomes_supabase(session, target_date: date) -> None:
    """After resolution: print signal_outcomes stats for target_date (betting only).

    Two sections:
      1. Results for target_date  — signals whose valid_for_date == target_date.
      2. Backfilled from previous days — signals from the 7 days prior that were
         resolved in the same run (shown only when any exist).
    """
    from core.models import Domain

    def _fetch(date_clause) -> list:
        return session.scalars(
            select(SignalOutcome)
            .join(Signal, SignalOutcome.signal_id == Signal.id)
            .join(Domain, Signal.domain_id == Domain.id)
            .where(date_clause, Domain.slug == "betting")
            .options(selectinload(SignalOutcome.signal))
        ).all()

    def _print_section(rows: list, header: str) -> None:
        total   = len(rows)
        correct = sum(1 for r in rows if r.was_correct)
        print(f"  --- {header} ---")
        print(f"  Outcome rows created : {total}")
        if total:
            print(f"  Correct              : {correct}/{total}  "
                  f"({correct/total:.0%} win rate)")
            clv_rows = [r for r in rows if r.outcome_metadata.get("clv") is not None]
            if clv_rows:
                avg_clv = sum(r.outcome_metadata["clv"] for r in clv_rows) / len(clv_rows)
                print(f"  CLV (avg)            : {avg_clv:+.2%}  "
                      f"({len(clv_rows)} picks with data)")
            else:
                print("  CLV                  : not available "
                      "(historical endpoint not on free tier)")
            print()
            for r in rows:
                f    = r.signal.features
                icon = "[W]" if r.was_correct else "[L]"
                hs   = r.outcome_metadata.get("home_score", "?")
                as_  = r.outcome_metadata.get("away_score", "?")
                winner = r.outcome_metadata.get("winner", "?")
                date_tag = (f"  [{r.signal.valid_for_date}]"
                            if r.signal.valid_for_date != target_date else "")
                print(f"  {icon}  {f.get('match','?'):<42}  "
                      f"pick={f.get('pick','?'):<30}  "
                      f"score={as_}-{hs}  winner={winner}{date_tag}")
        print()

    rows = _fetch(Signal.valid_for_date == target_date)
    _print_section(rows, f"Supabase signal_outcomes for {target_date}")

    cutoff = target_date - timedelta(days=7)
    backfill = _fetch(
        (Signal.valid_for_date >= cutoff) & (Signal.valid_for_date < target_date)
    )
    if backfill:
        _print_section(backfill, "Backfilled from previous days")


def _verify_supabase(session, today: date, domain: str) -> None:
    """Query the DB to confirm signals were persisted for today, filtered by domain."""
    from core.models import Domain
    tomorrow = today + timedelta(days=1)

    # Both queries join through domain_id so betting and flights signals don't bleed
    # into each other's Supabase check output.  Count only active signals; void/resolved
    # are historical records and don't represent today's slate.
    sigs = session.scalars(
        select(Signal)
        .join(Domain, Signal.domain_id == Domain.id)
        .where(
            Signal.valid_for_date.in_([today, tomorrow]),
            Signal.status == "active",
            Domain.slug == domain,
        )
        .order_by(Signal.created_at.desc())
        .limit(20)
    ).all()

    count = len(sigs)
    print(f"  Supabase check [{domain}]: {count} active signal(s) for {today} / {tomorrow}")
    if sigs:
        print("  Signal UUIDs in DB:")
        for s in sigs:
            f = s.features
            if domain == "flights":
                route = f"{f.get('origin','?')}->{f.get('destination','?')}"
                dep = f.get("departure_date", "?")
                subtype = f.get("signal_subtype", "?")
                print(f"    {s.id}  {route}  dep={dep}  type={subtype}  ${f.get('price_usd','?')}")
            else:
                print(f"    {s.id}  {f.get('match','?')}  pick={f.get('pick','?')}")
    print()


# --------------------------------------------------------------------------- #
# Notification helpers                                                         #
# --------------------------------------------------------------------------- #

def _send_picks_notification(signals: list[Signal], for_date: date, domain: str) -> None:
    from core.output import notify_picks
    try:
        notify_picks(signals, for_date, domain=domain)
        print(f"  Telegram picks notification sent ({len(signals)} signal(s)).")
    except Exception as exc:
        print(f"  Telegram notification failed (picks): {exc}")


def _send_results_notification(session, for_date: date, domain: str) -> None:
    from core.output import notify_results
    # Include today's picks PLUS any backfill from the preceding 7 days.
    # _format_results() partitions them into two sections by valid_for_date.
    cutoff = for_date - timedelta(days=7)
    signals = list(session.scalars(
        select(Signal)
        .where(
            Signal.valid_for_date >= cutoff,
            Signal.valid_for_date <= for_date,
            Signal.status.in_(["resolved", "void"]),
        )
        .options(selectinload(Signal.outcome))
        .order_by(Signal.valid_for_date.desc(), Signal.expected_value.desc())
    ).all())
    try:
        notify_results(signals, for_date, domain=domain)
        resolved = sum(1 for s in signals if s.status == "resolved"
                       and s.valid_for_date == for_date)
        print(f"  Telegram results notification sent ({resolved} resolved today).")
    except Exception as exc:
        print(f"  Telegram notification failed (results): {exc}")


def _send_refresh_notification(
    signals: list[Signal],
    for_date: date,
    prev_signals: dict[str, dict],
    followed_ids: set[uuid.UUID],
) -> None:
    from core.output import notify_refresh
    try:
        notify_refresh(signals, for_date, prev_signals=prev_signals,
                       followed_ids=followed_ids)
        print(f"  Telegram refresh notification sent ({len(signals)} signal(s)).")
    except Exception as exc:
        print(f"  Telegram notification failed (refresh): {exc}")


# --------------------------------------------------------------------------- #
# Display helpers                                                              #
# --------------------------------------------------------------------------- #

def _print_by_run(
    session, run_id: uuid.UUID, domain: str, *, show_outcomes: bool = False
) -> list[Signal]:
    """Show signals touched by this model run. Falls back to date query for betting."""
    today = datetime.now(timezone.utc).date()

    signals = list(session.scalars(
        select(Signal)
        .where(
            Signal.model_run_id == run_id,
            Signal.valid_for_date == today,
            Signal.status == "active",
        )
        .options(selectinload(Signal.outcome))
        .order_by(Signal.expected_value.desc())
    ).all())

    if not signals and domain == "betting":
        # Betting fallback: show today's existing slate even when odds are unchanged.
        # Filter to betting-domain signals only to avoid mixing in flight alerts.
        from core.models import Domain, ModelRun
        signals = list(session.scalars(
            select(Signal)
            .join(ModelRun, Signal.model_run_id == ModelRun.id)
            .join(Domain, ModelRun.domain_id == Domain.id)
            .where(
                Signal.valid_for_date == today,
                Signal.status == "active",
                Domain.slug == "betting",
            )
            .options(selectinload(Signal.outcome))
            .order_by(Signal.expected_value.desc())
        ).all())

    if domain == "flights":
        _render_flights(signals, today, show_outcomes=show_outcomes)
    else:
        _render_betting(signals, today, show_outcomes=show_outcomes)

    return signals


def _print_by_date(
    session, target_date: date, domain: str, *, show_outcomes: bool = False
) -> None:
    """Show signals for target_date, filtered to the current domain.

    The domain filter (join Signal -> Domain) prevents betting signals from
    appearing in the flights renderer and vice-versa, which would otherwise
    crash on missing domain-specific feature keys (e.g. 'match' for betting).
    """
    from core.models import Domain
    # When showing outcomes (--resolve / --date), include resolved signals so
    # results are visible.  Always exclude void — they have no meaningful result.
    status_filter = (
        Signal.status.in_(["active", "resolved"])
        if show_outcomes
        else Signal.status == "active"
    )
    signals = session.scalars(
        select(Signal)
        .join(Domain, Signal.domain_id == Domain.id)
        .where(
            Signal.valid_for_date == target_date,
            Domain.slug == domain,
            status_filter,
        )
        .options(selectinload(Signal.outcome))
        .order_by(Signal.expected_value.desc())
    ).all()

    if domain == "flights":
        _render_flights(list(signals), target_date, show_outcomes=show_outcomes)
    else:
        _render_betting(list(signals), target_date, show_outcomes=show_outcomes)


# --------------------------------------------------------------------------- #
# Domain-specific renderers                                                    #
# --------------------------------------------------------------------------- #

def _render_betting(signals: list[Signal], label: date, *, show_outcomes: bool) -> None:
    print(f"\n{'='*64}\n  SENTINEL - value bets for {label}\n{'='*64}")
    if not signals:
        print("  No +EV opportunities found.")
        print(f"{'='*64}\n")
        return

    for s in signals:
        f = s.features
        if "match" not in f:
            # Safety guard: a non-betting signal leaked into the betting renderer
            # (should not happen after the domain filter fixes, but defensive).
            import logging
            logging.warning("_render_betting: skipping signal %s — no 'match' key", s.id)
            continue
        units = f.get("kelly_units")
        stars = f.get("star_rating", "")
        units_str = f"  {stars}  {units}u" if units is not None else ""
        lines = [
            f"\n  {f['match']}  ({f['sport'].split('_')[0]})",
            f"    Pick: {f['pick']} @ {f['best_odd']}{units_str}",
            f"    Edge:        {f['edge']:+.1%}",
            f"    EV:          {s.expected_value:+.1%}",
        ]
        if justification := f.get("justification"):
            lines.append(f"  💡 {justification}")
        if show_outcomes:
            lines.append(f"    Result:      {_betting_outcome_line(s)}")
        print("\n".join(lines))

    print(f"\n{'='*64}")
    print(f"  {len(signals)} pick(s). Paper-trade these and track results.")
    print(f"  1u = 1% of bankroll  |  Sizing: 1/10 Kelly")
    print(f"{'='*64}\n")


def _render_flights(signals: list[Signal], label: date, *, show_outcomes: bool) -> None:
    print(f"\n{'='*64}\n  SENTINEL - flight alerts for {label}\n{'='*64}")
    if not signals:
        print("  No flight price alerts.")
        print("  (On first run this is expected -- accumulating price history.)")
        print(f"{'='*64}\n")
        return

    # Group by signal_subtype for clarity
    by_type: dict[str, list[Signal]] = {}
    for s in signals:
        subtype = s.features.get("signal_subtype", "unknown")
        by_type.setdefault(subtype, []).append(s)

    for subtype, sigs in by_type.items():
        print(f"\n  -- {subtype.replace('_', ' ').upper()} ({len(sigs)}) --")
        for s in sigs:
            f = s.features
            origin = f.get("origin", "?")
            dest = f.get("destination", "?")
            dep = f.get("departure_date", "?")
            price = f.get("price_usd", "?")
            airline = f.get("airline", "?")
            stops = f.get("stops", "?")
            dur = f.get("duration_hours", "?")
            avg = f.get("rolling_avg_price")
            obs = f.get("observations_count", 0)
            google_assessment = f.get("google_assessment")
            typical_range = f.get("typical_range")

            # Show Google's price assessment when the fast-path fired;
            # fall back to rolling-average context (avg, n) otherwise.
            if google_assessment and typical_range and len(typical_range) == 2:
                signal_context = (
                    f"  (Google: low -- typical ${int(typical_range[0])}--${int(typical_range[1])})"
                )
            elif avg:
                signal_context = f"  (avg ${avg:.0f}, n={obs})"
            else:
                signal_context = f"  (n={obs})"
            print(f"\n  {origin} -> {dest}  dep {dep}")
            print(f"    Price:       ${price}  ({airline}, {stops} stop(s), {dur}h)")
            print(f"    Signal:      {subtype}{signal_context}")
            print(f"    EV:          {s.expected_value:+.1%}   Confidence: {s.confidence:.1%}")
            if show_outcomes:
                print(f"    Result:      {_flights_outcome_line(s)}")
            print(f"    UUID:        {s.id}")

    print(f"\n{'='*64}")
    print(f"  {len(signals)} alert(s). Prices update daily -- act within 7 days.")
    print(f"{'='*64}\n")


def _betting_outcome_line(signal: Signal) -> str:
    if signal.status == "void":
        return "void  (postponed / suspended / cancelled)"
    if signal.status == "resolved" and signal.outcome is not None:
        o = signal.outcome
        icon = "[W]" if o.was_correct else "[L]"
        label = "won " if o.was_correct else "lost"
        hs = o.outcome_metadata.get("home_score")
        as_ = o.outcome_metadata.get("away_score")
        score = f"  ({hs}-{as_})" if hs is not None and as_ is not None else ""
        clv = o.outcome_metadata.get("clv")
        clv_str = f"  CLV: {clv:+.1%}" if clv is not None else ""
        return f"{icon}  {label}{score}{clv_str}"
    return "--  pending"


def _flights_outcome_line(signal: Signal) -> str:
    if signal.status == "void":
        reason = signal.outcome.outcome_metadata.get("void_reason", "") if signal.outcome else ""
        return f"void  ({reason})" if reason else "void"
    if signal.status == "resolved" and signal.outcome is not None:
        o = signal.outcome
        icon = "[CORRECT]" if o.was_correct else "[WRONG]"
        pct = o.outcome_metadata.get("price_change_pct", "?")
        pct_str = f"{pct:+.1f}%" if isinstance(pct, float) else str(pct)
        return f"{icon}  price changed {pct_str} after 7 days"
    return "--  pending (resolves in 7 days)"


# --------------------------------------------------------------------------- #
# Refresh helpers                                                              #
# --------------------------------------------------------------------------- #

def _get_followed_ids(session, for_date: date) -> set[uuid.UUID]:
    """Return the set of signal UUIDs the Phase 0 user followed for this date.

    Uses SENTINEL_USER_ID / SENTINEL_USER_EMAIL from the env, same as track.py.
    Returns an empty set if no user is configured or no picks are followed.
    """
    import os
    from core.models import User, UserSignalView, Domain as _Domain
    user_id_str = os.getenv("SENTINEL_USER_ID", "").strip()
    email       = os.getenv("SENTINEL_USER_EMAIL", "phase0@sentinel.local").strip()

    user = None
    if user_id_str:
        try:
            user = session.get(User, uuid.UUID(user_id_str))
        except ValueError:
            pass
    if user is None:
        user = session.scalar(select(User).where(User.email == email))
    if user is None:
        return set()

    views = session.scalars(
        select(UserSignalView)
        .join(Signal, UserSignalView.signal_id == Signal.id)
        .where(
            UserSignalView.user_id == user.id,
            UserSignalView.followed.is_(True),
            Signal.valid_for_date == for_date,
        )
    ).all()
    return {v.signal_id for v in views}


def _delta_line(signal: Signal, prev: dict) -> str:
    """One-line summary of what changed vs the pre-pipeline snapshot.

    Returns "" (empty) when no changes exceed the noise threshold.
    Returns "🆕  (new pick)" when this signal wasn't in the snapshot.
    Icons: 📈 for improved odds, 📉 for worsened odds.
    """
    if not prev:
        return ""

    raw_key = str(signal.raw_event_id)
    snap = prev.get(raw_key)
    if snap is None:
        return "  🆕  (new pick)"

    f = signal.features
    curr_odd   = f.get("best_odd")
    curr_kelly = f.get("kelly_units")
    prev_odd   = snap.get("best_odd")
    prev_kelly = snap.get("kelly_units")

    parts: list[str] = []

    if (curr_odd is not None and prev_odd is not None
            and abs(float(curr_odd) - float(prev_odd)) > 0.02):
        icon = "📈" if float(curr_odd) > float(prev_odd) else "📉"
        parts.append(f"{icon} {prev_odd}→{curr_odd}")

    if (curr_kelly is not None and prev_kelly is not None
            and abs(float(curr_kelly) - float(prev_kelly)) > 0.2):
        arrow = "↑" if float(curr_kelly) > float(prev_kelly) else "↓"
        parts.append(f"Kelly: {prev_kelly}u→{curr_kelly}u {arrow}")

    if not parts:
        return ""   # no changes — omit entirely
    return "  " + "  |  ".join(parts)


def _print_refresh(
    session,
    run,
    for_date: date,
    prev_signals: dict[str, dict],
    followed_ids: set[uuid.UUID],
) -> list[Signal]:
    """Render the refresh display: all today's picks with follow status and deltas."""
    from core.models import Domain as _Domain, ModelRun as _ModelRun

    # Fetch all active betting signals for today (not just this run).
    signals = list(session.scalars(
        select(Signal)
        .join(_Domain, Signal.domain_id == _Domain.id)
        .where(
            Signal.valid_for_date == for_date,
            Signal.status == "active",
            _Domain.slug == "betting",
        )
        .options(selectinload(Signal.outcome))
        .order_by(Signal.expected_value.desc())
    ).all())

    now_utc = datetime.now(timezone.utc)
    time_str = now_utc.strftime("%H:%M")

    print(f"\n{'='*64}")
    print(f"  SENTINEL refresh — {for_date}  ({time_str} UTC)")
    print(f"{'='*64}")

    if not signals:
        print("  No active picks for today.")
        print(f"{'='*64}\n")
        return signals

    no_prev = not prev_signals

    for i, s in enumerate(signals, 1):
        f        = s.features
        match    = f.get("match", "?")
        pick     = f.get("pick", "?")
        sport    = f.get("sport", "").split("_")[0]
        odd      = f.get("best_odd", "?")
        edge     = f.get("edge", 0.0)
        kelly    = f.get("kelly_units")
        stars    = f.get("star_rating", "")
        followed = s.id in followed_ids
        justification = f.get("justification")
        regenerated   = f.get("justification_regenerated", False)

        follow_icon = "📌" if followed else "⚪"
        units_str   = f"  {stars}  {kelly}u" if kelly is not None else ""
        odd_str     = f"{odd:.2f}" if isinstance(odd, float) else str(odd)
        edge_str    = f"{float(edge):+.1%}" if edge else "+0.0%"

        # Delta vs morning snapshot (odds movement indicator)
        delta_str = ""
        if not no_prev:
            dl = _delta_line(s, prev_signals)
            if dl:
                delta_str = dl.strip()

        print(f"\n  {i}. {follow_icon}  {match}  ({sport})")
        print(f"     Pick: {pick} @ {odd_str}{units_str}")
        ev_line = f"     Edge: {edge_str} | EV: {s.expected_value:+.1%}"
        if delta_str:
            ev_line += f"  {delta_str}"
        print(ev_line)
        if justification:
            jfx_prefix = "🔄💡" if regenerated else "💡"
            print(f"     {jfx_prefix} {justification}")
        elif regenerated:
            # Justification was cleared — will be regenerated on next full run
            print(f"     🔄 Justification will refresh on next run")
        print(f"     {s.id}")

    followed_count   = sum(1 for s in signals if s.id in followed_ids)
    available_count  = len(signals)
    print(f"\n{'='*64}")
    print(f"  {followed_count} followed, {available_count} available today.")
    print(f"  1u = 1% of bankroll  |  Sizing: 1/10 Kelly")
    print(f"{'='*64}\n")

    return signals


if __name__ == "__main__":
    main()
