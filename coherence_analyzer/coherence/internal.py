"""Internal coherence: does the profile contradict itself? (docs/spec-factors.md §20.9, D8).

Profile only (no portfolio). Layer 1 = contradiction rules C1-C5 (code); layer 2 = N6 `profile_consistency` (LLM,
gated). Never `violation`; never feeds `limits`; does not change the portfolio verdict. P0.11.
"""
from __future__ import annotations

RULE_IDS: tuple[str, ...] = ("C1", "C2", "C3", "C4", "C5")
GROWTH_OBJECTIVES: frozenset[str] = frozenset({"growth", "aggressive_growth"})
MAX_FINDINGS = 5
MAX_REASON_CHARS = 200
KIND_STATUS: dict[str, str] = {"contradiction": "warning", "tension": "info"}


def rule_findings(profile: dict, as_of: str) -> list[dict]:
    """C1-C5 findings, in rule order (C1 may yield one finding per goal)."""
    raise NotImplementedError


def n6_payload(profile: dict, rule_hits: list[dict]) -> dict:
    """Declared fields only + values.notes + the rule findings already raised (ids + fields)."""
    raise NotImplementedError


def check_profile(profile: dict, as_of: str, *, use_llm: bool = True) -> dict:
    """`{status, findings, llm: {gate_status, call_log} | None}`; status = worst finding status or `pass`."""
    raise NotImplementedError
