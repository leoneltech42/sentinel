"""Adapter contract — the heart of the framework's genericity.

Every domain (betting, flights, crypto, ...) implements this interface. The
`core/` orchestrator only ever talks to an `Adapter`; it never knows what a bet
or a flight is. Adding a new domain means writing a class that implements this
contract — the core is never touched.

The data-transfer objects below (RawEventData, SignalData, OutcomeData) keep
adapters decoupled from the ORM: an adapter returns plain dataclasses, and the
orchestrator translates them into database rows. An adapter never imports the
ORM models.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass
class RawEventData:
    """One immutable event ingested from a source, before any modeling."""

    event_key: str  # stable unique id within the domain; used for dedup
    payload: dict[str, Any]  # raw source data, stored untransformed (jsonb)
    event_at: datetime  # when the real-world event occurs (e.g. match start)
    source: str  # which data_source produced it (e.g. "the-odds-api")


@dataclass
class SignalData:
    """A detected opportunity. Domain-specific detail lives in `features`."""

    raw_event_key: str  # links the signal back to the event that produced it
    signal_type: str  # e.g. "value_bet", "restock_alert", "entry_signal"
    confidence: float  # model probability for the selection, 0..1
    expected_value: float  # EV per unit staked; > 0 means +EV
    features: dict[str, Any]  # everything domain-specific (jsonb)
    valid_for_date: date  # the day this signal applies to
    valid_until: datetime | None = None  # validity window; None = no expiry


@dataclass
class ResolvableSignal:
    """Minimal view of a stored signal the orchestrator hands to `resolve`."""

    signal_id: str
    event_key: str
    features: dict[str, Any]
    valid_for_date: date


@dataclass
class OutcomeData:
    """The resolved result of a signal, closing the loop."""

    was_correct: bool
    actual_value: float  # e.g. the odd that paid, or the % move
    metadata: dict[str, Any] = field(default_factory=dict)


class Adapter(ABC):
    """Interface every domain adapter must implement."""

    #: short identifier, e.g. "betting". Becomes the domain slug.
    domain_slug: str
    #: how this domain resolves signals: "binary" | "threshold" | "continuous"
    resolution_rule: str

    @property
    @abstractmethod
    def model_version(self) -> str:
        """Version string recorded on every model_run for traceability."""

    @abstractmethod
    def hyperparams(self) -> dict[str, Any]:
        """Current model configuration, recorded on every model_run."""

    @abstractmethod
    def fetch_raw_events(self) -> list[RawEventData]:
        """Pull fresh data from the domain's source(s) and normalize to events."""

    @abstractmethod
    def generate_signals(self, events: list[RawEventData]) -> list[SignalData]:
        """Run the model over events and return detected opportunities."""

    @abstractmethod
    def resolve(self, signal: ResolvableSignal) -> OutcomeData | None:
        """Compute a signal's outcome once the real event has happened.

        Returns None if the event hasn't resolved yet (e.g. match not finished).
        """
