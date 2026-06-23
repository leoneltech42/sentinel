"""GET /config — exposes server-side config the frontend needs to render
correctly, starting with the model-version floor.

Without this, the web dashboard's "Production (vX.Y.Z+)" label would have
to be a hand-edited string baked into the frontend. If PRODUCTION_MODEL_BASELINE
is ever bumped on Railway (e.g. retiring poisson_v0.3.0 once v0.4.0 ships),
that label would silently lie about what's actually included unless someone
remembers to edit and redeploy the frontend in lockstep. Reading it from here
keeps the label always accurate with zero frontend deploys required.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.auth import require_api_key
from api.lib.versioning import production_baseline
from api.schemas import ConfigResponse

router = APIRouter(tags=["config"], dependencies=[Depends(require_api_key)])


@router.get("/config", response_model=ConfigResponse)
def get_config() -> ConfigResponse:
    return ConfigResponse(production_model_baseline=production_baseline())
