"""Reusable refresh logic shared by the CLI (`paper_trade.py --refresh`) and
the FastAPI `POST /refresh` endpoint, so there is exactly one code path that
builds the live adapter and re-runs the pipeline -- the CLI and the API
differ only in what they do with the result (terminal/Telegram display vs a
JSON summary).
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.betting.adapter import BettingAdapter
from core.models import Signal
from core.orchestrator import get_or_create_domain, run_pipeline, snapshot_signals


def build_betting_adapter(mock: bool = False) -> BettingAdapter:
    """Construct a BettingAdapter from env vars (live) or sample fixtures (mock).

    Decoupled from argparse/CLI args so both scripts/paper_trade.py and the
    API endpoint can call this directly.
    """
    today = datetime.now(timezone.utc).date()
    season = int(os.getenv("SEASON", today.year))
    events_override = None
    mlb_runs_override = None
    mlb_pitchers_override = None
    justifier = None
    if mock:
        from scripts.sample_data import sample_events, sample_mlb_pitchers, sample_mlb_runs
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


def run_refresh(session: Session, for_date: date | None = None) -> dict:
    """Re-fetch odds and regenerate today's signals; report what changed.

    Idempotent: relies on run_pipeline()'s existing upsert-or-insert
    semantics (CLAUDE.md: upsert fires when confidence or EV delta > 0.5%),
    so calling this repeatedly is always safe -- a signal whose odds haven't
    moved past the noise threshold is simply left untouched and isn't
    counted as "updated" below.

    Returns {"refreshed": n, "odds_updated": n, "justifications_updated": n,
    "errors": [...]}. Exceptions are caught and reported in "errors" rather
    than raised, since this is normally invoked from a background task with
    nothing waiting on the return value but logs.
    """
    if for_date is None:
        for_date = datetime.now(timezone.utc).date()

    errors: list[str] = []
    summary: dict = {
        "refreshed": 0,
        "odds_updated": 0,
        "justifications_updated": 0,
        "errors": errors,
    }

    try:
        adapter = build_betting_adapter(mock=False)
        domain = get_or_create_domain(session, adapter)

        # Snapshot BEFORE the pipeline runs -- run_pipeline() upserts active
        # signals in place, so querying afterwards would only see new values.
        before = snapshot_signals(session, domain.id, for_date)

        run = run_pipeline(session, adapter)

        touched = session.scalars(
            select(Signal).where(
                Signal.model_run_id == run.id,
                Signal.domain_id == domain.id,
            )
        ).all()

        odds_updated = 0
        justifications_updated = 0
        for sig in touched:
            prev = before.get(str(sig.raw_event_id))
            if prev is not None:
                prev_odd = prev.get("best_odd")
                new_odd = sig.features.get("best_odd")
                if (
                    prev_odd is not None
                    and new_odd is not None
                    and float(prev_odd) != float(new_odd)
                ):
                    odds_updated += 1
            if sig.features.get("justification_regenerated"):
                justifications_updated += 1

        summary["refreshed"] = len(touched)
        summary["odds_updated"] = odds_updated
        summary["justifications_updated"] = justifications_updated
    except Exception as exc:
        errors.append(str(exc))

    return summary
