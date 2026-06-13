from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from api.auth import require_api_key
from api.dependencies import get_db
from api.routers.picks import _derive_sport_league
from api.schemas import OutcomeResponse
from core.models import Domain, Signal, SignalOutcome

router = APIRouter(tags=["outcomes"], dependencies=[Depends(require_api_key)])


@router.get("/outcomes", response_model=list[OutcomeResponse])
def get_outcomes(
    target_date: Annotated[date | None, Query(alias="date")] = None,
    sport: str | None = None,
    league: str | None = None,
    session: Session = Depends(get_db),
) -> list[OutcomeResponse]:
    if target_date is None:
        target_date = datetime.now(timezone.utc).date()

    q = (
        select(SignalOutcome)
        .join(Signal, SignalOutcome.signal_id == Signal.id)
        .join(Domain, Signal.domain_id == Domain.id)
        .where(
            Domain.slug == "betting",
            Signal.valid_for_date == target_date,
        )
        .options(selectinload(SignalOutcome.signal))
        .order_by(Signal.expected_value.desc())
    )
    if sport:
        q = q.where(Signal.features["sport"].as_string().like(f"{sport}_%"))
    if league:
        q = q.where(Signal.features["sport"].as_string().like(f"%_{league}"))

    rows = session.scalars(q).all()

    results: list[OutcomeResponse] = []
    for row in rows:
        f = row.signal.features
        sport_key = f.get("sport", "_")
        s, lg = _derive_sport_league(sport_key)
        meta = row.outcome_metadata or {}
        hs = meta.get("home_score", "?")
        as_ = meta.get("away_score", "?")
        results.append(
            OutcomeResponse(
                signal_id=row.signal_id,
                valid_for_date=row.signal.valid_for_date,
                sport=s,
                league=lg,
                pick=f.get("pick", ""),
                was_correct=row.was_correct,
                score=f"{as_}-{hs}",
                ev=row.signal.expected_value,
                confidence=row.signal.confidence,
            )
        )
    return results
