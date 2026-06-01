"""Idempotency test for run_pipeline().

Runs the pipeline twice against an isolated in-memory SQLite DB (never touches
sentinel.db) and asserts:
  * raw_events: exactly one row per event_key (no duplicates after two runs)
  * signals:    same count after the second run as after the first (dedup works)

Run with:
    python -m scripts.test_dedup
"""

from __future__ import annotations

import sys

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from core.db import Base
from core.models import RawEvent, Signal
from core.orchestrator import run_pipeline


def _make_session():
    """Fresh in-memory SQLite — isolated from sentinel.db."""
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def main() -> None:
    from adapters.betting.adapter import BettingAdapter
    from scripts.sample_data import sample_events, sample_mlb_runs

    adapter = BettingAdapter(
        api_key="",
        season=2026,
        events_override=sample_events(),
        mlb_runs_override=sample_mlb_runs(),
    )

    SessionLocal = _make_session()

    # --- Run 1 ---
    with SessionLocal() as session:
        run1 = run_pipeline(session, adapter)

    with SessionLocal() as session:
        raw_after_run1 = session.scalars(select(RawEvent)).all()
        signals_after_run1 = session.scalars(select(Signal)).all()
        n_raw_1 = len(raw_after_run1)
        n_sig_1 = len(signals_after_run1)

    # --- Run 2 (same events) ---
    adapter2 = BettingAdapter(
        api_key="",
        season=2026,
        events_override=sample_events(),
        mlb_runs_override=sample_mlb_runs(),
    )
    with SessionLocal() as session:
        run2 = run_pipeline(session, adapter2)

    with SessionLocal() as session:
        raw_after_run2 = session.scalars(select(RawEvent)).all()
        signals_after_run2 = session.scalars(select(Signal)).all()
        n_raw_2 = len(raw_after_run2)
        n_sig_2 = len(signals_after_run2)

    # --- Assertions ---
    print("\n-- Dedup test results ------------------------------")
    print(f"  Sample events:          {len(sample_events())} (soccer + mlb)")
    print(f"  raw_events after run 1: {n_raw_1}")
    print(f"  raw_events after run 2: {n_raw_2}  (expected: {n_raw_1})")
    print(f"  signals after run 1:    {n_sig_1}")
    print(f"  signals after run 2:    {n_sig_2}  (expected: {n_sig_1})")
    print()

    failures: list[str] = []

    if n_raw_1 != len(sample_events()):
        failures.append(
            f"run 1 raw_events ({n_raw_1}) != sample event count ({len(sample_events())})"
        )
    if n_raw_2 != n_raw_1:
        failures.append(
            f"raw_events grew on run 2: {n_raw_1} -> {n_raw_2} (dedup broken)"
        )
    if n_sig_2 != n_sig_1:
        failures.append(
            f"signals grew on run 2: {n_sig_1} -> {n_sig_2} (signal dedup broken)"
        )

    if failures:
        print("FAIL")
        for f in failures:
            print(f"  [X] {f}")
        sys.exit(1)
    else:
        print("PASS")
        print("  [OK] raw_events deduplicated correctly")
        print("  [OK] signals deduplicated correctly")


if __name__ == "__main__":
    main()
