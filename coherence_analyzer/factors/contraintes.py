"""Factor view: contraintes (docs/spec-factors.md §20.2-20.3). A view, not a second implementation: names what belongs to
the factor and builds its section facts. Rules stay in rules/, checks in coherence/.

ESG split: explicit approved exclusions (`values.*.exclusions`, approved exclude_activity / max_theme_weight rules) are
limits, so they live here; leanings and importance scores live in `preferences`."""
from __future__ import annotations

import sys

from ..intake import REQUIRED as _REQUIRED
from ._common import build_facts

ID = "contraintes"
TITLE: dict[str, str] = {"fr-CA": "Contraintes", "en-CA": "Constraints"}
QUESTION: dict[str, str] = {"fr-CA": "Quelles limites s'appliquent?", "en-CA": "Which limits apply?"}
FIELDS: tuple[str, ...] = (
    "situation.emergency_fund_months",
    "situation.income_stability",
    "identity.employer_sector",
    "preferences.asset_types_refused",
    "preferences.max_fee_mer_pct",
    "knowledge.score",
    "values.*.exclusions",
    "values.parsed_notes[approved_by_user]",   # approved exclude_activity / max_theme_weight rules only
)
METRICS: tuple[str, ...] = ()                  # H10 quotes sector risk shares through its check entry
REQUIRED: tuple[str, ...] = tuple(_REQUIRED[ID])    # intake checklist (§20.6)
NOT_COVERED: tuple[str, ...] = ("fiscalite",)       # account type (TFSA/RRSP/non-registered), US withholding


def facts(ctx) -> dict:
    """Section facts payload (§20.4). Pure."""
    return build_facts(sys.modules[__name__], ctx)
