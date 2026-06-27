"""Mock verification for the personal_stake fix (api/routers/picks.py, follow.py).

Simulates exactly the reported scenario:
  1. Follow a signal with a custom stake (1.8u) while the model suggests 1.8u too.
  2. A later refresh upserts the signal, bumping kelly_units to 8.2u.
  3. GET /picks should still report personal_stake=1.8 (locked in at follow
     time) even though stake_units (the model's current suggestion) is 8.2.

Never touches Supabase. Run with:
    python -m scripts.test_personal_stake
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from core.db import configure_mock_db, init_db
from core.models import Domain, ModelRun, RawEvent, Signal


def main() -> None:
    configure_mock_db()
    init_db()
    from core.db import SessionLocal  # noqa: PLC0415

    user_id = uuid.uuid4()

    with SessionLocal() as session:
        domain = Domain(slug="betting", name="Betting")
        session.add(domain)
        session.flush()
        raw = RawEvent(domain_id=domain.id, event_key="mlb::t::2026-06-26",
                        payload={}, event_at=datetime.now(timezone.utc))
        session.add(raw)
        session.flush()
        run = ModelRun(domain_id=domain.id, model_version="poisson_v0.3.1", status="completed")
        session.add(run)
        session.flush()
        sig = Signal(
            domain_id=domain.id, model_run_id=run.id, raw_event_id=raw.id,
            signal_type="value_bet", confidence=0.667, expected_value=0.127,
            features={"pick": "Phillies", "match": "Mets vs Phillies", "best_odd": 1.69, "kelly_units": 1.8},
            status="active", valid_for_date=date(2026, 6, 26),
        )
        session.add(sig)
        session.commit()
        sig_id = sig.id

    # --- Step 1: follow with a custom stake (matches model suggestion: 1.8) ---
    from api.routers.follow import follow_signal
    from api.schemas import FollowRequest

    with SessionLocal() as session:
        result = follow_signal(sig_id, FollowRequest(stake=1.8), session=session, user_id=user_id)
        print(f"After follow: stake_units={result.stake_units}  personal_stake={result.personal_stake}")
        assert result.personal_stake == 1.8, result.personal_stake

    # --- Step 2: simulate a refresh upserting kelly_units to 8.2 (model only) ---
    with SessionLocal() as session:
        sig = session.get(Signal, sig_id)
        sig.features = {**sig.features, "kelly_units": 8.2, "model_probability": 0.9249}
        sig.confidence = 0.9249
        sig.expected_value = 0.563
        session.commit()

    # --- Step 3: GET /picks should show personal_stake=1.8, stake_units=8.2 ---
    from api.routers.picks import get_picks

    with SessionLocal() as session:
        picks = get_picks(target_date=date(2026, 6, 26), sport=None, league=None,
                           session=session, user_id=user_id)
        assert len(picks) == 1, picks
        p = picks[0]
        print(f"GET /picks: stake_units={p.stake_units}  personal_stake={p.personal_stake}  followed={p.followed}")
        assert p.stake_units == 8.2, p.stake_units
        assert p.personal_stake == 1.8, p.personal_stake
        assert p.followed is True

    print("\nALL CHECKS PASS -- personal_stake stays locked in at 1.8 even after the model's "
          "suggestion drifts to 8.2.")


if __name__ == "__main__":
    main()
