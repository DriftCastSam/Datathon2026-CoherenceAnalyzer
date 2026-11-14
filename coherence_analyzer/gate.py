"""Deterministic gate for every LLM output (SPEC §11). P0 step 6.

Order: abstain -> evidence (event_ids exist, quotes are exact substrings of notes) ->
enum/range -> movement clamp -> confidence -> stamp.
"""
from __future__ import annotations

from typing import Literal

GateStatus = Literal["accepted", "clamped", "kept:abstain", "rejected:evidence", "rejected:schema"]


def gate(node: str, old_field: dict | None, llm_output: dict, context: dict) -> tuple[dict | None, GateStatus]:
    """context: {"event_ids": set[str], "notes": str, "model_id", "prompt_version", "input_hash"}."""
    raise NotImplementedError
