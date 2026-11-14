"""Shared view builder for the five factor modules (docs/spec-factors.md §20.3-20.4). Pure: reads the context, never
mutates it, never calls an LLM. Rules stay in rules/, checks in coherence/; this only selects and pre-formats."""
from __future__ import annotations

import copy
from types import ModuleType
from typing import Any

from ..coherence import report
from ..rules import derive as _derive  # noqa: F401  (importing registers the rules in REGISTRY)
from ..rules.registry import REGISTRY
from . import fmt
from .mapping import factor_of

RANK = {"pass": 0, "info": 1, "unknown": 2, "warning": 3, "violation": 4}      # §20.4
LIMIT_RULE_KINDS = ("exclude_activity", "max_theme_weight")                      # the only rules that reach `limits`
PARSED_RULES = "values.parsed_notes"
_MISSING = object()


def worst(statuses) -> str:
    return max(statuses, key=RANK.__getitem__, default="pass")


# ---------------------------------------------------------------- profile access

def _lookup(profile: dict, path: str) -> Any:
    cur: Any = profile
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def _val(node: Any) -> Any:
    return node["value"] if isinstance(node, dict) and "value" in node else node


def _is_derived(node: Any) -> bool:
    return isinstance(node, dict) and "by" in node


def _expand(profile: dict, pattern: str) -> list[str]:
    """Concrete dotted paths for a FIELDS entry (`*` = one level, sorted for determinism)."""
    if "*" not in pattern:
        return [pattern]
    head, _, tail = pattern.partition("*")
    parent = _lookup(profile, head.rstrip(".")) if head.rstrip(".") else profile
    if not isinstance(parent, dict):
        return []
    rest = tail.lstrip(".")
    return sorted(f"{head}{k}.{rest}" for k in parent if isinstance(parent[k], dict))


def _inline(v: Any, loc: str) -> str:
    pv = fmt.profile_value(v, "", loc)
    if pv is None:
        return fmt.DASH
    if isinstance(pv, list):
        return "; ".join(_inline(x, loc) for x in pv)
    if isinstance(pv, dict):
        return ", ".join(f"{k}: {_inline(x, loc)}" for k, x in pv.items())
    return str(pv)


def _because(path: str, node: dict, profile: dict, loc: str) -> list[str]:
    """One level of the derivation chain, formatted `path = value` (§20.4)."""
    rule = REGISTRY.get(path)
    out: list[str] = []
    if rule is not None:
        for p in rule.inputs:
            n = _lookup(profile, p)
            if n is not _MISSING:
                out.append(f"{p} = {_inline(fmt.profile_value(_val(n), p, loc), loc)}")
        return out
    for e in node.get("evidence", []) or []:
        if isinstance(e, dict) and e.get("event_id"):
            out.append(f"{e['event_id']} = {e.get('signal', e.get('score', fmt.DASH))}")
    return out


def _parsed_rules(profile: dict, selector: str) -> list[dict]:
    """§20.2 ESG split. `approved_by_user`: approved exclude_activity / max_theme_weight rules (limits, contraintes).
    anything else: every other parsed rule, i.e. unapproved or non-checkable (S4, preferences)."""
    node = _lookup(profile, PARSED_RULES)
    rules = list(_val(node) or []) if node is not _MISSING else []
    is_limit = lambda r: bool(r.get("approved_by_user")) and r.get("rule_kind") in LIMIT_RULE_KINDS  # noqa: E731
    return [r for r in rules if is_limit(r) == (selector == "approved_by_user")]


def declared_and_derived(fields: tuple[str, ...], profile: dict, loc: str) -> tuple[dict, dict]:
    declared: dict[str, Any] = {}
    derived: dict[str, Any] = {}
    for pattern in fields:
        if "[" in pattern:
            base, _, sel = pattern.partition("[")
            rules = _parsed_rules(profile, sel.rstrip("]"))
            node = _lookup(profile, base)
            if rules and node is not _MISSING:
                derived[base] = {"value": fmt.profile_value(rules, base, loc), "by": node.get("by", "llm:values_parser"),
                                 "because": []}
            continue
        for path in _expand(profile, pattern):
            node = _lookup(profile, path)
            if node is _MISSING:
                continue
            if _is_derived(node):
                entry = {"value": fmt.profile_value(_val(node), path, loc), "by": str(node["by"]),
                         "because": _because(path, node, profile, loc)}
                if isinstance(node.get("confidence"), (int, float)):
                    entry["confidence"] = fmt.number(node["confidence"], loc)
                derived[path] = entry
            else:
                declared[path] = fmt.profile_value(node, path, loc)
    return declared, derived


# ---------------------------------------------------------------- facts pieces

def _entry(c: dict, loc: str) -> dict:
    unit = c.get("unit")
    e = {"id": c["id"], "status": c["status"], "subject": c.get("subject"),
         "value": fmt.check_value(c.get("value"), unit, loc), "limit": fmt.check_value(c.get("limit"), unit, loc),
         "message_key": c.get("message_key"), "source": c.get("source"),
         "derived_from": list(c.get("derived_from", []))}
    if c.get("date"):
        e["date"] = str(c["date"])
    return e


def _intent_facts(intent: dict | None, loc: str) -> dict | None:
    if not intent:
        return None
    f = intent.get("funding") or {}
    kind = f.get("kind", "")
    if kind == "specific":
        funding = str(f.get("ticker"))
    elif kind == "pro_rata_class":
        funding = f"pro_rata_class:{f.get('asset_class')}"
    elif kind == "cash_contribution":
        funding = f"cash_contribution:{fmt.amount(f.get('amount_cad', 0), loc)}"
    else:
        funding = str(kind)
    return {"ticker": intent.get("ticker"), "target_weight": fmt.percent_points(intent.get("target_weight_pct", 0), loc),
            "funding": funding}


def _metrics(keys: tuple[str, ...], m: dict | None, loc: str) -> dict:
    return {k: fmt.metric(k, m[k], loc) for k in keys if m and k in m}


def _evidence(entries: list[dict], ctx) -> list[dict]:
    ids: list[str] = []
    for e in entries:
        if e["id"] == "S1":
            node = _lookup(ctx.profile, "risk.behavioural")
            evid = node.get("evidence", []) if isinstance(node, dict) else []
            ids += [x["event_id"] for x in evid if isinstance(x, dict) and x.get("event_id")]
        elif e["id"] == "S3" and ctx.intent and ctx.intent.get("event_id"):
            ids.append(ctx.intent["event_id"])
    by_id = {ev["event_id"]: ev for ev in ctx.events}
    out: list[dict] = []
    for i in dict.fromkeys(ids):
        ev = by_id.get(i)           # real events only (invariant 5): an id with no event is dropped
        if ev is not None and ev.get("text"):
            out.append({"event_id": i, "date": str(ev.get("ts", ""))[:10], "text": ev["text"]})
    return out


def reported_checks(ctx) -> list[dict]:
    """Flow B reports on the proposed checks, flow C on the current ones (§20.4)."""
    return ctx.checks_proposed if ctx.checks_proposed is not None else ctx.checks_current


def owned_checks(factor: str, ctx) -> list[dict]:
    return [c for c in reported_checks(ctx) if factor_of(c["id"]) == factor]


def build_facts(mod: ModuleType, ctx) -> dict:
    """The section facts payload (§20.4) for one factor module."""
    loc = fmt.check_locale(ctx.locale)
    owned = owned_checks(mod.ID, ctx)
    entries = [_entry(c, loc) for c in owned if c["status"] != "pass"]
    declared, derived = declared_and_derived(mod.FIELDS, ctx.profile, loc)
    return {
        "factor": mod.ID, "locale": loc, "question": mod.QUESTION[loc],
        "status": worst(c["status"] for c in owned),
        "verdict": report.verdict(reported_checks(ctx)),
        "intent": _intent_facts(ctx.intent, loc),
        "declared": declared, "derived": derived, "checks": entries,
        "metrics": {"current": _metrics(mod.METRICS, ctx.metrics_current, loc),
                    "proposed": _metrics(mod.METRICS, ctx.metrics_proposed, loc)},
        "evidence_events": _evidence(entries, ctx),
        "unknowns": [copy.deepcopy(e) for e in entries if e["status"] == "unknown"],
    }
