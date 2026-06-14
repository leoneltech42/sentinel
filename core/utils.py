"""Shared helpers used across core/ and scripts/."""

from __future__ import annotations


def confidence_star_level(confidence: float) -> int:
    """Return star level 1–5 for a model confidence value.

    Thresholds match web/lib/utils.tsx renderStars():
      < 0.60  → 1  |  0.60–0.69 → 2  |  0.70–0.79 → 3
      0.80–0.89 → 4  |  ≥ 0.90  → 5
    """
    if confidence >= 0.90:
        return 5
    if confidence >= 0.80:
        return 4
    if confidence >= 0.70:
        return 3
    if confidence >= 0.60:
        return 2
    return 1
