"""Fit a post-hoc probability calibrator for the MLB Poisson model.

The live analysis of 103 resolved poisson_v0.3.0 picks found model_probability
is overconfident by ~16pp on average (raw Brier worse than the constant-mean
baseline). Isotonic regression (monotonic, robust to the non-monotonic
miscalibration seen in the 60-70% confidence band) maps raw model_probability
-> a calibrated probability that better matches observed win rates.

This is a MANUAL script, not part of the daily pipeline. Re-run it roughly
every ~50 newly resolved picks and overwrite calibration_v1.joblib -- see
CLAUDE.md decision log.

Usage:
    python -m scripts.calibrate --mock   # in-memory DB, proves no Supabase write/read
    python -m scripts.calibrate          # fits against live resolved poisson_v0.3.0 picks

Read-only against the database: this script never writes a row. It only
writes the serialized calibrator file.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from joblib import dump
from sklearn.isotonic import IsotonicRegression
from sklearn.model_selection import KFold
from sqlalchemy import select

from core.db import configure_mock_db, init_db
from core.models import ModelRun, Signal, SignalOutcome

MODEL_VERSION = "poisson_v0.3.0"
OUT_PATH = Path(__file__).resolve().parent.parent / "adapters" / "betting" / "calibration_v1.joblib"
MIN_ROWS = 30  # below this, isotonic regression overfits noise -- refuse to fit


def _fetch_rows(session) -> list[tuple[float, bool]]:
    """Return (raw_model_probability, was_correct) for resolved poisson_v0.3.0
    signals. Void signals have no SignalOutcome row, so the join already
    excludes them (see CLAUDE.md decision log)."""
    rows = session.execute(
        select(Signal, SignalOutcome)
        .join(SignalOutcome, SignalOutcome.signal_id == Signal.id)
        .join(ModelRun, Signal.model_run_id == ModelRun.id)
        .where(
            ModelRun.model_version == MODEL_VERSION,
            SignalOutcome.was_correct.is_not(None),
        )
    ).all()
    out: list[tuple[float, bool]] = []
    for signal, outcome in rows:
        raw_p = (signal.features or {}).get("raw_model_probability")
        if raw_p is None:
            # Pre-calibration signals only ever stored model_probability (raw).
            raw_p = (signal.features or {}).get("model_probability")
        if raw_p is None:
            continue
        out.append((float(raw_p), bool(outcome.was_correct)))
    return out


def _brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def _cv_calibrated_brier(raw: np.ndarray, y: np.ndarray, n_splits: int = 5) -> float:
    """Out-of-fold Brier score for the isotonic calibrator (honest estimate --
    each fold's calibrator never sees the rows it's scored on)."""
    kf = KFold(n_splits=n_splits, shuffle=True, random_state=42)
    oof = np.zeros_like(raw)
    for train_idx, test_idx in kf.split(raw):
        iso = IsotonicRegression(out_of_bounds="clip")
        iso.fit(raw[train_idx], y[train_idx])
        oof[test_idx] = iso.predict(raw[test_idx])
    return _brier(oof, y)


def _print_calibration_table(raw: np.ndarray, y: np.ndarray, final_iso: IsotonicRegression) -> None:
    bins = [
        ("0.50-0.59", 0.50, 0.60),
        ("0.60-0.69", 0.60, 0.70),
        ("0.70-0.79", 0.70, 0.80),
        ("0.80-0.89", 0.80, 0.90),
        ("0.90-1.00", 0.90, 1.01),
    ]
    print("\n  Calibration curve (bin, n, raw avg, calibrated avg, actual win rate):")
    print(f"  {'bin':<10} {'n':>4} {'raw avg':>9} {'calib avg':>10} {'actual win%':>12}")
    for label, lo, hi in bins:
        mask = (raw >= lo) & (raw < hi)
        n = int(mask.sum())
        if n == 0:
            continue
        raw_avg = raw[mask].mean()
        calib_avg = final_iso.predict(raw[mask]).mean()
        actual = y[mask].mean()
        print(f"  {label:<10} {n:>4} {raw_avg:>9.3f} {calib_avg:>10.3f} {actual:>11.1%}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fit isotonic calibrator for model_probability")
    parser.add_argument("--mock", action="store_true",
                         help="use an in-memory DB -- proves this never touches Supabase")
    args = parser.parse_args()

    if args.mock:
        configure_mock_db()
    init_db()

    from core.db import SessionLocal  # noqa: PLC0415  re-import after possible mock swap

    with SessionLocal() as session:
        data = _fetch_rows(session)

    if args.mock:
        print(f"[--mock] queried in-memory DB only -- {len(data)} rows found "
              "(0 expected on a fresh mock DB; confirms no Supabase access).")
        if not data:
            return

    if len(data) < MIN_ROWS:
        print(f"Only {len(data)} resolved {MODEL_VERSION} picks found "
              f"(need >= {MIN_ROWS}). Refusing to fit -- isotonic regression "
              "would overfit noise on this few points.")
        sys.exit(1)

    raw = np.array([r[0] for r in data])
    y = np.array([int(r[1]) for r in data])

    brier_raw = _brier(raw, y)
    brier_baseline = _brier(np.full_like(raw, y.mean()), y)
    brier_calibrated = _cv_calibrated_brier(raw, y)

    print(f"\nResolved {MODEL_VERSION} picks used: {len(data)}")
    print(f"Actual win rate:            {y.mean():.1%}")
    print(f"Raw Brier score:            {brier_raw:.4f}")
    print(f"Baseline Brier (mean):      {brier_baseline:.4f}")
    print(f"Calibrated Brier (5-fold):  {brier_calibrated:.4f}")
    if brier_calibrated < brier_raw:
        print("-> Calibration improves Brier score vs raw.")
    else:
        print("-> WARNING: calibration did NOT improve Brier score vs raw.")

    # Fit the final calibrator on all available data for production use.
    final_iso = IsotonicRegression(out_of_bounds="clip")
    final_iso.fit(raw, y)

    _print_calibration_table(raw, y, final_iso)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    dump(final_iso, OUT_PATH)
    print(f"\nSaved calibrator: {OUT_PATH}")
    print("Not wired into the live pipeline by this script -- see adapters/betting/calibration.py "
          "and the poisson_v0.3.1 model_version bump for where it's applied.")


if __name__ == "__main__":
    main()
