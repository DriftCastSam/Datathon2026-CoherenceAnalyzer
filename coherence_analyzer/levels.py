"""Risk-level ordering + helpers (SPEC §3.3)."""
from __future__ import annotations

LEVELS: tuple[str, ...] = ("low", "low_moderate", "moderate", "moderate_high", "high")


def idx(level: str) -> int:
    return LEVELS.index(level)


def at(i: int) -> str:
    """Level at index i, clamped to the valid range."""
    return LEVELS[max(0, min(len(LEVELS) - 1, i))]


def lower(a: str, b: str) -> str:
    return a if idx(a) <= idx(b) else b


def step(level: str, delta: int) -> str:
    return at(idx(level) + delta)


def clamp_move(old: str | None, new: str, max_step: int = 1) -> str:
    """Move from old toward new by at most max_step levels."""
    if old is None:
        return new
    d = idx(new) - idx(old)
    return step(old, max(-max_step, min(max_step, d)))
