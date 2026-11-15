"""@rule registry (SPEC §7.1). Records each rule's input profile paths.

The registry builds `limits_trace` and lets tests/profile/test_limits_no_llm_inputs.py enforce
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


# ---------------------------------------------------------------- skipped intake answers (docs/spec-factors.md §20.6)

SKIPPED_BY = "user:skipped"      # same marker as intake.SKIPPED_BY: {value: None, by: 'user:skipped'}


def is_skipped(node: Any) -> bool:
    """True for a skipped intake answer. A skip is never a value: whatever needs it is unknown, not defaulted."""
    return isinstance(node, dict) and node.get("by") == SKIPPED_BY


def node_at(profile: dict, path: str) -> Any:
    """Raw node at a dotted path; None when any part is absent."""
    node: Any = profile
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def skipped_paths(profile: dict, paths: list[str] | tuple[str, ...]) -> list[str]:
    """The paths among `paths` (order kept, no duplicates) whose node is a skipped answer."""
    out: list[str] = []
    for p in paths:
        if p not in out and is_skipped(node_at(profile, p)):
            out.append(p)
    return out
