"""One function per LLM node (SPEC §6.2). Each returns raw validated output; gating happens in portee.gate."""
from __future__ import annotations


def behaviour_classifier(events: list[dict]) -> dict:
    """N1. Input: last 30 days of question/order_intent events (max 20). No profile fields."""
    raise NotImplementedError


def question_rubric(event: dict) -> dict:
    """N2. Sub-scores only; code computes score = sum/12."""
    raise NotImplementedError


def values_parser(notes: str) -> dict:
    """N3. Rules with exact-substring quotes; start unapproved."""
    raise NotImplementedError


def intent_parser(text: str, universe: list[dict], weights_pct: dict[str, float]) -> dict:
    """N4. Draft intent; ticker enum generated from the universe at runtime."""
    raise NotImplementedError


def explainer(payload: dict) -> dict:
    """N5. See portee.explain."""
    raise NotImplementedError
