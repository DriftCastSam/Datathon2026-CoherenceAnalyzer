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


STATUS_RANK = {"violation": 0, "warning": 1, "unknown": 2, "info": 3, "pass": 4}   # report order: worst first


def sort_checks(checks: list[Check]) -> list[Check]:
    """Worst status first (violation, warning, unknown, info, pass); stable, so the engine's order is kept inside a
    status. Only the report's check list is ordered this way: section facts keep the checks' native order."""
    return sorted(checks, key=lambda c: STATUS_RANK[c["status"]])


def build_review(*, run_id: str, profile_id: str, profile_version: int, portfolio_id: str, checks: list[dict],
                 metrics: dict, sections: list[dict], explanation: dict, assumptions: list[str],
                 data_window: list[str], disclaimer: str, profile_coherence: dict) -> dict:
    """The Flow C report (SPEC §13 + §20.4): a baseline for the current portfolio, so no intent, no proposed side, no
    diff and no alternatives (the resizer and the trade diff are P2, D7 point 3). `checks` are the tagged checks;
    `sections` already carry their `narrative`. Pure: the verdict comes from the checks and must equal the one derived
    from the section statuses."""
    verdict_ = verdict(checks)
    checks = sort_checks(checks)
    if sections_verdict(sections) != verdict_:
        raise AssertionError(f"section statuses give {sections_verdict(sections)!r}, checks give {verdict_!r}")
    return {
        "run_id": run_id, "flow": "C", "profile_id": profile_id, "profile_version": profile_version,
        "portfolio_id": portfolio_id, "intent": None, "verdict": verdict_,
        "checks_current": checks, "checks_proposed": None, "diff": None,
        "metrics_current": metrics, "metrics_proposed": None, "alternatives": [],
        "unknowns": unknowns(checks), "assumptions": assumptions, "data_window": data_window,
        "explanation": explanation, "disclaimer": disclaimer, "sections": sections,
        "profile_coherence": profile_coherence,      # separate block (§20.9): never enters the verdict
    }


def diff(current: list[Check], proposed: list[Check], m_cur: dict, m_new: dict) -> dict:
    """Checks whose status flipped + metric deltas (vol, mdd, p5_T, width, loss_prob, top-3 drivers)."""
    raise NotImplementedError
