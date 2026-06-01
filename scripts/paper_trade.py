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
import os
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from core.db import SessionLocal, init_db
from core.models import Signal
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
    args = parser.parse_args()

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
            _print_by_date(session, target_date, show_outcomes=True)
        else:
            # Normal mode: run today's pipeline and display this run's picks.
            run = run_pipeline(session, adapter)
            if args.resolve:
                n = run_resolution(session, adapter)
                print(f"Resolved {n} past signal(s).\n")
            _print_by_run(session, run.id, show_outcomes=args.resolve)


# --------------------------------------------------------------------------- #
# Display helpers                                                              #
# --------------------------------------------------------------------------- #

def _print_by_run(session, run_id: uuid.UUID, *, show_outcomes: bool = False) -> None:
    """Show signals produced by a specific model run.

    If the run created no new signals (all deduped), falls back to showing
    existing signals for today and tomorrow — so repeat runs still show picks.
    """
    signals = session.scalars(
        select(Signal)
        .where(Signal.model_run_id == run_id)
        .options(selectinload(Signal.outcome))
        .order_by(Signal.expected_value.desc())
    ).all()

    today = datetime.now(timezone.utc).date()
    if not signals:
        # All picks were already in the DB (deduped). Show the existing slate.
        tomorrow = today + timedelta(days=1)
        signals = session.scalars(
            select(Signal)
            .where(Signal.valid_for_date.in_([today, tomorrow]))
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
    print(f"\n{'='*64}\n  SENTINEL — value bets for {label}\n{'='*64}")
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
        icon = "✓" if o.was_correct else "✗"
        label = "won" if o.was_correct else "lost"
        hs = o.outcome_metadata.get("home_score")
        as_ = o.outcome_metadata.get("away_score")
        score = f"  ({hs}–{as_})" if hs is not None and as_ is not None else ""
        clv = o.outcome_metadata.get("clv")
        clv_str = f"  CLV: {clv:+.1%}" if clv is not None else ""
        return f"{icon}  {label}{score}{clv_str}"
    return "—  pending"


if __name__ == "__main__":
    main()
