from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from api.auth import require_api_key
from api.dependencies import get_db, get_user_id
from api.routers.picks import _build_pick
from api.schemas import FollowRequest, PickResponse
from core.models import RawEvent, Signal, UserSignalView

router = APIRouter(
    prefix="/signals",
    tags=["follow"],
    dependencies=[Depends(require_api_key)],
)


def _get_signal_or_404(session: Session, signal_id: uuid.UUID) -> Signal:
    sig = session.scalar(
        select(Signal)
        .where(Signal.id == signal_id)
        .options(selectinload(Signal.outcome))
    )
    if sig is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Signal not found")
    return sig


def _get_view(
    session: Session, signal_id: uuid.UUID, user_id: uuid.UUID
) -> UserSignalView | None:
    return session.scalar(
        select(UserSignalView).where(
            UserSignalView.signal_id == signal_id,
            UserSignalView.user_id == user_id,
        )
    )


@router.post("/{signal_id}/follow", response_model=PickResponse, status_code=status.HTTP_201_CREATED)
def follow_signal(
    signal_id: uuid.UUID,
    body: FollowRequest,
    session: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_user_id),
) -> PickResponse:
    sig = _get_signal_or_404(session, signal_id)
    view = _get_view(session, signal_id, user_id)

    if view is not None and view.followed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Already following")

    stake = body.stake if body.stake is not None else float(sig.features.get("kelly_units", 1.0))

    if view is None:
        view = UserSignalView(
            signal_id=sig.id,
            user_id=user_id,
            followed=True,
            stake=stake,
        )
        session.add(view)
    else:
        view.followed = True
        view.stake = stake

    session.commit()

    raw = session.get(RawEvent, sig.raw_event_id)
    event_key = raw.event_key if raw else ""
    return _build_pick(sig, event_key, followed=True, personal_stake=stake)


@router.delete("/{signal_id}/follow", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def unfollow_signal(
    signal_id: uuid.UUID,
    session: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_user_id),
) -> None:
    view = _get_view(session, signal_id, user_id)
    if view is None or not view.followed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Not following")

    view.followed = False
    session.commit()
