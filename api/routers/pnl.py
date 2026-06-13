from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from api.auth import require_api_key
from api.dependencies import get_db, get_user_id
from api.schemas import PnlResponse
from core.models import Domain, Signal, SignalOutcome, UserSignalView

router = APIRouter(prefix="/pnl", tags=["pnl"], dependencies=[Depends(require_api_key)])


def _compute_pnl(rows: list[SignalOutcome], stakes: dict[uuid.UUID, float]) -> PnlResponse:
    """Aggregate resolved outcomes into a PnlResponse.

    stakes maps signal_id → stake (units). Missing entries default to 1u.
    kelly_roi = total_pnl / total_staked (or 0 when nothing is staked).
    """
    total_pnl = 0.0
    total_staked = 0.0
    wins = 0

    for row in rows:
        stake = stakes.get(row.signal_id, 1.0)
        odds = float(row.signal.features.get("best_odd", 1.0))
        if row.was_correct:
            total_pnl += stake * (odds - 1)
            wins += 1
        else:
            total_pnl -= stake
        total_staked += stake

    n = len(rows)
    return PnlResponse(
        picks=n,
        wins=wins,
        win_rate=wins / n if n else 0.0,
        kelly_roi=total_pnl / total_staked if total_staked else 0.0,
    )


def _global_outcomes(session: Session) -> list[SignalOutcome]:
    return session.scalars(
        select(SignalOutcome)
        .join(Signal, SignalOutcome.signal_id == Signal.id)
        .join(Domain, Signal.domain_id == Domain.id)
        .where(Domain.slug == "betting")
        .options(selectinload(SignalOutcome.signal))
    ).all()


def _personal_outcomes(
    session: Session, user_id: uuid.UUID
) -> tuple[list[SignalOutcome], dict[uuid.UUID, float]]:
    views = session.scalars(
        select(UserSignalView).where(
            UserSignalView.user_id == user_id,
            UserSignalView.followed.is_(True),
        )
    ).all()
    followed_ids = {v.signal_id for v in views}
    stakes = {v.signal_id: float(v.stake or 1.0) for v in views}

    if not followed_ids:
        return [], {}

    outcomes = session.scalars(
        select(SignalOutcome)
        .join(Signal, SignalOutcome.signal_id == Signal.id)
        .join(Domain, Signal.domain_id == Domain.id)
        .where(
            Domain.slug == "betting",
            SignalOutcome.signal_id.in_(followed_ids),
        )
        .options(selectinload(SignalOutcome.signal))
    ).all()
    return list(outcomes), stakes


@router.get("/global", response_model=PnlResponse)
def pnl_global(session: Session = Depends(get_db)) -> PnlResponse:
    rows = _global_outcomes(session)
    stakes = {r.signal_id: float(r.signal.features.get("kelly_units", 1.0)) for r in rows}
    return _compute_pnl(rows, stakes)


@router.get("/personal", response_model=PnlResponse)
def pnl_personal(
    session: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_user_id),
) -> PnlResponse:
    rows, stakes = _personal_outcomes(session, user_id)
    return _compute_pnl(rows, stakes)
