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
N2_NODE = "question_rubric"
N3_NODE = "values_parser"
N4_NODE = "intent_parser"
N5_NODE = "explainer"
N6_NODE = "profile_consistency"
PROMPT_VERSIONS = {N1_NODE: f"{N1_NODE}.v1", N2_NODE: f"{N2_NODE}.v1", N3_NODE: f"{N3_NODE}.v1", N4_NODE: f"{N4_NODE}.v1",
                   N5_NODE: f"{N5_NODE}.v2", N6_NODE: f"{N6_NODE}.v1"}
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


def n2_events(events: list[dict]) -> list[dict]:
    """SPEC §6.2 N2: the question events that carry text, oldest first (ties broken by event_id)."""
    keep = [e for e in events if e.get("type") == "question" and e.get("text")]
    return sorted(keep, key=lambda e: (datetime.fromisoformat(e["ts"]), e["event_id"]))


def n2_payload(event: dict) -> dict:
    """SPEC §6.2 N2 input: one question event as {event_id, text}. No profile field ever enters this payload."""
    return {"event_id": event["event_id"], "text": event["text"]}


def question_rubric_call(event: dict) -> dict:
    """N2 `{output, call_log}` for one question event."""
    return bedrock.call_tool(N2_NODE, load_schema(N2_NODE), load_prompt(N2_NODE), n2_payload(event),
                             prompt_version(N2_NODE))


def question_rubric(event: dict) -> dict:
    """N2. Sub-scores only; code computes score = sum/12 (`derive.question_score`). Gated by `gate.gate`."""
    return question_rubric_call(event)["output"]


def n3_payload(notes: str) -> dict:
    """SPEC §6.2 N3 input: `values.notes` only. No other profile field enters the payload."""
    return {"notes": notes}


def n3_schema() -> dict:
    """The static schema plus the vocabularies generated at runtime, like N4's ticker enum (SPEC §3.2, §6.2 N3, §11):
    `tag` from the §3.2 activity-tag constant, `theme` from the themes in asset_meta.csv (read-only). The cache key does
    not include the schema, so the recorded mock entries are unchanged. The gate re-asserts both."""
    from .. import gate    # lazy: gate imports the data layer

    schema = copy.deepcopy(load_schema(N3_NODE))
    props = schema["properties"]["rules"]["items"]["properties"]["params"]["properties"]
    props["tag"]["enum"] = list(gate.ACTIVITY_TAG_VOCABULARY)
    themes = list(gate.theme_vocabulary())
    if themes:
        props["theme"]["enum"] = themes
    return schema


def values_parser_call(notes: str) -> dict:
    return bedrock.call_tool(N3_NODE, n3_schema(), load_prompt(N3_NODE), n3_payload(notes),
                             prompt_version(N3_NODE))


def values_parser(notes: str) -> dict:
    """N3. Rules with exact-substring quotes. The gate (§11) checks the quotes and starts every rule unapproved."""
    return values_parser_call(notes)["output"]


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


def explainer_call(facts: dict, retry_failures: list[str] | None = None) -> dict:
    """N5, one call per section (docs/spec-factors.md §20.5). The user payload is the section facts, unchanged; the
    retry adds `retry_failures` (the verifier's failure list), which gives it its own cache key. Returns {output, call_log}."""
    payload = copy.deepcopy(facts)
    if retry_failures is not None:
        payload["retry_failures"] = list(retry_failures)
    return bedrock.call_tool(N5_NODE, load_schema(N5_NODE), load_prompt(N5_NODE), payload, prompt_version(N5_NODE))


def explainer(facts: dict, retry_failures: list[str] | None = None) -> dict:
    """N5. Raw output {segments}; verification and the retry loop live in `coherence_analyzer.explain`."""
    return explainer_call(facts, retry_failures)["output"]


def profile_consistency_call(payload: dict) -> dict:
    """N6 (§20.9): `{output, call_log}` for `internal.n6_payload(...)`."""
    return bedrock.call_tool(N6_NODE, load_schema(N6_NODE), load_prompt(N6_NODE), payload, prompt_version(N6_NODE))


def profile_consistency(payload: dict) -> dict:
    """N6. Findings between >= 2 declared fields, exact quotes; gated by gate.gate("profile_consistency", ...)."""
    return profile_consistency_call(payload)["output"]
