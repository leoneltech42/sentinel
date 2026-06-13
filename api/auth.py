from __future__ import annotations

import os

from fastapi import HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader

_header_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(api_key: str | None = Security(_header_scheme)) -> str:
    expected = os.environ.get("SENTINEL_API_KEY", "")
    if not api_key or api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
    return api_key
