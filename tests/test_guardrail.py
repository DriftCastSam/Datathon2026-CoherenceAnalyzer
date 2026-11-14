"""Bedrock Guardrail on the explainer call only + the guardrail CloudFormation resources (P1.3).

SPEC §6.1 (optional ``BEDROCK_GUARDRAIL_ID`` + version, applied to the explainer call only), §9.3 (optional guardrail,
denied topic: personalised investment recommendations), §14 (AWS footprint), §1 inv. 3, 4; docs/spec-decisions.md D6
(no ``temperature``); docs/spec-factors.md §20.5 (narration loop: llm -> llm_retry -> template).

Offline: ``call_tool`` runs in live mode against a real boto3 ``bedrock-runtime`` client (dummy credentials, us-east-1)
wrapped in ``botocore.stub.Stubber``, which also validates every request against the service model. The fixture hands
that client to ``boto3.client(...)`` / ``boto3.Session(...).client(...)``. The request parameters are captured through
a ``before-parameter-build`` hook, so absence of ``guardrailConfig`` can be asserted exactly. Caches go to tmp dirs.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
from pathlib import Path

import boto3
import pytest
from botocore.stub import Stubber

from coherence_analyzer import config, explain
from coherence_analyzer.llm import bedrock, extractors

ROOT = Path(__file__).resolve().parent.parent
MODEL_ID = "test-model-id"
GR_ID = "gr0abc123def"
GR_VERSION = "3"
EM_DASH = "\u2014"
SCHEMA = {"type": "object", "properties": {"answer": {"type": "integer"}}, "required": ["answer"],
          "additionalProperties": False}
PAYLOAD = {"question": "marker-q-4417"}
OTHER_NODES = ["behaviour_classifier", "values_parser", "intent_parser", "profile_consistency"]   # N1, N3, N4, N6


def tool_response(node: str, tool_input: dict) -> dict:
    return {"output": {"message": {"role": "assistant", "content": [
                {"toolUse": {"toolUseId": "tu_1", "name": node, "input": tool_input}}]}},
            "stopReason": "tool_use", "usage": {"inputTokens": 11, "outputTokens": 7, "totalTokens": 18},
            "metrics": {"latencyMs": 5}}


def intervened_response() -> dict:
    return {"output": {"message": {"role": "assistant", "content": [{"text": "Sorry, I can't help with that."}]}},
            "stopReason": "guardrail_intervened",
            "usage": {"inputTokens": 11, "outputTokens": 3, "totalTokens": 14}, "metrics": {"latencyMs": 5}}


def _flatten(node, path=""):
    """(dotted key path, leaf value) pairs of a nested dict/list."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _flatten(v, f"{path}.{k}" if path else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _flatten(v, f"{path}.{i}")
    else:
        yield path, node


class BedrockEnv:
    def __init__(self, monkeypatch, tmp_path: Path):
        self.mp = monkeypatch
        self.cache = tmp_path / "runtime_llm"
        self.requests: list[dict] = []
        session = boto3.session.Session(aws_access_key_id="testing", aws_secret_access_key="testing",
                                        region_name="us-east-1")
        self.client = session.client("bedrock-runtime")        # built BEFORE boto3 is patched
        self.client.meta.events.register("before-parameter-build.bedrock-runtime.Converse", self._capture)
        self.stub = Stubber(self.client)
        self.stub.activate()
        client = self.client
        self.built = 0

        def fake_client(service_name, *args, **kwargs):
            assert service_name == "bedrock-runtime", f"asked for a {service_name!r} client"
            self.built += 1
            return client

        monkeypatch.setattr(boto3, "client", fake_client)
        monkeypatch.setattr(boto3.session.Session, "client", lambda s, service_name, *a, **k: fake_client(service_name))
        self.configure(GR_ID, GR_VERSION)

    def _capture(self, params, **kwargs):
        self.requests.append(copy.deepcopy(params))

    def configure(self, guardrail_id: str, version: str, mode: str = "live") -> None:
        mp = self.mp
        settings = {"COHERENCE_ANALYZER_LLM": mode, "BEDROCK_MODEL_ID": MODEL_ID,
                    "BEDROCK_GUARDRAIL_ID": guardrail_id, "BEDROCK_GUARDRAIL_VERSION": version}
        for name, value in settings.items():
            mp.setenv(name, value)
            for mod in (config, bedrock, extractors):
                mp.setattr(mod, name, value, raising=False)
        for mod in (config, bedrock):
            mp.setattr(mod, "RUNTIME_CACHE_DIR", self.cache, raising=False)

    def queue(self, *responses: dict) -> None:
        for r in responses:
            self.stub.add_response("converse", r)

    def call(self, node: str, payload: dict | None = None) -> dict:
        return bedrock.call_tool(node, SCHEMA, "You answer.", payload or PAYLOAD, f"{node}.v1")


@pytest.fixture
def bd(monkeypatch, tmp_path):
    for var, val in (("AWS_ACCESS_KEY_ID", "testing"), ("AWS_SECRET_ACCESS_KEY", "testing"),
                     ("AWS_DEFAULT_REGION", "us-east-1"), ("AWS_REGION", "us-east-1")):
        monkeypatch.setenv(var, val)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    env = BedrockEnv(monkeypatch, tmp_path)
    yield env
    env.stub.deactivate()


# ---------------------------------------------------------------- request params per node (SPEC §6.1)

def test_explainer_request_carries_the_guardrail_config(bd):
    bd.queue(tool_response("explainer", {"answer": 1}))
    res = bd.call("explainer")
    assert res["output"] == {"answer": 1}
    (req,) = bd.requests
    assert req["guardrailConfig"] == {"guardrailIdentifier": GR_ID, "guardrailVersion": GR_VERSION,
                                      "trace": "enabled"}
    assert req["modelId"] == MODEL_ID


@pytest.mark.parametrize("node", OTHER_NODES + ["toy"])
def test_other_nodes_never_get_a_guardrail_config(bd, node):
    # SPEC §6.1: "applied to the explainer call only" (N1, N3, N4, N6 and anything else stay unguarded).
    bd.queue(tool_response(node, {"answer": 1}))
    res = bd.call(node)
    assert res["output"] == {"answer": 1}
    (req,) = bd.requests
    assert "guardrailConfig" not in req
    assert GR_ID not in json.dumps(res["call_log"])           # and the log makes no guardrail claim for them


@pytest.mark.parametrize("gid,version", [(GR_ID, ""), ("", GR_VERSION), ("", "")])
def test_explainer_needs_both_id_and_version(bd, gid, version):
    # Task: only when BOTH are set. A half configuration must not send a malformed guardrailConfig.
    bd.configure(gid, version)
    bd.queue(tool_response("explainer", {"answer": 1}))
    bd.call("explainer")
    (req,) = bd.requests
    assert "guardrailConfig" not in req


def test_guardrail_changes_nothing_else_in_the_request(bd, tmp_path):
    # D6: still no temperature; the toolChoice stays as configured (D9: auto); only guardrailConfig is added.
    bd.queue(tool_response("explainer", {"answer": 1}))
    bd.call("explainer")
    bd.configure("", "")
    bd.mp.setattr(bedrock, "RUNTIME_CACHE_DIR", tmp_path / "second_cache", raising=False)
    bd.mp.setattr(config, "RUNTIME_CACHE_DIR", tmp_path / "second_cache", raising=False)
    bd.queue(tool_response("explainer", {"answer": 1}))
    bd.call("explainer")
    guarded, plain = bd.requests
    assert {k: v for k, v in guarded.items() if k != "guardrailConfig"} == plain
    assert "temperature" not in guarded["inferenceConfig"]
    assert guarded["toolConfig"]["toolChoice"] == {"auto": {}}          # D9


def test_guardrail_log_records_id_version_and_not_intervened(bd):
    # SPEC-AMBIGUITY: key names are not specified; some key under the log must carry "guardrail" and the id / the
    # version, and some key containing "intervened" must be False. Values are what is pinned, not the spelling.
    bd.queue(tool_response("explainer", {"answer": 1}))
    log = bd.call("explainer")["call_log"]
    flat = list(_flatten(log))
    assert any("guardrail" in p.lower() and v == GR_ID for p, v in flat), log
    assert any("guardrail" in p.lower() and str(v) == GR_VERSION for p, v in flat), log
    assert any("intervened" in p.lower() and v is False for p, v in flat), log
    assert {"node", "model_id", "prompt_version", "input_hash", "cache_hit", "latency_ms",
            "tokens_in", "tokens_out"} <= set(log)           # the §6.1 log fields are all still there
    assert log["cache_hit"] is False


# ---------------------------------------------------------------- cache key and mock mode

def test_cache_key_is_independent_of_the_guardrail(bd):
    # Recorded mock entries stay valid: key = sha256(node, prompt_version, model_id, canonical payload).
    canon = json.dumps(PAYLOAD, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    expected = hashlib.sha256("\x1f".join(["explainer", "explainer.v1", MODEL_ID, canon]).encode()).hexdigest()
    assert bedrock.cache_key("explainer", "explainer.v1", MODEL_ID, PAYLOAD) == expected
    bd.queue(tool_response("explainer", {"answer": 1}))
    bd.call("explainer")
    assert [p.name for p in bd.cache.glob("*.json")] == [f"explainer.{expected}.json"]


def test_live_cache_hit_makes_no_call_even_with_a_guardrail(bd):
    bd.queue(tool_response("explainer", {"answer": 1}))
    bd.call("explainer")
    again = bd.call("explainer")                              # second call: cache hit, no converse queued
    assert again["call_log"]["cache_hit"] is True and again["output"] == {"answer": 1}
    assert len(bd.requests) == 1


def test_mock_mode_with_a_guardrail_reads_the_recorded_entry_and_builds_no_client(bd, tmp_path):
    fixtures = tmp_path / "recorded"
    bedrock.write_entry(fixtures, "explainer", "explainer.v1", bedrock.MOCK_MODEL_ID, PAYLOAD, {"answer": 9},
                        {"tokens_in": 1, "tokens_out": 2})
    bd.configure(GR_ID, GR_VERSION, mode="mock")
    bd.mp.setenv("BEDROCK_MODEL_ID", "")
    for mod in (config, bedrock):
        bd.mp.setattr(mod, "BEDROCK_MODEL_ID", "", raising=False)
        bd.mp.setattr(mod, "LLM_CACHE_DIR", fixtures, raising=False)
    bd.mp.setitem(sys.modules, "boto3", None)                 # any boto3 import in mock mode would fail
    res = bd.call("explainer")
    assert res["output"] == {"answer": 9} and res["call_log"]["cache_hit"] is True
    with pytest.raises(bedrock.LLMCacheMiss):                 # a miss stays a miss: no fallback to a live call
        bd.call("explainer", {"question": "never recorded"})
    assert bd.built == 0 and bd.requests == []


# ---------------------------------------------------------------- intervention (SPEC §1 inv. 3, §6.1, §20.5)

def test_guardrail_intervened_raises_guardrailintervened_once_and_caches_nothing(bd):
    exc_cls = getattr(bedrock, "GuardrailIntervened", None)
    assert exc_cls is not None, "bedrock.GuardrailIntervened is missing"
    assert issubclass(exc_cls, bedrock.LLMOutputInvalid)
    bd.queue(intervened_response())                           # a second converse() would raise in the Stubber
    with pytest.raises(exc_cls) as ei:
        bd.call("explainer")
    assert len(bd.requests) == 1                              # no schema-retry: the answer was blocked, not malformed
    bd.stub.assert_no_pending_responses()
    assert "guardrail" in str(ei.value).lower()
    assert not bd.cache.exists() or list(bd.cache.glob("*")) == []     # a blocked answer is never cached
    # SPEC-AMBIGUITY: the log of an intervened call can only travel on the exception (call_tool returns nothing);
    # read strictly as `exc.call_log`, with the same guardrail keys as a normal log and intervened = True.
    flat = list(_flatten(ei.value.call_log))
    assert any("guardrail" in p.lower() and v == GR_ID for p, v in flat)
    assert any("guardrail" in p.lower() and str(v) == GR_VERSION for p, v in flat)
    assert any("intervened" in p.lower() and v is True for p, v in flat)
    assert ei.value.call_log["node"] == "explainer" and ei.value.call_log["model_id"] == MODEL_ID


def _facts() -> dict:
    """A hand-built non-pass section (shape of factors.build_sections facts), numbers pre-formatted as strings."""
    return {"factor": "risque", "status": "violation", "locale": "fr-CA", "declared": {}, "derived": {},
            "evidence_events": [],
            "checks": [{"id": "H3", "status": "violation", "subject": "NVDA", "value": "10,0 %", "limit": "5,0 %",
                        "derived_from": []},
                       {"id": "H8", "status": "violation", "value": "-13,4 %", "limit": "-12,0 %",
                        "derived_from": []}]}


def test_narration_ends_in_the_template_when_the_guardrail_intervenes(bd):
    # §20.5: a section whose writer is blocked ends in template_section(facts), mode template, never LLM text.
    facts = _facts()
    bd.queue(intervened_response(), intervened_response())
    narr = explain.narrate_section(facts)
    assert narr["mode"] == "template"
    assert narr["segments"] == explain.template_section(facts)["segments"]
    assert explain.verify_section({"segments": narr["segments"]}, facts) == []
    # SPEC-AMBIGUITY: "1 or 2 calls": a blocked answer may skip the retry; it must never exceed the §20.5 cap of 2.
    assert 1 <= len(bd.requests) <= 2
    assert all(r["guardrailConfig"]["guardrailIdentifier"] == GR_ID for r in bd.requests)
    # reason recorded: the narrative says the guardrail ended the LLM path (attempts, llm_error or verify)
    assert "guardrail" in json.dumps(narr, ensure_ascii=False).lower()


def test_explain_reports_mode_template_and_keeps_the_fixed_text(bd):
    facts = _facts()
    bd.queue(intervened_response(), intervened_response())
    out = explain.explain({"verdict": "incoherent", "locale": "fr-CA",
                           "sections": [{"factor": "risque", "status": "violation", "facts": facts}]})
    assert out["mode"] == "template"
    assert out["segments"] == [{"factor": "risque", **s} for s in explain.template_section(facts)["segments"]]
    assert out["disclaimer"] == config.DISCLAIMER["fr-CA"]
    assert "H3" in out["headline"] and "H8" in out["headline"]
    assert out["sections"][0]["narrative"]["mode"] == "template"


def test_without_a_guardrail_the_same_loop_reaches_mode_llm(bd):
    # Control: the template above is the guardrail's doing, not a problem of the hand-built facts.
    facts = _facts()
    good = {"segments": [{"kind": "fact", "text": "H3: NVDA pèse 10,0 %, au-delà du maximum de 5,0 %.",
                          "refs": ["H3"]},
                         {"kind": "fact", "text": "H8: le repli est de -13,4 %, plus profond que -12,0 %.",
                          "refs": ["H8"]}]}
    assert explain.verify_section(good, facts) == []
    bd.configure("", "")
    bd.queue(tool_response("explainer", good))
    narr = explain.narrate_section(facts)
    assert narr["mode"] == "llm" and narr["segments"] == good["segments"]
    (req,) = bd.requests
    assert "guardrailConfig" not in req


# ---------------------------------------------------------------- CloudFormation: guardrail resources

def _template() -> dict:
    # SPEC-AMBIGUITY: file name not specified; the one JSON file under deploy/aws/ with AWSTemplateFormatVersion.
    found = []
    for p in sorted((ROOT / "deploy" / "aws").glob("*.json")):
        doc = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(doc, dict) and "AWSTemplateFormatVersion" in doc:
            found.append(doc)
    assert len(found) == 1, f"expected exactly one CloudFormation JSON template in deploy/aws/, got {len(found)}"
    return found[0]


def _guardrail() -> tuple[str, dict]:
    found = {k: v for k, v in _template()["Resources"].items() if v["Type"] == "AWS::Bedrock::Guardrail"}
    assert len(found) == 1, list(found)
    ((name, res),) = found.items()
    return name, res["Properties"]


FR_MARKERS = ("dois-je", "devrais", "acheter", "vendre", "mon portefeuille", "quelle action", "quel fnb",
              "conseille", "recommande", "mes économies", "placer", "j'")
EN_MARKERS = ("should i", "buy", "sell", "my portfolio", "which stock", "which etf", "recommend", "advise",
              "my savings", "invest my")


def test_guardrail_denies_the_personalised_recommendation_topic_with_fr_and_en_examples():
    _, props = _guardrail()
    topics = props["TopicPolicyConfig"]["TopicsConfig"]
    assert len(topics) >= 1
    topic = next(t for t in topics if "recommend" in t["Name"].lower() or "recommand" in t["Name"].lower())
    assert topic["Type"] == "DENY"
    assert re.fullmatch(r"[0-9a-zA-Z_ !?.\-]{1,100}", topic["Name"])      # Bedrock topic-name rule
    definition = topic["Definition"]
    assert isinstance(definition, str) and 0 < len(definition) <= 200       # Bedrock limit
    assert "invest" in definition.lower() and "recommend" in definition.lower()
    examples = topic["Examples"]
    assert 2 <= len(examples) <= 5 and all(isinstance(e, str) and 0 < len(e) <= 100 for e in examples)
    low = [e.lower() for e in examples]
    assert any(any(m in e for m in FR_MARKERS) for e in low), "no French example"
    assert any(any(m in e for m in EN_MARKERS) for e in low), "no English example"


def test_guardrail_blocked_messaging_is_fixed_plain_text():
    _, props = _guardrail()
    for key in ("BlockedInputMessaging", "BlockedOutputsMessaging"):
        msg = props[key]
        assert isinstance(msg, str) and msg.strip(), key                # a literal, not an intrinsic function
        assert len(msg) <= 500 and EM_DASH not in msg
    assert props["Name"] and EM_DASH not in json.dumps(props, ensure_ascii=False)


def test_guardrail_version_resource_points_at_the_guardrail():
    name, _ = _guardrail()
    versions = {k: v for k, v in _template()["Resources"].items() if v["Type"] == "AWS::Bedrock::GuardrailVersion"}
    assert len(versions) == 1
    ((_, ver),) = versions.items()
    assert name in json.dumps(ver["Properties"]["GuardrailIdentifier"])
