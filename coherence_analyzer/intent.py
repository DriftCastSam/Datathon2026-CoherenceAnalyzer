"""Apply a confirmed intent to a portfolio (SPEC §3.6). P0 step 5.

Funding kinds: specific | pro_rata_all | pro_rata_class | cash_contribution.
"""
from __future__ import annotations

FUNDING_KINDS = ("specific", "pro_rata_all", "pro_rata_class", "cash_contribution")


def apply(weights_pct: dict[str, float], intent: dict, value_cad: float) -> tuple[dict[str, float], float]:
    """Return (new weights_pct summing to 100, new value_cad)."""
    raise NotImplementedError
