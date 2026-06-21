from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from api.auth import require_api_key
from api.dependencies import get_db, get_user_id
from api.lib.versioning import production_baseline, qualifying_versions_for_domain
from api.routers.picks import _derive_sport_league
from api.schemas import OutcomeResponse
from core.models import Domain, ModelRun, Signal, SignalOutcome, UserSignalView

router = APIRouter(tags=["outcomes"], dependencies=[Depends(require_api_key)])


@router.get("/outcomes", response_model=list[OutcomeResponse])
def get_outcomes(
    target_date: Annotated[date | None, Query(alias="date")] = None,
    sport: str | None = None,
    league: str | None = None,
    model_version: str | None = None,
    session: Session = Depends(get_db),
    user_id: uuid.UUID = Depends(get_user_id),
) -> list[OutcomeResponse]:
    q = (
        select(SignalOutcome)
        .join(Signal, SignalOutcome.signal_id == Signal.id)
        .join(Domain, Signal.domain_id == Domain.id)
        .where(Domain.slug == "betting")
        .options(selectinload(SignalOutcome.signal))
    )

    if target_date is not None:
        q = q.where(Signal.valid_for_date == target_date).order_by(
            Signal.expected_value.desc()
        )
    else:
        q = q.order_by(Signal.valid_for_date.asc(), Signal.expected_value.desc())

    if sport:
        q = q.where(Signal.features["sport"].as_string().like(f"{sport}_%"))
    if league:
        q = q.where(Signal.features["sport"].as_string().like(f"%_{league}"))

    if model_version is None:
        # No explicit version requested -- default to the production floor
        # (this version and everything semantically greater), not a single
        # hardcoded exact string.
        qualifying = qualifying_versions_for_domain(session, "betting", production_baseline())
        q = q.join(ModelRun, Signal.model_run_id == ModelRun.id).where(
            ModelRun.model_version.in_(qualifying)
        )
    elif model_version != "all":
        q = (
            q.join(ModelRun, Signal.model_run_id == ModelRun.id)
            .where(ModelRun.model_version == model_version)
        )

    rows = list(session.scalars(q).all())

    sig_ids = [r.signal_id for r in rows]

    # Bulk-fetch model_version for all returned signals (single query)
    model_version_map: dict[uuid.UUID, str] = {}
    if sig_ids:
        for sig_id, mv in session.execute(
            select(Signal.id, ModelRun.model_version)
            .join(ModelRun, Signal.model_run_id == ModelRun.id)
            .where(Signal.id.in_(sig_ids))
        ).all():
            model_version_map[sig_id] = mv

    # Bulk-fetch UserSignalViews for followed/personal_stake
    views: dict[uuid.UUID, UserSignalView] = {}
    if sig_ids:
        for v in session.scalars(
            select(UserSignalView).where(
                UserSignalView.signal_id.in_(sig_ids),
                UserSignalView.user_id == user_id,
            )
        ).all():
            views[v.signal_id] = v

    results: list[OutcomeResponse] = []
    for row in rows:
        f = row.signal.features
        sport_key = f.get("sport", "_")
        s, lg = _derive_sport_league(sport_key)
        meta = row.outcome_metadata or {}
        hs = meta.get("home_score", "?")
        as_ = meta.get("away_score", "?")
        view = views.get(row.signal_id)
        results.append(
            OutcomeResponse(
                signal_id=row.signal_id,
                valid_for_date=row.signal.valid_for_date,
                sport=s,
                league=lg,
                pick=f.get("pick", ""),
                matchup=f.get("match", ""),
                was_correct=row.was_correct,
                score=f"{as_}-{hs}",
                ev=row.signal.expected_value,
                confidence=row.signal.confidence,
                odds=float(f.get("best_odd", 0)),
                stake_units=float(f.get("kelly_units", 0)),
                followed=bool(view and view.followed),
                personal_stake=(
                    float(view.stake)
                    if view and view.followed and view.stake is not None
                    else None
                ),
                model_version=model_version_map.get(row.signal_id, ""),
            )
        )
    return results
