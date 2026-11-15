"""Factor view: objectif (docs/spec-factors.md §20.2-20.3). A view, not a second implementation: names what belongs to
the factor and builds its section facts. Rules stay in rules/, checks in coherence/."""
from __future__ import annotations

import sys

from ..intake import REQUIRED as _REQUIRED
from . import fmt
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
NOT_COVERED: tuple[str, ...] = ()   # objective_vs_portfolio is covered by O1 (hard.run_all)


def facts(ctx) -> dict:
    """Section facts payload (§20.4). Pure. G1 rows (goal probabilities, always info) are shown under `goals` with
    their assumption labels; they never enter `status` or the verdict (§20.8, P2.14)."""
    out = build_facts(sys.modules[__name__], ctx)
    goal_checks = [c for c in getattr(ctx, "goal_checks", None) or [] if c.get("id") == "G1"]
    if goal_checks:
        loc = fmt.check_locale(ctx.locale)
        out["goals"] = [
            {"name": c.get("subject"), "date": c.get("date"), "amount": fmt.amount(c["amount"], loc),
             "probability": None if c.get("value") is None else fmt.percent(c["value"], loc),
             "status": c["status"], "check": c["id"]} for c in goal_checks]
        out["goal_assumptions"] = list(dict.fromkeys(a for c in goal_checks for a in c.get("assumptions", [])))
    return out
