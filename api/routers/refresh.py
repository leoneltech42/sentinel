"""POST /refresh — on-demand odds refresh, triggered from the web dashboard.

Runs the same logic as the daily_refresh GitHub Action
(`python -m scripts.paper_trade --refresh`): re-fetch odds, upsert signals
whose confidence/EV moved past the noise threshold, regenerate stale
justifications. See adapters/betting/refresh.py for the shared
implementation -- this endpoint is a thin wrapper that runs it in the
background so the HTTP response returns immediately.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends

from api.auth import require_api_key
from api.schemas import RefreshResponse

router = APIRouter(tags=["refresh"], dependencies=[Depends(require_api_key)])


def _run_refresh_background() -> None:
    """Opens its own DB session -- the request-scoped session from
    Depends(get_db) closes as soon as the HTTP response returns, before
    this background task runs."""
    from adapters.betting.refresh import run_refresh
    from core.db import SessionLocal

    with SessionLocal() as session:
        summary = run_refresh(session)
        if summary["errors"]:
            logging.warning("POST /refresh completed with errors: %s", summary["errors"])
        else:
            logging.info(
                "POST /refresh completed: refreshed=%d odds_updated=%d justifications_updated=%d",
                summary["refreshed"], summary["odds_updated"], summary["justifications_updated"],
            )


@router.post("/refresh", response_model=RefreshResponse, status_code=202)
def post_refresh(background_tasks: BackgroundTasks) -> RefreshResponse:
    """Idempotent: safe to call repeatedly -- run_pipeline()'s upsert-or-insert
    semantics mean a signal whose odds haven't moved is simply left alone."""
    background_tasks.add_task(_run_refresh_background)
    return RefreshResponse(status="started")
