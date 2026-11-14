"""Explanation: facts payload -> N5 explainer -> post-checks -> template fallback (SPEC §9). P0 step 7."""
from __future__ import annotations

from .config import DISCLAIMER

ADVISORY_BLOCKLIST = ("tu devrais acheter", "tu devrais vendre", "je recommande", "nous recommandons",
                      "you should buy", "you should sell", "we recommend", "i recommend")


def build_payload(report: dict, profile: dict, events: list[dict], locale: str = "fr-CA") -> dict:
    raise NotImplementedError


def numcheck(text: str, payload: dict) -> list[str]:
    """Return numbers in `text` that don't match a payload number (empty list = pass)."""
    raise NotImplementedError


def template_explain(payload: dict) -> dict:
    """Deterministic FR/EN explanation from the same checks. Always available."""
    raise NotImplementedError


def explain(payload: dict) -> dict:
    """llm -> llm_retry -> template. Returns {mode, headline, segments, disclaimer}."""
    raise NotImplementedError


def disclaimer(locale: str = "fr-CA") -> str:
    return DISCLAIMER[locale]
