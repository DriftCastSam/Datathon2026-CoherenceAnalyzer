"""Profile v2 JSON Schema + Robert fixture conversion (SPEC §3.3, §3.4, §3.6, §16 robert/, §17 P0.3).

Offline and deterministic: reads only files under fixtures/ and the package schemas dir. No clock reads.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from coherence_analyzer import config

DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"
AS_OF = "2026-11-12"  # portfolio.as_of (docs/spec-decisions.md D1)
ROBERT_VALUE = 400000

LEVELS = ["low", "low_moderate", "moderate", "moderate_high", "high"]  # §3.3
EVENT_TYPES = {"question", "order_intent", "quiz_result", "profile_edit", "note", "intent_confirmed"}  # §3.4
EVENT_KEYS = {"event_id", "profile_id", "ts", "type", "text", "payload", "source"}  # §3.4 example
SIGNALS = {  # §6.2 N1
    "hype_chasing", "concentration_request", "panic_selling_intent", "loss_aversion", "overtrading",
    "market_timing", "long_term_framing", "diversification_request", "risk_questioning",
}
RULE_KINDS = {"exclude_activity", "max_theme_weight", "prefer_attribute", "avoid_attribute", "other"}  # §6.2 N3
APPLIES_TO = {"all", "single_stocks", "etf"}  # §6.2 N3
LLM_STAMP = ("prompt_version", "model_id", "updated_at", "input_hash")  # §3.3 item 1, §11 step 6
LLM_PATHS = {"risk.behavioural", "knowledge.question_quality", "values.parsed_notes"}

# §16 robert/: id -> (type, local date, text)
SPEC_EVENTS = {
    "ev_robert_0001": ("quiz_result", "2026-11-01", None),
    "ev_robert_0004": ("question", "2026-11-10", "Which AI stock is the next big one?"),
    "ev_robert_0005": ("question", "2026-11-12", "What does XBB's duration mean for my bonds if rates drop?"),
    "ev_robert_0007": ("order_intent", "2026-11-14", "I want to put 10% in NVDA, everyone's talking about it"),
}


# ---------------------------------------------------------------- fixtures / helpers

@pytest.fixture(scope="module")
def robert_dir(root) -> Path:
    return root / "fixtures" / "robert"


@pytest.fixture(scope="module")
def schema() -> dict:
    # SPEC-AMBIGUITY: §3.3 says `schemas/profile.v2.json`; §2 has no top-level schemas/ and config.SCHEMAS_DIR
    # is the package schemas dir, so the schema must live at config.SCHEMAS_DIR / "profile.v2.json".
    path = config.SCHEMAS_DIR / "profile.v2.json"
    assert path.is_file(), f"missing {path}"
    return json.loads(path.read_text())


@pytest.fixture(scope="module")
def validator(schema) -> Draft202012Validator:
    return Draft202012Validator(schema)


@pytest.fixture()
def robert(robert_dir) -> dict:
    return json.loads((robert_dir / "profile.json").read_text())


@pytest.fixture(scope="module")
def events(robert_dir) -> list[dict]:
    lines = [ln for ln in (robert_dir / "events.jsonl").read_text().splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


@pytest.fixture(scope="module")
def events_by_id(events) -> dict[str, dict]:
    return {e["event_id"]: e for e in events}


@pytest.fixture(scope="module")
def intents(robert_dir) -> list[dict]:
    return json.loads((robert_dir / "intents.json").read_text())


@pytest.fixture(scope="module")
def portfolio(robert_dir) -> dict:
    return json.loads((robert_dir / "portfolio.json").read_text())


def _get(profile: dict, path: str):
    node = profile
    for part in path.split("."):
        node = node[part]
    return node


def _walk_llm_objects(node, path: str = ""):
    """Yield (path, obj) for every dict whose `by` starts with `llm:` (§1 invariant 1)."""
    if isinstance(node, dict):
        by = node.get("by")
        if isinstance(by, str) and by.startswith("llm:"):
            yield path, node
        for k, v in node.items():
            yield from _walk_llm_objects(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_llm_objects(v, f"{path}[{i}]")


def _errors(validator: Draft202012Validator, instance) -> list[str]:
    return [f"{list(e.absolute_path)}: {e.message}" for e in validator.iter_errors(instance)]


def _iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


# ---------------------------------------------------------------- schema file (§3.3)

def test_schema_is_draft_2020_12(schema):
    assert schema.get("$schema") == DRAFT_2020_12
    Draft202012Validator.check_schema(schema)


def test_robert_profile_validates(validator, robert):
    assert _errors(validator, robert) == []


def test_robert_schema_version_is_2(robert):
    v = robert["meta"]["schema_version"]
    assert v == 2 and type(v) is int


# SPEC-AMBIGUITY: §3.3 item 6 says `meta.schema_version: 2`; stricter reading = the v2 schema requires it and pins it to 2.
@pytest.mark.parametrize("bad", [1, 3, "2", None, "missing"])
def test_schema_rejects_wrong_schema_version(validator, robert, bad):
    p = copy.deepcopy(robert)
    if bad == "missing":
        del p["meta"]["schema_version"]
    else:
        p["meta"]["schema_version"] = bad
    assert not validator.is_valid(p)


@pytest.mark.parametrize("path", ["risk.declared", "risk.capacity.value", "risk.effective.value",
                                  "risk.behavioural.value"])
def test_schema_enforces_risk_level_enum(validator, robert, path):
    p = copy.deepcopy(robert)
    parent_path, key = path.rsplit(".", 1)
    parent = _get(p, parent_path)
    assert parent[key] in LEVELS
    parent[key] = "very_high"
    assert not validator.is_valid(p)


# ---------------------------------------------------------------- llm: objects (§3.3 item 1, §1 inv. 1, §11)

def test_llm_objects_are_exactly_the_three_llm_fields(robert):
    # SPEC-AMBIGUITY: base profile has exactly three llm: fields; the conversion must neither drop nor add any.
    assert {p for p, _ in _walk_llm_objects(robert)} == LLM_PATHS


def test_every_llm_object_has_stamp(robert):
    found = list(_walk_llm_objects(robert))
    assert found
    for path, obj in found:
        for key in LLM_STAMP:
            assert isinstance(obj.get(key), str) and obj[key].strip(), f"{path}.{key}"
        # SPEC-AMBIGUITY: prompt versions follow `prompts/<node>.v<N>.md` (§6.2), so require a `v<N>` marker.
        assert re.search(r"v\d+", obj["prompt_version"]), f"{path}.prompt_version"
        _iso(obj["updated_at"])  # must parse as ISO-8601 date or datetime
        assert isinstance(obj.get("confidence"), (int, float)) and 0 <= obj["confidence"] <= 1, path
        assert "value" in obj, path


@pytest.mark.parametrize("path", sorted(LLM_PATHS))
@pytest.mark.parametrize("key", LLM_STAMP)
def test_schema_rejects_llm_object_missing_stamp(validator, robert, path, key):
    p = copy.deepcopy(robert)
    del _get(p, path)[key]
    assert not validator.is_valid(p)


@pytest.mark.parametrize("path", sorted(LLM_PATHS))
def test_schema_rejects_llm_field_with_rule_by(validator, robert, path):
    # §1 invariant 1: these fields are inferred; a `rule:` stamp would let them masquerade as deterministic.
    p = copy.deepcopy(robert)
    _get(p, path)["by"] = "rule:forged"
    assert not validator.is_valid(p)


# ---------------------------------------------------------------- risk.behavioural (§3.3 item 2, §6.2 N1)

def test_behavioural_evidence_shape_and_refs(robert, events_by_id):
    beh = robert["risk"]["behavioural"]
    assert beh["by"] == "llm:behaviour_classifier"
    assert beh["value"] == "moderate_high"          # unchanged from the example profile
    assert beh["confidence"] == pytest.approx(0.55, abs=1e-9)
    assert beh["confidence"] <= config.BEHAVIOUR_MAX_CONF
    ev = beh["evidence"]
    assert isinstance(ev, list) and ev
    for item in ev:
        # SPEC-AMBIGUITY: "[{event_id, signal, note}]" read as exactly these keys.
        assert isinstance(item, dict) and set(item) == {"event_id", "signal", "note"}, item
        assert item["event_id"] in events_by_id, item["event_id"]
        # §6.2 N1 reads only question + order_intent events.
        assert events_by_id[item["event_id"]]["type"] in {"question", "order_intent"}
        assert item["signal"] in SIGNALS
        assert isinstance(item["note"], str) and item["note"].strip()
    # SPEC-AMBIGUITY: the two v1 free-string items ("10% in NVDA" 11-14, "next big one" 11-10) map to ev_0007 / ev_0004.
    assert {i["event_id"] for i in ev} == {"ev_robert_0007", "ev_robert_0004"}


@pytest.mark.parametrize("bad", [
    ["2026-11-14: asked to put 10% in NVDA after news coverage"],                 # v1 free string
    [{"event_id": "ev_robert_0007", "signal": "fomo", "note": "x"}],              # signal not in enum
    [{"event_id": "ev_robert_0007", "note": "x"}],                                # missing signal
    [{"signal": "hype_chasing", "note": "x"}],                                    # missing event_id
    [{"event_id": "ev_robert_0007", "signal": "hype_chasing"}],                   # missing note
])
def test_schema_rejects_bad_behavioural_evidence(validator, robert, bad):
    p = copy.deepcopy(robert)
    p["risk"]["behavioural"]["evidence"] = bad
    assert not validator.is_valid(p)


# ---------------------------------------------------------------- knowledge.question_quality (§3.3 item 3)

def test_question_quality_evidence_shape_and_refs(robert, events_by_id):
    qq = robert["knowledge"]["question_quality"]
    assert qq["by"] == "llm:question_rubric"
    ev = qq["evidence"]
    assert isinstance(ev, list) and ev
    for item in ev:
        # Question text lives in the event log, not duplicated here: exactly {event_id, score}.
        assert isinstance(item, dict) and set(item) == {"event_id", "score"}, item
        assert item["event_id"] in events_by_id
        assert events_by_id[item["event_id"]]["type"] == "question"
        assert isinstance(item["score"], (int, float)) and 0 <= item["score"] <= 1
    # Conversion of the example's two evidence items (XBB question 0.9, AI question 0.3).
    assert {i["event_id"]: i["score"] for i in ev} == {"ev_robert_0005": 0.9, "ev_robert_0004": 0.3}


@pytest.mark.parametrize("bad", [
    [{"date": "2026-11-12", "question": "What does XBB's duration mean?", "score": 0.9}],  # v1 shape
    [{"event_id": "ev_robert_0005", "score": 0.9, "question": "dup text"}],               # text duplicated
    [{"event_id": "ev_robert_0005"}],                                                     # missing score
    [{"event_id": "ev_robert_0005", "score": 1.5}],                                       # out of range
])
def test_schema_rejects_bad_question_quality_evidence(validator, robert, bad):
    p = copy.deepcopy(robert)
    p["knowledge"]["question_quality"]["evidence"] = bad
    assert not validator.is_valid(p)


# ---------------------------------------------------------------- values.parsed_notes (§3.3 item 4, §6.2 N3)

def test_parsed_notes_items(robert):
    notes = robert["values"]["notes"]
    pn = robert["values"]["parsed_notes"]
    assert pn["by"] == "llm:values_parser"
    items = pn["value"]
    assert isinstance(items, list) and items
    for item in items:
        assert {"rule_kind", "params", "quote", "approved_by_user", "applies_to"} <= set(item), item
        assert item["rule_kind"] in RULE_KINDS
        assert item["applies_to"] in APPLIES_TO
        assert isinstance(item["params"], dict)
        assert type(item["approved_by_user"]) is bool
        assert isinstance(item["quote"], str) and item["quote"], item
        assert item["quote"] in notes, f"quote not an exact substring of values.notes: {item['quote']!r}"
        # Params per kind (§6.2 N3).
        if item["rule_kind"] == "exclude_activity":
            assert "tag" in item["params"]
        elif item["rule_kind"] == "max_theme_weight":
            assert "theme" in item["params"]
        elif item["rule_kind"] in {"prefer_attribute", "avoid_attribute"}:
            assert "attribute" in item["params"]


@pytest.mark.parametrize("field", ["rule_kind", "params", "quote", "approved_by_user"])
def test_schema_rejects_parsed_rule_missing_field(validator, robert, field):
    p = copy.deepcopy(robert)
    del p["values"]["parsed_notes"]["value"][0][field]
    assert not validator.is_valid(p)


@pytest.mark.parametrize("field,bad", [("rule_kind", "prefer_understandable_business"),
                                       ("applies_to", "everything"),
                                       ("approved_by_user", "yes")])
def test_schema_rejects_parsed_rule_bad_enum(validator, robert, field, bad):
    p = copy.deepcopy(robert)
    p["values"]["parsed_notes"]["value"][0][field] = bad
    assert not validator.is_valid(p)


# ---------------------------------------------------------------- limits / limits_trace (§3.3 item 5, §7.2)

def test_limits_trace_present_and_generated(robert):
    from coherence_analyzer.rules import derive, limits as limits_mod

    # SPEC-AMBIGUITY: §3.3 item 5 adds `limits_trace` to the profile; stricter reading = the converted fixture
    # carries it, and it must equal what rules/limits.py generates (never hand-written, §7.2).
    assert "limits_trace" in robert
    derived = derive.derive_all(robert, portfolio_value_cad=ROBERT_VALUE, as_of=AS_OF)
    _, trace = limits_mod.build_limits(derived, ROBERT_VALUE)
    assert set(robert["limits_trace"]) == set(trace)
    for name, paths in trace.items():
        assert sorted(robert["limits_trace"][name]) == sorted(paths), name


def test_derived_profile_still_validates(validator, robert):
    # §3.3: validate on every load and save; rule output written back must stay schema-valid.
    from coherence_analyzer.rules import derive

    derived = derive.derive_all(robert, portfolio_value_cad=ROBERT_VALUE, as_of=AS_OF)
    assert _errors(validator, derived) == []


# ---------------------------------------------------------------- events.jsonl (§3.4, §16)

def test_events_jsonl_lines_are_wellformed(robert_dir, events):
    raw = [ln for ln in (robert_dir / "events.jsonl").read_text().splitlines() if ln.strip()]
    assert len(raw) == len(events)
    ids = [e["event_id"] for e in events]
    assert len(ids) == len(set(ids)), "event_id must be unique"
    for e in events:
        assert EVENT_KEYS <= set(e), e
        assert e["profile_id"] == "persona-robert"
        assert e["type"] in EVENT_TYPES
        assert re.fullmatch(r"ev_robert_\d{4}", e["event_id"])
        ts = _iso(e["ts"])
        assert ts.tzinfo is not None, f"ts must carry a UTC offset: {e['ts']}"
    # Append-only log: timestamps non-decreasing in file order.
    tss = [_iso(e["ts"]) for e in events]
    assert tss == sorted(tss)


def test_events_match_spec_16(events_by_id):
    # SPEC-AMBIGUITY: §16 lists four Robert events; stricter reading = exactly these four, no extras.
    assert set(events_by_id) == set(SPEC_EVENTS)
    for eid, (etype, date, text) in SPEC_EVENTS.items():
        e = events_by_id[eid]
        assert e["type"] == etype, eid
        assert e["ts"][:10] == date, eid
        if text is not None:
            assert e["text"] == text, eid


def test_quiz_event_payload(events_by_id):
    e = events_by_id["ev_robert_0001"]
    assert e["payload"] == {"score": 0.8, "quiz_id": "kq_v1"}  # §3.4, §16


def test_quiz_score_matches_profile(robert, events_by_id):
    assert robert["knowledge"]["quiz_score"] == events_by_id["ev_robert_0001"]["payload"]["score"]


# ---------------------------------------------------------------- intents.json (§3.6, §16)

def test_intent_in_0001(intents, events_by_id, portfolio):
    by_id = {i["intent_id"]: i for i in intents}
    assert "in_0001" in by_id
    it = by_id["in_0001"]
    assert it["event_id"] == "ev_robert_0007"
    assert events_by_id[it["event_id"]]["type"] == "order_intent"
    assert it["action"] == "buy"
    assert it["ticker"] == "NVDA"
    assert it["target_weight_pct"] == 10
    assert it["funding"] == {"kind": "specific", "ticker": "ZST.TO"}
    assert it["status"] == "confirmed"
    confirmed = _iso(it["confirmed_at"])
    assert confirmed.tzinfo is not None
    assert confirmed >= _iso(events_by_id["ev_robert_0007"]["ts"])
    # Funding source must be a current holding.
    assert portfolio["weights_pct"].get("ZST.TO", 0) > 0


# ---------------------------------------------------------------- cross-file consistency

def test_profile_ids_consistent(robert, events, portfolio):
    pid = robert["meta"]["profile_id"]
    assert pid == "persona-robert"
    assert portfolio["profile_id"] == pid
    assert {e["profile_id"] for e in events} == {pid}
    assert portfolio["as_of"] == AS_OF
