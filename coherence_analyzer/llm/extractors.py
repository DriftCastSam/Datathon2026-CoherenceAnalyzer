"""One function per LLM node (SPEC §6.2). Each returns raw validated output; gating happens in coherence_analyzer.gate.

Every node has a `<node>_call(...)` variant that returns the wrapper's full `{output, call_log}` (the pipeline needs the
call_log for the audit record) and a plain `<node>(...)` that returns just the output.
"""
from __future__ import annotations

import copy
import json
from datetime import date, datetime, timedelta

from .. import config
from . import bedrock

N1_NODE = "behaviour_classifier"
N4_NODE = "intent_parser"
PROMPT_VERSIONS = {N1_NODE: f"{N1_NODE}.v1", N4_NODE: f"{N4_NODE}.v1"}
N1_EVENT_TYPES = ("question", "order_intent")
N1_MAX_EVENTS = 20
N1_WINDOW_DAYS = 30


def prompt_version(node: str) -> str:
    return PROMPT_VERSIONS[node]


def load_schema(node: str) -> dict:
    return json.loads((config.LLM_SCHEMAS_DIR / f"{node}.json").read_text(encoding="utf-8"))


def load_prompt(node: str) -> str:
    return (config.PROMPTS_DIR / f"{prompt_version(node)}.md").read_text(encoding="utf-8").strip()


def n1_events(events: list[dict], as_of: date | None = None) -> list[dict]:
    """SPEC §6.2 N1 input: question + order_intent events, oldest first, newest 20, as [{event_id, ts, type, text}].

    `as_of` (portfolio.as_of, docs/spec-decisions.md D1) drops events older than 30 days before it; there is no upper
    bound (ev_robert_0007 is dated after as_of and is part of the run). None: no window filter.
    No profile field ever enters this payload (anchoring).
    """
    keep = [e for e in events if e.get("type") in N1_EVENT_TYPES and e.get("text")]
    if as_of is not None:
        floor = datetime.combine(as_of - timedelta(days=N1_WINDOW_DAYS), datetime.min.time())
        keep = [e for e in keep if datetime.fromisoformat(e["ts"]).replace(tzinfo=None) >= floor]
    keep.sort(key=lambda e: (datetime.fromisoformat(e["ts"]), e["event_id"]))
    return [{k: e[k] for k in ("event_id", "ts", "type", "text")} for e in keep[-N1_MAX_EVENTS:]]


def behaviour_classifier_call(events: list[dict], as_of: date | None = None) -> dict:
    payload = {"events": n1_events(events, as_of)}
    return bedrock.call_tool(N1_NODE, load_schema(N1_NODE), load_prompt(N1_NODE), payload, prompt_version(N1_NODE))


def behaviour_classifier(events: list[dict], as_of: date | None = None) -> dict:
    """N1. Input: last 30 days of question/order_intent events (max 20). No profile fields."""
    return behaviour_classifier_call(events, as_of)["output"]


def question_rubric(event: dict) -> dict:
    """N2. Sub-scores only; code computes score = sum/12."""
    raise NotImplementedError


def values_parser(notes: str) -> dict:
    """N3. Rules with exact-substring quotes; start unapproved."""
    raise NotImplementedError


def n4_payload(text: str, universe: list[dict], weights_pct: dict[str, float]) -> dict:
    """SPEC §6.2 N4 input: the order_intent text + universe tickers/names + current weights (percent, as stored)."""
    return {"text": text,
            "universe": sorted(({"ticker": u["ticker"], "name": u["name"]} for u in universe),
                               key=lambda u: u["ticker"]),
            "weights_pct": {t: weights_pct[t] for t in sorted(weights_pct)}}


def n4_schema(universe: list[dict]) -> dict:
    """The static schema plus the ticker enum generated from the universe (top-level and `funding.specific`)."""
    schema = copy.deepcopy(load_schema(N4_NODE))
    tickers = sorted({u["ticker"] for u in universe})
    schema["properties"]["ticker"]["enum"] = tickers
    for branch in schema["properties"]["funding"]["anyOf"]:
        if "ticker" in branch["properties"]:
            branch["properties"]["ticker"]["enum"] = tickers
    return schema


def intent_parser_call(text: str, universe: list[dict], weights_pct: dict[str, float]) -> dict:
    return bedrock.call_tool(N4_NODE, n4_schema(universe), load_prompt(N4_NODE),
                             n4_payload(text, universe, weights_pct), prompt_version(N4_NODE))


def intent_parser(text: str, universe: list[dict], weights_pct: dict[str, float]) -> dict:
    """N4. Draft intent; ticker enum generated from the universe at runtime."""
    return intent_parser_call(text, universe, weights_pct)["output"]


def explainer(payload: dict) -> dict:
    """N5. See coherence_analyzer.explain."""
    raise NotImplementedError
