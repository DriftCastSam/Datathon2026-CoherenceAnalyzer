"""Internal coherence: does the profile contradict itself? (docs/spec-factors.md §20.9, D8).

Profile only (no portfolio). Layer 1 = contradiction rules C1-C5 (code); layer 2 = N6 `profile_consistency` (LLM,
gated). Never `violation`; never feeds `limits`; does not change the portfolio verdict. P0.11.
"""
from __future__ import annotations

import copy
from typing import Any

from ..gate import PROFILE_SECTIONS, gate
from ..levels import LEVELS
from ..llm import bedrock, extractors
from ..rules.limits import LIMITS_TABLE
from ..rules.registry import is_skipped

RULE_IDS: tuple[str, ...] = ("C1", "C2", "C3", "C4", "C5")
GROWTH_OBJECTIVES: frozenset[str] = frozenset({"growth", "aggressive_growth"})
MAX_FINDINGS = 5
MAX_REASON_CHARS = 200
KIND_STATUS: dict[str, str] = {"contradiction": "warning", "tension": "info"}
STATUS_RANK = {"pass": 0, "info": 1, "warning": 2}

# One fixed clarification question per C-rule (fr-CA / en-CA). Written by code, never by the LLM (D7 point 1).
RULE_QUESTIONS: dict[str, dict[str, str]] = {
    "C1": {"fr-CA": "Un de vos objectifs tombe après la fin de votre horizon de placement. Quelle date ou quel horizon est le bon?",
           "en-CA": "One of your goals falls after the end of your investment horizon. Which date or horizon is correct?"},
    "C2": {"fr-CA": "Votre baisse maximale acceptable ne cadre pas avec votre niveau de risque déclaré. Lequel des deux reflète le mieux votre situation?",
           "en-CA": "Your maximum acceptable drop does not fit your declared risk level. Which of the two reflects you better?"},
    "C3": {"fr-CA": "Votre objectif principal est la croissance, mais votre niveau de risque déclaré est bas. Lequel voulez-vous garder?",
           "en-CA": "Your main objective is growth, but your declared risk level is low. Which one do you want to keep?"},
    "C4": {"fr-CA": "Un même type d'actif figure à la fois dans vos types acceptés et dans vos types refusés. Dans quelle liste doit-il se trouver?",
           "en-CA": "The same asset type is both in your allowed and in your refused types. Which list should it be in?"},
    "C5": {"fr-CA": "Votre réaction à une baisse de 20 % ne cadre pas avec votre niveau de risque déclaré. Laquelle des deux réponses est la bonne?",
           "en-CA": "Your reaction to a 20% drop does not fit your declared risk level. Which of the two answers is right?"},
}


def _n6_question(fields: list[str]) -> dict[str, str]:
    a, b = fields[0], fields[1]
    return {"fr-CA": f"Vos réponses « {a} » et « {b} » semblent aller dans des directions différentes. Pouvez-vous préciser ce que vous voulez?",
            "en-CA": f"Your answers for \"{a}\" and \"{b}\" seem to pull in different directions. Can you clarify what you want?"}


# ---------------------------------------------------------------- layer 1: rules

def _dig(profile: dict, *path: str) -> Any:
    node: Any = profile
    for part in path:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return None if is_skipped(node) else node       # a skipped answer is no value: the rule is skipped (§20.6)


def _is_num(x: object) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _month_index(ym: str) -> int | None:
    """'YYYY-MM' (or a longer ISO date) -> year * 12 + month - 1; None when it does not parse."""
    try:
        year, month = int(ym[0:4]), int(ym[5:7])
    except (TypeError, ValueError):
        return None
    return year * 12 + month - 1 if 1 <= month <= 12 else None


def _finding(rule: str, fields: list[str], values: list[Any]) -> dict:
    return {"id": rule, "source": f"rule:{rule}", "kind": "contradiction", "status": KIND_STATUS["contradiction"],
            "fields": fields, "values": copy.deepcopy(values), "reason": None,
            "question": dict(RULE_QUESTIONS[rule])}


def _c1(profile: dict, as_of: str) -> list[dict]:
    horizon, goals = _dig(profile, "objectives", "horizon_years"), _dig(profile, "objectives", "goals")
    now = _month_index(as_of)
    if not _is_num(horizon) or not isinstance(goals, list) or now is None:
        return []
    end = now + round(horizon * 12)
    out = []
    for i, goal in enumerate(goals):
        date = goal.get("date") if isinstance(goal, dict) else None
        idx = _month_index(date) if isinstance(date, str) else None
        if idx is not None and idx > end:
            out.append(_finding("C1", ["objectives.horizon_years", f"objectives.goals[{i}].date"], [horizon, date]))
    return out


def _c2(profile: dict) -> list[dict]:
    declared, dd = _dig(profile, "risk", "declared"), _dig(profile, "risk", "max_acceptable_drawdown_pct")
    if declared not in LEVELS or not _is_num(dd):
        return []
    i = LEVELS.index(declared)
    tighter = i > 0 and dd > LIMITS_TABLE[LEVELS[i - 1]]["max_dd_pct"]
    looser = i < len(LEVELS) - 1 and dd < LIMITS_TABLE[LEVELS[i + 1]]["max_dd_pct"]
    if tighter or looser:
        return [_finding("C2", ["risk.declared", "risk.max_acceptable_drawdown_pct"], [declared, dd])]
    return []


def _c3(profile: dict) -> list[dict]:
    primary, declared = _dig(profile, "objectives", "primary"), _dig(profile, "risk", "declared")
    if primary in GROWTH_OBJECTIVES and declared in ("low", "low_moderate"):
        return [_finding("C3", ["objectives.primary", "risk.declared"], [primary, declared])]
    return []


def _c4(profile: dict) -> list[dict]:
    allowed = _dig(profile, "preferences", "asset_types_allowed")
    refused = _dig(profile, "preferences", "asset_types_refused")
    if not isinstance(allowed, list) or not isinstance(refused, list):
        return []
    if set(map(str, allowed)) & set(map(str, refused)):
        return [_finding("C4", ["preferences.asset_types_allowed", "preferences.asset_types_refused"],
                         [allowed, refused])]
    return []


def _c5(profile: dict) -> list[dict]:
    reaction, declared = _dig(profile, "risk", "reaction_to_20pct_drop"), _dig(profile, "risk", "declared")
    if declared not in LEVELS:
        return []
    i = LEVELS.index(declared)
    if (reaction == "buy_more" and i <= LEVELS.index("low_moderate")) or \
            (reaction == "sell_all" and i >= LEVELS.index("moderate_high")):
        return [_finding("C5", ["risk.reaction_to_20pct_drop", "risk.declared"], [reaction, declared])]
    return []


def rule_findings(profile: dict, as_of: str) -> list[dict]:
    """C1-C5 findings, in rule order (C1 may yield one finding per goal). A missing field skips the rule."""
    return _c1(profile, as_of) + _c2(profile) + _c3(profile) + _c4(profile) + _c5(profile)


# ---------------------------------------------------------------- layer 2: N6

def _declared(node: Any) -> Any:
    """Strip every computed / llm-derived `{value, by, ...}` object (recursively); keep declared plain values."""
    if isinstance(node, dict):
        out: dict = {}
        for k, v in node.items():
            if not (isinstance(v, dict) and "by" in v):
                out[k] = _declared(v)
        return out
    if isinstance(node, list):
        return [_declared(v) for v in node if not (isinstance(v, dict) and "by" in v)]
    return copy.deepcopy(node)


def n6_payload(profile: dict, rule_hits: list[dict]) -> dict:
    """Declared fields only + values.notes + the rule findings already raised (ids + fields)."""
    sections = {s: _declared(profile[s]) for s in PROFILE_SECTIONS if isinstance(profile.get(s), dict)}
    return {"profile": sections,
            "rule_hits": [{"id": h["id"], "fields": list(h["fields"])} for h in rule_hits]}


def _n6_finding(k: int, f: dict) -> dict:
    return {"id": f"N6.{k}", "source": "llm:profile_consistency", "kind": f["kind"], "status": KIND_STATUS[f["kind"]],
            "fields": list(f["fields"]), "quotes": list(f["quotes"]), "reason": f["reason"],
            "question": _n6_question(list(f["fields"]))}


def _status(findings: list[dict]) -> str:
    return max((f["status"] for f in findings), key=STATUS_RANK.__getitem__, default="pass")


def check_profile(profile: dict, as_of: str, *, use_llm: bool = True) -> dict:
    """`{status, findings, llm: {gate_status, call_log} | None}`; status = worst finding status or `pass`."""
    findings = rule_findings(profile, as_of)
    if not use_llm:
        return {"status": _status(findings), "findings": findings, "llm": None}

    payload = n6_payload(profile, findings)
    try:
        call = extractors.profile_consistency_call(payload)
    except bedrock.LLMCacheMiss:
        return {"status": _status(findings), "findings": findings,
                "llm": {"gate_status": "skipped:cache_miss", "call_log": None}}
    log = call["call_log"]
    context = {"event_ids": set(), "notes": _dig(profile, "values", "notes") or "", "profile": profile,
               "model_id": log.get("model_id", bedrock.MOCK_MODEL_ID),
               "prompt_version": extractors.prompt_version(extractors.N6_NODE),
               "input_hash": log.get("input_hash") or bedrock.input_hash(payload)}
    new, gate_status = gate(extractors.N6_NODE, None, call["output"], context)
    if gate_status == "accepted" and new is not None:
        rule_sets = {frozenset(f["fields"]) for f in findings}      # rules win on an identical path set
        findings = findings + [_n6_finding(k, f) for k, f in enumerate(new["value"], start=1)
                               if frozenset(f["fields"]) not in rule_sets]
    return {"status": _status(findings), "findings": findings,
            "llm": {"gate_status": gate_status, "call_log": log}}
