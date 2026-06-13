from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from api.auth import require_api_key
from api.dependencies import get_db, get_user_id
from api.schemas import PickResponse
from core.models import Domain, RawEvent, Signal, UserSignalView

router = APIRouter(tags=["picks"], dependencies=[Depends(require_api_key)])


def _derive_sport_league(sport_key: str) -> tuple[str, str]:
    parts = sport_key.split("_", 1)
    return parts[0], parts[1] if len(parts) > 1 else ""


def _outcome_label(signal: Signal) -> str | None:
    if signal.status == "void":
        return "void"
    if signal.status == "resolved" and signal.outcome is not None:
        return "won" if signal.outcome.was_correct else "lost"
    return None


def _build_pick(
    signal: Signal,
    event_key: str,
    followed: bool,
) -> PickResponse:
    f = signal.features
    sport_key = f.get("sport", "_")
    sport, league = _derive_sport_league(sport_key)
    return PickResponse(
        id=signal.id,
        event_key=event_key,
        valid_for_date=signal.valid_for_date,
        sport=sport,
        league=league,
        pick=f.get("pick", ""),
        confidence=signal.confidence,
        ev=signal.expected_value,
        odds=float(f.get("best_odd", 0)),
        stake_units=float(f.get("kelly_units", 0)),
        justification=f.get("justification"),
        followed=followed,
        outcome=_outcome_label(signal),
    )


@router.get("/picks", response_model=list[PickResponse])
def get_picks(
    target_date: Annotated[date | None, Query(alias="date")] = None,
    sport: str | None = None,
    league: str | None = None,
    session: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_user_id),
) -> list[PickResponse]:
    if target_date is None:
        target_date = datetime.now(timezone.utc).date()

    q = (
        select(Signal)
        .join(Domain, Signal.domain_id == Domain.id)
        .where(
            Domain.slug == "betting",
            Signal.valid_for_date == target_date,
            Signal.status.in_(["active", "resolved", "void"]),
        )
        .options(selectinload(Signal.outcome))
        .order_by(Signal.expected_value.desc())
    )
    if sport:
        q = q.where(Signal.features["sport"].as_string().like(f"{sport}_%"))
    if league:
        q = q.where(Signal.features["sport"].as_string().like(f"%_{league}"))

    signals = session.scalars(q).all()

    # Bulk-fetch raw event keys and followed status to avoid N+1 queries.
    raw_ids = [s.raw_event_id for s in signals]
    raw_events: dict[uuid.UUID, str] = {}
    if raw_ids:
        for row in session.scalars(
            select(RawEvent).where(RawEvent.id.in_(raw_ids))
        ).all():
            raw_events[row.id] = row.event_key

    followed_ids: set[uuid.UUID] = set()
    if signals:
        sig_ids = [s.id for s in signals]
        for view in session.scalars(
            select(UserSignalView).where(
                UserSignalView.signal_id.in_(sig_ids),
                UserSignalView.user_id == user_id,
                UserSignalView.followed.is_(True),
            )
        ).all():
            followed_ids.add(view.signal_id)

    return [
        _build_pick(s, raw_events.get(s.raw_event_id, ""), s.id in followed_ids)
        for s in signals
    ]
