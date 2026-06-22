from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel


class PickResponse(BaseModel):
    id: UUID
    event_key: str
    valid_for_date: date
    sport: str           # derived: features['sport'].split('_')[0]
    league: str          # derived: features['sport'].split('_', 1)[1]
    pick: str            # features['pick']
    matchup: str         # features['match']
    confidence: float
    ev: float            # Signal.expected_value
    odds: float          # features['best_odd']
    stake_units: float   # features['kelly_units']
    justification: str | None
    followed: bool
    status: str          # 'active' | 'expired' | 'resolved' | 'void'
    outcome: str | None  # 'won' | 'lost' | 'void' | None
    score: str | None    # "{away_score}-{home_score}" from outcome_metadata


class FollowRequest(BaseModel):
    stake: float | None = None


class OutcomeResponse(BaseModel):
    signal_id: UUID
    valid_for_date: date
    sport: str
    league: str
    pick: str
    matchup: str         # features['match']
    was_correct: bool
    score: str           # "{away_score}-{home_score}" from outcome_metadata
    ev: float
    confidence: float
    odds: float          # features['best_odd']
    stake_units: float   # features['kelly_units']
    followed: bool
    personal_stake: float | None  # user_signal_views.stake if followed
    model_version: str


class PnlResponse(BaseModel):
    picks: int
    wins: int
    win_rate: float
    kelly_roi: float


class RefreshResponse(BaseModel):
    status: str  # "started" -- the refresh runs in a background task
