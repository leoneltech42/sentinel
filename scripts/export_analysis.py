"""Export resolved poisson_v0.3.0 signals with full feature data to CSV.

Usage:
    python -m scripts.export_analysis

Output:
    sentinel_v030_analysis.csv in the project root (next to this script set).

This is a read-only export; it writes nothing to the database.
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from core.db import SessionLocal, init_db
from core.models import ModelRun, Signal, SignalOutcome

MODEL_VERSION = "poisson_v0.3.0"
OUT_PATH = Path(__file__).resolve().parent.parent / "sentinel_v030_analysis.csv"


def main() -> None:
    init_db()

    with SessionLocal() as session:
        rows = session.execute(
            select(SignalOutcome, Signal, ModelRun)
            .join(Signal, SignalOutcome.signal_id == Signal.id)
            .join(ModelRun, Signal.model_run_id == ModelRun.id)
            .where(
                ModelRun.model_version == MODEL_VERSION,
                SignalOutcome.was_correct.is_not(None),
            )
            .options(selectinload(SignalOutcome.signal))
            .order_by(Signal.valid_for_date)
        ).all()

    if not rows:
        print(f"No resolved {MODEL_VERSION} picks found.")
        return

    # --- Discover the full set of feature keys from all rows ---
    all_feature_keys: list[str] = []
    seen: set[str] = set()
    for outcome, signal, _ in rows:
        for k in (signal.features or {}).keys():
            if k not in seen:
                seen.add(k)
                all_feature_keys.append(k)

    print(f"Feature keys found ({len(all_feature_keys)}):")
    for k in all_feature_keys:
        print(f"  {k}")
    print()

    # --- Build columns ---
    # Fixed columns first, then every feature key, then outcome columns.
    fixed_cols = ["signal_id", "valid_for_date", "confidence", "ev"]
    outcome_cols = ["was_correct", "score", "home_score", "away_score"]
    columns = fixed_cols + all_feature_keys + outcome_cols

    # --- Write CSV ---
    wins = losses = 0
    with OUT_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()

        for outcome, signal, model_run in rows:
            meta = outcome.outcome_metadata or {}
            was_correct = outcome.was_correct
            if was_correct:
                wins += 1
            else:
                losses += 1

            row: dict = {
                "signal_id": str(signal.id),
                "valid_for_date": str(signal.valid_for_date),
                "confidence": signal.confidence,
                "ev": signal.expected_value,
                "was_correct": int(was_correct),
                "score": meta.get("score", f"{meta.get('away_score', '?')}-{meta.get('home_score', '?')}"),
                "home_score": meta.get("home_score", ""),
                "away_score": meta.get("away_score", ""),
            }
            for k in all_feature_keys:
                row[k] = (signal.features or {}).get(k, "")

            writer.writerow(row)

    total = wins + losses
    print(f"Rows written : {total}")
    print(f"Columns      : {len(columns)}")
    print(f"  {columns}")
    print(f"Wins         : {wins}")
    print(f"Losses       : {losses}")
    print(f"Win rate     : {wins/total*100:.1f}%" if total else "Win rate: n/a")
    print(f"\nSaved to: {OUT_PATH}")


if __name__ == "__main__":
    main()
