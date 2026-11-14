"""Aggregation, verdict, diff (SPEC §7.5, §13). Flow C report: `build_review`; Flow B report: `build_trade_check`."""
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
                 data_window: list[str], disclaimer: str, profile_coherence: dict,
                 goals: dict | None = None) -> dict:
    """The Flow C report (SPEC §13 + §20.4): a baseline for the current portfolio, so no intent, no proposed side, no
    diff and no alternatives (the resizer and the trade diff are P2, D7 point 3). `checks` are the tagged checks;
    `sections` already carry their `narrative`. Pure: the verdict comes from the checks and must equal the one derived
    from the section statuses."""
    verdict_ = verdict(checks)
    checks = sort_checks(checks)
    if sections_verdict(sections) != verdict_:
        raise AssertionError(f"section statuses give {sections_verdict(sections)!r}, checks give {verdict_!r}")
    rep = {
        "run_id": run_id, "flow": "C", "profile_id": profile_id, "profile_version": profile_version,
        "portfolio_id": portfolio_id, "intent": None, "verdict": verdict_,
        "checks_current": checks, "checks_proposed": None, "diff": None,
        "metrics_current": metrics, "metrics_proposed": None, "alternatives": [],
        "unknowns": unknowns(checks), "assumptions": assumptions, "data_window": data_window,
        "explanation": explanation, "disclaimer": disclaimer, "sections": sections,
        "profile_coherence": profile_coherence,      # separate block (§20.9): never enters the verdict
    }
    if goals is not None:
        rep["goals"] = goals                          # G1 block (P2.14): info only, never enters the verdict
    return rep


DIFF_METRICS = ("vol", "mdd", "p5_T", "width", "loss_prob")     # SPEC §7.5
DIFF_DRIVERS = 3
_DELTA_DECIMALS = 10                                              # strips float noise, far below the 1e-9 tests use
_WORST = {"violation": 4, "warning": 3, "unknown": 2, "info": 1, "pass": 0}


def _worst_by_id(checks: list[Check]) -> dict[str, Check]:
    """One representative check per id: the worst status (first in list order on ties). An id such as H3 or H10 has
    several results (per ticker / per sector); the diff compares them as one."""
    out: dict[str, Check] = {}
    for c in checks:
        cur = out.get(c["id"])
        if cur is None or _WORST[c["status"]] > _WORST[cur["status"]]:
            out[c["id"]] = c
    return out


def _driver_view(d: dict, before: dict | None) -> dict:
    return {"ticker": d["ticker"], "weight": float(d["weight"]), "risk": float(d["risk"]),
            "weight_current": float(before["weight"]) if before else 0.0,
            "risk_current": float(before["risk"]) if before else 0.0}


def diff(current: list[Check], proposed: list[Check], m_cur: dict, m_new: dict) -> dict:
    """Checks whose status flipped + metric deltas (vol, mdd, p5_T, width, loss_prob, top-3 drivers). SPEC §7.5.

    `flipped`: {id, from, to, subject} per check id present on both sides whose worst status changed (a check that
    exists on one side only, e.g. S3 which is attached to the trade, is not a flip), in the proposed list's order.
    `metrics_delta`: proposed - current as plain numbers, plus `drivers` = the proposed top-3 by risk contribution
    with their current weight and risk. Pure: inputs are not mutated, the result is JSON-serialisable."""
    a, b = _worst_by_id(current), _worst_by_id(proposed)
    flipped = [{"id": cid, "from": a[cid]["status"], "to": c["status"], "subject": c.get("subject")}
               for cid, c in b.items() if cid in a and a[cid]["status"] != c["status"]]
    delta: dict = {k: round(float(m_new[k]) - float(m_cur[k]), _DELTA_DECIMALS) for k in DIFF_METRICS}
    before = {d["ticker"]: d for d in m_cur.get("drivers", [])}
    top = sorted(m_new.get("drivers", []), key=lambda d: -float(d["risk"]))[:DIFF_DRIVERS]
    delta["drivers"] = [_driver_view(d, before.get(d["ticker"])) for d in top]
    return {"flipped": flipped, "metrics_delta": delta}


def build_trade_check(*, run_id: str, profile_id: str, profile_version: int, portfolio_id: str, intent: dict,
                      checks_current: list[dict], checks_proposed: list[dict], metrics_current: dict,
                      metrics_proposed: dict, sections: list[dict], explanation: dict, alternatives: list[dict],
                      assumptions: list[str], data_window: list[str], disclaimer: str,
                      profile_coherence: dict) -> dict:
    """The Flow B report (SPEC §13 + §20.4): the confirmed intent, both sides, the diff, the resizer options. The
    verdict is computed on the PROPOSED checks (the question is whether the trade is coherent) and must equal the one
    derived from the section statuses. `unknowns` = the proposed checks with status unknown. Pure."""
    verdict_ = verdict(checks_proposed)
    if sections_verdict(sections) != verdict_:
        raise AssertionError(f"section statuses give {sections_verdict(sections)!r}, checks give {verdict_!r}")
    d = diff(checks_current, checks_proposed, metrics_current, metrics_proposed)
    cur, new = sort_checks(checks_current), sort_checks(checks_proposed)
    return {
        "run_id": run_id, "flow": "B", "profile_id": profile_id, "profile_version": profile_version,
        "portfolio_id": portfolio_id, "intent": intent, "verdict": verdict_,
        "checks_current": cur, "checks_proposed": new, "diff": d,
        "metrics_current": metrics_current, "metrics_proposed": metrics_proposed, "alternatives": alternatives,
        "unknowns": unknowns(new), "assumptions": assumptions, "data_window": data_window,
        "explanation": explanation, "disclaimer": disclaimer, "sections": sections,
        "profile_coherence": profile_coherence,
    }
