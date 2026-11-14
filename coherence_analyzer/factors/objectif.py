"""Factor view: objectif (docs/spec-factors.md §20.2-20.3). A view, not a second implementation: names what belongs to
the factor and builds its section facts. Rules stay in rules/, checks in coherence/."""
from __future__ import annotations

import sys

from ..intake import REQUIRED as _REQUIRED
from ._common import build_facts

ID = "objectif"
TITLE: dict[str, str] = {"fr-CA": "Objectif", "en-CA": "Objective"}
QUESTION: dict[str, str] = {"fr-CA": "Pourquoi investir?", "en-CA": "Why invest?"}
FIELDS: tuple[str, ...] = (
    "objectives.primary",
    "objectives.secondary",
    "objectives.goals",
    "identity.life_stage",
)
METRICS: tuple[str, ...] = ("p50_T",)
REQUIRED: tuple[str, ...] = tuple(_REQUIRED[ID])    # intake checklist (§20.6)
NOT_COVERED: tuple[str, ...] = ("objective_vs_portfolio",)   # until O1 lands


def facts(ctx) -> dict:
    """Section facts payload (§20.4). Pure."""
    return build_facts(sys.modules[__name__], ctx)
