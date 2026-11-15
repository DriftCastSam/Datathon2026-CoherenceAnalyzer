"""P4.WARNEN: the gate stamps `warn_enabled` on the N1 / N2 / N3 llm: fields.

SPEC §11 step 5: "Confidence: stored as-is. `warn_enabled = confidence >= WARN_MIN_CONF`." (WARN_MIN_CONF = 0.5, §7.4:
soft checks only warn when the source field passed the gate and confidence >= WARN_MIN_CONF.) Step 6 stamps the
rest. Every other gate status and output stays as before (§11 statuses, §1 inv. 4: a kept/rejected field is the previous
value untouched). §3.3: llm: fields are {value, by, confidence, prompt_version, model_id, updated_at, input_hash, ...};
schemas/profile.v2.json must accept the new boolean. soft.py keeps its own comparison (not tested here).
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

from coherence_analyzer import config  # noqa: E402
from coherence_analyzer.gate import gate  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "fixtures" / "robert"
STATUSES = {"accepted", "clamped", "kept:abstain", "rejected:evidence", "rejected:schema"}
STAMP_KEYS = {"by", "prompt_version", "model_id", "updated_at", "input_hash"}
LEVELS = ("low", "low_moderate", "moderate", "moderate_high", "high")

NOTES = "I prefer companies I understand. No hype stocks unless I can explain the business."
EVENTS = {e["event_id"]: e for e in (json.loads(line) for line in (FIX / "events.jsonl").read_text().splitlines()
                                     if line.strip())}
Q_XBB = "ev_robert_0005"
CTX = {"event_ids": set(EVENTS), "events_by_id": copy.deepcopy(EVENTS), "event_id": Q_XBB, "notes": NOTES,
       "themes": (), "model_id": "fixture:hand-written", "prompt_version": "behaviour_classifier.v1",
       "input_hash": "sha256:" + "ab" * 32, "updated_at": "2026-11-14T10:03:00-05:00"}


def _n1(value: str = "moderate_high", confidence: float = 0.55) -> dict:
    return {"abstain": False, "value": value, "confidence": confidence,
            "evidence": [{"event_id": "ev_robert_0007", "signal": "hype_chasing", "note": "asks 10% in one stock"}]}


def _n1_old(value: str = "moderate", confidence: float = 0.5, **extra) -> dict:
    return {"value": value, "by": "llm:behaviour_classifier", "confidence": confidence,
            "prompt_version": "behaviour_classifier.v0", "model_id": "fixture:old",
            "updated_at": "2026-11-01T00:00:00-05:00", "input_hash": "sha256:old",
            "evidence": [{"event_id": "ev_robert_0004", "signal": "hype_chasing", "note": "old"}], **extra}


def _n2(event_id: str = Q_XBB, quote: str = "XBB's duration") -> dict:
    return {"event_id": event_id, "quote": quote, "specificity": 3, "correct_concepts": 3, "risk_awareness": 2,
            "horizon_awareness": 2, "is_investing_question": True, "rationale": "Names a fund and a concept."}


def _n3(*quotes: str, confidence: float | None = None) -> dict:
    out = {"rules": [{"rule_kind": "prefer_attribute", "params": {"attribute": "understandable_business"},
                      "applies_to": "single_stocks", "quote": q} for q in quotes]}
    if confidence is not None:
        out["confidence"] = confidence
    return out


def _n3_old(confidence: float = 0.8, **extra) -> dict:
    return {"value": [], "by": "llm:values_parser", "confidence": confidence, **extra}


def _accepted_n1(confidence: float, old: dict | None = None) -> dict:
    new, status = gate("behaviour_classifier", old if old is not None else _n1_old(), _n1(confidence=confidence), CTX)
    assert status == "accepted"
    return new


# ---------------------------------------------------------------- N1 behaviour_classifier

@pytest.mark.parametrize("conf, expected", [
    (0.8, True), (0.55, True), (0.5, True),      # SPEC §11 step 5: >= WARN_MIN_CONF, so the boundary is enabled
    (0.4999, False), (0.3, False), (0.0, False),
])
def test_n1_warn_enabled_is_confidence_at_least_warn_min_conf(conf, expected):
    new = _accepted_n1(conf)
    assert config.WARN_MIN_CONF == 0.5
    assert new["warn_enabled"] is expected
    assert new["warn_enabled"] is (new["confidence"] >= config.WARN_MIN_CONF)
    assert new["confidence"] == conf          # still stored as-is


def test_n1_clamped_output_is_stamped_too():
    # SPEC §11: a clamped status still goes through steps 5 and 6.
    new, status = gate("behaviour_classifier", _n1_old("low_moderate"), _n1("high", 0.7), CTX)
    assert status == "clamped" and new["value"] == "moderate"
    assert new["warn_enabled"] is True
    new, status = gate("behaviour_classifier", _n1_old("low_moderate"), _n1("high", 0.4), CTX)
    assert status == "clamped"
    assert new["warn_enabled"] is False


def test_n1_first_value_without_previous_field_is_stamped():
    new, status = gate("behaviour_classifier", None, _n1(confidence=0.6), CTX)
    assert status == "accepted"
    assert new["warn_enabled"] is True


def test_n1_output_is_otherwise_identical_apart_from_the_new_key():
    out = _n1("moderate_high", 0.55)
    new, status = gate("behaviour_classifier", _n1_old("moderate"), out, CTX)
    assert status == "accepted"
    assert set(new) == {"value", "confidence", "evidence", "warn_enabled"} | STAMP_KEYS
    assert {k: v for k, v in new.items() if k != "warn_enabled"} == {
        "value": "moderate_high", "confidence": 0.55, "evidence": out["evidence"], "by": "llm:behaviour_classifier",
        "prompt_version": CTX["prompt_version"], "model_id": CTX["model_id"], "updated_at": CTX["updated_at"],
        "input_hash": CTX["input_hash"]}


def test_n1_stale_warn_enabled_on_the_old_field_is_recomputed_not_inherited():
    old = _n1_old("moderate", 0.8, warn_enabled=True)
    assert _accepted_n1(0.3, old)["warn_enabled"] is False
    old = _n1_old("moderate", 0.3, warn_enabled=False)
    assert _accepted_n1(0.7, old)["warn_enabled"] is True


@pytest.mark.parametrize("out, expected_status", [
    ({**_n1(), "abstain": True}, "kept:abstain"),
    ({**_n1(), "evidence": [{"event_id": "ev_robert_9999", "signal": "hype_chasing", "note": "invented"}]},
     "rejected:evidence"),
    ({**_n1(), "value": "extreme"}, "rejected:schema"),
])
@pytest.mark.parametrize("old_extra", [{}, {"warn_enabled": True}, {"warn_enabled": False}])
def test_n1_kept_and_rejected_outputs_return_the_previous_field_untouched(out, expected_status, old_extra):
    # SPEC §1 inv. 4 + §11: no new stamp; the previous field comes back exactly as it was.
    old = _n1_old("moderate", 0.8, **old_extra)
    new, status = gate("behaviour_classifier", copy.deepcopy(old), out, CTX)
    assert status == expected_status
    assert new == old
    assert ("warn_enabled" in new) == ("warn_enabled" in old)


def test_n1_rejection_with_no_previous_field_stays_none():
    new, status = gate("behaviour_classifier", None, {**_n1(), "value": "extreme"}, CTX)
    assert status == "rejected:schema"
    assert new is None


# ---------------------------------------------------------------- N2 question_rubric

def test_n2_warn_enabled_follows_the_code_computed_confidence():
    # SPEC §6.2 N2 / §11 step 5: the model sets no confidence; the stored (code-computed) one drives the flag.
    new, status = gate("question_rubric", None, _n2(), CTX)
    assert status == "accepted"
    assert isinstance(new["warn_enabled"], bool)
    assert new["warn_enabled"] is (new["confidence"] >= config.WARN_MIN_CONF)


def test_n2_flag_flips_as_scored_questions_accumulate():
    # The confidence grows with the number of scored questions (README Assumptions), so both flag values occur
    # on the way to the cap, and each stamp equals its own confidence test.
    qs = {f"ev_q_{i:04d}": {"event_id": f"ev_q_{i:04d}", "type": "question", "text": f"Q{i} about XBB's duration",
                            "ts": f"2026-10-{1 + i:02d}T09:00:00-05:00"} for i in range(6)}
    field, seen = None, []
    for eid in sorted(qs):
        ctx = {**CTX, "event_ids": set(qs), "events_by_id": qs, "event_id": eid}
        field, status = gate("question_rubric", field, _n2(eid), ctx)
        assert status == "accepted"
        assert field["warn_enabled"] is (field["confidence"] >= config.WARN_MIN_CONF)
        seen.append(field["warn_enabled"])
    assert seen[0] is False and seen[-1] is True
    assert seen == sorted(seen)                  # confidence never decreases with more questions, so no flip back


def test_n2_output_is_otherwise_identical_apart_from_the_new_key():
    new, status = gate("question_rubric", None, _n2(), CTX)
    assert status == "accepted"
    assert set(new) == {"value", "confidence", "evidence", "warn_enabled"} | STAMP_KEYS
    assert new["by"] == "llm:question_rubric"
    assert [set(i) for i in new["evidence"]] == [{"event_id", "score"}]     # §3.3 item 3: still {event_id, score}
    assert 0 <= new["value"] <= 1 and 0 <= new["confidence"] <= config.QUESTION_QUALITY_MAX_CONF


def test_n2_non_investing_question_is_ignored_without_a_new_stamp():
    old = {"value": 0.5, "by": "llm:question_rubric", "confidence": 0.6, "evidence": []}
    out = {**_n2(), "is_investing_question": False}
    new, status = gate("question_rubric", copy.deepcopy(old), out, CTX)
    assert status in STATUSES
    assert new == old and "warn_enabled" not in new


def test_n2_rejections_keep_the_previous_field_untouched():
    old = {"value": 0.5, "by": "llm:question_rubric", "confidence": 0.6, "evidence": [], "warn_enabled": True}
    new, status = gate("question_rubric", copy.deepcopy(old), _n2("ev_robert_9999"), CTX)
    assert status == "rejected:evidence" and new == old
    new, status = gate("question_rubric", copy.deepcopy(old), _n2(quote="not in the question"), CTX)
    assert status == "rejected:evidence" and new == old
    new, status = gate("question_rubric", copy.deepcopy(old), {**_n2(), "specificity": 9}, CTX)
    assert status == "rejected:schema" and new == old
    new, status = gate("question_rubric", copy.deepcopy(old), {**_n2(), "abstain": True}, CTX)
    assert status == "kept:abstain" and new == old


# ---------------------------------------------------------------- N3 values_parser

Q1, Q2 = "I prefer companies I understand.", "No hype stocks unless I can explain the business."


@pytest.mark.parametrize("old_conf, expected", [(0.8, True), (0.5, True), (0.4999, False), (0.2, False)])
def test_n3_warn_enabled_uses_the_stored_confidence(old_conf, expected):
    # The gate keeps the previous confidence when N3 emits none (VALUES_DEFAULT_CONF otherwise); the flag follows it.
    new, status = gate("values_parser", _n3_old(old_conf), _n3(Q1, Q2), CTX)
    assert status == "accepted"
    assert new["confidence"] == old_conf
    assert new["warn_enabled"] is expected


def test_n3_first_parse_without_previous_field_is_stamped_from_its_confidence():
    new, status = gate("values_parser", None, _n3(Q1), CTX)
    assert status == "accepted"
    assert new["warn_enabled"] is (new["confidence"] >= config.WARN_MIN_CONF)


def test_n3_output_confidence_when_present_drives_the_flag():
    new, status = gate("values_parser", _n3_old(0.8), _n3(Q1, confidence=0.3), CTX)
    assert status == "accepted"
    assert new["confidence"] == 0.3 and new["warn_enabled"] is False
    new, status = gate("values_parser", _n3_old(0.2), _n3(Q1, confidence=0.5), CTX)
    assert status == "accepted"
    assert new["confidence"] == 0.5 and new["warn_enabled"] is True


def test_n3_output_is_otherwise_identical_apart_from_the_new_key():
    new, status = gate("values_parser", _n3_old(0.8, warn_enabled=False), _n3(Q1, Q2), CTX)
    assert status == "accepted"
    assert set(new) == {"value", "confidence", "warn_enabled"} | STAMP_KEYS
    assert new["by"] == "llm:values_parser"
    assert [r["quote"] for r in new["value"]] == [Q1, Q2]
    for r in new["value"]:
        assert r["approved_by_user"] is False                     # §3.3 #4: parsed rules start unapproved
        assert set(r) == {"rule_kind", "params", "applies_to", "quote", "approved_by_user"}
    assert new["warn_enabled"] is True                            # recomputed from 0.8, not inherited from the old key


@pytest.mark.parametrize("out, expected_status", [
    ({"rules": [], "abstain": True}, "kept:abstain"),
    (_n3("I never want to own tobacco companies."), "rejected:evidence"),
    ({"rules": [{"rule_kind": "nonsense", "params": {}, "applies_to": "all", "quote": Q1}]}, "rejected:schema"),
])
def test_n3_kept_and_rejected_outputs_return_the_previous_field_untouched(out, expected_status):
    old = _n3_old(0.8, warn_enabled=True)
    new, status = gate("values_parser", copy.deepcopy(old), out, CTX)
    assert status == expected_status
    assert new == old
    old = _n3_old(0.3)
    new, status = gate("values_parser", copy.deepcopy(old), out, CTX)
    assert new == old and "warn_enabled" not in new


# ---------------------------------------------------------------- shared

def test_gate_does_not_mutate_its_inputs_when_stamping():
    old, out, ctx = _n1_old("low_moderate"), _n1("high", 0.7), copy.deepcopy(CTX)
    old0, out0 = copy.deepcopy(old), copy.deepcopy(out)
    gate("behaviour_classifier", old, out, ctx)
    assert old == old0 and out == out0 and ctx == CTX


# ---------------------------------------------------------------- schemas/profile.v2.json accepts the key

def _schema() -> dict:
    return json.loads((config.SCHEMAS_DIR / "profile.v2.json").read_text(encoding="utf-8"))


def _robert() -> dict:
    return json.loads((FIX / "profile.json").read_text(encoding="utf-8"))


def _errors(profile: dict) -> list[str]:
    return [e.message for e in Draft202012Validator(_schema()).iter_errors(profile)]


def test_schema_declares_warn_enabled_as_a_boolean_on_llm_fields():
    llm_field = _schema()["$defs"]["llmField"]
    assert llm_field["properties"]["warn_enabled"] == {"type": "boolean"}
    # Existing profiles (and every rejected/kept field) carry no flag: it must not become required.
    assert "warn_enabled" not in llm_field["required"]


def test_robert_fixture_without_the_key_still_validates():
    assert _errors(_robert()) == []


@pytest.mark.parametrize("flag", [True, False])
@pytest.mark.parametrize("path", [("risk", "behavioural"), ("knowledge", "question_quality")])
def test_profile_with_boolean_warn_enabled_validates(path, flag):
    profile = _robert()
    profile[path[0]][path[1]]["warn_enabled"] = flag
    assert _errors(profile) == []


@pytest.mark.parametrize("bad", ["yes", 1, 0.5, None, []])
@pytest.mark.parametrize("path", [("risk", "behavioural"), ("knowledge", "question_quality"),
                                  ("values", "parsed_notes")])
def test_profile_with_non_boolean_warn_enabled_is_rejected(path, bad):
    profile = _robert()
    if path[1] not in profile[path[0]]:
        profile[path[0]][path[1]] = _n3_old(0.8)
        profile[path[0]][path[1]].update(prompt_version="values_parser.v1", model_id="m",
                                          updated_at="2026-11-14T10:03:00-05:00", input_hash="sha256:x")
    profile[path[0]][path[1]]["warn_enabled"] = bad
    assert _errors(profile) != []


def test_gated_outputs_for_all_three_nodes_validate_inside_the_profile():
    profile = _robert()
    n1, s1 = gate("behaviour_classifier", copy.deepcopy(profile["risk"]["behavioural"]), _n1("moderate_high", 0.55), CTX)
    n2, s2 = gate("question_rubric", None, _n2(), CTX)
    n3, s3 = gate("values_parser", None, _n3(Q1, Q2), CTX)
    assert (s1, s2, s3) == ("accepted",) * 3
    profile["risk"]["behavioural"] = n1
    profile["knowledge"]["question_quality"] = n2
    profile["values"]["notes"] = NOTES
    profile["values"]["parsed_notes"] = n3
    assert all("warn_enabled" in f for f in (n1, n2, n3))
    assert _errors(profile) == []
