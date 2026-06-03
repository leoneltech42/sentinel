"""Probabilistic models for the betting domain.

Soccer: a Poisson goals model (the classic, well-fitted approach — goals per
team are close to Poisson-distributed). Given each team's expected goals
(lambda), the full score matrix gives win/draw/loss probabilities.

Baseball (MLB): a Poisson-on-runs model. Flagged as v0 — runs are not as cleanly
Poisson as soccer goals, and there is no draw (extra innings decide), so tie mass
is redistributed. A production model would use richer run-distribution methods.

The value logic is domain-neutral and lives at the bottom:
  * de-vig the market to get fair market probabilities
  * EV = model_prob * decimal_odd - 1
  * a value bet is one where model_prob exceeds the de-vigged market prob AND
    EV clears a configurable threshold.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# A modest cap on goals/runs; tail probability beyond this is negligible.
_MAX_GOALS = 12
_MAX_RUNS = 20


def _poisson_pmf(k: int, lam: float) -> float:
    """P(X = k) for X ~ Poisson(lam). Implemented without scipy for portability."""
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * lam**k / math.factorial(k)


def soccer_match_probs(lam_home: float, lam_away: float) -> tuple[float, float, float]:
    """Return (P_home_win, P_draw, P_away_win) from expected goals per team."""
    p_home = p_draw = p_away = 0.0
    for h in range(_MAX_GOALS + 1):
        ph = _poisson_pmf(h, lam_home)
        for a in range(_MAX_GOALS + 1):
            p = ph * _poisson_pmf(a, lam_away)
            if h > a:
                p_home += p
            elif h == a:
                p_draw += p
            else:
                p_away += p
    total = p_home + p_draw + p_away
    return p_home / total, p_draw / total, p_away / total


def baseball_match_probs(lam_home: float, lam_away: float) -> tuple[float, float]:
    """Return (P_home_win, P_away_win). Ties redistributed (no draws in MLB)."""
    p_home = p_away = p_tie = 0.0
    for h in range(_MAX_RUNS + 1):
        ph = _poisson_pmf(h, lam_home)
        for a in range(_MAX_RUNS + 1):
            p = ph * _poisson_pmf(a, lam_away)
            if h > a:
                p_home += p
            elif h == a:
                p_tie += p
            else:
                p_away += p
    # Split tie mass proportionally to each side's non-tie strength.
    decided = p_home + p_away
    if decided > 0:
        p_home += p_tie * (p_home / decided)
        p_away += p_tie * (p_away / decided)
    total = p_home + p_away
    return p_home / total, p_away / total


# --------------------------------------------------------------------------- #
# Value logic (domain-neutral)                                                #
# --------------------------------------------------------------------------- #
def devig(odds: list[float]) -> list[float]:
    """Convert decimal odds to fair (de-vigged) probabilities that sum to 1."""
    implied = [1.0 / o for o in odds]
    overround = sum(implied)
    return [p / overround for p in implied]


def expected_value(model_prob: float, decimal_odd: float) -> float:
    """EV per unit staked. Positive means the bet has positive expected value."""
    return model_prob * decimal_odd - 1.0


@dataclass
class ValueBet:
    selection: str
    decimal_odd: float
    model_prob: float
    market_prob: float  # de-vigged
    edge: float  # model_prob - market_prob
    ev: float


def kelly_units(
    model_prob: float,
    decimal_odd: float,
    fraction: float = 0.10,
    scale: float = 100.0,
) -> float:
    """Fractional Kelly stake in units.

    One unit = 1% of bankroll (standard betting convention).
    fraction=0.10 = tenth Kelly — conservative, suitable for models in
    validation.  Typical MLB signals (EV +8–20%, odds 1.65–2.20) land in
    the 1–5u range.  Example: $1000 bankroll → 1u = $10.

    Returns 0.0 for negative or zero edge (no bet recommended).
    """
    if decimal_odd <= 1.0:
        return 0.0
    edge = model_prob * decimal_odd - 1.0
    if edge <= 0:
        return 0.0
    kelly = edge / (decimal_odd - 1.0)
    return max(0.1, round(kelly * fraction * scale, 1))


def star_rating(units: float) -> str:
    """Visual stake size indicator based on Kelly units (absolute scale)."""
    if units < 1.0:
        return "★☆☆☆☆"   # ★☆☆☆☆
    elif units < 2.0:
        return "★★☆☆☆"   # ★★☆☆☆
    elif units < 3.5:
        return "★★★☆☆"   # ★★★☆☆
    elif units < 5.0:
        return "★★★★☆"   # ★★★★☆
    else:
        return "★★★★★"   # ★★★★★


def find_value_bets(
    selections: list[str],
    odds: list[float],
    model_probs: list[float],
    min_ev: float,
    min_confidence: float,
) -> list[ValueBet]:
    """Compare model probabilities against the de-vigged market and flag +EV."""
    market_probs = devig(odds)
    bets: list[ValueBet] = []
    for sel, odd, mp, mkt in zip(selections, odds, model_probs, market_probs):
        ev = expected_value(mp, odd)
        if mp >= min_confidence and ev >= min_ev and mp > mkt:
            bets.append(
                ValueBet(
                    selection=sel,
                    decimal_odd=odd,
                    model_prob=mp,
                    market_prob=mkt,
                    edge=mp - mkt,
                    ev=ev,
                )
            )
    return bets
