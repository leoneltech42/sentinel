"""Semantic-version floor logic for `model_runs.model_version`.

Default (no explicit `model_version` param) views should include "this
version and everything semantically greater," not a single hardcoded exact
string -- otherwise every default view silently excludes new picks the
moment a new model version ships. The floor is configured via
PRODUCTION_MODEL_BASELINE (see .env.example). v0.1.0/v0.2.0 stay
permanently excluded from default views even as the floor advances --
they were discarded for cause (HOME_ADVANTAGE bugs), not superseded.

An explicit `model_version` param (a specific version, or "all") bypasses
this floor entirely and behaves as an exact match -- the floor only
applies when the param is omitted.
"""

from __future__ import annotations

import os

from sqlalchemy import select
from sqlalchemy.orm import Session

_PREFIX = "poisson_v"
_DEFAULT_BASELINE = "poisson_v0.3.0"


def parse_model_version(version_str: str) -> tuple[int, int, int]:
    """Parse "poisson_v0.3.1" -> (0, 3, 1).

    Hardcodes the "poisson_v" prefix -- there's only one versioned model
    family right now (the flights adapter uses "price_series_v0.2.0", a
    different family entirely, never compared against this baseline).
    Revisit if a second poisson-style versioned family appears.
    """
    if not version_str.startswith(_PREFIX):
        raise ValueError(f"Unrecognized model_version format: {version_str!r}")
    numeric = version_str[len(_PREFIX):]
    parts = numeric.split(".")
    if len(parts) != 3:
        raise ValueError(f"Expected poisson_vMAJOR.MINOR.PATCH, got: {version_str!r}")
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError as exc:
        raise ValueError(f"Non-numeric version component in: {version_str!r}") from exc


def meets_baseline(version_str: str, baseline: str) -> bool:
    """True if version_str's parsed semantic version >= baseline's (inclusive)."""
    return parse_model_version(version_str) >= parse_model_version(baseline)


def production_baseline() -> str:
    """The configured floor for default (omitted model_version) views."""
    return os.getenv("PRODUCTION_MODEL_BASELINE", _DEFAULT_BASELINE)


def qualifying_versions(all_versions: list[str], baseline: str) -> list[str]:
    """Filter all_versions down to those meeting the baseline.

    Skips any version string that fails to parse (e.g. a different model
    family's version) rather than raising -- a malformed or foreign version
    string shouldn't crash a default view.
    """
    out: list[str] = []
    for v in all_versions:
        try:
            if meets_baseline(v, baseline):
                out.append(v)
        except ValueError:
            continue
    return out


def qualifying_versions_for_domain(session: Session, domain_slug: str, baseline: str) -> list[str]:
    """Distinct model_versions present for a domain that meet the baseline.

    Scoped to a domain because different domains have entirely different
    model-version families (betting's "poisson_vX.Y.Z" vs flights'
    "price_series_vX.Y.Z") -- comparing across domains would be meaningless.
    """
    from core.models import Domain, ModelRun, Signal  # local import: keep this module DB-light

    versions = session.scalars(
        select(ModelRun.model_version)
        .join(Signal, Signal.model_run_id == ModelRun.id)
        .join(Domain, Signal.domain_id == Domain.id)
        .where(Domain.slug == domain_slug)
        .distinct()
    ).all()
    return qualifying_versions(list(versions), baseline)
