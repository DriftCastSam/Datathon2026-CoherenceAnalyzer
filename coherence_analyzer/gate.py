"""Deterministic gate for every LLM output (SPEC §11). P0 step 6.

Order: abstain -> evidence (event_ids exist, quotes are exact substrings of notes) ->
enum/range -> movement clamp -> confidence -> stamp.

Pure apart from the `updated_at` stamp, which is audit metadata (context["updated_at"] overrides the wall clock).
Rejections and abstentions return a copy of the previous field untouched (SPEC §1 inv. 4).
"""
from __future__ import annotations

import copy
import re
from datetime import datetime
from typing import Literal

from .config import BEHAVIOUR_MAX_CONF, BEHAVIOUR_MAX_STEP
from .levels import LEVELS, clamp_move
from .rules.derive import N2_SUBSCORES, question_quality_confidence, question_quality_ewma, question_score

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


# ---------------------------------------------------------------- N6 profile_consistency (docs/spec-factors.md §20.9)

PROFILE_SECTIONS = ("identity", "situation", "objectives", "risk", "knowledge", "values", "preferences")
N6_KINDS = ("tension", "contradiction")
N6_MAX_FINDINGS = 5
N6_MAX_REASON_CHARS = 200
NOTES_PATH = "values.notes"
_MISSING = object()
_PATH_PART = re.compile(r"(\w+)(?:\[(\d+)\])?")


def _resolve_declared(profile: dict, path: str) -> object:
    """Value of `a.b[1].c` in the declared part of the profile; _MISSING for a made-up path, a computed
    `{value, by}` object (or anything below one), `limits`, `limits_trace`, `meta`, ..."""
    parts = path.split(".")
    if parts[0] not in PROFILE_SECTIONS:
        return _MISSING
    node: object = profile
    for part in parts:
        m = _PATH_PART.fullmatch(part)
        if m is None or not isinstance(node, dict) or "by" in node or m.group(1) not in node:
            return _MISSING
        node = node[m.group(1)]
        if m.group(2) is not None:
            i = int(m.group(2))
            if not isinstance(node, list) or i >= len(node):
                return _MISSING
            node = node[i]
    if isinstance(node, dict) and "by" in node:
        return _MISSING
    return node


def _quote_ok(path: str, quote: str, value: object) -> bool:
    if not quote:
        return False
    if path == NOTES_PATH:
        return isinstance(value, str) and quote in value        # exact substring, no folding
    if isinstance(value, list):
        return any(not isinstance(e, (dict, list)) and e is not None and str(e) == quote for e in value)
    if isinstance(value, (dict, type(None))):
        return False
    return str(value) == quote


def _n6_shape_ok(findings: object) -> bool:
    if not isinstance(findings, list):
        return False
    for f in findings:
        if not (isinstance(f, dict) and f.get("kind") in N6_KINDS and isinstance(f.get("reason"), str)
                and isinstance(f.get("fields"), list) and all(isinstance(x, str) for x in f["fields"])
                and isinstance(f.get("quotes"), list) and all(isinstance(x, str) for x in f["quotes"])):
            return False
    return True


def _profile_consistency(old_field: dict | None, out: dict, context: dict) -> tuple[dict | None, GateStatus]:
    from .explain import advisory_hits          # lazy: explain imports the llm layer

    findings = out.get("findings")
    if not _n6_shape_ok(findings) or len(findings) > N6_MAX_FINDINGS:
        return _kept(old_field, "rejected:schema")
    profile = context["profile"]

    # 2. evidence: declared paths, >= 2 distinct, one quote per path equal to its value (notes: exact substring)
    for f in findings:
        fields, quotes = f["fields"], f["quotes"]
        if len(fields) < 2 or len(set(fields)) != len(fields) or len(quotes) != len(fields):
            return _kept(old_field, "rejected:evidence")
        for path, quote in zip(fields, quotes):
            value = _resolve_declared(profile, path)
            if value is _MISSING or not _quote_ok(path, quote, value):
                return _kept(old_field, "rejected:evidence")

    # 3. reason: <= 200 chars, no digit, no advisory phrase (case- and accent-insensitive, as explain V4)
    for f in findings:
        reason = f["reason"]
        if (not reason or len(reason) > N6_MAX_REASON_CHARS or any(c.isdigit() for c in reason)
                or advisory_hits(reason)):
            return _kept(old_field, "rejected:schema")

    return {"value": copy.deepcopy(findings), **_stamp("profile_consistency", context)}, "accepted"


def gate(node: str, old_field: dict | None, llm_output: dict, context: dict) -> tuple[dict | None, GateStatus]:
    """context: {"event_ids": set[str], "notes": str, "model_id", "prompt_version", "input_hash"};
    N2 needs "events_by_id" and "event_id" (the input event) instead of "notes";
    N6 also needs "profile" (the declared profile the finding paths resolve in)."""
    # 1. abstain (before anything else: an abstaining output is never "rejected")
    if llm_output.get("abstain") is True:
        return _kept(old_field, "kept:abstain")
    if node == "behaviour_classifier":
        return _behaviour(old_field, llm_output, context)
    if node == "question_rubric":
        return _question_rubric(old_field, llm_output, context)
    if node == "values_parser":
        return _values(old_field, llm_output, context)
    if node == "profile_consistency":
        return _profile_consistency(old_field, llm_output, context)
    return _kept(old_field, "rejected:schema")   # unknown node


# ---------------------------------------------------------------- N2 question_rubric (SPEC §6.2 N2, §11)

N2_KEYS = frozenset(("event_id", "quote", "is_investing_question", "rationale", *N2_SUBSCORES))
N2_MAX_RATIONALE = 150


def _event_ts(event: dict | None) -> datetime | None:
    try:
        return datetime.fromisoformat(event["ts"]) if event else None
    except (KeyError, TypeError, ValueError):
        return None


def _question_rubric(old_field: dict | None, out: dict, context: dict) -> tuple[dict | None, GateStatus]:
    node = "question_rubric"
    eid, quote = out.get("event_id"), out.get("quote")

    # 2. evidence: the output cites the input event, a real question event; the quote is an exact substring of its text.
    events = context.get("events_by_id", {})
    event = events.get(eid) if isinstance(eid, str) else None
    if (not isinstance(eid, str) or not eid or eid != context.get("event_id") or eid not in context["event_ids"]
            or not isinstance(event, dict) or event.get("type") != "question" or not isinstance(event.get("text"), str)
            or not isinstance(quote, str) or not quote or quote not in event["text"]):
        return _kept(old_field, "rejected:evidence")

    # 3. enum / range (re-asserted): closed key set (no model-set score or confidence), integers 0-3, a real boolean
    rationale = out.get("rationale")
    ok = (set(out) - {"abstain"} == N2_KEYS
          and all(isinstance(out[k], int) and not isinstance(out[k], bool) and 0 <= out[k] <= 3 for k in N2_SUBSCORES)
          and isinstance(out["is_investing_question"], bool)
          and isinstance(rationale, str) and len(rationale) <= N2_MAX_RATIONALE)
    if not ok:
        return _kept(old_field, "rejected:schema")

    # Not an investing question: ignored (nothing to score, the previous value stays).
    if out["is_investing_question"] is False:
        return _kept(old_field, "kept:abstain")

    # 4-5. code computes the score; question_quality = EWMA over the scored questions, oldest first; the confidence is
    # computed from the number of scored questions and capped (the model sets neither).
    score = question_score(out)
    items = [{"event_id": i["event_id"], "score": i["score"]}
             for i in ((old_field or {}).get("evidence") or [])
             if isinstance(i, dict) and isinstance(i.get("event_id"), str) and _is_num(i.get("score"))
             and i["event_id"] != eid]
    items.append({"event_id": eid, "score": score})

    def order(i: dict) -> tuple:
        ts = _event_ts(events.get(i["event_id"]))
        return (0, datetime.min, i["event_id"]) if ts is None else (1, ts.replace(tzinfo=None), i["event_id"])

    items.sort(key=order)
    value = question_quality_ewma([i["score"] for i in items])

    # 6. stamp. The quote is checked, not stored: evidence items are exactly {event_id, score} (§3.3 item 3).
    return {"value": round(value, 4), "confidence": question_quality_confidence(len(items)), **_stamp(node, context),
            "evidence": items}, "accepted"
