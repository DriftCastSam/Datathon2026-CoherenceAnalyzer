"""Bedrock wrapper (mock / record / live), N1 + N4 schemas and prompts, recorded Robert responses.

SPEC §6.1 (wrapper), §6.2 N1 + N4, §1 inv. 4, 5, 8, §15 ("all tests run with COHERENCE_ANALYZER_LLM=mock"), §16.

Offline by construction: there is no network and no AWS credentials. record/live paths are exercised with a fake
``boto3`` injected through ``sys.modules`` (the wrapper must import boto3 lazily, only in record/live mode). The wrapper
is pointed at temporary cache dirs, so these tests never write to ``fixtures/llm_cache`` or ``.cache/llm``.
"""
import copy
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from coherence_analyzer import config, data
from coherence_analyzer.gate import gate
from coherence_analyzer.llm import bedrock, extractors
from coherence_analyzer.llm.bedrock import LLMCacheMiss, LLMOutputInvalid, cache_key, call_tool

ROOT = Path(__file__).resolve().parent.parent
ROBERT = ROOT / "fixtures" / "robert"
SCHEMAS = ROOT / "coherence_analyzer" / "llm" / "schemas"
PROMPTS = ROOT / "coherence_analyzer" / "llm" / "prompts"
DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"

SIGNALS = ["hype_chasing", "concentration_request", "panic_selling_intent", "loss_aversion", "overtrading",
           "market_timing", "long_term_framing", "diversification_request", "risk_questioning"]   # SPEC §6.2 N1
LEVELS = ["low", "low_moderate", "moderate", "moderate_high", "high"]

TOY_SCHEMA = {"type": "object", "properties": {"answer": {"type": "integer"}}, "required": ["answer"],
              "additionalProperties": False}
TOY_PAYLOAD = {"question": "marker-q-7731"}


# ---------------------------------------------------------------- helpers

def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _robert_events() -> list[dict]:
    return _jsonl(ROBERT / "events.jsonl")


def _n1_inputs() -> list[dict]:
    """What the caller hands N1: the question + order_intent events, with every stored field (trimming is N1's job)."""
    return [e for e in _robert_events() if e["type"] in ("question", "order_intent")]


def _n1_events() -> list[dict]:
    """SPEC §6.2 N1 input shape: [{event_id, ts, type, text}]."""
    return [{k: e[k] for k in ("event_id", "ts", "type", "text")} for e in _n1_inputs()]


def _universe() -> list[dict]:
    assets = data.load_assets()
    return [{"ticker": t, "name": n} for t, n in assets["name"].items()]


def _robert_weights() -> dict[str, float]:
    return json.loads((ROBERT / "portfolio.json").read_text())["weights_pct"]


def _set_mode(monkeypatch, mode: str, fixtures_dir: Path | None = None, runtime_dir: Path | None = None) -> None:
    """Switch the wrapper mode and redirect its cache dirs, whichever way it reads its config."""
    monkeypatch.setenv("COHERENCE_ANALYZER_LLM", mode)
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-model-id")
    for mod in (config, bedrock):
        monkeypatch.setattr(mod, "COHERENCE_ANALYZER_LLM", mode, raising=False)
        monkeypatch.setattr(mod, "BEDROCK_MODEL_ID", "test-model-id", raising=False)
        if fixtures_dir is not None:
            monkeypatch.setattr(mod, "LLM_CACHE_DIR", fixtures_dir, raising=False)
        if runtime_dir is not None:
            monkeypatch.setattr(mod, "RUNTIME_CACHE_DIR", runtime_dir, raising=False)


def _files(d: Path) -> list[Path]:
    return [p for p in d.rglob("*") if p.is_file() and p.name != ".gitkeep"] if d.exists() else []


def tool_response(node: str, tool_input: dict, tokens_in: int = 11, tokens_out: int = 7) -> dict:
    """A Converse response carrying one forced toolUse block."""
    return {"output": {"message": {"role": "assistant", "content": [
                {"toolUse": {"toolUseId": "tu_1", "name": node, "input": tool_input}}]}},
            "stopReason": "tool_use",
            "usage": {"inputTokens": tokens_in, "outputTokens": tokens_out, "totalTokens": tokens_in + tokens_out},
            "metrics": {"latencyMs": 5}}


class FakeBedrock:
    """Stands in for boto3: records every converse() request and replays queued responses."""

    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.requests: list[dict] = []

    def converse(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        assert self.responses, "unexpected extra converse() call"
        return self.responses.pop(0)

    def install(self, monkeypatch) -> "FakeBedrock":
        mod = types.ModuleType("boto3")
        mod.client = lambda *a, **k: self
        mod.Session = lambda *a, **k: types.SimpleNamespace(client=lambda *a2, **k2: self)
        monkeypatch.setitem(sys.modules, "boto3", mod)
        return self


def _block_boto3(monkeypatch) -> None:
    """`import boto3` raises ImportError: any mock-mode import of boto3 fails the test."""
    monkeypatch.setitem(sys.modules, "boto3", None)


@pytest.fixture
def dirs(tmp_path):
    fx = tmp_path / "fixtures_llm_cache"
    fx.mkdir()
    return fx, tmp_path / "runtime" / "llm"      # runtime dir not pre-created: the wrapper must create it


def _call(node="toy", payload=None, version="toy.v1", schema=None):
    return call_tool(node, schema or TOY_SCHEMA, "You answer.", payload if payload is not None else TOY_PAYLOAD, version)


# ---------------------------------------------------------------- mock mode

def test_mock_miss_raises_llmcachemiss_and_never_imports_boto3(monkeypatch, dirs):
    fx, rt = dirs
    _set_mode(monkeypatch, "mock", fx, rt)
    _block_boto3(monkeypatch)
    with pytest.raises(LLMCacheMiss):
        _call()
    assert _files(fx) == [] and _files(rt) == []          # a miss writes nothing


def test_importing_the_llm_layer_does_not_import_boto3():
    # SPEC §6.1 + task note: boto3 is imported lazily, only in record/live mode.
    code = ("import sys; import coherence_analyzer.llm.bedrock, coherence_analyzer.llm.extractors, "
            "coherence_analyzer.gate; assert 'boto3' not in sys.modules, 'boto3 imported at module import'")
    env = {**os.environ, "COHERENCE_ANALYZER_LLM": "mock"}
    res = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_record_then_mock_round_trip_is_keyed_by_cache_key(monkeypatch, dirs):
    fx, rt = dirs
    fake = FakeBedrock([tool_response("toy", {"answer": 42})]).install(monkeypatch)
    _set_mode(monkeypatch, "record", fx, rt)
    rec = _call()
    assert rec["output"] == {"answer": 42}
    assert len(fake.requests) == 1

    key = cache_key("toy", "toy.v1", "test-model-id", TOY_PAYLOAD)
    # SPEC-AMBIGUITY: §6.1 says "stored as files"; read as one file per call whose name contains the cache key.
    assert any(key in p.name for p in _files(fx)), [p.name for p in _files(fx)]
    assert _files(rt) == []                                # record writes to fixtures/llm_cache only

    _set_mode(monkeypatch, "mock", fx, rt)
    _block_boto3(monkeypatch)
    hit = _call()
    assert hit["output"] == {"answer": 42}
    assert hit["call_log"]["cache_hit"] is True
    assert len(fake.requests) == 1                          # mock made no call


@pytest.mark.parametrize("change", ["payload", "prompt_version", "node"])
def test_mock_key_depends_on_payload_prompt_version_and_node(monkeypatch, dirs, change):
    fx, rt = dirs
    FakeBedrock([tool_response("toy", {"answer": 1})]).install(monkeypatch)
    _set_mode(monkeypatch, "record", fx, rt)
    _call()
    _set_mode(monkeypatch, "mock", fx, rt)
    _block_boto3(monkeypatch)
    kwargs = {"payload": {"question": "marker-q-OTHER"}} if change == "payload" else \
             {"version": "toy.v2"} if change == "prompt_version" else {"node": "toy_other"}
    with pytest.raises(LLMCacheMiss):
        _call(**kwargs)


def test_mock_miss_never_falls_back_to_the_live_cache(monkeypatch, dirs):
    # mock reads fixtures/llm_cache only (SPEC §6.1), never .cache/llm.
    fx, rt = dirs
    FakeBedrock([tool_response("toy", {"answer": 5})]).install(monkeypatch)
    _set_mode(monkeypatch, "live", fx, rt)
    _call()
    assert _files(rt)
    _set_mode(monkeypatch, "mock", fx, rt)
    _block_boto3(monkeypatch)
    with pytest.raises(LLMCacheMiss):
        _call()


# ---------------------------------------------------------------- record / live request shape (§6.1)

def test_converse_request_forces_the_single_tool_at_temperature_zero(monkeypatch, dirs):
    fx, rt = dirs
    fake = FakeBedrock([tool_response("toy", {"answer": 1})]).install(monkeypatch)
    _set_mode(monkeypatch, "record", fx, rt)
    _call()
    (req,) = fake.requests
    assert req["modelId"] == "test-model-id"
    tools = req["toolConfig"]["tools"]
    assert len(tools) == 1
    assert tools[0]["toolSpec"]["name"] == "toy"
    assert tools[0]["toolSpec"]["inputSchema"] == {"json": TOY_SCHEMA}
    assert req["toolConfig"]["toolChoice"] == {"tool": {"name": "toy"}}
    assert req["inferenceConfig"]["temperature"] == 0
    assert isinstance(req["inferenceConfig"]["maxTokens"], int) and req["inferenceConfig"]["maxTokens"] > 0
    assert "You answer." in json.dumps(req["system"])
    assert req["messages"][0]["role"] == "user"
    assert "marker-q-7731" in json.dumps(req["messages"])
    assert "guardrailConfig" not in req                    # guardrail applies to the explainer call only (§6.1)


def test_call_log_carries_the_audit_fields(monkeypatch, dirs):
    fx, rt = dirs
    FakeBedrock([tool_response("toy", {"answer": 1}, tokens_in=11, tokens_out=7)]).install(monkeypatch)
    _set_mode(monkeypatch, "record", fx, rt)
    log = _call()["call_log"]
    assert log["node"] == "toy"
    assert log["model_id"] == "test-model-id"
    assert log["prompt_version"] == "toy.v1"
    assert log["cache_hit"] is False
    assert log["tokens_in"] == 11 and log["tokens_out"] == 7
    assert isinstance(log["latency_ms"], (int, float)) and log["latency_ms"] >= 0
    assert isinstance(log["input_hash"], str) and log["input_hash"]


def test_live_mode_caches_in_the_runtime_dir_and_second_call_is_a_hit(monkeypatch, dirs):
    fx, rt = dirs
    fake = FakeBedrock([tool_response("toy", {"answer": 9})]).install(monkeypatch)
    _set_mode(monkeypatch, "live", fx, rt)
    first, second = _call(), _call()
    assert first["output"] == second["output"] == {"answer": 9}
    assert first["call_log"]["cache_hit"] is False and second["call_log"]["cache_hit"] is True
    assert len(fake.requests) == 1                          # SPEC §6.1: cache hit -> no call
    assert any(cache_key("toy", "toy.v1", "test-model-id", TOY_PAYLOAD) in p.name for p in _files(rt))
    assert _files(fx) == []                                # live never writes fixtures/llm_cache


# ---------------------------------------------------------------- validation + retry (§6.1, inv. 4)

def test_invalid_tool_output_is_retried_once_with_the_validator_message(monkeypatch, dirs):
    fx, rt = dirs
    fake = FakeBedrock([tool_response("toy", {"answer": "not-an-int"}),
                        tool_response("toy", {"answer": 3})]).install(monkeypatch)
    _set_mode(monkeypatch, "record", fx, rt)
    out = _call()
    assert out["output"] == {"answer": 3}
    assert len(fake.requests) == 2
    first, second = fake.requests
    assert len(second["messages"]) > len(first["messages"])
    assert second["messages"][-1]["role"] == "user"        # "appended as a user turn"
    assert "not-an-int" in json.dumps(second["messages"][-1])      # jsonschema's message quotes the bad value


def test_second_invalid_output_raises_llmoutputinvalid_and_caches_nothing(monkeypatch, dirs):
    fx, rt = dirs
    fake = FakeBedrock([tool_response("toy", {"answer": "x"}), tool_response("toy", {"answer": "y"})]).install(monkeypatch)
    _set_mode(monkeypatch, "record", fx, rt)
    with pytest.raises(LLMOutputInvalid):
        _call()
    assert len(fake.requests) == 2                          # exactly one retry
    assert _files(fx) == [] and _files(rt) == []
    _set_mode(monkeypatch, "mock", fx, rt)
    with pytest.raises(LLMCacheMiss):
        _call()


def test_extra_property_violates_the_schema(monkeypatch, dirs):
    fx, rt = dirs
    FakeBedrock([tool_response("toy", {"answer": 1, "extra": True})] * 2).install(monkeypatch)
    _set_mode(monkeypatch, "record", fx, rt)
    with pytest.raises(LLMOutputInvalid):
        _call()


# ---------------------------------------------------------------- N1 / N4 schema + prompt files (§6.2)

def _schema(name: str) -> dict:
    return json.loads((SCHEMAS / f"{name}.json").read_text())


@pytest.mark.parametrize("node", ["behaviour_classifier", "intent_parser"])
def test_node_schema_is_valid_draft_2020_12(node):
    schema = _schema(node)
    Draft202012Validator.check_schema(schema)
    assert schema.get("$schema", DRAFT_2020_12) == DRAFT_2020_12
    assert schema.get("type") == "object"          # Converse toolSpec inputSchema must be an object schema


@pytest.mark.parametrize("node", ["behaviour_classifier", "intent_parser"])
def test_node_prompt_v1_exists_and_is_not_empty(node):
    assert (PROMPTS / f"{node}.v1.md").read_text().strip()


def test_n1_prompt_states_the_spec_rules():
    text = (PROMPTS / "behaviour_classifier.v1.md").read_text().lower()
    assert "abstain" in text            # abstain if fewer than 2 relevant events
    assert "event_id" in text           # every claim cites an event_id
    assert "0.8" in text                # confidence <= 0.8
    assert "2" in text


N1_OK = {"abstain": False, "value": "moderate_high", "confidence": 0.55,
         "evidence": [{"event_id": "ev_robert_0007", "signal": "hype_chasing",
                       "note": "asks 10% in one stock after news"}]}      # SPEC §6.2 N1 example, verbatim


def _n1_valid(doc: dict) -> bool:
    return Draft202012Validator(_schema("behaviour_classifier")).is_valid(doc)


def _with(doc: dict, **over) -> dict:
    out = copy.deepcopy(doc)
    out.update(over)
    return out


def test_n1_schema_accepts_the_spec_example():
    assert _n1_valid(N1_OK)


@pytest.mark.parametrize("signal", SIGNALS)
def test_n1_schema_accepts_every_spec_signal(signal):
    ev = [{"event_id": "ev_robert_0007", "signal": signal, "note": "n"}]
    assert _n1_valid(_with(N1_OK, evidence=ev))


@pytest.mark.parametrize("level", LEVELS)
def test_n1_schema_accepts_every_risk_level(level):
    assert _n1_valid(_with(N1_OK, value=level))


@pytest.mark.parametrize("bad", [
    _with(N1_OK, value="extreme"),
    _with(N1_OK, confidence=0.81),          # SPEC §6.2 N1: confidence <= 0.8 by construction
    _with(N1_OK, confidence=1.0),
    _with(N1_OK, confidence=-0.01),
    _with(N1_OK, evidence=[{"event_id": "ev_robert_0007", "signal": "vibes", "note": "n"}]),
    _with(N1_OK, evidence=[{"signal": "hype_chasing", "note": "n"}]),
    _with(N1_OK, evidence=[{"event_id": "ev_robert_0007", "note": "n"}]),
    _with(N1_OK, evidence=["ev_robert_0007"]),     # SPEC §3.3 #2: evidence items are objects, not strings
    {k: v for k, v in N1_OK.items() if k != "abstain"},
    {k: v for k, v in N1_OK.items() if k != "evidence"},
])
def test_n1_schema_rejects_out_of_spec_outputs(bad):
    assert not _n1_valid(bad)


N4_OK = {"action": "buy", "ticker": "NVDA", "target_weight_pct": 10, "funding": {"kind": "pro_rata_all"},
         "ambiguous_fields": ["funding"], "unresolved_mentions": []}
# SPEC-AMBIGUITY: §6.2 N4 says "a draft intent (§3.6 fields) + ambiguous_fields + unresolved_mentions"; read as one
# flat object. intent_id / event_id / status are not LLM output (the input has no event id; the user confirms).


def _n4_valid(doc: dict) -> bool:
    return Draft202012Validator(_schema("intent_parser")).is_valid(doc)


@pytest.mark.parametrize("funding", [
    {"kind": "specific", "ticker": "ZST.TO"},
    {"kind": "pro_rata_all"},
    {"kind": "pro_rata_class", "asset_class": "etf_equity"},
    {"kind": "cash_contribution", "amount_cad": 5000},
])
def test_n4_schema_accepts_every_funding_kind(funding):
    assert _n4_valid(_with(N4_OK, funding=funding))


@pytest.mark.parametrize("bad", [
    _with(N4_OK, funding={"kind": "bogus"}),
    _with(N4_OK, funding={"kind": "specific"}),                     # needs ticker (SPEC §3.6)
    _with(N4_OK, funding={"kind": "pro_rata_class"}),               # needs asset_class
    _with(N4_OK, funding={"kind": "cash_contribution"}),            # needs amount_cad
    _with(N4_OK, target_weight_pct=-1),
    _with(N4_OK, target_weight_pct=101),
    _with(N4_OK, target_weight_pct="ten"),
    {k: v for k, v in N4_OK.items() if k != "ambiguous_fields"},
    {k: v for k, v in N4_OK.items() if k != "unresolved_mentions"},
    {k: v for k, v in N4_OK.items() if k != "ticker"},
])
def test_n4_schema_rejects_out_of_spec_outputs(bad):
    assert not _n4_valid(bad)


# ---------------------------------------------------------------- N1 / N4 through the wrapper (record via fake)

def test_n1_request_carries_events_only_no_profile_fields(monkeypatch, dirs):
    # SPEC §6.2 N1: "Don't include the declared risk or any profile field, to avoid anchoring."
    fx, rt = dirs
    fake = FakeBedrock([tool_response("behaviour_classifier", N1_OK)]).install(monkeypatch)
    _set_mode(monkeypatch, "record", fx, rt)
    out = extractors.behaviour_classifier(_n1_inputs())
    assert out == N1_OK
    (req,) = fake.requests
    sent = json.dumps(req["messages"]) + json.dumps(req.get("system", ""))
    for ev in _n1_events():
        assert ev["event_id"] in sent and ev["ts"] in sent
    assert "everyone" in sent and "next big one" in sent             # event texts are sent
    # SPEC §6.2 N1: input is [{event_id, ts, type, text}]; other event fields and any profile field stay out.
    for banned in ("low_moderate", "declared", "capacity", "persona-robert", "ui_chat"):
        assert banned not in sent, banned
    assert req["toolConfig"]["toolChoice"] == {"tool": {"name": "behaviour_classifier"}}
    assert req["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"] == _schema("behaviour_classifier")


def test_n4_schema_ticker_enum_is_generated_from_the_universe(monkeypatch, dirs):
    fx, rt = dirs
    universe = [u for u in _universe() if u["ticker"] in {"NVDA", "ZST.TO", "SPY", "XIU.TO"}]
    fake = FakeBedrock([tool_response("intent_parser", N4_OK)]).install(monkeypatch)
    _set_mode(monkeypatch, "record", fx, rt)
    extractors.intent_parser("I want to put 10% in NVDA, everyone's talking about it", universe, _robert_weights())
    schema = fake.requests[0]["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]
    # SPEC-AMBIGUITY: §6.2 N4 "enum in the schema, generated at runtime"; read as a flat top-level `ticker` property.
    assert set(schema["properties"]["ticker"]["enum"]) == {"NVDA", "ZST.TO", "SPY", "XIU.TO"}


def test_n4_ticker_outside_the_universe_is_invalid(monkeypatch, dirs):
    fx, rt = dirs
    universe = [u for u in _universe() if u["ticker"] in {"NVDA", "ZST.TO"}]
    fake = FakeBedrock([tool_response("intent_parser", _with(N4_OK, ticker="TSLA"))] * 2).install(monkeypatch)
    _set_mode(monkeypatch, "record", fx, rt)
    with pytest.raises(LLMOutputInvalid):
        extractors.intent_parser("buy TSLA", universe, _robert_weights())
    assert len(fake.requests) == 2


# ---------------------------------------------------------------- recorded Robert responses (fixtures/llm_cache, SPEC §16)

@pytest.fixture
def real_mock(monkeypatch):
    """The committed cache, mock mode, boto3 unavailable: proves the whole call is served from fixtures/llm_cache."""
    monkeypatch.setenv("COHERENCE_ANALYZER_LLM", "mock")
    for mod in (config, bedrock):
        monkeypatch.setattr(mod, "COHERENCE_ANALYZER_LLM", "mock", raising=False)
    _block_boto3(monkeypatch)
    # SPEC-AMBIGUITY: the model id is left to the environment/default; cache entries are keyed by whatever
    # the wrapper resolves in mock mode, so no model id is forced here.


def test_robert_n1_is_served_from_the_recorded_cache_and_matches_the_schema(real_mock):
    out = extractors.behaviour_classifier(_n1_inputs())
    assert Draft202012Validator(_schema("behaviour_classifier")).is_valid(out)
    assert out["abstain"] is False
    # SPEC §6.2 N1 example + §7.4 S1 + §4 matrix: Robert's behaviour reads >= 2 levels above effective low_moderate.
    assert out["value"] == "moderate_high"
    assert 0.5 <= out["confidence"] <= 0.8
    cited = {e["event_id"]: e["signal"] for e in out["evidence"]}
    assert cited.get("ev_robert_0007") == "hype_chasing"          # S3 needs the intent event cited with hype_chasing
    assert set(cited) <= {e["event_id"] for e in _n1_events()}    # inv. 5: only real events


def test_robert_n1_output_passes_the_gate_against_the_profile_fixture(real_mock):
    out = extractors.behaviour_classifier(_n1_inputs())
    profile = json.loads((ROBERT / "profile.json").read_text())
    ctx = {"event_ids": {e["event_id"] for e in _robert_events()}, "notes": "", "model_id": "mock",
           "prompt_version": "behaviour_classifier.v1", "input_hash": "sha256:" + "0" * 64}
    new, status = gate("behaviour_classifier", profile["risk"]["behavioural"], out, ctx)
    assert status == "accepted"
    assert new["value"] == out["value"]
    assert new["by"] == "llm:behaviour_classifier"


def test_robert_n1_call_is_deterministic(real_mock):
    events = _n1_inputs()
    assert extractors.behaviour_classifier(events) == extractors.behaviour_classifier(copy.deepcopy(events))


def test_robert_n4_draft_intent_is_served_from_the_recorded_cache(real_mock):
    text = next(e["text"] for e in _robert_events() if e["event_id"] == "ev_robert_0007")
    universe = _universe()
    out = extractors.intent_parser(text, universe, _robert_weights())
    assert Draft202012Validator(_schema("intent_parser")).is_valid(out)
    assert out["action"] == "buy"
    assert out["ticker"] == "NVDA"
    assert out["target_weight_pct"] == 10                          # SPEC §3.6 / §16: "I want to put 10% in NVDA"
    assert out["ticker"] in {u["ticker"] for u in universe}
    assert out["unresolved_mentions"] == []
    # SPEC-AMBIGUITY: §3.6 "Default when the user doesn't say: pro_rata_all, shown to the user as the default";
    # the text names no funding source, so the draft carries the default and flags `funding` as ambiguous.
    assert out["funding"] == {"kind": "pro_rata_all"}
    assert "funding" in out["ambiguous_fields"]


def test_robert_n4_draft_has_no_execution_fields(real_mock):
    # SPEC inv. 7 / §6.2 N4: never auto-executed, always confirmed by the user (B2): the draft cannot be pre-confirmed.
    text = next(e["text"] for e in _robert_events() if e["event_id"] == "ev_robert_0007")
    out = extractors.intent_parser(text, _universe(), _robert_weights())
    assert out.get("status", "draft") != "confirmed"
    assert "confirmed_at" not in out
