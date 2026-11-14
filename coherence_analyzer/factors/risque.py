"""Factor view: risque (docs/spec-factors.md §20.3). P0 step 7. Stub."""
from __future__ import annotations

ID = "risque"
TITLE: dict[str, str] = {}
QUESTION: dict[str, str] = {}
FIELDS: tuple[str, ...] = ()
METRICS: tuple[str, ...] = ()
REQUIRED: tuple[str, ...] = ()
NOT_COVERED: tuple[str, ...] = ()


def facts(ctx) -> dict:
    """Section facts payload (§20.4). Pure."""
    raise NotImplementedError
