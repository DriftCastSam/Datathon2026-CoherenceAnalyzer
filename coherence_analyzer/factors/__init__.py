"""Five profile factors: mapping, section builder (docs/spec-factors.md §20.2-20.4).

Factor modules are views (§20.3): `build_sections` only assembles what each module's `facts()` builds. The section
status is the worst status of the factor's checks in the checks the report is about (proposed in flow B, current in
flow C); `unknown` never changes the verdict (D3).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import contraintes, horizon, objectif, preferences, risque
from . import fmt
from ._common import owned_checks
from .mapping import CHECK_FACTOR, COMPUTE_ORDER, DISPLAY_ORDER, factor_of, tag_checks

MODULES = {m.ID: m for m in (objectif, horizon, risque, contraintes, preferences)}

__all__ = ["CHECK_FACTOR", "COMPUTE_ORDER", "DISPLAY_ORDER", "MODULES", "SectionContext", "factor_of", "tag_checks",
           "build_sections"]


@dataclass(frozen=True)
class SectionContext:
    profile: dict
    limits: dict
    limits_trace: dict
    checks_current: list[dict]
    checks_proposed: list[dict] | None = None
    metrics_current: dict = field(default_factory=dict)
    metrics_proposed: dict | None = None
    intent: dict | None = None
    events: list[dict] = field(default_factory=list)
    locale: str = "fr-CA"


def _section(mod, ctx: SectionContext) -> dict:
    loc = fmt.check_locale(ctx.locale)
    facts = mod.facts(ctx)
    ids = list(dict.fromkeys(c["id"] for c in owned_checks(mod.ID, ctx)))
    return {"factor": mod.ID, "title": mod.TITLE[loc], "question": mod.QUESTION[loc], "status": facts["status"],
            "checks": ids, "unknowns": [dict(u) for u in facts["unknowns"]], "not_covered": list(mod.NOT_COVERED),
            "facts": facts}


def build_sections(ctx: SectionContext) -> list[dict]:
    """Five sections in DISPLAY_ORDER (§20.4). Computed in COMPUTE_ORDER (dependencies first), returned in slide order."""
    built = {fid: _section(MODULES[fid], ctx) for fid in COMPUTE_ORDER}
    return [built[fid] for fid in DISPLAY_ORDER]
