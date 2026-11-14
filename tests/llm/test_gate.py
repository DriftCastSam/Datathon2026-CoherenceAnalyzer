"""Gate (SPEC §11, §1 inv. 4-5, §15 test_gate).

Contract (coherence_analyzer/gate.py): gate(node, old_field, llm_output, context) -> (new_field, status)
  context = {"event_ids": set[str], "notes": str, "model_id", "prompt_version", "input_hash"}
Steps in order: abstain -> evidence -> enum/range -> movement clamp -> confidence -> stamp.
Status in {accepted, clamped, kept:abstain, rejected:evidence, rejected:schema}.
"""
import copy

import pytest

from coherence_analyzer.gate import gate

LEVELS = ("low", "low_moderate", "moderate", "moderate_high", "high")   # SPEC §3.3
STATUSES = {"accepted", "clamped", "kept:abstain", "rejected:evidence", "rejected:schema"}

EVENT_IDS = {"ev_robert_0004", "ev_robert_0005", "ev_robert_0007"}      # SPEC §16
NOTES = "I prefer companies I understand. No hype stocks unless I can explain the business."
CTX = {
    "event_ids": EVENT_IDS,
    "notes": NOTES,
    "model_id": "fixture:hand-written",
    "prompt_version": "behaviour_classifier.v1",
    "input_hash": "sha256:" + "ab" * 32,
}


def _old(value: str | None = "low_moderate") -> dict | None:
    if value is None:
        return None
    return {"value": value, "by": "llm:behaviour_classifier", "confidence": 0.5,
            "prompt_version": "behaviour_classifier.v0", "model_id": "fixture:old",
            "updated_at": "2026-11-01T00:00:00-05:00", "input_hash": "sha256:old",
            "evidence": [{"event_id": "ev_robert_0004", "signal": "hype_chasing", "note": "old"}]}


def _n1(value: str = "moderate_high", confidence: float = 0.55, evidence: list | None = None,
        abstain: bool = False) -> dict:
    """SPEC §6.2 N1 output shape."""
    if evidence is None:
        evidence = [{"event_id": "ev_robert_0007", "signal": "hype_chasing",
                     "note": "asks 10% in one stock after news"}]
    return {"abstain": abstain, "value": value, "confidence": confidence, "evidence": evidence}


def _n3(*quotes: str) -> dict:
    """SPEC §6.2 N3 output shape."""
    return {"rules": [{"rule_kind": "prefer_attribute", "params": {"attribute": "understandable_business"},
                       "applies_to": "single_stocks", "quote": q} for q in quotes]}


# ---------------------------------------------------------------- evidence (§11 step 2, inv. 5)

def test_invented_event_id_is_rejected_and_old_value_kept():
    old = _old("low_moderate")
    out = _n1(evidence=[{"event_id": "ev_robert_9999", "signal": "hype_chasing", "note": "made up"}])
    new, status = gate("behaviour_classifier", copy.deepcopy(old), out, CTX)
    assert status == "rejected:evidence"
    assert new == old


def test_one_invented_event_id_among_valid_ones_rejects_the_whole_output():
    # SPEC §1 inv. 5: "Invented evidence means the whole output is rejected."
    old = _old("low_moderate")
    out = _n1(value="moderate", evidence=[
        {"event_id": "ev_robert_0007", "signal": "hype_chasing", "note": "real"},
        {"event_id": "ev_robert_9999", "signal": "concentration_request", "note": "invented"},
    ])
    new, status = gate("behaviour_classifier", copy.deepcopy(old), out, CTX)
    assert status == "rejected:evidence"
    assert new == old


def test_invented_event_id_with_no_previous_value_returns_none():
    out = _n1(evidence=[{"event_id": "ev_nope", "signal": "hype_chasing", "note": "x"}])
    new, status = gate("behaviour_classifier", None, out, CTX)
    assert status == "rejected:evidence"
    assert new is None


def test_quote_not_in_notes_is_rejected_and_old_value_kept():
    old = {"value": [], "by": "llm:values_parser", "confidence": 0.8}
    out = _n3("I never want to own tobacco companies.")
    new, status = gate("values_parser", copy.deepcopy(old), out, CTX)
    assert status == "rejected:evidence"
    assert new == old


@pytest.mark.parametrize("quote", [
    "i prefer companies i understand.",            # case differs
    "I prefer  companies I understand.",           # whitespace differs
    "I prefer companies I understand. Always.",    # longer than the notes
])
def test_quote_must_be_an_exact_substring_of_notes(quote):
    # SPEC §6.2 N3: "quote must be an exact substring of notes" (no case or whitespace folding).
    old = {"value": [], "by": "llm:values_parser", "confidence": 0.8}
    new, status = gate("values_parser", copy.deepcopy(old), _n3(quote), CTX)
    assert status == "rejected:evidence"
    assert new == old


def test_one_bad_quote_among_good_ones_rejects_the_whole_values_output():
    old = {"value": [], "by": "llm:values_parser", "confidence": 0.8}
    out = _n3("I prefer companies I understand.", "No unicorns ever.")
    new, status = gate("values_parser", copy.deepcopy(old), out, CTX)
    assert status == "rejected:evidence"
    assert new == old


def test_exact_substring_quotes_are_accepted_and_start_unapproved():
    # SPEC §3.3 #4 and §6.2 N3: parsed rules start with approved_by_user false.
    out = _n3("I prefer companies I understand.", "No hype stocks unless I can explain the business.")
    old = {"value": [], "by": "llm:values_parser", "confidence": 0.8}
    new, status = gate("values_parser", old, out, CTX)
    assert status == "accepted"
    assert new["by"] == "llm:values_parser"
    assert [r["quote"] for r in new["value"]] == [r["quote"] for r in out["rules"]]
    for r in new["value"]:
        assert r["approved_by_user"] is False
        assert {"rule_kind", "params", "applies_to", "quote"} <= set(r)


# ---------------------------------------------------------------- abstain (§11 step 1)

def test_abstain_keeps_the_old_value():
    old = _old("moderate")
    new, status = gate("behaviour_classifier", copy.deepcopy(old), {"abstain": True}, CTX)
    assert status == "kept:abstain"
    assert new == old


def test_abstain_with_no_previous_value_returns_none():
    new, status = gate("behaviour_classifier", None, {"abstain": True}, CTX)
    assert status == "kept:abstain"
    assert new is None


def test_abstain_is_checked_before_evidence():
    # SPEC §11: step 1 is abstain, step 2 is evidence; an abstaining output is never "rejected".
    old = _old("moderate")
    out = _n1(abstain=True, evidence=[{"event_id": "ev_nope", "signal": "hype_chasing", "note": "x"}])
    new, status = gate("behaviour_classifier", copy.deepcopy(old), out, CTX)
    assert status == "kept:abstain"
    assert new == old


# ---------------------------------------------------------------- movement clamp (§11 step 4)

def test_behavioural_jump_low_moderate_to_high_is_clamped_to_moderate():
    new, status = gate("behaviour_classifier", _old("low_moderate"), _n1(value="high"), CTX)
    assert status == "clamped"
    assert new["value"] == "moderate"


@pytest.mark.parametrize("old_v", LEVELS)
@pytest.mark.parametrize("new_v", LEVELS)
def test_behavioural_moves_at_most_one_level_per_run(old_v, new_v):
    d = LEVELS.index(new_v) - LEVELS.index(old_v)
    expected = LEVELS[LEVELS.index(old_v) + max(-1, min(1, d))]
    new, status = gate("behaviour_classifier", _old(old_v), _n1(value=new_v), CTX)
    assert new["value"] == expected
    assert status == ("clamped" if abs(d) > 1 else "accepted")


def test_first_behavioural_value_has_no_previous_level_so_is_not_clamped():
    new, status = gate("behaviour_classifier", None, _n1(value="high"), CTX)
    assert status == "accepted"
    assert new["value"] == "high"


# ---------------------------------------------------------------- accepted path, confidence, stamp (§11 steps 5-6)

def test_accepted_output_is_stamped_and_confidence_stored_as_is():
    out = _n1(value="moderate_high", confidence=0.55)
    new, status = gate("behaviour_classifier", _old("moderate"), out, CTX)
    assert status == "accepted"
    assert new["value"] == "moderate_high"
    assert new["by"] == "llm:behaviour_classifier"
    assert new["confidence"] == 0.55
    assert new["prompt_version"] == CTX["prompt_version"]
    assert new["model_id"] == CTX["model_id"]
    assert new["input_hash"] == CTX["input_hash"]
    assert isinstance(new["updated_at"], str) and new["updated_at"]
    assert new["evidence"] == out["evidence"]


def test_clamped_output_is_still_stamped_with_its_own_evidence():
    out = _n1(value="high", confidence=0.7)
    new, status = gate("behaviour_classifier", _old("low_moderate"), out, CTX)
    assert status == "clamped"
    assert new["confidence"] == 0.7
    assert new["by"] == "llm:behaviour_classifier"
    assert new["input_hash"] == CTX["input_hash"]
    assert new["evidence"] == out["evidence"]


def test_low_confidence_is_stored_as_is_not_rejected():
    # SPEC §11 step 5: confidence is stored as-is; warn_enabled (>= WARN_MIN_CONF) is derived, not enforced here.
    new, status = gate("behaviour_classifier", _old("moderate"), _n1(value="moderate", confidence=0.3), CTX)
    assert status == "accepted"
    assert new["confidence"] == 0.3


def test_gate_does_not_mutate_its_inputs():
    old, out, ctx = _old("low_moderate"), _n1(value="high"), copy.deepcopy(CTX)
    old0, out0 = copy.deepcopy(old), copy.deepcopy(out)
    gate("behaviour_classifier", old, out, ctx)
    assert old == old0 and out == out0 and ctx == CTX


# ---------------------------------------------------------------- enum / range (§11 step 3)

def test_value_outside_the_level_enum_is_rejected_as_schema():
    old = _old("moderate")
    new, status = gate("behaviour_classifier", copy.deepcopy(old), _n1(value="extreme"), CTX)
    assert status == "rejected:schema"
    assert new == old


@pytest.mark.parametrize("conf", [0.81, 1.0, -0.1])
def test_confidence_out_of_range_is_rejected_as_schema(conf):
    # SPEC-AMBIGUITY: §6.2 N1 says confidence <= 0.8 "by construction"; read as a hard schema bound (config BEHAVIOUR_MAX_CONF).
    old = _old("moderate")
    new, status = gate("behaviour_classifier", copy.deepcopy(old), _n1(value="moderate", confidence=conf), CTX)
    assert status == "rejected:schema"
    assert new == old


def test_unknown_signal_is_rejected_as_schema():
    old = _old("moderate")
    out = _n1(evidence=[{"event_id": "ev_robert_0007", "signal": "vibes", "note": "x"}])
    new, status = gate("behaviour_classifier", copy.deepcopy(old), out, CTX)
    assert status == "rejected:schema"
    assert new == old


def test_evidence_is_checked_before_enum():
    # SPEC §11 order: evidence (step 2) precedes enum/range (step 3).
    old = _old("moderate")
    out = _n1(value="extreme", evidence=[{"event_id": "ev_nope", "signal": "hype_chasing", "note": "x"}])
    new, status = gate("behaviour_classifier", copy.deepcopy(old), out, CTX)
    assert status == "rejected:evidence"
    assert new == old


def test_every_status_returned_is_in_the_spec_vocabulary():
    cases = [
        (_old("moderate"), {"abstain": True}),
        (_old("moderate"), _n1(value="moderate_high")),
        (_old("low"), _n1(value="high")),
        (_old("low"), _n1(evidence=[{"event_id": "x", "signal": "hype_chasing", "note": "n"}])),
        (_old("low"), _n1(value="extreme")),
    ]
    got = {gate("behaviour_classifier", old, out, CTX)[1] for old, out in cases}
    assert got == STATUSES      # all five statuses are reachable and nothing else is returned
