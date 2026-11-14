"""Deterministic gate for every LLM output (SPEC §11). P0 step 6.

Order: abstain -> evidence (event_ids exist, quotes are exact substrings of notes) ->
enum/range -> movement clamp -> confidence -> stamp.

Pure apart from the `updated_at` stamp, which is audit metadata (context["updated_at"] overrides the wall clock).
Rejections and abstentions return a copy of the previous field untouched (SPEC §1 inv. 4).
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Literal

from .config import BEHAVIOUR_MAX_CONF, BEHAVIOUR_MAX_STEP
from .levels import LEVELS, clamp_move

GateStatus = Literal["accepted", "clamped", "kept:abstain", "rejected:evidence", "rejected:schema"]

SIGNALS = ("hype_chasing", "concentration_request", "panic_selling_intent", "loss_aversion", "overtrading",
           "market_timing", "long_term_framing", "diversification_request", "risk_questioning")
RULE_KINDS = ("exclude_activity", "max_theme_weight", "prefer_attribute", "avoid_attribute", "other")
APPLIES_TO = ("all", "single_stocks", "etf")
VALUES_DEFAULT_CONF = 0.8   # N3 emits no confidence; used when there is no previous value to inherit from


def _kept(old_field: dict | None, status: GateStatus) -> tuple[dict | None, GateStatus]:
    return copy.deepcopy(old_field), status


def _is_num(x: object) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _stamp(node: str, context: dict) -> dict:
    return {"by": f"llm:{node}",
            "prompt_version": context["prompt_version"],
            "model_id": context["model_id"],
            "updated_at": context.get("updated_at") or datetime.now().astimezone().isoformat(timespec="seconds"),
            "input_hash": context["input_hash"]}


# ---------------------------------------------------------------- N1 behaviour_classifier

def _behaviour(old_field: dict | None, out: dict, context: dict) -> tuple[dict | None, GateStatus]:
    node = "behaviour_classifier"
    evidence = out.get("evidence")
    items = [e for e in evidence if isinstance(e, dict)] if isinstance(evidence, list) else []

    # 2. evidence: every cited event_id must exist; a claim with no evidence at all is unsupported.
    if isinstance(evidence, list):
        if not evidence or any(isinstance(e, dict) and e.get("event_id") not in context["event_ids"] for e in items):
            return _kept(old_field, "rejected:evidence")

    # 3. enum / range (re-asserted; the wrapper already validated the schema)
    value, conf = out.get("value"), out.get("confidence")
    ok = (isinstance(evidence, list) and len(items) == len(evidence)
          and all(isinstance(e.get("event_id"), str) and e.get("signal") in SIGNALS and isinstance(e.get("note"), str)
                  for e in items)
          and value in LEVELS
          and _is_num(conf) and 0 <= conf <= BEHAVIOUR_MAX_CONF)
    if not ok:
        return _kept(old_field, "rejected:schema")

    # 4. movement clamp: at most BEHAVIOUR_MAX_STEP level(s) per run from the previous value
    old_value = old_field.get("value") if old_field else None
    new_value = clamp_move(old_value if old_value in LEVELS else None, value, BEHAVIOUR_MAX_STEP)
    status: GateStatus = "clamped" if new_value != value else "accepted"

    # 5. confidence stored as-is; 6. stamp
    return {"value": new_value, "confidence": conf, **_stamp(node, context),
            "evidence": copy.deepcopy(evidence)}, status


# ---------------------------------------------------------------- N3 values_parser

def _values(old_field: dict | None, out: dict, context: dict) -> tuple[dict | None, GateStatus]:
    node = "values_parser"
    rules = out.get("rules")
    if not isinstance(rules, list):
        return _kept(old_field, "rejected:schema")
    notes = context["notes"]

    # 2. evidence: every quote is an exact substring of the user's notes (no case or whitespace folding)
    for r in rules:
        quote = r.get("quote") if isinstance(r, dict) else None
        if not isinstance(quote, str) or not quote or quote not in notes:
            return _kept(old_field, "rejected:evidence")

    # 3. enum / range
    if not all(r.get("rule_kind") in RULE_KINDS and isinstance(r.get("params"), dict)
               and r.get("applies_to") in APPLIES_TO for r in rules):
        return _kept(old_field, "rejected:schema")

    # Parsed rules start unapproved (SPEC §3.3 #4, §6.2 N3). A rule identical to one the user already approved
    # keeps that approval (re-parsing must not silently drop a user action).
    def ident(r: dict) -> tuple:
        return (r["quote"], r["rule_kind"], repr(sorted(r["params"].items())), r["applies_to"])

    approved = {ident(r) for r in (old_field or {}).get("value", []) if isinstance(r, dict) and r.get("approved_by_user")}
    value = [{"rule_kind": r["rule_kind"], "params": copy.deepcopy(r["params"]), "applies_to": r["applies_to"],
              "quote": r["quote"], "approved_by_user": ident(r) in approved} for r in rules]
    conf = out.get("confidence", (old_field or {}).get("confidence", VALUES_DEFAULT_CONF))
    if not _is_num(conf) or not 0 <= conf <= 1:
        return _kept(old_field, "rejected:schema")
    return {"value": value, "confidence": conf, **_stamp(node, context)}, "accepted"


def gate(node: str, old_field: dict | None, llm_output: dict, context: dict) -> tuple[dict | None, GateStatus]:
    """context: {"event_ids": set[str], "notes": str, "model_id", "prompt_version", "input_hash"}."""
    # 1. abstain (before anything else: an abstaining output is never "rejected")
    if llm_output.get("abstain") is True:
        return _kept(old_field, "kept:abstain")
    if node == "behaviour_classifier":
        return _behaviour(old_field, llm_output, context)
    if node == "values_parser":
        return _values(old_field, llm_output, context)
    return _kept(old_field, "rejected:schema")   # nodes gated in later build steps (N2 question_rubric)
