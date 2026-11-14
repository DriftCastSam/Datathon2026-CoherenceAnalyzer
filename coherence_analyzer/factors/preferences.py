"""Factor view: preferences (docs/spec-factors.md §20.2-20.3). A view, not a second implementation: names what belongs
to the factor and builds its section facts. Rules stay in rules/, checks in coherence/.

Preferences orient the choices inside the limits: importance scores, notes, leanings, and every parsed rule that is not
an approved limit rule (unapproved or non-checkable, S4). They can only ever produce `info`."""
from __future__ import annotations

import sys

from ..intake import REQUIRED as _REQUIRED
from ._common import build_facts

ID = "preferences"
TITLE: dict[str, str] = {"fr-CA": "Préférences", "en-CA": "Preferences"}
QUESTION: dict[str, str] = {"fr-CA": "Qu'est-ce qui oriente les choix?", "en-CA": "What guides the choices?"}
FIELDS: tuple[str, ...] = (
    "values.*.importance",
    "values.notes",
    "preferences.prefers_dividends",
    "preferences.prefers_large_caps",
    "preferences.home_bias_ok",
    "preferences.interests",
    "values.parsed_notes[other_rules]",        # unapproved or non-checkable parsed rules
)
METRICS: tuple[str, ...] = ()
REQUIRED: tuple[str, ...] = tuple(_REQUIRED[ID])    # intake checklist (§20.6)
NOT_COVERED: tuple[str, ...] = ()


def facts(ctx) -> dict:
    """Section facts payload (§20.4). Pure."""
    return build_facts(sys.modules[__name__], ctx)
