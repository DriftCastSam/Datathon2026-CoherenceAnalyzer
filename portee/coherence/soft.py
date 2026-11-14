"""Soft checks S1-S4 (SPEC §7.4). Never `violation` (invariant 3). P1 step 9."""
from __future__ import annotations

from .hard import Check

ALLOWED = {"pass", "warning", "info", "unknown"}


def run_all(profile: dict, intent: dict | None) -> list[Check]:
    raise NotImplementedError
