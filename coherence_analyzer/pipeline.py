"""Flows A (profile refresh), B (trade check), C (portfolio review) (SPEC §4). P0 step 8."""
from __future__ import annotations

from pathlib import Path


def refresh(fixture_dir: Path, *, upto_event_id: str | None = None) -> dict:
    """Flow A: derive -> LLM extractors -> gate -> derive -> limits -> validate/save/audit."""
    raise NotImplementedError


def check(fixture_dir: Path, intent_id: str, *, auto_confirm: bool = False) -> dict:
    """Flow B: B1 intent_parser ... B9 report + audit."""
    raise NotImplementedError


def review(fixture_dir: Path) -> dict:
    """Flow C: B5-B9 without an intent."""
    raise NotImplementedError
