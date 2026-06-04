"""The data model — the 8 tables designed in DESIGN.md, as SQLAlchemy models.

Two groups:
  * Framework tables (domain-agnostic): domains, data_sources, raw_events,
    model_runs, signals, signal_outcomes
  * Product tables (the SaaS layer): users, user_signal_views

Domain-specific data always lives in JSONType columns (payload, features,
auth_config, hyperparams), never as typed columns — that is what keeps the
schema generic across betting, flights, crypto, and any future domain.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base, JSONType


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Framework tables                                                            #
# --------------------------------------------------------------------------- #
class Domain(Base):
    __tablename__ = "domains"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    config: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    data_sources: Mapped[list["DataSource"]] = relationship(back_populates="domain")
    raw_events: Mapped[list["RawEvent"]] = relationship(back_populates="domain")


class DataSource(Base):
    __tablename__ = "data_sources"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    domain_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("domains.id"))
    provider: Mapped[str] = mapped_column(String(128))
    endpoint: Mapped[str] = mapped_column(String(512))
    auth_config: Mapped[dict] = mapped_column(JSONType, default=dict)  # encrypted creds
    status: Mapped[str] = mapped_column(String(32), default="active")
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    domain: Mapped["Domain"] = relationship(back_populates="data_sources")


class RawEvent(Base):
    """Append-only log of everything ingested. Never modified or deleted."""

    __tablename__ = "raw_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    domain_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("domains.id"))
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("data_sources.id"))
    event_key: Mapped[str] = mapped_column(String(256), index=True)  # dedup key
    payload: Mapped[dict] = mapped_column(JSONType)  # raw source data, untransformed
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    domain: Mapped["Domain"] = relationship(back_populates="raw_events")


class ModelRun(Base):
    __tablename__ = "model_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    domain_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("domains.id"))
    model_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="running")
    hyperparams: Mapped[dict] = mapped_column(JSONType, default=dict)
    overall_score: Mapped[float | None] = mapped_column(Float)  # backtest accuracy
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Signal(Base):
    """The central table — the model's output. Domain detail lives in features."""

    __tablename__ = "signals"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    domain_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("domains.id"))
    model_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("model_runs.id"))
    raw_event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("raw_events.id"))
    signal_type: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float)
    expected_value: Mapped[float] = mapped_column(Float)
    features: Mapped[dict] = mapped_column(JSONType)  # domain-specific (jsonb)
    # Valid values: "active" | "resolved" | "void" | "expired"
    # "void"    = match didn't finish (postponed, suspended, cancelled); no outcome row.
    # "expired" = signal's valid_until passed before resolution (game already started
    #             when the pipeline ran); treated like void for display and P&L.
    status: Mapped[str] = mapped_column(String(32), default="active")
    valid_for_date: Mapped[date] = mapped_column(Date, index=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # Set automatically when the ORM updates this row (e.g. upsert on odds change).
    # Nullable so existing rows are unaffected until their first update.
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=_now
    )

    outcome: Mapped["SignalOutcome | None"] = relationship(back_populates="signal")


class SignalOutcome(Base):
    """Closes the loop: was the signal correct, and what actually happened."""

    __tablename__ = "signal_outcomes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    signal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("signals.id"), unique=True)
    was_correct: Mapped[bool] = mapped_column(Boolean)
    actual_value: Mapped[float] = mapped_column(Float)
    outcome_metadata: Mapped[dict] = mapped_column(JSONType, default=dict)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    signal: Mapped["Signal"] = relationship(back_populates="outcome")


# --------------------------------------------------------------------------- #
# Product tables (the SaaS layer)                                             #
# --------------------------------------------------------------------------- #
class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(256), unique=True)
    plan: Mapped[str] = mapped_column(String(32), default="free")  # free | paid
    domain_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("domains.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class UserSignalView(Base):
    """Per-user tracking: what they followed, their stake, their P&L."""

    __tablename__ = "user_signal_views"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    signal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("signals.id"))
    followed: Mapped[bool] = mapped_column(Boolean, default=False)
    stake: Mapped[float | None] = mapped_column(Float)
    pnl: Mapped[float | None] = mapped_column(Float)
    viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    signal: Mapped["Signal"] = relationship()
