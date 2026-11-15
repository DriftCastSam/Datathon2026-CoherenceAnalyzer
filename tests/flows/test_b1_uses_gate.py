"""P4.N4GATE: the live N4 draft goes through gate.gate("intent_parser"); the duplicate pipeline._gate_intent is gone.

Sources: CLAUDE.md invariant "every LLM output goes through coherence_analyzer/gate.py"; SPEC §11 (gate, statuses),
§6.2 N4 (the draft is buy only, universe ticker, weight in (0, 100], funding kind with its parameter), §3.6 (intent.apply
has no sell semantics), §4 Flow B B1 (N4 then the draft gate), §10 (audit llm_calls carry the gate status).

The outcomes (accepted / rejected:schema, the returned draft, the audit call entry) must be the same as before the
change: the expected values below are the pre-change behaviour of pipeline._gate_intent, stated here as a table.
Offline: COHERENCE_ANALYZER_LLM=mock, the N4 call is replaced by a canned output where the recorded cache has none.
"""
import copy
import json
import os
import shutil
from pathlib import Path

import pytest

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

from coherence_analyzer import data, gate, pipeline  # noqa: E402
from coherence_analyzer.llm import bedrock, extractors  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "fixtures"
CACHE = FIX / "llm_cache"
ROBERT = FIX / "robert"
N4 = "intent_parser"
CALL_KEYS = {"node", "model_id", "prompt_version", "input_hash", "cache_hit", "latency_ms", "tokens_in",
             "tokens_out", "gate"}
B1_KEYS = {"fixture_dir", "intent_id", "run_id", "started_at", "event", "fixture_intent", "draft", "draft_gate",
           "llm_calls", "llm_errors"}


# ---------------------------------------------------------------- helpers

def _draft(**over) -> dict:
    d = {"action": "buy", "ticker": "NVDA", "target_weight_pct": 10, "funding": {"kind": "specific", "ticker": "ZST.TO"},
         "ambiguous_fields": [], "unresolved_mentions": []}
    d.update(over)
    return d


def _canned(output: dict):
    """Replacement for extractors.intent_parser_call: the real call-result shape {output, call_log}."""
    def call(text, universe, weights_pct):
        return {"output": copy.deepcopy(output),
                "call_log": {"node": N4, "model_id": "fixture:hand-written", "prompt_version": "intent_parser.v1",
                             "input_hash": "sha256:test", "cache_hit": True, "latency_ms": 0, "tokens_in": 0,
                             "tokens_out": 0}}
    return call


@pytest.fixture
def gate_spy(monkeypatch):
    """Wraps gate.gate (the attribute pipeline looks up at call time); records every call and its result."""
    calls: list[dict] = []
    real = gate.gate

    def spy(node, old_field, llm_output, context):
        result = real(node, old_field, llm_output, context)
        calls.append({"node": node, "old": copy.deepcopy(old_field), "output": copy.deepcopy(llm_output),
                      "context": context, "result": result})
        return result

    monkeypatch.setattr(gate, "gate", spy)
    return calls


def _recorded_n4_output() -> dict:
    entries = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(CACHE.glob("intent_parser.*.json"))]
    assert entries, "the recorded N4 cache entry is missing"
    robert = [e for e in entries if "NVDA" in e["user_payload"]["text"]]
    return robert[0]["output"]


def _persona_dir(tmp_path: Path, persona: str) -> Path:
    """A copy of the persona fixture with one order_intent event + intents.json (only robert ships them)."""
    dst = tmp_path / f"fx_{persona}"
    shutil.copytree(FIX / persona, dst)
    events = (dst / "events.jsonl").read_text(encoding="utf-8").rstrip("\n").splitlines()
    event = {"event_id": f"ev_{persona}_9001", "profile_id": f"persona-{persona}", "ts": "2026-11-14T10:02:00-05:00",
             "type": "order_intent", "text": "I want to put 10% in NVDA", "payload": None, "source": "ui_chat"}
    (dst / "events.jsonl").write_text("\n".join([*events, json.dumps(event)]) + "\n", encoding="utf-8")
    intent = {"intent_id": "in_9001", "event_id": event["event_id"], "action": "buy", "ticker": "NVDA",
              "target_weight_pct": 10, "funding": {"kind": "pro_rata_all"}, "status": "confirmed",
              "confirmed_at": "2026-11-14T10:05:00-05:00"}
    (dst / "intents.json").write_text(json.dumps([intent]), encoding="utf-8")
    return dst


# ---------------------------------------------------------------- the copy is gone, b1 calls gate.gate

def test_pipeline_no_longer_defines_its_own_n4_gate():
    # CLAUDE.md: every LLM output goes through gate.py; a second N4 gate in pipeline.py is the audit gap.
    assert not hasattr(pipeline, "_gate_intent")


def test_b1_calls_gate_gate_once_for_the_n4_node(gate_spy):
    state = pipeline.b1_parse_intent(ROBERT, "in_0001")
    assert [c["node"] for c in gate_spy] == [N4]
    call = gate_spy[0]
    # SPEC-AMBIGUITY: §6.2 N4 has no previous value to protect (a draft is not a profile field): old_field is None.
    assert call["old"] is None
    assert call["output"] == _recorded_n4_output()
    new, status = call["result"]
    assert status == state["draft_gate"]


def test_b1_gate_context_lets_the_gate_see_the_real_universe(gate_spy):
    pipeline.b1_parse_intent(ROBERT, "in_0001")
    ctx = gate_spy[0]["context"]
    assert isinstance(ctx, dict)
    # SPEC-AMBIGUITY: the N4 context keys are "tickers" or "universe" (gate._universe_tickers); passing neither makes
    # the gate fall back to the real universe, which is the same set. Either way the universe tickers must resolve.
    raw = ctx.get("tickers") if ctx.get("tickers") is not None else ctx.get("universe")
    if raw is not None:
        names = {u["ticker"] if isinstance(u, dict) else u for u in raw}
        assert names == {str(t) for t in data.load_assets().index}


# ---------------------------------------------------------------- recorded cache: unchanged outcome

def test_recorded_n4_cache_is_accepted_and_the_draft_is_unchanged(gate_spy):
    state = pipeline.b1_parse_intent(ROBERT, "in_0001")
    assert state["draft_gate"] == "accepted"
    assert state["draft"] == _recorded_n4_output()
    assert state["llm_errors"] == []
    assert set(state) == B1_KEYS
    assert state["intent_id"] == "in_0001"
    assert state["event"]["event_id"] == "ev_robert_0007"


def test_recorded_n4_audit_call_entry_is_unchanged(gate_spy):
    state = pipeline.b1_parse_intent(ROBERT, "in_0001")
    (entry,) = state["llm_calls"]
    assert set(entry) == CALL_KEYS
    assert entry["node"] == N4
    assert entry["prompt_version"] == "intent_parser.v1"
    assert entry["gate"] == "accepted"
    assert entry["gate"] == gate_spy[0]["result"][1]


def test_accepted_draft_is_a_copy_not_the_model_output_object(monkeypatch):
    out = _draft()
    monkeypatch.setattr(extractors, "intent_parser_call", lambda *a: {"output": out, "call_log": {"node": N4}})
    state = pipeline.b1_parse_intent(ROBERT, "in_0001")
    assert state["draft"] == out and state["draft"] is not out


# ---------------------------------------------------------------- outcome table (same as the removed copy)

ACCEPTED = {
    "specific": _draft(),
    "pro_rata_all": _draft(funding={"kind": "pro_rata_all"}),
    "pro_rata_class": _draft(funding={"kind": "pro_rata_class", "asset_class": "equity"}),
    "cash_contribution": _draft(funding={"kind": "cash_contribution", "amount_cad": 5000}),
    "weight_100": _draft(target_weight_pct=100),
    "weight_fraction": _draft(target_weight_pct=0.5),
    "float_weight": _draft(target_weight_pct=7.5),
}
REJECTED = {
    "sell": _draft(action="sell"),
    "Sell": _draft(action="Sell"),
    "short": _draft(action="short"),
    "action_null": _draft(action=None),
    "ticker_not_in_universe": _draft(ticker="NOTATICKER"),
    "ticker_null": _draft(ticker=None),
    "weight_zero": _draft(target_weight_pct=0),
    "weight_negative": _draft(target_weight_pct=-5),
    "weight_over_100": _draft(target_weight_pct=100.5),
    "weight_string": _draft(target_weight_pct="10"),
    "weight_null": _draft(target_weight_pct=None),
    "funding_missing": {k: v for k, v in _draft().items() if k != "funding"},
    "funding_not_a_dict": _draft(funding="pro_rata_all"),
    "funding_unknown_kind": _draft(funding={"kind": "margin"}),
    "specific_without_ticker": _draft(funding={"kind": "specific"}),
    "specific_unknown_ticker": _draft(funding={"kind": "specific", "ticker": "NOTATICKER"}),
    "class_without_asset_class": _draft(funding={"kind": "pro_rata_class"}),
    "class_empty_asset_class": _draft(funding={"kind": "pro_rata_class", "asset_class": ""}),
    "cash_zero": _draft(funding={"kind": "cash_contribution", "amount_cad": 0}),
    "cash_negative": _draft(funding={"kind": "cash_contribution", "amount_cad": -100}),
    "cash_missing_amount": _draft(funding={"kind": "cash_contribution"}),
}


@pytest.mark.parametrize("name", sorted(ACCEPTED))
def test_b1_accepts_the_same_drafts_as_before_through_gate_gate(monkeypatch, gate_spy, name):
    out = ACCEPTED[name]
    monkeypatch.setattr(extractors, "intent_parser_call", _canned(out))
    state = pipeline.b1_parse_intent(ROBERT, "in_0001")
    assert [c["node"] for c in gate_spy] == [N4]
    assert state["draft_gate"] == "accepted"
    assert state["draft"] == out                      # same fields, nothing added, nothing dropped
    assert [c["gate"] for c in state["llm_calls"]] == ["accepted"]
    assert state["llm_errors"] == []


@pytest.mark.parametrize("name", sorted(REJECTED))
def test_b1_rejects_the_same_drafts_as_before_through_gate_gate(monkeypatch, gate_spy, name):
    out = REJECTED[name]
    monkeypatch.setattr(extractors, "intent_parser_call", _canned(out))
    state = pipeline.b1_parse_intent(ROBERT, "in_0001")
    assert [c["node"] for c in gate_spy] == [N4]
    # SPEC-AMBIGUITY: §11 has no N4-specific status; the pre-change behaviour (and the gate's) is rejected:schema.
    assert state["draft_gate"] == "rejected:schema"
    assert state["draft"] is None                     # a rejected draft is dropped, never prefilled
    assert [c["gate"] for c in state["llm_calls"]] == ["rejected:schema"]
    assert gate_spy[0]["result"][1] == "rejected:schema"


# ---------------------------------------------------------------- a sell draft is rejected through gate.gate

def test_sell_draft_is_rejected_through_gate_gate_and_never_reaches_the_state(monkeypatch, gate_spy):
    monkeypatch.setattr(extractors, "intent_parser_call", _canned(_draft(action="sell")))
    state = pipeline.b1_parse_intent(ROBERT, "in_0001")
    assert len(gate_spy) == 1 and gate_spy[0]["node"] == N4
    assert gate_spy[0]["output"]["action"] == "sell"
    new, status = gate_spy[0]["result"]
    assert status.startswith("rejected")
    assert "sell" not in json.dumps(new)
    assert state["draft"] is None
    assert state["draft_gate"] == status
    assert "sell" not in json.dumps(state["draft"])
    assert state["llm_calls"][0]["gate"] == status


def test_sell_draft_does_not_become_a_confirmable_intent(monkeypatch):
    monkeypatch.setattr(extractors, "intent_parser_call", _canned(_draft(action="sell")))
    state = pipeline.b1_parse_intent(ROBERT, "in_0001")
    confirmed = pipeline.b2_confirm(state, auto_confirm=True)
    assert confirmed["intent"]["action"] == "buy"      # the user's declared fixture intent, not the draft
    assert confirmed.get("draft") is None


# ---------------------------------------------------------------- the stubbed gate is really what decides

def test_b1_takes_its_status_from_gate_gate_not_from_a_local_rule(monkeypatch):
    seen = []

    def fake_gate(node, old_field, llm_output, context):
        seen.append(node)
        return None, "rejected:evidence"

    monkeypatch.setattr(gate, "gate", fake_gate)
    state = pipeline.b1_parse_intent(ROBERT, "in_0001")      # the recorded draft is a valid buy
    assert seen == [N4]
    assert state["draft_gate"] == "rejected:evidence"
    assert state["draft"] is None
    assert state["llm_calls"][0]["gate"] == "rejected:evidence"


def test_b1_uses_the_draft_the_gate_returns_for_an_accepted_status(monkeypatch):
    returned = _draft(target_weight_pct=12)

    def fake_gate(node, old_field, llm_output, context):
        return copy.deepcopy(returned), "accepted"

    monkeypatch.setattr(gate, "gate", fake_gate)
    state = pipeline.b1_parse_intent(ROBERT, "in_0001")
    # SPEC-AMBIGUITY: either reading (gate's draft or the parsed output) is defensible when they are equal, which is
    # always so for the real gate; here they differ, and the stricter reading is: only what the gate returns is used.
    assert state["draft"] == returned


# ---------------------------------------------------------------- errors and other personas: unchanged

@pytest.mark.parametrize("exc", [bedrock.LLMCacheMiss("miss"), bedrock.LLMOutputInvalid("bad")])
def test_llm_error_never_reaches_the_gate_and_is_reported_as_before(monkeypatch, gate_spy, exc):
    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(extractors, "intent_parser_call", boom)
    state = pipeline.b1_parse_intent(ROBERT, "in_0001")
    assert gate_spy == []
    assert state["draft"] is None
    assert state["draft_gate"] == "rejected:schema"
    assert state["llm_calls"] == []
    assert [e["node"] for e in state["llm_errors"]] == [N4]
    assert type(exc).__name__ in state["llm_errors"][0]["error"]


def test_unknown_intent_id_is_a_value_error_before_any_llm_or_gate_call(monkeypatch, gate_spy):
    def boom(*a, **k):
        raise AssertionError("LLM called")

    monkeypatch.setattr(extractors, "intent_parser_call", boom)
    with pytest.raises(ValueError):
        pipeline.b1_parse_intent(ROBERT, "in_9999")
    assert gate_spy == []


@pytest.mark.parametrize("persona", ["robert", "amira", "mateo"])
def test_every_persona_fixture_goes_through_gate_gate_with_the_same_outcome(tmp_path, monkeypatch, gate_spy, persona):
    fixture = ROBERT if persona == "robert" else _persona_dir(tmp_path, persona)
    intent_id = "in_0001" if persona == "robert" else "in_9001"
    out = _draft(funding={"kind": "pro_rata_all"})
    monkeypatch.setattr(extractors, "intent_parser_call", _canned(out))
    ok = pipeline.b1_parse_intent(fixture, intent_id)
    assert ok["draft_gate"] == "accepted" and ok["draft"] == out
    assert ok["llm_calls"][0]["gate"] == "accepted"

    monkeypatch.setattr(extractors, "intent_parser_call", _canned(_draft(action="sell")))
    bad = pipeline.b1_parse_intent(fixture, intent_id)
    assert bad["draft_gate"] == "rejected:schema" and bad["draft"] is None
    assert [c["node"] for c in gate_spy] == [N4, N4]
    assert [c["result"][1] for c in gate_spy] == ["accepted", "rejected:schema"]
