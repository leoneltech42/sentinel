from __future__ import annotations

import os
import uuid
from collections.abc import Generator

from sqlalchemy.orm import Session

from core.db import SessionLocal


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def get_user_id() -> uuid.UUID:
    return uuid.UUID(os.environ["SENTINEL_USER_ID"])
