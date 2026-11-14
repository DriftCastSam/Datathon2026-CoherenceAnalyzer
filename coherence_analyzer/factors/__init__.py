"""Five profile factors: mapping, section builder (docs/spec-factors.md §20.2-20.4). P0 step 7. Stub."""
from __future__ import annotations

from dataclasses import dataclass, field

DISPLAY_ORDER = ("objectif", "horizon", "risque", "contraintes", "preferences")
COMPUTE_ORDER = ("horizon", "contraintes", "risque", "objectif", "preferences")
CHECK_FACTOR: dict[str, str] = {}   # check id -> factor id (§20.2 table), filled in P0.7


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


def factor_of(check_id: str) -> str:
    raise NotImplementedError


def tag_checks(checks: list[dict]) -> list[dict]:
    """Return copies of the checks with a `factor` key (hard.py / soft.py stay unchanged)."""
    raise NotImplementedError


def build_sections(ctx: SectionContext) -> list[dict]:
    """Five sections in DISPLAY_ORDER (§20.4)."""
    raise NotImplementedError
