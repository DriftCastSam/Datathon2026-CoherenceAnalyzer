"""N2 `question_rubric` (P2.N2): schema, gate, mock cache, opt-in knowledge evidence.

Sources: SPEC §3.3 item 3 (question_quality.evidence = [{event_id, score}]), §3.4 events, §4 Flow A (A2 question_rubric
on question events, A4 derive again), §6.1, §6.2 N2 (input {event_id, text}; sub-scores only, code computes
score = sum/12; is_investing_question = false -> ignored; question_quality = EWMA(alpha 0.3) oldest first), §7.1
knowledge.score (0.4 quiz + 0.3 declared + 0.3 question_quality, llm term moves <= 5 pts per update; not a limits
input), §7.4 S2, §11 gate, §1 invariants 2-5; docs/spec-decisions.md D1, D7 point 3 (N2 deferred to P2: while it is
off, knowledge.score comes from declared/quiz fields and the stored question_quality only).

Contract pinned here (P2.N2):
  config.N2_QUESTION_RUBRIC          opt-in flag (env COHERENCE_ANALYZER_N2), default False (D7)
  config.QUESTION_QUALITY_MAX_CONF   cap on the code-computed question_quality confidence (0.8, as N1)
  extractors.n2_events(events)       question events with text, oldest first
  extractors.n2_payload(event)       {"event_id", "text"} and nothing else (no profile field)
  extractors.question_rubric_call(event) -> {output, call_log};  extractors.question_rubric(event) -> output
  derive.question_score(output)      sum of the four sub-scores / 12
  derive.question_quality_ewma(scores)  EWMA alpha 0.3 over scores oldest first (first score seeds it)
  derive.question_quality_confidence(n) min(cap, 0.3 + 0.15 n)  (Robert's fixture: 2 questions -> 0.6)
  gate.gate("question_rubric", old_question_quality, output, context) -> (new_question_quality, status)
     context = {event_ids, events_by_id, event_id (the input event), model_id, prompt_version, input_hash, updated_at}
     The N2 output cites its own `event_id` and a `quote` that must be an exact substring of that event's text.
  pipeline.refresh: with the flag on, Flow A runs N2 on every question event, gates each output into
     knowledge.question_quality, then derives (knowledge.score moves at most 5 points).
"""
import copy
import json
import os
import shutil
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

from coherence_analyzer import config, data, gate as gate_mod, pipeline  # noqa: E402
from coherence_analyzer.coherence import soft  # noqa: E402
from coherence_analyzer.llm import bedrock, extractors  # noqa: E402
from coherence_analyzer.rules import derive, limits as limits_mod, registry  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "fixtures" / "robert"
AS_OF = "2026-11-12"
ROBERT_VALUE = 400_000
NODE = "question_rubric"
SUBSCORES = ("specificity", "correct_concepts", "risk_awareness", "horizon_awareness")
STATUSES = {"accepted", "clamped", "kept:abstain", "rejected:evidence", "rejected:schema"}
LLM_FIELDS = {"risk.behavioural", "knowledge.question_quality", "values.parsed_notes"}
NOT_LIMIT_INPUTS = LLM_FIELDS | {"knowledge.score"}
_REAL_CALL_TOOL = bedrock.call_tool
_FIXTURE_SNAPSHOT = {p.name: p.read_bytes() for p in FIX.iterdir() if p.is_file()}


def _read(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _events() -> list[dict]:
    return [json.loads(line) for line in (FIX / "events.jsonl").read_text().splitlines() if line.strip()]


EVENTS = _events()
EV = {e["event_id"]: e for e in EVENTS}
Q_AI, Q_XBB = "ev_robert_0004", "ev_robert_0005"


@pytest.fixture()
def robert() -> dict:
    return _read(FIX / "profile.json")


def _schema() -> dict:
    return extractors.load_schema(NODE)


def _ctx(event_id: str = Q_XBB, **over) -> dict:
    ctx = {"event_ids": set(EV), "events_by_id": copy.deepcopy(EV), "event_id": event_id,
           "model_id": bedrock.MOCK_MODEL_ID, "prompt_version": "question_rubric.v1",
           "input_hash": "sha256:" + "cd" * 32, "updated_at": "2026-11-14T10:03:00-05:00"}
    ctx.update(over)
    return ctx


def _out(event_id: str = Q_XBB, quote: str = "XBB's duration", s=(3, 3, 2, 2), investing: bool = True,
         rationale: str = "Names a fund and a concept, links it to rates.") -> dict:
    return {"event_id": event_id, "quote": quote, **dict(zip(SUBSCORES, s)), "is_investing_question": investing,
            "rationale": rationale}


def _old_qq(robert: dict) -> dict:
    return copy.deepcopy(robert["knowledge"]["question_quality"])


def _spy(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    def call_tool(node, schema, system, user_payload, prompt_version):
        calls.append({"node": node, "payload": copy.deepcopy(user_payload)})
        return _REAL_CALL_TOOL(node, schema, system, user_payload, prompt_version)

    monkeypatch.setattr(bedrock, "call_tool", call_tool)
    return calls


def _flag(monkeypatch, on: bool) -> None:
    monkeypatch.setattr(config, "N2_QUESTION_RUBRIC", on)


@pytest.fixture(autouse=True)
def _fixtures_untouched():
    yield
    assert {p.name: p.read_bytes() for p in FIX.iterdir() if p.is_file()} == _FIXTURE_SNAPSHOT


# ---------------------------------------------------------------- schema + prompt (§6.1, §6.2 N2)

def test_schema_is_valid_draft_2020_12_and_closed():
    s = _schema()
    Draft202012Validator.check_schema(s)
    assert s["type"] == "object" and s["additionalProperties"] is False
    assert set(s["required"]) >= set(SUBSCORES) | {"is_investing_question", "rationale", "event_id", "quote"}
    for k in SUBSCORES:
        assert s["properties"][k]["type"] == "integer"
        assert s["properties"][k]["minimum"] == 0 and s["properties"][k]["maximum"] == 3
    assert s["properties"]["rationale"]["maxLength"] == 150
    # Sub-scores only: code computes the score; the model sets no score and no confidence.
    assert "score" not in s["properties"] and "confidence" not in s["properties"]


@pytest.mark.parametrize("mutate", [
    lambda o: o.update(specificity=4),
    lambda o: o.update(risk_awareness=-1),
    lambda o: o.update(horizon_awareness=1.5),
    lambda o: o.update(is_investing_question="yes"),
    lambda o: o.update(rationale="x" * 151),
    lambda o: o.update(score=0.9),
    lambda o: o.update(confidence=0.95),
    lambda o: o.pop("correct_concepts"),
    lambda o: o.pop("event_id"),
    lambda o: o.pop("quote"),
])
def test_schema_rejects_bad_outputs(mutate):
    v = Draft202012Validator(_schema())
    good = _out()
    assert v.is_valid(good)
    bad = copy.deepcopy(good)
    mutate(bad)
    assert not v.is_valid(bad)


def test_prompt_file_and_version():
    assert extractors.prompt_version(NODE) == "question_rubric.v1"
    assert (config.PROMPTS_DIR / "question_rubric.v1.md").exists()
    text = extractors.load_prompt(NODE)
    assert "event_id" in text and "quote" in text and "is_investing_question" in text


# ---------------------------------------------------------------- input payload (§6.2 N2: one event {event_id, text})

def test_n2_events_are_the_question_events_oldest_first():
    assert [e["event_id"] for e in extractors.n2_events(EVENTS)] == [Q_AI, Q_XBB]
    shuffled = list(reversed(EVENTS))
    assert [e["event_id"] for e in extractors.n2_events(shuffled)] == [Q_AI, Q_XBB]


def test_n2_payload_is_event_id_and_text_only():
    assert extractors.n2_payload(EV[Q_XBB]) == {"event_id": Q_XBB, "text": EV[Q_XBB]["text"]}


# ---------------------------------------------------------------- mock cache (§6.1, §16)

@pytest.mark.parametrize("eid", [Q_AI, Q_XBB])
def test_mock_cache_hit_for_robert_questions_passes_schema_and_gate(eid):
    res = extractors.question_rubric_call(EV[eid])
    log, out = res["call_log"], res["output"]
    assert log["cache_hit"] is True and log["node"] == NODE and log["prompt_version"] == "question_rubric.v1"
    assert log["input_hash"] == bedrock.input_hash(extractors.n2_payload(EV[eid]))
    Draft202012Validator(_schema()).validate(out)
    assert out["event_id"] == eid and out["quote"] in EV[eid]["text"]
    assert out["is_investing_question"] is True
    assert extractors.question_rubric(EV[eid]) == out
    _, status = gate_mod.gate(NODE, None, out, _ctx(eid, input_hash=log["input_hash"]))
    assert status == "accepted"


def test_mock_scores_match_the_spec_sanity_checks():
    """§6.2 N2 fixture expectations: XBB-duration question >= 0.75, 'next big AI stock' <= 0.35."""
    assert derive.question_score(extractors.question_rubric(EV[Q_XBB])) >= 0.75
    assert derive.question_score(extractors.question_rubric(EV[Q_AI])) <= 0.35


def test_every_recorded_n2_entry_is_schema_valid_and_gated():
    files = sorted(config.LLM_CACHE_DIR.glob(f"{NODE}.*.json"))
    assert files
    v = Draft202012Validator(_schema())
    for f in files:
        doc = _read(f)
        assert doc["cache_key"] == bedrock.cache_key(NODE, doc["prompt_version"], doc["model_id"], doc["user_payload"])
        assert set(doc["user_payload"]) == {"event_id", "text"}
        v.validate(doc["output"])
        ev = {"event_id": doc["user_payload"]["event_id"], "type": "question", "text": doc["user_payload"]["text"],
              "ts": "2026-11-01T00:00:00-05:00"}
        ctx = _ctx(ev["event_id"], event_ids={ev["event_id"]}, events_by_id={ev["event_id"]: ev})
        assert gate_mod.gate(NODE, None, doc["output"], ctx)[1] in {"accepted", "kept:abstain"}


def test_cache_miss_raises_in_mock_mode():
    ev = {"event_id": "ev_robert_0099", "type": "question", "text": "Is gold a hedge against inflation?",
          "ts": "2026-11-13T09:00:00-05:00"}
    with pytest.raises(bedrock.LLMCacheMiss):
        extractors.question_rubric_call(ev)


# ---------------------------------------------------------------- code computes the score (§6.2 N2)

@pytest.mark.parametrize("s,expected", [((0, 0, 0, 0), 0.0), ((3, 3, 3, 3), 1.0), ((3, 3, 2, 1), 0.75),
                                        ((1, 1, 0, 0), 2 / 12)])
def test_question_score_is_sum_over_12(s, expected):
    assert derive.question_score(_out(s=s)) == pytest.approx(expected, abs=1e-4)


def test_ewma_alpha_03_oldest_first():
    assert derive.question_quality_ewma([]) is None
    assert derive.question_quality_ewma([0.4]) == pytest.approx(0.4)
    assert derive.question_quality_ewma([0.2, 0.8]) == pytest.approx(0.3 * 0.8 + 0.7 * 0.2, abs=1e-4)
    a, b, c = 0.1, 0.9, 0.5
    e = 0.3 * c + 0.7 * (0.3 * b + 0.7 * a)
    assert derive.question_quality_ewma([a, b, c]) == pytest.approx(e, abs=1e-4)
    assert config.QUESTION_EWMA_ALPHA == 0.3


# ---------------------------------------------------------------- confidence cap

def test_confidence_is_code_computed_and_capped():
    cap = config.QUESTION_QUALITY_MAX_CONF
    assert cap == pytest.approx(0.8)
    assert derive.question_quality_confidence(2) == pytest.approx(0.6)       # Robert fixture: 2 questions -> 0.6
    values = [derive.question_quality_confidence(n) for n in range(0, 60)]
    assert all(0 <= v <= cap for v in values)
    assert values == sorted(values) and values[-1] == pytest.approx(cap)


def test_gate_confidence_never_exceeds_the_cap(robert):
    evs = {f"ev_q_{i:03d}": {"event_id": f"ev_q_{i:03d}", "type": "question", "text": f"Question {i} about XBB's duration",
                             "ts": f"2026-10-{1 + i % 28:02d}T09:00:00-05:00"} for i in range(12)}
    field = None
    for eid in sorted(evs):
        field, status = gate_mod.gate(NODE, field, _out(eid, quote="XBB's duration"),
                                      _ctx(eid, event_ids=set(evs), events_by_id=evs))
        assert status == "accepted"
        assert 0 <= field["confidence"] <= config.QUESTION_QUALITY_MAX_CONF
    assert field["confidence"] == pytest.approx(config.QUESTION_QUALITY_MAX_CONF)


# ---------------------------------------------------------------- gate: evidence (§11 step 2, inv. 5)

@pytest.mark.parametrize("eid", ["ev_robert_9999", "ev_invented", ""])
def test_gate_rejects_invented_event_id(robert, eid):
    old = _old_qq(robert)
    new, status = gate_mod.gate(NODE, copy.deepcopy(old), _out(event_id=eid), _ctx(Q_XBB))
    assert status == "rejected:evidence"
    assert new == old


def test_gate_rejects_an_existing_event_that_is_not_the_input_event(robert):
    old = _old_qq(robert)
    new, status = gate_mod.gate(NODE, copy.deepcopy(old), _out(event_id=Q_AI, quote="AI stock"), _ctx(Q_XBB))
    assert status == "rejected:evidence" and new == old


def test_gate_rejects_a_non_question_event(robert):
    old = _old_qq(robert)
    out = _out(event_id="ev_robert_0007", quote="10% in NVDA")
    new, status = gate_mod.gate(NODE, copy.deepcopy(old), out, _ctx("ev_robert_0007"))
    assert status == "rejected:evidence" and new == old


@pytest.mark.parametrize("quote", [
    "xbb's duration",                       # case folded
    "XBB's  duration",                      # whitespace changed
    "XBB’s duration",                       # typographic apostrophe
    "the duration of XBB",                  # paraphrase
    "next big one",                         # substring of another event
    "",                                     # empty
])
def test_gate_rejects_quotes_that_are_not_exact_substrings_of_the_question(robert, quote):
    old = _old_qq(robert)
    new, status = gate_mod.gate(NODE, copy.deepcopy(old), _out(quote=quote), _ctx(Q_XBB))
    assert status == "rejected:evidence" and new == old


@pytest.mark.parametrize("mutate", [
    lambda o: o.update(specificity=4),
    lambda o: o.update(correct_concepts=True),
    lambda o: o.update(rationale="x" * 151),
    lambda o: o.update(is_investing_question=1),
    lambda o: o.update(score=0.9),
    lambda o: o.update(confidence=0.99),
])
def test_gate_reasserts_enum_and_range(robert, mutate):
    old = _old_qq(robert)
    out = _out()
    mutate(out)
    new, status = gate_mod.gate(NODE, copy.deepcopy(old), out, _ctx(Q_XBB))
    assert status == "rejected:schema" and new == old


def test_gate_abstain_keeps_old(robert):
    old = _old_qq(robert)
    out = {**_out(), "abstain": True}
    new, status = gate_mod.gate(NODE, copy.deepcopy(old), out, _ctx(Q_XBB))
    assert status == "kept:abstain" and new == old


def test_gate_non_investing_question_is_ignored():
    ev = {"event_id": "ev_x_0001", "type": "question", "text": "What time does the branch open?",
          "ts": "2026-11-11T09:00:00-05:00"}
    ctx = _ctx("ev_x_0001", event_ids={"ev_x_0001"}, events_by_id={"ev_x_0001": ev})
    new, status = gate_mod.gate(NODE, None, _out("ev_x_0001", quote="branch open", s=(1, 0, 0, 0), investing=False), ctx)
    assert status == "kept:abstain" and new is None


# ---------------------------------------------------------------- gate: accepted field (§3.3 item 3, §11 steps 5-6)

def test_gate_accepted_field_shape_and_stamp(robert):
    ctx = _ctx(Q_XBB)
    new, status = gate_mod.gate(NODE, None, _out(s=(3, 3, 2, 2)), ctx)
    assert status == "accepted"
    assert new["by"] == "llm:question_rubric"
    for k in ("prompt_version", "model_id", "input_hash", "updated_at"):
        assert new[k] == ctx[k]
    assert new["evidence"] == [{"event_id": Q_XBB, "score": pytest.approx(10 / 12, abs=1e-4)}]
    assert new["value"] == pytest.approx(10 / 12, abs=1e-4)
    assert new["confidence"] == pytest.approx(derive.question_quality_confidence(1))
    # The quote is checked, not stored: evidence items are exactly {event_id, score} (§3.3 item 3).
    assert all(set(i) == {"event_id", "score"} for i in new["evidence"])


def test_gate_merges_oldest_first_and_rescoring_replaces(robert):
    old = _old_qq(robert)                                             # fixture: ev_0005 0.9, ev_0004 0.3
    f1, s1 = gate_mod.gate(NODE, copy.deepcopy(old), _out(s=(3, 3, 3, 3)), _ctx(Q_XBB))
    assert s1 == "accepted"
    assert [i["event_id"] for i in f1["evidence"]] == [Q_AI, Q_XBB]   # oldest first (event ts)
    assert {i["event_id"]: i["score"] for i in f1["evidence"]} == {Q_AI: 0.3, Q_XBB: pytest.approx(1.0)}
    assert f1["value"] == pytest.approx(0.3 * 1.0 + 0.7 * 0.3, abs=1e-4)
    f2, s2 = gate_mod.gate(NODE, f1, _out(Q_AI, quote="AI stock", s=(0, 0, 0, 0)), _ctx(Q_AI))
    assert s2 == "accepted"
    assert {i["event_id"]: i["score"] for i in f2["evidence"]} == {Q_AI: 0.0, Q_XBB: pytest.approx(1.0)}
    assert f2["value"] == pytest.approx(0.3, abs=1e-4)
    assert f2["confidence"] == pytest.approx(derive.question_quality_confidence(2))


# ---------------------------------------------------------------- opt-in flag (D7 point 3)

def test_flag_defaults_off():
    assert config.N2_QUESTION_RUBRIC is False


def test_flag_off_refresh_calls_no_n2_and_keeps_robert_at_74(tmp_path, monkeypatch, robert):
    _flag(monkeypatch, False)
    monkeypatch.chdir(tmp_path)
    calls = _spy(monkeypatch)
    p = pipeline.refresh(FIX)["profile"]
    assert NODE not in {c["node"] for c in calls}
    assert p["knowledge"]["score"]["value"] == pytest.approx(74, abs=1e-9)
    assert p["knowledge"]["question_quality"] == robert["knowledge"]["question_quality"]


def _refresh_on(tmp_path, monkeypatch):
    _flag(monkeypatch, True)
    monkeypatch.chdir(tmp_path)
    calls = _spy(monkeypatch)
    return pipeline.refresh(FIX), calls


def test_flag_on_refresh_runs_n2_once_per_question_with_event_payload_only(tmp_path, monkeypatch):
    out, calls = _refresh_on(tmp_path, monkeypatch)
    n2 = [c for c in calls if c["node"] == NODE]
    assert [c["payload"] for c in n2] == [extractors.n2_payload(EV[Q_AI]), extractors.n2_payload(EV[Q_XBB])]
    audit = [c for c in out["audit"]["llm_calls"] if c["node"] == NODE]
    assert len(audit) == 2 and all(c["gate"] == "accepted" and c["cache_hit"] is True for c in audit)
    assert not [e for e in out["audit"]["llm_errors"] if e.get("node") == NODE]


def test_flag_on_question_quality_enters_knowledge_score_as_7_1(tmp_path, monkeypatch, robert):
    """§7.1: knowledge.score = 0.4 quiz*100 + 0.3 declared_map + 0.3 question_quality*100 (llm term moves <= 5 pts from
    the previous run), a rule: field with confidence < 1; question_quality is the llm: field with N2 provenance."""
    out, _ = _refresh_on(tmp_path, monkeypatch)
    p = out["profile"]
    qq = p["knowledge"]["question_quality"]
    assert qq["by"] == "llm:question_rubric" and qq["prompt_version"] == "question_rubric.v1"
    assert qq["model_id"] == bedrock.MOCK_MODEL_ID
    scores = [derive.question_score(extractors.question_rubric(EV[e])) for e in (Q_AI, Q_XBB)]
    assert [i["event_id"] for i in qq["evidence"]] == [Q_AI, Q_XBB]
    assert [i["score"] for i in qq["evidence"]] == pytest.approx(scores, abs=1e-4)
    assert qq["value"] == pytest.approx(derive.question_quality_ewma(scores), abs=1e-4)
    assert qq["confidence"] == pytest.approx(derive.question_quality_confidence(2))

    base = 0.4 * 0.8 * 100 + 0.3 * 78                                   # quiz 0.8, advanced
    prev_term = 74 - base                                               # stored score 74 -> previous llm term 18.6
    term = 0.3 * qq["value"] * 100
    term = max(prev_term - 5, min(prev_term + 5, term))
    ks = p["knowledge"]["score"]
    assert ks["value"] == pytest.approx(round(base + term, 1), abs=1e-9)
    assert abs(ks["value"] - 74) <= 5 + 1e-9
    assert ks["by"] == "rule:knowledge_score" and ks["confidence"] < 1
    assert ks["history"][-1] == {"date": AS_OF, "value": ks["value"]}
    assert ks["value"] == pytest.approx(69.0, abs=1e-9)                 # recorded rubric -> clamped -5 (README)
    assert "knowledge.score" in {d["path"] for d in out["profile_diff"]}


def test_flag_on_unclamped_formula_on_a_first_run(robert):
    """No previous score -> no clamp: the N2 term enters at full weight."""
    p = copy.deepcopy(robert)
    p["knowledge"].pop("score")
    field = None
    for eid in (Q_AI, Q_XBB):
        res = extractors.question_rubric_call(EV[eid])
        field, status = gate_mod.gate(NODE, field, res["output"], _ctx(eid, input_hash=res["call_log"]["input_hash"]))
        assert status == "accepted"
    p["knowledge"]["question_quality"] = field
    d = derive.derive_all(p, portfolio_value_cad=ROBERT_VALUE, as_of=AS_OF)
    expected = round(0.4 * 0.8 * 100 + 0.3 * 78 + 0.3 * field["value"] * 100, 1)
    assert d["knowledge"]["score"]["value"] == pytest.approx(expected, abs=1e-9)


def test_flag_on_cache_miss_keeps_previous_value_and_is_audited(tmp_path, monkeypatch, robert):
    work = tmp_path / "robert"
    shutil.copytree(FIX, work)
    extra = {"event_id": "ev_robert_0006", "profile_id": "persona-robert", "ts": "2026-11-13T09:00:00-05:00",
             "type": "question", "text": "Is gold a hedge against inflation?", "payload": None, "source": "ui_chat"}
    with (work / "events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(extra) + "\n")
    _flag(monkeypatch, True)
    monkeypatch.chdir(tmp_path)
    out = pipeline.refresh(work)
    errs = [e for e in out["audit"]["llm_errors"] if e.get("node") == NODE]
    assert errs and "LLMCacheMiss" in errs[0]["error"]
    qq = out["profile"]["knowledge"]["question_quality"]
    assert "ev_robert_0006" not in {i["event_id"] for i in qq["evidence"]}
    assert {i["event_id"] for i in qq["evidence"]} == {Q_AI, Q_XBB}       # the two recorded calls still apply


# ---------------------------------------------------------------- invariant 2: N2 never feeds limits

def test_flag_on_limits_and_trace_unchanged(tmp_path, monkeypatch, robert):
    out, _ = _refresh_on(tmp_path, monkeypatch)
    p = out["profile"]
    assert p["limits"] == robert["limits"]
    assert p["limits_trace"] == robert["limits_trace"]
    for name, paths in p["limits_trace"].items():
        closure = set(paths)
        for path in paths:
            closure |= set(registry.expand(path))
        assert not (closure & NOT_LIMIT_INPUTS), name


@pytest.mark.parametrize("s", [(0, 0, 0, 0), (3, 3, 3, 3)])
def test_extreme_n2_outputs_do_not_change_limits(robert, s):
    base, base_trace = limits_mod.build_limits(
        derive.derive_all(robert, portfolio_value_cad=ROBERT_VALUE, as_of=AS_OF), ROBERT_VALUE)
    p = copy.deepcopy(robert)
    field = p["knowledge"]["question_quality"]
    for eid, quote in ((Q_AI, "AI stock"), (Q_XBB, "XBB's duration")):
        field, status = gate_mod.gate(NODE, field, _out(eid, quote=quote, s=s), _ctx(eid))
        assert status == "accepted"
    p["knowledge"]["question_quality"] = field
    p["knowledge"].pop("score")                                      # first run: the N2 term is not even clamped
    lim, trace = limits_mod.build_limits(derive.derive_all(p, portfolio_value_cad=ROBERT_VALUE, as_of=AS_OF),
                                         ROBERT_VALUE)
    strip = lambda b: {k: v for k, v in b.items() if k not in {"_note", "by"}}  # noqa: E731
    assert strip(lim) == strip(base) and trace == base_trace


def test_question_quality_is_not_a_registered_rule_output():
    assert "knowledge.question_quality" not in registry.REGISTRY
    assert "knowledge.question_quality" in registry.expand("knowledge.score")


# ---------------------------------------------------------------- invariant 3: S2 only warning / info

def _low_knowledge_profile(robert: dict, s) -> dict:
    p = copy.deepcopy(robert)
    p["knowledge"]["quiz_score"] = 0.2
    p["knowledge"]["declared_level"] = "novice"
    p["knowledge"].pop("score")
    field = None
    for eid, quote in ((Q_AI, "AI stock"), (Q_XBB, "XBB's duration")):
        field, _ = gate_mod.gate(NODE, field, _out(eid, quote=quote, s=s), _ctx(eid))
    p["knowledge"]["question_quality"] = field
    return derive.derive_all(p, portfolio_value_cad=ROBERT_VALUE, as_of=AS_OF)


@pytest.mark.parametrize("s", [(0, 0, 0, 0), (1, 1, 1, 1), (3, 3, 3, 3)])
@pytest.mark.parametrize("weights", [
    {"XIU.TO": 0.5, "XBB.TO": 0.5},
    {"NVDA": 0.10, "XIU.TO": 0.4, "XBB.TO": 0.5},
    {"BTC-USD": 0.05, "XIU.TO": 0.45, "XBB.TO": 0.5},
])
def test_s2_with_n2_driven_score_is_never_a_violation(robert, s, weights):
    assets = data.load_assets()
    p = _low_knowledge_profile(robert, s)
    checks = soft.s2(p, weights, assets)
    assert checks and all(c.kind == "soft" and c.status in {"pass", "warning", "info", "unknown"} for c in checks)
    score = p["knowledge"]["score"]["value"]
    complex_held = bool(soft.complex_holdings(weights, assets))
    if score < soft.S2_MIN_SCORE and complex_held:
        assert checks[0].status == "warning"
    assert "knowledge.question_quality" in checks[0].derived_from


def test_low_n2_score_produces_an_s2_warning_not_a_violation(robert):
    p = _low_knowledge_profile(robert, (0, 0, 0, 0))
    assert p["knowledge"]["score"]["value"] < soft.S2_MIN_SCORE
    [c] = soft.s2(p, {"NVDA": 0.10, "XIU.TO": 0.4, "XBB.TO": 0.5}, data.load_assets())
    assert c.status == "warning"
