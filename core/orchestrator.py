"""Core orchestrator — runs the pipeline by talking ONLY to the Adapter contract.

This file is the proof that the framework is generic: there is not a single
mention of betting, odds, or sports here. Swap in a crypto or flights adapter and
this code runs unchanged.

Pipeline:  ingest → record model_run → generate signals → persist
Resolution: for past unresolved signals, ask the adapter for the outcome.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.base import Adapter, ResolvableSignal
from core.models import (
    Domain,
    ModelRun,
    RawEvent,
    Signal,
    SignalOutcome,
    UserSignalView,
)


def get_or_create_domain(session: Session, adapter: Adapter) -> Domain:
    domain = session.scalar(select(Domain).where(Domain.slug == adapter.domain_slug))
    if domain is None:
        domain = Domain(slug=adapter.domain_slug, name=adapter.domain_slug.title())
        session.add(domain)
        session.flush()
    return domain


def run_pipeline(session: Session, adapter: Adapter) -> ModelRun:
    """Ingest fresh data, run the model, and persist the resulting signals."""
    domain = get_or_create_domain(session, adapter)

    # 1. Ingest — append-only, deduplicated on (domain, event_key).
    events = adapter.fetch_raw_events()
    raw_by_key: dict[str, RawEvent] = {}
    for ev in events:
        existing = session.scalar(
            select(RawEvent).where(
                RawEvent.domain_id == domain.id, RawEvent.event_key == ev.event_key
            )
        )
        if existing is None:
            existing = RawEvent(
                domain_id=domain.id,
                event_key=ev.event_key,
                payload=ev.payload,
                event_at=ev.event_at,
            )
            session.add(existing)
            session.flush()
        raw_by_key[ev.event_key] = existing

    # 2. Record the model run for traceability.
    run = ModelRun(
        domain_id=domain.id,
        model_version=adapter.model_version,
        hyperparams=adapter.hyperparams(),
        status="running",
    )
    session.add(run)
    session.flush()

    # 3. Generate signals and persist, each linked to its run and raw event.
    # Dedup: skip if a signal with the same (raw_event, signal_type, pick)
    # already exists for any prior run. Enforces the "one global slate per day"
    # invariant — running the pipeline twice should not double the pick list.
    signal_data = adapter.generate_signals(events)
    for sd in signal_data:
        raw = raw_by_key.get(sd.raw_event_key)
        if raw is None:
            continue

        pick = sd.features.get("pick")
        prior = session.scalars(
            select(Signal).where(
                Signal.domain_id == domain.id,
                Signal.raw_event_id == raw.id,
                Signal.signal_type == sd.signal_type,
            )
        ).all()
        if any(s.features.get("pick") == pick for s in prior):
            continue  # identical pick already in DB from a previous run today

        session.add(
            Signal(
                domain_id=domain.id,
                model_run_id=run.id,
                raw_event_id=raw.id,
                signal_type=sd.signal_type,
                confidence=sd.confidence,
                expected_value=sd.expected_value,
                features=sd.features,
                valid_for_date=sd.valid_for_date,
                valid_until=sd.valid_until,
            )
        )

    run.status = "completed"
    session.commit()
    return run


def run_resolution(session: Session, adapter: Adapter) -> int:
    """Resolve past signals that don't have an outcome yet. Returns count resolved."""
    domain = get_or_create_domain(session, adapter)
    pending = session.scalars(
        select(Signal).where(
            Signal.domain_id == domain.id, Signal.status == "active"
        )
    ).all()

    resolved = 0
    for sig in pending:
        raw = session.get(RawEvent, sig.raw_event_id)
        outcome = adapter.resolve(
            ResolvableSignal(
                signal_id=str(sig.id),
                event_key=raw.event_key if raw else "",
                features=sig.features,
                valid_for_date=sig.valid_for_date,
            )
        )
        if outcome is None:
            continue

        if outcome.metadata.get("void"):
            # Match didn't finish (postponed/suspended/cancelled) — no outcome row.
            sig.status = "void"
            resolved += 1
            continue

        session.add(
            SignalOutcome(
                signal_id=sig.id,
                was_correct=outcome.was_correct,
                actual_value=outcome.actual_value,
                outcome_metadata=outcome.metadata,
            )
        )
        sig.status = "resolved"
        _update_user_pnl(session, sig, outcome.was_correct, outcome.actual_value)
        resolved += 1

    session.commit()
    return resolved


def _update_user_pnl(
    session: Session, signal: Signal, won: bool, paid_odd: float
) -> None:
    """Update P&L for every user who followed this signal."""
    views = session.scalars(
        select(UserSignalView).where(
            UserSignalView.signal_id == signal.id, UserSignalView.followed.is_(True)
        )
    ).all()
    for view in views:
        stake = view.stake or 0.0
        view.pnl = stake * (paid_odd - 1.0) if won else -stake
