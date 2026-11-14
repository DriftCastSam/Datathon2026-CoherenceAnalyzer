"""Factor view: horizon (docs/spec-factors.md §20.2-20.3). A view, not a second implementation: names what belongs to
the factor and builds its section facts. Rules stay in rules/, checks in coherence/."""
from __future__ import annotations

import sys

from ..intake import REQUIRED as _REQUIRED
from ._common import build_facts

ID = "horizon"
TITLE: dict[str, str] = {"fr-CA": "Horizon", "en-CA": "Horizon"}
QUESTION: dict[str, str] = {"fr-CA": "Quand l'argent sera-t-il requis?", "en-CA": "When will the money be needed?"}
FIELDS: tuple[str, ...] = (
    "objectives.horizon_years",
    "objectives.effective_horizon_years",
    "situation.planned_withdrawals",
    "identity.retirement_target_age",
)
METRICS: tuple[str, ...] = ("p5_T", "p50_T", "p95_T", "width")
REQUIRED: tuple[str, ...] = tuple(_REQUIRED[ID])    # intake checklist (§20.6)
NOT_COVERED: tuple[str, ...] = ()


def facts(ctx) -> dict:
    """Section facts payload (§20.4). Pure."""
    return build_facts(sys.modules[__name__], ctx)
