"""Derived `rule:` fields (SPEC §7.1).

Each function is pure and registered with @rule so limits_trace can be generated from the
registry. `derive_all` reads exactly the paths each rule declares and never mutates its input.
"""
from __future__ import annotations

import copy
from typing import Any

from .. import config
from ..levels import LEVELS, idx, lower, step
from .registry import REGISTRY, is_skipped, rule

DECLARED_KNOWLEDGE_MAP: dict[str, float] = {"novice": 20, "intermediate": 50, "advanced": 78, "expert": 95}
KNOWLEDGE_W_QUIZ, KNOWLEDGE_W_DECLARED, KNOWLEDGE_W_QQ = 0.4, 0.3, 0.3
KNOWLEDGE_QQ_MAX_MOVE = 5.0          # points per update (SPEC §7.1, §11 step 4)
KNOWLEDGE_SCORE_CONFIDENCE = 0.85    # rule: field mixing an llm: input -> confidence < 1 (README Assumptions)

NEAR_WITHDRAWAL_MONTHS = 24
NEAR_WITHDRAWAL_SHARE = 0.10
LOW_EF_MONTHS = 3.0

_INCOME_STABILITY = {
    "salaried": "high",
    "self_employed": "medium",
    "contract": "low",
    "commission": "low",
    "unemployed": "low",
    "retired": "low",   # SPEC: retired-without-pension -> low; pension case unspecified, kept conservative
}


# ---------------------------------------------------------------- helpers

def _month_index(date: str) -> int:
    """'YYYY-MM' or 'YYYY-MM-DD' -> absolute month count."""
    y, m = int(date[0:4]), int(date[5:7])
    return y * 12 + (m - 1)


def months_between(as_of: str, date: str) -> int:
    """Whole calendar months from as_of's month to date's month (2026-11 -> 2030-06 = 43)."""
    return _month_index(date) - _month_index(as_of)


def _val(node: Any) -> Any:
    return node["value"] if isinstance(node, dict) and "value" in node else node


def _get(profile: dict, path: str) -> Any:
    node: Any = profile
    for part in path.split("."):
        node = node[part]
    return node


def _set(profile: dict, path: str, field: dict) -> None:
    parts = path.split(".")
    node = profile
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = field


def _field(output: str, value: Any, confidence: float = 1.0) -> dict:
    return {"value": value, "by": f"rule:{REGISTRY[output].name}", "confidence": confidence}


# ---------------------------------------------------------------- rules

@rule("life_stage", "identity.life_stage", ["identity.age", "identity.retirement_target_age"])
def life_stage(age: int, retirement_target_age: int) -> str:
    if age < 35:
        return "early_accumulation"
    if age < 55:
        return "accumulation"
    if age < retirement_target_age:
        return "pre_retirement"
    return "retired"


@rule("income_stability", "situation.income_stability", ["identity.employment_type"])
def income_stability(employment_type: str) -> str:
    try:
        return _INCOME_STABILITY[employment_type]
    except KeyError:
        raise ValueError(f"unknown employment_type: {employment_type!r}") from None


@rule("emergency_fund_months", "situation.emergency_fund_months",
      ["situation.emergency_fund", "situation.monthly_expenses"])
def emergency_fund_months(emergency_fund: float, monthly_expenses: float) -> float:
    if monthly_expenses <= 0:
        raise ValueError("monthly_expenses must be > 0")
    return round(emergency_fund / monthly_expenses, 1)


@rule("effective_horizon", "objectives.effective_horizon_years",
      ["objectives.horizon_years", "situation.planned_withdrawals"])
def effective_horizon_years(horizon_years: float, planned_withdrawals: list[dict], as_of: str) -> float:
    """min(horizon, months to the earliest planned withdrawal / 12), 1 decimal. Past dates count as 0."""
    h = float(horizon_years)
    if planned_withdrawals:
        first = min(max(0, months_between(as_of, w["date"])) for w in planned_withdrawals)
        h = min(h, first / 12)
    return round(h, 1)


def _base_capacity(eh: float) -> str:
    if eh < 3:
        return "low"
    if eh < 5:
        return "low_moderate"
    if eh < 10:
        return "moderate"
    if eh < 15:
        return "moderate_high"
    return "high"


@rule("capacity", "risk.capacity",
      ["objectives.effective_horizon_years", "situation.emergency_fund_months",
       "situation.income_stability", "situation.planned_withdrawals"])
def capacity(effective_horizon_years: float, ef_months: float, income_stability: str,
             planned_withdrawals: list[dict], portfolio_value_cad: float, as_of: str) -> str:
    level = _base_capacity(effective_horizon_years)
    notches = 0
    if ef_months < LOW_EF_MONTHS:
        notches += 1
    if income_stability == "low":
        notches += 1
    near = sum(float(w["amount"]) for w in planned_withdrawals
               if 0 <= months_between(as_of, w["date"]) <= NEAR_WITHDRAWAL_MONTHS)
    if near > NEAR_WITHDRAWAL_SHARE * portfolio_value_cad:
        notches += 1
    return step(level, -notches)


@rule("effective_risk", "risk.effective", ["risk.declared", "risk.capacity"])
def effective_risk(declared: str, capacity_level: str) -> str:
    return lower(declared, capacity_level)


N2_SUBSCORES = ("specificity", "correct_concepts", "risk_awareness", "horizon_awareness")


def question_score(output: dict) -> float:
    """N2 (SPEC §6.2): code computes score = sum of the four 0-3 sub-scores / 12, in 0..1 (4 decimals)."""
    return round(sum(int(output[k]) for k in N2_SUBSCORES) / (3 * len(N2_SUBSCORES)), 4)


def question_quality_ewma(scores: list[float]) -> float | None:
    """EWMA (alpha = config.QUESTION_EWMA_ALPHA) over the question scores, oldest first; the first score seeds it."""
    if not scores:
        return None
    value = float(scores[0])
    for s in scores[1:]:
        value = config.QUESTION_EWMA_ALPHA * float(s) + (1 - config.QUESTION_EWMA_ALPHA) * value
    return value


def question_quality_confidence(n_questions: int) -> float:
    """Code-computed confidence of question_quality: 0.3 + 0.15 per scored question, capped (README Assumptions)."""
    return round(min(config.QUESTION_QUALITY_MAX_CONF, 0.3 + 0.15 * max(0, n_questions)), 4)


def _deterministic_knowledge_points(quiz: float, declared_level: str) -> float:
    try:
        declared = DECLARED_KNOWLEDGE_MAP[declared_level]
    except KeyError:
        raise ValueError(f"unknown knowledge.declared_level: {declared_level!r}") from None
    return KNOWLEDGE_W_QUIZ * quiz * 100 + KNOWLEDGE_W_DECLARED * declared


@rule("knowledge_score", "knowledge.score",
      ["knowledge.quiz_score", "knowledge.declared_level", "knowledge.question_quality"])
def knowledge_score(quiz: float, declared_level: str, question_quality: float | None,
                    previous: float | None) -> float:
    """0.4*quiz*100 + 0.3*declared_map + 0.3*question_quality*100, 1 decimal.

    `previous` is the previous run's question-quality term in points (None = first run, no clamp).
    The question-quality term moves at most KNOWLEDGE_QQ_MAX_MOVE points from it. A missing
    question_quality keeps the previous term (0 on a first run).
    Mixes an llm: input -> NOT an input to limits (only S2).
    """
    base = _deterministic_knowledge_points(quiz, declared_level)
    if question_quality is None:
        qq_term = previous if previous is not None else 0.0
    else:
        qq_term = KNOWLEDGE_W_QQ * question_quality * 100
        if previous is not None:
            qq_term = max(previous - KNOWLEDGE_QQ_MAX_MOVE, min(previous + KNOWLEDGE_QQ_MAX_MOVE, qq_term))
    return round(base + qq_term, 1)


# ---------------------------------------------------------------- derive_all

def derive_all(profile: dict, *, portfolio_value_cad: float, as_of: str) -> dict:
    """Recompute every rule: field and return the updated profile (no mutation).

    A derived field whose input is missing (a skipped intake answer, `{value: null, by: user:skipped}`, or a derived
    input that is itself null) is `{value: null, by: rule:...}`: never a guessed default (§20.6, SPEC §7.1).
    """
    p = copy.deepcopy(profile)
    g = lambda path: _val(_get(p, path))  # noqa: E731

    def put(output: str, fn, *args) -> None:
        missing = any(a is None for a in args)
        _set(p, output, _field(output, None if missing else fn(*args)))

    put("identity.life_stage", life_stage, g("identity.age"), g("identity.retirement_target_age"))
    put("situation.income_stability", income_stability, g("identity.employment_type"))
    put("situation.emergency_fund_months", emergency_fund_months,
        g("situation.emergency_fund"), g("situation.monthly_expenses"))

    wd_node = _get(p, "situation.planned_withdrawals")
    # a skipped list is unknown ("no withdrawal" is the user's answer []); a null list that was never skipped is empty
    withdrawals = None if is_skipped(wd_node) else list(_val(wd_node) or [])
    put("objectives.effective_horizon_years",
        lambda h, w: effective_horizon_years(h, w, as_of), g("objectives.horizon_years"), withdrawals)
    put("risk.capacity",
        lambda eh, efm, stab, w: capacity(eh, efm, stab, w, portfolio_value_cad, as_of),
        g("objectives.effective_horizon_years"), g("situation.emergency_fund_months"),
        g("situation.income_stability"), withdrawals)
    declared = g("risk.declared")
    if declared is not None:
        idx(declared)  # validate the enum (raises ValueError)
    put("risk.effective", effective_risk, declared, g("risk.capacity"))

    _derive_knowledge_score(p, as_of)
    return p


def _derive_knowledge_score(p: dict, as_of: str) -> None:
    k = p.get("knowledge")
    if not k or "quiz_score" not in k or "declared_level" not in k:
        return
    quiz, declared = _val(k["quiz_score"]), _val(k["declared_level"])
    qq_node = k.get("question_quality")
    qq = _val(qq_node) if qq_node is not None else None

    old = k.get("score")
    old_value = _val(old) if old is not None else None
    # SPEC-AMBIGUITY: "previous run" = the stored knowledge.score. Its question-quality term is what
    # remains after removing the current quiz/declared terms.
    previous_qq = None if old_value is None else old_value - _deterministic_knowledge_points(quiz, declared)
    score = knowledge_score(quiz, declared, qq, previous_qq)

    history = list(old.get("history", [])) if isinstance(old, dict) else []
    last = history[-1] if history else None
    if last is None or last.get("date") != as_of or abs(float(last.get("value", 0)) - score) > 1e-9:
        history.append({"date": as_of, "value": score})
    field = _field("knowledge.score", score, KNOWLEDGE_SCORE_CONFIDENCE)
    field["history"] = history
    k["score"] = field


__all__ = ["LEVELS", "life_stage", "income_stability", "emergency_fund_months", "effective_horizon_years",
           "capacity", "effective_risk", "knowledge_score", "derive_all", "months_between"]
