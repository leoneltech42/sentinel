"""Database setup: engine, session factory, and a portable JSON column type.

The connection target is driven entirely by the DATABASE_URL env var, so the
same code runs against Supabase/Postgres in production and SQLite locally for
fast model-validation loops — no code change, just the env var.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from sqlalchemy import JSON, create_engine, inspect as sa_inspect, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Load .env (if present) before reading DATABASE_URL. Does not override
# variables already set in the real environment.
load_dotenv()

# Default to a local SQLite file so the pipeline runs with zero setup.
# Point at Supabase by setting DATABASE_URL in your .env (see .env.example).
_raw_url = os.getenv("DATABASE_URL", "sqlite:///sentinel.db")
# Supabase connection strings use "postgresql://"; SQLAlchemy 2 + psycopg3
# needs "postgresql+psycopg://". Auto-correct so either form works.
DATABASE_URL = (
    _raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if _raw_url.startswith("postgresql://")
    else _raw_url
)

# JSONB on Postgres (indexable, queryable), plain JSON elsewhere (e.g. SQLite).
# This is what lets the schema stay generic: domain-specific fields live here.
JSONType = JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    pass


engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db() -> None:
    """Create all tables. For Phase 0; Alembic migrations own this in Phase 1."""
    from core import models  # noqa: F401  (import registers the models)

    Base.metadata.create_all(engine)
    _add_signals_updated_at()


def _add_signals_updated_at() -> None:
    """Add updated_at to signals for tables created before this column existed.

    create_all() only creates tables that are entirely absent — it never alters
    existing tables. This ALTER TABLE handles the Supabase table that was already
    created without updated_at. Safe to call repeatedly; skips if already present.
    """
    try:
        existing_cols = {c["name"] for c in sa_inspect(engine).get_columns("signals")}
    except Exception:
        return  # table doesn't exist yet; create_all() will create it with the column

    if "updated_at" in existing_cols:
        return  # already present — nothing to do

    col_type = "TIMESTAMPTZ" if engine.dialect.name == "postgresql" else "DATETIME"
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE signals ADD COLUMN updated_at {col_type}"))
