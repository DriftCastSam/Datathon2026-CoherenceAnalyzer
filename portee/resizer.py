"""What fits within the user's own limits (SPEC §8). Facts about the profile, not advice. P0 step 5."""
from __future__ import annotations


def resize(portfolio: dict, intent: dict, profile: dict, market, meta: dict) -> dict:
    """Return {"options": [{funding, max_weight_pct, binding_check, metrics}, ...<=3]}
    or {"none_fit": True, "binding_checks": [...]}."""
    raise NotImplementedError
