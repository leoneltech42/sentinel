"""Mock verification for the PRODUCTION_MODEL_BASELINE floor (api/lib/versioning.py).

Seeds an in-memory DB with synthetic model_runs at v0.2.0/v0.3.0/v0.3.1 and
confirms:
  - omitting model_version includes v0.3.0 AND v0.3.1, excludes v0.2.0
  - explicit model_version="poisson_v0.3.0" still returns ONLY v0.3.0

Never touches Supabase. Run with:
    python -m scripts.test_version_baseline
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone

from core.db import SessionLocal, configure_mock_db, init_db
from core.models import Domain, ModelRun, RawEvent, Signal, SignalOutcome


def _seed():
    configure_mock_db()
    init_db()
    from core.db import SessionLocal  # noqa: PLC0415  re-import after mock swap

    with SessionLocal() as session:
        domain = Domain(slug="betting", name="Betting")
        session.add(domain)
        session.flush()

        raw_event = RawEvent(
            domain_id=domain.id,
            event_key="mlb::test::2026-06-21",
            payload={},
            event_at=datetime.now(timezone.utc),
        )
        session.add(raw_event)
        session.flush()

        for version in ("poisson_v0.2.0", "poisson_v0.3.0", "poisson_v0.3.1"):
            run = ModelRun(domain_id=domain.id, model_version=version, status="completed")
            session.add(run)
            session.flush()
            signal = Signal(
                domain_id=domain.id,
                model_run_id=run.id,
                raw_event_id=raw_event.id,
                signal_type="value_bet",
                confidence=0.65,
                expected_value=0.08,
                features={"pick": "Team A", "match": "A vs B", "best_odd": 1.9, "kelly_units": 1.0},
                status="resolved",
                valid_for_date=date(2026, 6, 21),
            )
            session.add(signal)
            session.flush()
            outcome = SignalOutcome(
                signal_id=signal.id,
                was_correct=True,
                actual_value=1.9,
                outcome_metadata={},
            )
            session.add(outcome)

        session.commit()
        return SessionLocal


def main() -> None:
    SessionLocalRef = _seed()
    from api.lib.versioning import production_baseline, qualifying_versions_for_domain
    from api.routers.pnl import _global_outcomes

    with SessionLocalRef() as session:
        # Omitted model_version -> production floor (default baseline poisson_v0.3.0)
        baseline = production_baseline()
        qualifying = qualifying_versions_for_domain(session, "betting", baseline)
        print(f"baseline={baseline}  qualifying_versions={sorted(qualifying)}")
        assert set(qualifying) == {"poisson_v0.3.0", "poisson_v0.3.1"}, qualifying

        default_rows = _global_outcomes(session, None)
        versions_seen = {
            session.get(ModelRun, session.get(Signal, r.signal_id).model_run_id).model_version
            for r in default_rows
        }
        print(f"model_version=None -> {len(default_rows)} rows, versions={sorted(versions_seen)}")
        assert versions_seen == {"poisson_v0.3.0", "poisson_v0.3.1"}, versions_seen
        assert "poisson_v0.2.0" not in versions_seen

        # Explicit exact match -> unaffected by the floor, only that version
        explicit_rows = _global_outcomes(session, "poisson_v0.3.0")
        versions_seen_explicit = {
            session.get(ModelRun, session.get(Signal, r.signal_id).model_run_id).model_version
            for r in explicit_rows
        }
        print(f"model_version='poisson_v0.3.0' -> {len(explicit_rows)} rows, "
              f"versions={sorted(versions_seen_explicit)}")
        assert versions_seen_explicit == {"poisson_v0.3.0"}, versions_seen_explicit

        # "all" -> everything, including v0.2.0
        all_rows = _global_outcomes(session, "all")
        versions_seen_all = {
            session.get(ModelRun, session.get(Signal, r.signal_id).model_run_id).model_version
            for r in all_rows
        }
        print(f"model_version='all' -> {len(all_rows)} rows, versions={sorted(versions_seen_all)}")
        assert versions_seen_all == {"poisson_v0.2.0", "poisson_v0.3.0", "poisson_v0.3.1"}

    print("\nALL CHECKS PASS")


if __name__ == "__main__":
    main()
