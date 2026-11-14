"""Aggregation, verdict, diff (SPEC §7.5, §13)."""
from __future__ import annotations

from .hard import Check


def verdict_from_statuses(statuses) -> str:
    """§7.5: any violation -> incoherent; else any warning -> coherent_with_warnings; else coherent.
    `unknown` and `info` never change the verdict (D3)."""
    st = set(statuses)
    if "violation" in st:
        return "incoherent"
    if "warning" in st:
        return "coherent_with_warnings"
    return "coherent"


def verdict(checks: list[Check]) -> str:
    return verdict_from_statuses(c["status"] for c in checks)


def sections_verdict(sections: list[dict]) -> str:
    """The verdict derived from the five section statuses (§20.4 consistency); equals `verdict(checks)`."""
    return verdict_from_statuses(s["status"] for s in sections)


def build_sections(ctx) -> list[dict]:
    """The report's `sections` (§20.4): five sections in display order (`factors.build_sections`)."""
    from .. import factors   # lazy: factors imports this module for `verdict`
    return factors.build_sections(ctx)


def tag_checks(checks: list[Check]) -> list[Check]:
    """Copies of the checks with a `factor` key, for `checks_current` / `checks_proposed` (§20.4)."""
    from .. import factors
    return factors.tag_checks(checks)


def unknowns(checks: list[Check]) -> list[Check]:
    return [c for c in checks if c["status"] == "unknown"]


def diff(current: list[Check], proposed: list[Check], m_cur: dict, m_new: dict) -> dict:
    """Checks whose status flipped + metric deltas (vol, mdd, p5_T, width, loss_prob, top-3 drivers)."""
    raise NotImplementedError
