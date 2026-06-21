"""Runtime loader for the fitted probability calibrator.

scripts/calibrate.py fits and serializes calibration_v1.joblib offline against
accumulated resolved poisson_v0.3.0 picks (manual step -- not auto-retrained
in the daily pipeline; CLAUDE.md: re-run every ~50 new resolved picks).
BettingAdapter loads it once at init and applies it to every raw
model_probability before edge/EV/Kelly/confidence/star_rating are derived.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from joblib import load

CALIBRATOR_PATH = Path(__file__).parent / "calibration_v1.joblib"


def load_calibrator() -> Any | None:
    """Return the fitted isotonic calibrator, or None if it hasn't been
    fitted yet (e.g. a fresh clone before scripts/calibrate.py has run)."""
    if not CALIBRATOR_PATH.exists():
        return None
    try:
        return load(CALIBRATOR_PATH)
    except Exception as exc:
        logging.warning("Failed to load calibrator %s: %s", CALIBRATOR_PATH, exc)
        return None


def calibrate(raw_prob: float, calibrator: Any | None) -> float:
    """Map a raw model probability through the calibrator.

    Degrades gracefully to the raw value when no calibrator is loaded, so the
    adapter works identically on a fresh clone before calibration_v1.joblib
    has ever been fitted.
    """
    if calibrator is None:
        return raw_prob
    return float(calibrator.predict([raw_prob])[0])
