"""Core orchestrator — runs the pipeline by talking ONLY to the Adapter contract.

This file is the proof that the framework is generic: there is not a single
mention of betting, odds, or sports here. Swap in a crypto or flights adapter and
this code runs unchanged.

Pipeline:  ingest → record model_run → generate signals → persist
Resolution: for past unresolved signals, ask the adapter for the outcome.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone

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


def snapshot_signals(
    session: Session,
    domain_id: uuid.UUID,
    for_date: date,
) -> dict[str, dict]:
    """Snapshot the current state of active signals for this domain+date.

    Call this **before** run_pipeline() so the values captured here reflect
    the morning-run state.  After run_pipeline() the upsert may have mutated
    features/confidence/ev in-place, so querying afterwards returns the new
    values — not the delta baseline.

    Keyed by str(raw_event_id).  Each value holds:
      {'confidence': float, 'expected_value': float,
       'kelly_units': float | None, 'best_odd': float | None}

    Returns an empty dict when no signals exist yet (first run of the day),
    which the refresh display interprets as "no previous run to compare."
    """
    active = session.scalars(
        select(Signal).where(
            Signal.domain_id == domain_id,
            Signal.valid_for_date == for_date,
            Signal.status == "active",
        )
    ).all()

    result: dict[str, dict] = {}
    for s in active:
        f = s.features
        result[str(s.raw_event_id)] = {
            "confidence":     s.confidence,
            "expected_value": s.expected_value,
            "kelly_units":    f.get("kelly_units"),
            "best_odd":       f.get("best_odd"),
        }
    return result


# Keep the old name as an alias so any future callers aren't broken silently.
get_previous_run_signals = snapshot_signals


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
    # Upsert logic: update the existing active signal if confidence or EV shifted
    # by more than the noise threshold; insert if no prior signal exists.
    # Resolved/void signals are never touched — they are historical records.
    _UPSERT_THRESHOLD = 0.005  # 0.5% — smaller swings are odds noise, not a new pick

    signal_data = adapter.generate_signals(events)
    for sd in signal_data:
        raw = raw_by_key.get(sd.raw_event_key)
        if raw is None:
            continue

        pick = sd.features.get("pick")

        # Look for a pre-existing signal for the same (raw_event, signal_type, pick).
        existing: Signal | None = next(
            (
                s for s in session.scalars(
                    select(Signal).where(
                        Signal.domain_id == domain.id,
                        Signal.raw_event_id == raw.id,
                        Signal.signal_type == sd.signal_type,
                    )
                ).all()
                if s.features.get("pick") == pick
            ),
            None,
        )

        if existing is not None:
            if existing.status != "active":
                # Historical record — never mutate resolved or void signals.
                continue
            conf_delta = abs(sd.confidence - existing.confidence)
            ev_delta = abs(sd.expected_value - existing.expected_value)
            if conf_delta > _UPSERT_THRESHOLD or ev_delta > _UPSERT_THRESHOLD:
                existing.confidence = sd.confidence
                existing.expected_value = sd.expected_value
                existing.features = sd.features
                existing.model_run_id = run.id   # tie to current run for display
                existing.updated_at = datetime.now(timezone.utc)
                logging.info("upserted signal %s  pick=%s  conf_delta=%.3f  ev_delta=%.3f",
                             existing.id, pick, conf_delta, ev_delta)
            else:
                logging.info("skipped signal (no significant change)  pick=%s  "
                             "conf_delta=%.4f  ev_delta=%.4f", pick, conf_delta, ev_delta)
            continue

        sig = Signal(
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
        session.add(sig)
        session.flush()  # get the UUID assigned before logging
        logging.info("inserted signal %s  pick=%s", sig.id, pick)

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
