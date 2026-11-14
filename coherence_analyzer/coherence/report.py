"""Aggregation, verdict, diff (SPEC §7.5, §13)."""
from __future__ import annotations

from .hard import Check


def verdict(checks: list[Check]) -> str:
    statuses = {c["status"] for c in checks}
    if "violation" in statuses:
        return "incoherent"
    if "warning" in statuses:
        return "coherent_with_warnings"
    return "coherent"


def unknowns(checks: list[Check]) -> list[Check]:
    return [c for c in checks if c["status"] == "unknown"]


def diff(current: list[Check], proposed: list[Check], m_cur: dict, m_new: dict) -> dict:
    """Checks whose status flipped + metric deltas (vol, mdd, p5_T, width, loss_prob, top-3 drivers)."""
    raise NotImplementedError
