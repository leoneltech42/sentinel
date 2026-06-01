"""Phase 0 paper-trading runner.

Runs the full pipeline (ingest → model → signals) and prints today's picks. This
is the Phase 0 deliverable: a real pick on screen, tracked on paper, no product
yet. The goal is to validate the model shows positive ROI / break-even before
building anything else.

Usage:
    python -m scripts.paper_trade --mock                          # sample data, no network
    python -m scripts.paper_trade                                 # live The Odds API (uses key)
    python -m scripts.paper_trade --resolve                       # resolve today's picks too
    python -m scripts.paper_trade --date 2026-05-30 --resolve     # resolve a specific past date

Env (see .env.example):
    ODDS_API_KEY    your The Odds API key
    DATABASE_URL    Supabase/Postgres URL, or omit for local SQLite
    SEASON          MLB season year (default: current year)
"""

from __future__ import annotations

import argparse
import logging
import os
import uuid
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from core.db import SessionLocal, init_db
from core.models import Signal, SignalOutcome
from core.orchestrator import run_pipeline, run_resolution


def main() -> None:
    parser = argparse.ArgumentParser(description="Sentinel Phase 0 paper trader")
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
    args = parser.parse_args()

    # Show orchestrator INFO messages when requested (or always in mock mode so
    # upsert behaviour is clearly visible during local testing).
    if args.verbose or args.mock:
        logging.basicConfig(level=logging.INFO,
                            format="  [orchestrator] %(message)s")

    today = datetime.now(timezone.utc).date()

    from adapters.betting.adapter import BettingAdapter

    season = int(os.getenv("SEASON", today.year))
    events_override = None
    mlb_runs_override = None
    if args.mock:
        from scripts.sample_data import sample_events, sample_mlb_runs
        events_override = sample_events()
        mlb_runs_override = sample_mlb_runs()

    adapter = BettingAdapter(
        api_key=os.getenv("ODDS_API_KEY", ""),
        season=season,
        events_override=events_override,
        mlb_runs_override=mlb_runs_override,
    )

    init_db()
    with SessionLocal() as session:
        if args.date:
            # Past-date mode: skip pipeline, resolve picks for the target date.
            target_date = date.fromisoformat(args.date)
            if args.resolve:
                n = run_resolution(session, adapter)
                print(f"Resolved {n} past signal(s).\n")
                _verify_outcomes_supabase(session, target_date)
            _print_by_date(session, target_date, show_outcomes=True)
            return

        # ------------------------------------------------------------------ #
        # Live / mock ingestion run                                           #
        # ------------------------------------------------------------------ #
        if not args.mock:
            # Fetch once, cache, pass to pipeline so we don't burn quota twice.
            print(f"\nFetching live odds from The Odds API ...")
            raw_events = adapter.fetch_raw_events()
            adapter._events_override = raw_events   # cache for pipeline

            _print_ingestion_summary(raw_events)

            # Full per-event model diagnostic before writing anything.
            evals = adapter.evaluate_events(raw_events)
            _print_model_diagnostic(evals)

            # Show API quota consumed by this run.
            quota = adapter._client.last_quota if adapter._client else {}
            _print_quota(quota)

        run = run_pipeline(session, adapter)

        if args.resolve:
            n = run_resolution(session, adapter)
            print(f"Resolved {n} past signal(s).\n")

        _print_by_run(session, run.id, show_outcomes=args.resolve)

        if not args.mock:
            _verify_supabase(session, today)


# --------------------------------------------------------------------------- #
# Live-run diagnostic helpers                                                  #
# --------------------------------------------------------------------------- #

def _print_ingestion_summary(raw_events: list) -> None:
    by_sport: Counter = Counter()
    for ev in raw_events:
        sport = ev.event_key.split("::", 1)[0]
        by_sport[sport] += 1
    print(f"\n  Ingested {len(raw_events)} raw event(s):")
    for sport, n in sorted(by_sport.items()):
        print(f"    {sport}: {n}")


def _print_model_diagnostic(evals: list[dict[str, Any]]) -> None:
    print(f"\n{'='*64}")
    print(f"  MODEL DIAGNOSTIC — {len(evals)} event(s) evaluated")
    print(f"{'='*64}")
    for ev in evals:
        tag = ev["sport"].split("_")[0]
        print(f"\n  {ev['match']}  ({tag})  {ev['game_time'][:10]}")
        if not ev["has_odds"]:
            print(f"    SKIP — {ev['skip_reason']}")
            continue
        if not ev["supported"]:
            print(f"    SKIP — {ev['skip_reason']}")
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


def _verify_outcomes_supabase(session, target_date: date) -> None:
    """After resolution: print signal_outcomes stats for target_date."""
    rows = session.scalars(
        select(SignalOutcome)
        .join(Signal, SignalOutcome.signal_id == Signal.id)
        .where(Signal.valid_for_date == target_date)
        .options(selectinload(SignalOutcome.signal))
    ).all()

    total = len(rows)
    correct = sum(1 for r in rows if r.was_correct)
    print(f"  --- Supabase signal_outcomes for {target_date} ---")
    print(f"  Outcome rows created : {total}")
    if total:
        print(f"  Correct              : {correct}/{total}  "
              f"({correct/total:.0%} win rate)")
        clv_rows = [r for r in rows if r.outcome_metadata.get("clv") is not None]
        if clv_rows:
            avg_clv = sum(r.outcome_metadata["clv"] for r in clv_rows) / len(clv_rows)
            print(f"  CLV (avg)            : {avg_clv:+.2%}  ({len(clv_rows)} picks with data)")
        else:
            print("  CLV                  : not available (historical endpoint not on free tier)")
        print()
        for r in rows:
            f = r.signal.features
            icon = "[W]" if r.was_correct else "[L]"
            hs = r.outcome_metadata.get("home_score", "?")
            as_ = r.outcome_metadata.get("away_score", "?")
            winner = r.outcome_metadata.get("winner", "?")
            print(f"  {icon}  {f.get('match','?'):<42}  "
                  f"pick={f.get('pick','?'):<30}  "
                  f"score={as_}-{hs}  winner={winner}")
    print()


def _verify_supabase(session, today: date) -> None:
    """Query Supabase directly to confirm signals were persisted."""
    from datetime import timedelta
    tomorrow = today + timedelta(days=1)
    count = session.scalar(
        select(func.count(Signal.id)).where(
            Signal.valid_for_date.in_([today, tomorrow])
        )
    )
    print(f"  Supabase check: {count} signal(s) stored for {today} / {tomorrow}")

    # Print UUIDs so user can spot-check in the Supabase dashboard.
    sigs = session.scalars(
        select(Signal)
        .where(Signal.valid_for_date.in_([today, tomorrow]))
        .order_by(Signal.created_at.desc())
        .limit(20)
    ).all()
    if sigs:
        print("  Signal UUIDs in DB:")
        for s in sigs:
            f = s.features
            print(f"    {s.id}  {f.get('match','?')}  pick={f.get('pick','?')}")
    print()


# --------------------------------------------------------------------------- #
# Display helpers                                                              #
# --------------------------------------------------------------------------- #

def _print_by_run(session, run_id: uuid.UUID, *, show_outcomes: bool = False) -> None:
    """Show today's signals touched by a specific model run.

    "Touched" means either inserted or upserted during this run (model_run_id
    matches). If nothing was touched (odds unchanged, all below noise threshold),
    fall back to showing today's existing signals by date — so a no-op repeat
    run still displays picks.

    Signals for future dates are stored but never shown here; the user always
    sees only games starting today.
    """
    today = datetime.now(timezone.utc).date()

    signals = session.scalars(
        select(Signal)
        .where(Signal.model_run_id == run_id, Signal.valid_for_date == today)
        .options(selectinload(Signal.outcome))
        .order_by(Signal.expected_value.desc())
    ).all()

    if not signals:
        # Nothing was inserted or upserted for today — odds were unchanged.
        # Show today's existing slate so the output is never empty after a stable run.
        signals = session.scalars(
            select(Signal)
            .where(Signal.valid_for_date == today)
            .options(selectinload(Signal.outcome))
            .order_by(Signal.expected_value.desc())
        ).all()

    _render(signals, today, show_outcomes=show_outcomes)


def _print_by_date(session, target_date: date, *, show_outcomes: bool = False) -> None:
    """Show all signals with valid_for_date == target_date."""
    signals = session.scalars(
        select(Signal)
        .where(Signal.valid_for_date == target_date)
        .options(selectinload(Signal.outcome))
        .order_by(Signal.expected_value.desc())
    ).all()
    _render(signals, target_date, show_outcomes=show_outcomes)


def _render(signals: list[Signal], label: date, *, show_outcomes: bool) -> None:
    print(f"\n{'='*64}\n  SENTINEL - value bets for {label}\n{'='*64}")
    if not signals:
        print("  No +EV opportunities found.")
        print(f"{'='*64}\n")
        return

    for s in signals:
        f = s.features
        lines = [
            f"\n  {f['match']}  ({f['sport'].split('_')[0]})",
            f"    Pick:        {f['pick']}  @ {f['best_odd']}",
            f"    Model prob:  {f['model_probability']:.1%}  "
            f"(market {f['market_probability']:.1%})",
            f"    Edge:        {f['edge']:+.1%}",
            f"    EV:          {s.expected_value:+.1%}   "
            f"Confidence: {s.confidence:.1%}",
        ]
        if show_outcomes:
            lines.append(f"    Result:      {_outcome_line(s)}")
        print("\n".join(lines))

    print(f"\n{'='*64}")
    print(f"  {len(signals)} pick(s). Paper-trade these and track results.")
    print(f"{'='*64}\n")


def _outcome_line(signal: Signal) -> str:
    if signal.status == "void":
        return "∅  void  (postponed / suspended / cancelled)"
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
    return "—  pending"


if __name__ == "__main__":
    main()
