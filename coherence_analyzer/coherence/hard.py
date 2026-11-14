"""Hard checks H1-H14 + G1 (SPEC §7.3). Only these may return `violation`. P0 step 4."""
from __future__ import annotations

from typing import Literal, TypedDict

Status = Literal["pass", "violation", "info", "unknown"]


class Check(TypedDict, total=False):
    id: str
    kind: Literal["hard", "soft"]
    status: str
    subject: str | None
    value: float | None
    limit: float | None
    unit: str
    derived_from: list[str]
    source: str
    confidence: float
    message_key: str


CHEAP = ("H1", "H2", "H3", "H4", "H5", "H6", "H11", "H12", "H13")  # no engine needed (resizer)
ENGINE = ("H7", "H8", "H9", "H10")


def run_cheap(weights: dict[str, float], profile: dict, assets, meta: dict) -> list[Check]:
    """H1-H6, H11-H13 on fraction weights."""
    raise NotImplementedError


def run_engine(metrics: dict, profile: dict) -> list[Check]:
    """H7-H10, H14, G1 on engine.analyse output."""
    raise NotImplementedError


def run_all(weights: dict[str, float], metrics: dict, profile: dict, assets, meta: dict) -> list[Check]:
    return run_cheap(weights, profile, assets, meta) + run_engine(metrics, profile)
