"""Aggregation, verdict, diff (SPEC §7.5, §13). Flow C report: `build_review`; Flow B report: `build_trade_check`."""
from __future__ import annotations

from .hard import Check
from ..numfacts import tokenize

NOTES_SOURCE = "values.notes"       # D13 (<source>) of a quote taken from a parsed note rule: the user's own notes


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


class _Sections(list):
    """The five sections, carrying the profile's `origin` (D16) so the disclaimer picked by `explain.explain` matches the
    profile. A plain list everywhere else (JSON, equality, copies)."""
    origin: str | None = None


def build_sections(ctx) -> list[dict]:
    """The report's `sections` (§20.4): five sections in display order (`factors.build_sections`)."""
    from .. import factors   # lazy: factors imports this module for `verdict`
    out = _Sections(factors.build_sections(ctx))
    out.origin = ((getattr(ctx, "profile", None) or {}).get("meta") or {}).get("origin")
    return out


def _pick_disclaimer(disclaimer: str, explanation: dict) -> str:
    """D16: the explanation already carries the disclaimer of the profile's origin (`explain.explain`); when the caller
    passed the seed text for the same locale and the explanation holds the user text, the report carries the user
    text too (`disclaimer` and `explanation.disclaimer` stay equal). Anything else: the caller's text."""
    from .. import config
    mine = (explanation or {}).get("disclaimer")
    for loc, seed in config.DISCLAIMER.items():
        if disclaimer == seed and mine == config.DISCLAIMER_USER[loc]:
            return mine
    return disclaimer


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
    diff and no headroom (the resizer and the trade diff are P2, D7 point 3). `checks` are the tagged checks;
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
        "metrics_current": metrics, "metrics_proposed": None, "headroom": None,
        "unknowns": unknowns(checks), "assumptions": assumptions, "data_window": data_window,
        "explanation": explanation, "disclaimer": _pick_disclaimer(disclaimer, explanation), "sections": sections,
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


def _note_rules(sections: list[dict]) -> list[dict]:
    """Parsed note rules shown in the sections' facts (verbatim `quote`), in section order, without duplicates."""
    out: list[dict] = []
    for s in sections:
        node = ((s.get("facts") or {}).get("derived") or {}).get("values.parsed_notes")
        for r in (node or {}).get("value") or []:
            if isinstance(r, dict) and isinstance(r.get("quote"), str) and r not in out:
                out.append(r)
    return out


def _limit_quote(c: dict, rules: list[dict]) -> tuple[str, str] | None:
    """D13 point 2: (verbatim quote, source) when the limit traces to the user's own words, i.e. its `derived_from`
    names `values.parsed_notes` and a parsed rule matches the check (H13: the theme; H11: the activity tag).
    Several rules on one theme: the one whose number is the check's limit, else the first. No match: None."""
    if not any(str(p).startswith("values.parsed_notes") for p in c.get("derived_from", [])):
        return None
    if c["id"] == "H13":
        cands = [r for r in rules if r.get("rule_kind") == "max_theme_weight"
                 and (r.get("params") or {}).get("theme") == c.get("subject")]
        lim = c.get("limit")

        def matches(r: dict) -> bool:
            pct = (r.get("params") or {}).get("max_pct")
            toks = tokenize(pct) if isinstance(pct, str) else []
            return lim is not None and bool(toks) and abs(float(toks[0].value) - float(lim) * 100) <= 0.05
        cands = sorted(cands, key=lambda r: not matches(r))      # stable: the matching rule first
    elif c["id"] == "H11":
        cands = [r for r in rules if r.get("rule_kind") == "exclude_activity"
                 and (r.get("params") or {}).get("tag") == c.get("value")]
    else:
        cands = []
    return (cands[0]["quote"], NOTES_SOURCE) if cands else None


def trade_statements(checks_current: list[dict], checks_proposed: list[dict], sections: list[dict]) -> list[dict]:
    """D13: one `{check_id, text, refs}` per hard limit the trade crosses: the set the headroom uses, i.e.
    `resizer.violations_added` (violations added or worsened, D14), one statement per id, in the order of the
    proposed list. Status and verdict are untouched. Pure: numbers are the checks' own values, the quote is the
    parsed rule's verbatim quote, the locale comes from the sections' facts."""
    from .. import explain, resizer
    locale = next((s["facts"]["locale"] for s in sections if (s.get("facts") or {}).get("locale")), "fr-CA")
    rules = _note_rules(sections)
    out: list[dict] = []
    for cid in resizer.violations_added(checks_current, checks_proposed):
        c = next(c for c in checks_proposed if c["id"] == cid and c["status"] == "violation"
                 and resizer.violations_added(checks_current, [c]))
        was = resizer._key(c)
        before = next((b for b in checks_current if b["status"] == "violation" and resizer._key(b) == was), None)
        quote = _limit_quote(c, rules)
        refs = [cid] + ([quote[0]] if quote else [])
        out.append({"check_id": cid, "text": explain.trade_statement(c, before, locale, quote), "refs": refs})
    return out


def build_trade_check(*, run_id: str, profile_id: str, profile_version: int, portfolio_id: str, intent: dict,
                      checks_current: list[dict], checks_proposed: list[dict], metrics_current: dict,
                      metrics_proposed: dict, sections: list[dict], explanation: dict, headroom: dict | None,
                      assumptions: list[str], data_window: list[str], disclaimer: str,
                      profile_coherence: dict, goals: dict | None = None, stress: dict | None = None) -> dict:
    """The Flow B report (SPEC §13 + §20.4): the confirmed intent, both sides, the diff, the resizer headroom (user funding only, D10). The
    verdict is computed on the PROPOSED checks (the question is whether the trade is coherent) and must equal the one
    derived from the section statuses. `unknowns` = the proposed checks with status unknown. Pure."""
    verdict_ = verdict(checks_proposed)
    if sections_verdict(sections) != verdict_:
        raise AssertionError(f"section statuses give {sections_verdict(sections)!r}, checks give {verdict_!r}")
    d = diff(checks_current, checks_proposed, metrics_current, metrics_proposed)
    cur, new = sort_checks(checks_current), sort_checks(checks_proposed)
    rep = {
        "run_id": run_id, "flow": "B", "profile_id": profile_id, "profile_version": profile_version,
        "portfolio_id": portfolio_id, "intent": intent, "verdict": verdict_,
        "checks_current": cur, "checks_proposed": new, "diff": d,
        "metrics_current": metrics_current, "metrics_proposed": metrics_proposed, "headroom": headroom,
        "unknowns": unknowns(new), "assumptions": assumptions, "data_window": data_window,
        "explanation": explanation, "disclaimer": _pick_disclaimer(disclaimer, explanation), "sections": sections,
        "profile_coherence": profile_coherence, "trade_statements": trade_statements(
            checks_current, checks_proposed, sections),
    }
    if goals is not None:
        rep["goals"] = goals          # G1 block (P2.14), proposed side: info only, never in the verdict
    if stress is not None:
        rep["stress"] = stress        # rate stress (P2.16), proposed side: info only, feeds_limits false
    return rep
