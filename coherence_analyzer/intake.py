"""Intake per factor: deterministic checklist + fixed questions (docs/spec-factors.md §20.6, D7). P0 step 10. Stub."""
from __future__ import annotations


def missing(profile: dict, factor: str) -> list[str]:
    raise NotImplementedError


def next_question(profile: dict, factor: str, locale: str = "fr-CA") -> dict | None:
    raise NotImplementedError


def skip(profile: dict, path: str) -> dict:
    """Mark a REQUIRED path as {value: None, by: 'user:skipped'}; returns a new profile."""
    raise NotImplementedError
