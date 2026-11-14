"""@rule registry (SPEC §7.1). Records each rule's input profile paths.

The registry builds `limits_trace` and lets tests/test_limits_no_llm_inputs.py enforce
invariant 2: no `llm:` field may feed `limits`, directly or transitively.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Rule:
    name: str
    output: str              # profile path written, e.g. "risk.capacity"
    inputs: tuple[str, ...]  # profile paths read
    fn: Callable[..., Any]


REGISTRY: dict[str, Rule] = {}


def rule(name: str, output: str, inputs: list[str]):
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        REGISTRY[output] = Rule(name, output, tuple(inputs), fn)
        return fn
    return deco


def expand(path: str, seen: set[str] | None = None) -> list[str]:
    """All profile paths `path` depends on, fully expanded through registered rules (order-stable)."""
    seen = set() if seen is None else seen
    out: list[str] = []
    r = REGISTRY.get(path)
    if r is None:
        return out
    for p in r.inputs:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
        out.extend(expand(p, seen))
    return out
