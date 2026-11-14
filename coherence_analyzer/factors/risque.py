"""Factor view: risque (docs/spec-factors.md §20.2-20.3). A view, not a second implementation: names what belongs to
the factor and builds its section facts. Rules stay in rules/, checks in coherence/."""
from __future__ import annotations

import sys

from ..intake import REQUIRED as _REQUIRED
from ._common import build_facts

ID = "risque"
TITLE: dict[str, str] = {"fr-CA": "Risque", "en-CA": "Risk"}
QUESTION: dict[str, str] = {"fr-CA": "Quelle perte peut être absorbée?", "en-CA": "What loss can be absorbed?"}
FIELDS: tuple[str, ...] = (
    "risk.declared",
    "risk.capacity",
    "risk.effective",
    "risk.behavioural",
    "risk.max_acceptable_drawdown_pct",
    "risk.reaction_to_20pct_drop",
)
METRICS: tuple[str, ...] = ("vol", "mdd", "loss_prob")
REQUIRED: tuple[str, ...] = tuple(_REQUIRED[ID])    # intake checklist (§20.6)
NOT_COVERED: tuple[str, ...] = ()


def facts(ctx) -> dict:
    """Section facts payload (§20.4). Pure."""
    return build_facts(sys.modules[__name__], ctx)
