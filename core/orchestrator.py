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


def _should_regenerate_justification(
    old_signal: "Signal",
    new_signal_data: "Any",
) -> bool:
    """Return True when the justification text stored on old_signal is stale.

    Staleness criteria (domain-agnostic — uses signal fields only):
      * The pick changed (different team / selection).
      * The expected value moved by more than 10 percentage points — a
        meaningful shift that invalidates the reasoning behind the old text.

    When True the caller clears features['justification'] = None before writing,
    so the adapter generates a fresh blurb on the next generate_signals() call.
    When False the caller copies the old justification into the new features dict
    so it is preserved across minor odds-noise upserts.
    """
    old_pick = old_signal.features.get("pick")
    new_pick = new_signal_data.features.get("pick")
    if old_pick != new_pick:
        return True
    ev_delta = abs(new_signal_data.expected_value - old_signal.expected_value)
    return ev_delta > 0.10   # 10 pp threshold


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

    # 3. Expire any active signals whose game has already started (valid_until in
    # the past).  These are pre-match picks that were never resolved because the
    # game began before the resolution loop ran.  Marking them "expired" keeps
    # them out of the daily display and prevents accidental follows.
    now = datetime.now(timezone.utc)
    expired_sigs = session.scalars(
        select(Signal).where(
            Signal.domain_id == domain.id,
            Signal.status == "active",
            Signal.valid_until.isnot(None),
            Signal.valid_until < now,
        )
    ).all()
    for sig in expired_sigs:
        sig.status = "expired"
    if expired_sigs:
        session.flush()
        logging.info("Expired %d signal(s) past valid_until.", len(expired_sigs))

    # 4. Generate signals and persist, each linked to its run and raw event.
    # Upsert logic: update the existing active signal if confidence or EV shifted
    # by more than the noise threshold; insert if no prior signal exists.
    # Resolved/void/expired signals are never touched — they are historical records.
    _UPSERT_THRESHOLD = 0.005  # 0.5% — smaller swings are odds noise, not a new pick

    signal_data = adapter.generate_signals(events)
    for sd in signal_data:
        raw = raw_by_key.get(sd.raw_event_key)
        if raw is None:
            continue

        pick = sd.features.get("pick")

        # Look for a pre-existing active signal for the same (raw_event, signal_type).
        # Intentionally does NOT filter by pick — one game = one signal maximum.
        # A pick flip updates the existing row in place rather than creating a
        # duplicate with the opposite selection.
        existing: Signal | None = session.scalars(
            select(Signal).where(
                Signal.domain_id == domain.id,
                Signal.raw_event_id == raw.id,
                Signal.signal_type == sd.signal_type,
                Signal.status == "active",
            )
        ).first()

        if existing is not None:
            # Historical records are never touched; active-only guard already in WHERE.
            old_pick = existing.features.get("pick")
            pick_changed = old_pick != pick

            conf_delta = abs(sd.confidence - existing.confidence)
            ev_delta   = abs(sd.expected_value - existing.expected_value)

            # Always update when the pick flips; otherwise respect the noise threshold.
            if pick_changed or conf_delta > _UPSERT_THRESHOLD or ev_delta > _UPSERT_THRESHOLD:
                # Decide whether to keep or clear the stored justification text.
                # Work on a copy so we never mutate the SignalData dataclass in-place.
                new_features = dict(sd.features)
                if _should_regenerate_justification(existing, sd):
                    # Pick changed or EV moved >10pp — old reasoning is stale.
                    # Clear it so the adapter produces fresh text on the next run.
                    new_features["justification"] = None
                    new_features["justification_regenerated"] = True
                    logging.info("justification cleared for signal %s (stale pick/ev)",
                                 existing.id)
                else:
                    # Minor odds drift — preserve the existing justification text
                    # to avoid burning LLM quota on near-identical blurbs.
                    old_justification = existing.features.get("justification")
                    new_features["justification"] = old_justification
                    new_features.pop("justification_regenerated", None)

                existing.confidence     = sd.confidence
                existing.expected_value = sd.expected_value
                existing.features       = new_features
                existing.valid_for_date = sd.valid_for_date
                existing.valid_until    = sd.valid_until
                existing.model_run_id   = run.id
                existing.updated_at     = datetime.now(timezone.utc)

                if pick_changed:
                    logging.info("updated signal %s  pick changed: %s -> %s",
                                 existing.id, old_pick, pick)
                else:
                    logging.info("updated signal %s  pick=%s  conf_delta=%.3f  ev_delta=%.3f",
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
    """Resolve past signals that don't have an outcome yet. Returns count resolved.

    Queries both "active" and "expired" signals — expired signals are games whose
    valid_until passed before the resolution loop ran (e.g. late West Coast games).
    They are fully resolvable once the game finishes; excluding them from resolution
    would leave real results unrecorded.
    """
    domain = get_or_create_domain(session, adapter)
    pending = session.scalars(
        select(Signal).where(
            Signal.domain_id == domain.id,
            Signal.status.in_(["active", "expired"]),
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

        # Defensive guard: skip if this signal already has an outcome row.
        # The UNIQUE(signal_id) constraint on signal_outcomes catches this at
        # the DB level, but checking here avoids an IntegrityError rollback.
        existing_outcome = session.scalar(
            select(SignalOutcome).where(SignalOutcome.signal_id == sig.id)
        )
        if existing_outcome:
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
