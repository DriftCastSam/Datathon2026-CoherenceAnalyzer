"""Derived `rule:` fields (SPEC §7.1). P0 step 2.

Each function is pure and registered with @rule so limits_trace can be generated.
"""
from __future__ import annotations

from .registry import rule


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
    raise NotImplementedError


@rule("emergency_fund_months", "situation.emergency_fund_months",
      ["situation.emergency_fund", "situation.monthly_expenses"])
def emergency_fund_months(emergency_fund: float, monthly_expenses: float) -> float:
    return round(emergency_fund / monthly_expenses, 1)


@rule("effective_horizon", "objectives.effective_horizon_years",
      ["objectives.horizon_years", "situation.planned_withdrawals"])
def effective_horizon_years(horizon_years: float, planned_withdrawals: list[dict], as_of: str) -> float:
    raise NotImplementedError


@rule("capacity", "risk.capacity",
      ["objectives.effective_horizon_years", "situation.emergency_fund_months",
       "situation.income_stability", "situation.planned_withdrawals"])
def capacity(effective_horizon_years: float, ef_months: float, income_stability: str,
             planned_withdrawals: list[dict], portfolio_value_cad: float, as_of: str) -> str:
    raise NotImplementedError


@rule("effective_risk", "risk.effective", ["risk.declared", "risk.capacity"])
def effective_risk(declared: str, capacity_level: str) -> str:
    from ..levels import lower
    return lower(declared, capacity_level)


@rule("knowledge_score", "knowledge.score",
      ["knowledge.quiz_score", "knowledge.declared_level", "knowledge.question_quality"])
def knowledge_score(quiz: float, declared_level: str, question_quality: float | None,
                    previous: float | None) -> float:
    """Mixes an llm: input -> NOT an input to limits (only S2). LLM term moves <= 5 pts per update."""
    raise NotImplementedError


def derive_all(profile: dict, *, portfolio_value_cad: float, as_of: str) -> dict:
    """Recompute every rule: field and return the updated profile (no mutation)."""
    raise NotImplementedError
