"""P3.FLOWA: POST /profiles/{id}/events runs Flow A for question / quiz_result / profile_edit / note events.

Sources: SPEC §4 Flow A (A1 derive -> A2 extractors only if inputs changed -> A3 gate -> A4 derive -> A5 limits +
limits_trace -> A6 validate, bump meta.version, save, audit), §3.4 (event types and payloads), §3.1 (quiz_score),
§12 (POST /profiles/{id}/events triggers Flow A), §10 (audit record), §11 (gate), §1 inv. 1-3, docs/spec-decisions.md
D7 (notes -> N3 -> gate; rules only, no N1/N2 in Flow A; parsed rules start unapproved), D10.

Everything runs offline with COHERENCE_ANALYZER_LLM=mock; the notes used for N3 are the recorded ones
(Robert's and Amira's, fixtures/llm_cache), so the mock cache hits. The API works on a copy under
`store.root()/profiles/<profile_id>/`; the last test checks `fixtures/` was never written.

Response shape (spec leaves it open; the task text says "the event plus the refreshed profile version / diff"):
`{"event": {...}, "run_id": ..., "profile_diff": [...], "profile_version": N}` for Flow A events (order_intent keeps
its `{event, intent}` shape). `profile_version` may also be given as `profile.meta.version`.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

from coherence_analyzer import api, data  # noqa: E402
from coherence_analyzer.rules import limits as limits_mod  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
ROBERT = "persona-robert"
AMIRA = "persona-amira"
TS = "2026-11-15T09:30:00-05:00"
ROBERT_NOTES = "I prefer companies I understand. No hype stocks unless I can explain the business."
AMIRA_NOTES = "I love AI and new tech but I don't want everything in tech."
UNRECORDED_NOTES = "Prefer Canadian dividend payers; nothing that depends on a single product launch."
BAD_STATUS = (400, 403, 409, 422)          # a refused edit: never 2xx, never 5xx
REFUSED_PATH_STATUS = (400, 403, 422)


def _hashes() -> dict[str, str]:
    return {str(p.relative_to(FIXTURES)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(FIXTURES.rglob("*")) if p.is_file()}


_FIXTURE_HASHES = _hashes()                                          # taken before any test runs


# ---------------------------------------------------------------- fixtures and helpers

@pytest.fixture(autouse=True)
def store_dir(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "store"
    d.mkdir()
    monkeypatch.setenv("COHERENCE_ANALYZER_STORE_DIR", str(d))
    monkeypatch.setenv("COHERENCE_ANALYZER_LLM", "mock")
    return d


@pytest.fixture()
def client() -> TestClient:
    return TestClient(api.app)


def _work(store_dir: Path, pid: str) -> Path:
    return store_dir / "profiles" / pid


def _events_file(store_dir: Path, pid: str) -> Path:
    return _work(store_dir, pid) / "events.jsonl"


def _profile(client: TestClient, pid: str) -> dict:
    r = client.get(f"/profiles/{pid}")
    assert r.status_code == 200, r.text
    return r.json()


def _post(client: TestClient, pid: str, etype: str, text: str, payload: dict | None = None):
    return client.post(f"/profiles/{pid}/events",
                       json={"type": etype, "text": text, "ts": TS, "source": "ui_chat", "payload": payload})


def _ok(client: TestClient, pid: str, etype: str, text: str, payload: dict | None = None) -> dict:
    r = _post(client, pid, etype, text, payload)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _edit(path: str, old, new) -> dict:
    return {"path": path, "old": old, "new": new}


def _version(body: dict) -> int:
    if "profile_version" in body:
        return body["profile_version"]
    assert isinstance(body.get("profile"), dict), f"no profile version in the response: {sorted(body)}"
    return body["profile"]["meta"]["version"]


def _audit(client: TestClient, body: dict) -> dict:
    assert isinstance(body.get("run_id"), str) and body["run_id"], "Flow A response must carry its run_id (§4)"
    r = client.get(f"/audit/{body['run_id']}")
    assert r.status_code == 200, r.text
    return r.json()


def _n3_calls(audit: dict) -> list[dict]:
    return [c for c in audit["llm_calls"] if c["node"] == "values_parser"]


def _event_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _limits_for(profile: dict, pid: str) -> dict:
    """Limits rebuilt from a profile by the rules module (invariant 2: no llm: input)."""
    fixture = {ROBERT: "robert", AMIRA: "amira"}[pid]
    pf = data.load_portfolio(FIXTURES / fixture / "portfolio.json", data.load_assets())
    lim, _ = limits_mod.build_limits(profile, pf["value_cad"])
    return json.loads(json.dumps(lim))


def _baseline(client: TestClient, pid: str) -> dict:
    """A first Flow A run (a note event) so later comparisons are refreshed-vs-refreshed, not fixture-vs-refreshed."""
    _ok(client, pid, "note", "Starting a session.")
    return _profile(client, pid)


def _assert_untouched(client, store_dir, pid, before_profile: dict, before_events: bytes) -> None:
    assert _events_file(store_dir, pid).read_bytes() == before_events, "a refused event must not be appended"
    assert _profile(client, pid) == before_profile, "a refused event must not change the working profile"


# ---------------------------------------------------------------- Flow A is triggered by the four event types

@pytest.mark.parametrize("etype,text,payload", [
    ("question", "What does duration mean for a bond fund?", None),
    ("note", "Thinking about my retirement date.", None),
    ("quiz_result", "Knowledge quiz kq_v1 completed.", {"score": 0.8, "quiz_id": "kq_v1"}),
    ("profile_edit", "Changed my fee ceiling.", _edit("preferences.max_fee_mer_pct", 0.5, 0.5)),
])
def test_every_flow_a_event_type_runs_flow_a_and_returns_event_run_and_version(client, store_dir, etype, text, payload):
    # §4 Trigger + §12: a question / quiz_result / profile_edit / note event appends the event, runs Flow A (run_id,
    # audit flow "A") and the response returns the event plus the refreshed profile version and diff.
    before = _profile(client, ROBERT)
    body = _ok(client, ROBERT, etype, text, payload)
    ev = body["event"]
    assert ev["event_id"] == "ev_robert_0008" and ev["type"] == etype and ev["profile_id"] == ROBERT
    assert ev["text"] == text and ev["payload"] == payload and ev["ts"] == TS
    assert _event_lines(_events_file(store_dir, ROBERT))[-1] == ev
    audit = _audit(client, body)
    assert audit["flow"] == "A"
    assert audit["profile_id"] == ROBERT
    assert "ev_robert_0008" in audit["events_considered"]
    assert audit["events_considered"][-1] == "ev_robert_0008"
    assert isinstance(body["profile_diff"], list)
    assert body["profile_diff"] == audit["profile_diff"]
    for entry in body["profile_diff"]:
        assert set(entry) >= {"path", "old", "new", "by"}
    assert _version(body) == audit["profile_version_out"] == _profile(client, ROBERT)["meta"]["version"]
    assert audit["profile_version_in"] == before["meta"]["version"]
    assert audit["profile_version_out"] in (audit["profile_version_in"], audit["profile_version_in"] + 1)
    assert (audit["profile_version_out"] == audit["profile_version_in"] + 1) == bool(body["profile_diff"])  # §4 A6


def test_question_event_runs_rules_only_no_n1_n3_and_keeps_llm_fields(client, store_dir):
    # D7 + §4: Flow A on a question event is rules only (no N1/N2 in the P0 pipeline, N3 only when values.notes
    # changed). The recorded llm: fields stay as they were and limits do not move.
    before = _profile(client, ROBERT)
    body = _ok(client, ROBERT, "question", "What does duration mean for a bond fund?")
    audit = _audit(client, body)
    assert not _n3_calls(audit)
    assert not [c for c in audit["llm_calls"] if c["node"] == "behaviour_classifier"]
    after = _profile(client, ROBERT)
    assert after["risk"]["behavioural"] == before["risk"]["behavioural"]
    assert after["values"]["parsed_notes"] == before["values"]["parsed_notes"]
    assert after["values"]["notes"] == before["values"]["notes"]
    assert after["knowledge"]["question_quality"] == before["knowledge"]["question_quality"]
    assert body["event"]["type"] == "question"
    # Limits are rebuilt from rule: fields and user-declared values only (inv. 2): equal to build_limits(profile).
    for k, v in _limits_for(after, ROBERT).items():
        if k not in ("_note",):
            assert after["limits"][k] == v, k


def test_note_event_does_not_edit_values_notes_and_does_not_call_n3(client, store_dir):
    # §4 A2: N3 runs only if values.notes changed. A note event is free text in the event log; it is not a declared
    # profile field.
    # SPEC-AMBIGUITY: §3.4 does not say a `note` event rewrites values.notes; stricter reading = it does not
    # (values.notes is declared by the user, only through profile_edit).
    before = _profile(client, ROBERT)
    body = _ok(client, ROBERT, "note", AMIRA_NOTES)
    audit = _audit(client, body)
    assert not _n3_calls(audit)
    after = _profile(client, ROBERT)
    assert after["values"]["notes"] == before["values"]["notes"] == ROBERT_NOTES
    assert after["values"]["parsed_notes"]["value"] == before["values"]["parsed_notes"]["value"]


# ---------------------------------------------------------------- quiz_result (§3.1, §3.4)

@pytest.mark.parametrize("score", [0.5, 0.95, 0.0, 1.0])
def test_quiz_result_sets_knowledge_quiz_score_and_rederives_knowledge_score(client, store_dir, score):
    # §3.1 / §3.4: quiz_result.payload.score becomes knowledge.quiz_score (a user-declared plain value, 0-1); A4
    # re-derives knowledge.score (rule:, 0.4*quiz + ...). Limits do not depend on knowledge (§7.2, inv. 2).
    before = _baseline(client, ROBERT)
    assert before["knowledge"]["quiz_score"] == pytest.approx(0.8, abs=1e-9)
    body = _ok(client, ROBERT, "quiz_result", "Knowledge quiz kq_v1 completed.", {"score": score, "quiz_id": "kq_v1"})
    after = _profile(client, ROBERT)
    assert after["knowledge"]["quiz_score"] == pytest.approx(score, abs=1e-9)
    assert not isinstance(after["knowledge"]["quiz_score"], dict)          # still a plain declared value
    old_score, new_score = before["knowledge"]["score"], after["knowledge"]["score"]
    assert new_score["by"].startswith("rule:")
    # The deterministic term is 0.4 * quiz * 100: a lower quiz lowers the score, a higher one raises it.
    assert (new_score["value"] < old_score["value"]) == (score < 0.8)
    assert (new_score["value"] > old_score["value"]) == (score > 0.8)
    assert after["limits"] == before["limits"]
    assert after["limits_trace"] == before["limits_trace"]
    assert not any("knowledge" in dep for deps in after["limits_trace"].values() for dep in deps)
    diff = {d["path"]: d for d in body["profile_diff"]}
    assert diff["knowledge.quiz_score"]["old"] == pytest.approx(0.8, abs=1e-9)
    assert diff["knowledge.quiz_score"]["new"] == pytest.approx(score, abs=1e-9)
    assert not any(p.startswith("limits") for p in diff)
    assert _version(body) == before["meta"]["version"] + 1


def test_quiz_result_with_the_same_score_leaves_the_profile_version(client, store_dir):
    # §4 A6: bump meta.version only when something changed.
    before = _baseline(client, ROBERT)
    body = _ok(client, ROBERT, "quiz_result", "Retook the quiz.", {"score": 0.8, "quiz_id": "kq_v1"})
    assert body["profile_diff"] == []
    assert _version(body) == before["meta"]["version"]
    assert _profile(client, ROBERT)["knowledge"]["quiz_score"] == pytest.approx(0.8, abs=1e-9)


@pytest.mark.parametrize("payload", [
    None, {}, {"quiz_id": "kq_v1"}, {"score": 1.2, "quiz_id": "kq_v1"}, {"score": -0.1, "quiz_id": "kq_v1"},
    {"score": "0.8", "quiz_id": "kq_v1"}, {"score": True, "quiz_id": "kq_v1"}, {"score": None, "quiz_id": "kq_v1"},
])
def test_invalid_quiz_result_is_rejected_before_anything_is_written(client, store_dir, payload):
    # §3.4 payload {score, quiz_id}; §3.1 quiz_score is a 0-1 fraction. A bad score never reaches the profile.
    before = _baseline(client, ROBERT)
    events = _events_file(store_dir, ROBERT).read_bytes()
    r = _post(client, ROBERT, "quiz_result", "Knowledge quiz.", payload)
    assert r.status_code == 422, r.text
    _assert_untouched(client, store_dir, ROBERT, before, events)


# ---------------------------------------------------------------- profile_edit: declared fields (§3.4, inv. 1)

def test_profile_edit_applies_new_at_path_and_rebuilds_limits(client, store_dir):
    # §3.4 {path, old, new} + §4 A5: a declared field changes and limits are rebuilt from it, trace included.
    # preferences.max_fee_mer_pct feeds limits.max_fee_mer_pct directly (rules/limits.py trace).
    before = _baseline(client, ROBERT)
    assert before["limits"]["max_fee_mer_pct"] == pytest.approx(0.5, abs=1e-9)
    body = _ok(client, ROBERT, "profile_edit", "Lowering my fee ceiling.",
               _edit("preferences.max_fee_mer_pct", 0.5, 0.3))
    after = _profile(client, ROBERT)
    assert after["preferences"]["max_fee_mer_pct"] == pytest.approx(0.3, abs=1e-9)
    assert after["limits"]["max_fee_mer_pct"] == pytest.approx(0.3, abs=1e-9)
    assert after["limits_trace"]["max_fee_mer_pct"] == ["preferences.max_fee_mer_pct"]
    other = {k: v for k, v in after["limits"].items() if k != "max_fee_mer_pct"}
    assert other == {k: v for k, v in before["limits"].items() if k != "max_fee_mer_pct"}
    assert after["meta"]["version"] == before["meta"]["version"] + 1 == _version(body)
    diff = {d["path"]: d for d in body["profile_diff"]}
    assert diff["preferences.max_fee_mer_pct"]["old"] == pytest.approx(0.5, abs=1e-9)
    assert diff["preferences.max_fee_mer_pct"]["new"] == pytest.approx(0.3, abs=1e-9)
    assert diff["limits.max_fee_mer_pct"]["new"] == pytest.approx(0.3, abs=1e-9)
    assert diff["limits.max_fee_mer_pct"]["by"].startswith("rule:")      # limits are rule-computed, never user-set
    audit = _audit(client, body)
    assert audit["profile_diff"] == body["profile_diff"]
    assert not _n3_calls(audit)                                          # notes unchanged -> no N3


def test_profile_edit_of_risk_declared_rederives_effective_risk_and_limits(client, store_dir):
    # §7.1 effective = min(declared, capacity) and §7.2 limits come from risk.effective: lowering the declared level
    # below the capacity (low_moderate) lowers risk.effective and the limits are rebuilt from it.
    before = _baseline(client, ROBERT)
    assert before["risk"]["declared"] == "low_moderate"
    body = _ok(client, ROBERT, "profile_edit", "I am more cautious than I said.",
               _edit("risk.declared", "low_moderate", "low"))
    after = _profile(client, ROBERT)
    assert after["risk"]["declared"] == "low"
    assert after["risk"]["effective"]["value"] == "low"
    assert after["risk"]["effective"]["by"].startswith("rule:")
    assert after["limits"] != before["limits"]
    for k, v in _limits_for(after, ROBERT).items():
        if k != "_note":
            assert after["limits"][k] == v, k
    assert "risk.effective" in after["limits_trace"]["vol_band_annual"]
    assert {d["path"] for d in body["profile_diff"]} >= {"risk.declared", "risk.effective"}
    # Declared above the capacity: risk.effective stays at the capacity (min rule).
    body2 = _ok(client, ROBERT, "profile_edit", "Back up.", _edit("risk.declared", "low", "moderate"))
    again = _profile(client, ROBERT)
    assert again["risk"]["declared"] == "moderate"
    assert again["risk"]["effective"]["value"] == before["risk"]["capacity"]["value"]
    assert _version(body2) == _version(body) + 1


def test_profile_edit_with_a_stale_old_value_is_a_conflict_and_changes_nothing(client, store_dir):
    # §3.4: payload.old is the value the user saw. A mismatch means the edit was made on stale data.
    # SPEC-AMBIGUITY: the task allows 409 or 422; the stricter reading is that either way nothing is appended and the
    # profile is untouched.
    before = _baseline(client, ROBERT)
    events = _events_file(store_dir, ROBERT).read_bytes()
    r = _post(client, ROBERT, "profile_edit", "Edit on stale data.", _edit("preferences.max_fee_mer_pct", 0.9, 0.3))
    assert r.status_code in (409, 422), r.text
    _assert_untouched(client, store_dir, ROBERT, before, events)


@pytest.mark.parametrize("payload", [
    None, {}, {"path": "preferences.max_fee_mer_pct", "old": 0.5}, {"old": 0.5, "new": 0.3},
    {"path": "", "old": 0.5, "new": 0.3},
])
def test_profile_edit_needs_path_and_new(client, store_dir, payload):
    # §3.4: profile_edit.payload = {path, old, new}.
    before = _baseline(client, ROBERT)
    events = _events_file(store_dir, ROBERT).read_bytes()
    r = _post(client, ROBERT, "profile_edit", "Incomplete edit.", payload)
    assert r.status_code == 422, r.text
    _assert_untouched(client, store_dir, ROBERT, before, events)


@pytest.mark.parametrize("path,old,new", [
    ("risk.declared", "low_moderate", "extreme"),                       # not in the risk-level enum
    ("preferences.max_fee_mer_pct", 0.5, "cheap"),                      # wrong type
    ("preferences.does_not_exist", None, True),                         # not a declared profile field
    ("nowhere.at.all", None, 1),
])
def test_profile_edit_that_breaks_the_schema_or_names_no_field_is_refused(client, store_dir, path, old, new):
    # §3.3: the profile is validated against profile.v2.json on every save; an invalid edit never lands.
    before = _baseline(client, ROBERT)
    events = _events_file(store_dir, ROBERT).read_bytes()
    r = _post(client, ROBERT, "profile_edit", "Bad edit.", _edit(path, old, new))
    assert r.status_code in (400, 409, 422), r.text
    _assert_untouched(client, store_dir, ROBERT, before, events)


# ---------------------------------------------------------------- profile_edit: never limits / llm: / rule: fields

def _node(profile: dict, path: str):
    cur = profile
    for part in path.split("."):
        cur = cur[part]
    return cur


@pytest.mark.parametrize("path,new", [
    ("limits.max_crypto_weight", 0.5),                                  # limits: only rules build them (inv. 2)
    ("limits.excluded_activities", []),
    ("limits.max_fee_mer_pct", 5.0),
    ("limits", {"max_crypto_weight": 1.0}),
    ("limits_trace.max_fee_mer_pct", []),
    ("limits_trace", {}),
    ("risk.behavioural", "low"),                                        # llm: fields (inv. 1, 2, 3)
    ("risk.behavioural.value", "low"),
    ("risk.behavioural.confidence", 0.99),
    ("knowledge.question_quality", 1.0),
    ("knowledge.question_quality.value", 1.0),
    ("values.parsed_notes", []),
    ("values.parsed_notes.value", []),
    ("values.parsed_notes.by", "user:declared"),
    ("risk.effective", "high"),                                         # rule: fields (inv. 1: computed, not declared)
    ("risk.capacity", "high"),
    ("knowledge.score", 100),
    ("identity.life_stage", "retired"),
    ("objectives.effective_horizon_years", 30),
    ("meta.version", 99),
    ("meta.profile_id", "persona-someone-else"),
])
def test_profile_edit_never_writes_limits_trace_llm_or_rule_fields(client, store_dir, path, new):
    # §1 inv. 1: only the user changes a plain value; an object {value, by, ...} is computed. Inv. 2: no request sets
    # limits or limits_trace. Inv. 3/4: llm: fields change only through the gate.
    # SPEC-AMBIGUITY: rule: fields are computed, not declared; stricter reading = they are refused too.
    before = _baseline(client, ROBERT)
    events = _events_file(store_dir, ROBERT).read_bytes()
    old = copy.deepcopy(_node(before, path))
    r = _post(client, ROBERT, "profile_edit", "Trying to set a computed field.", _edit(path, old, new))
    assert r.status_code in REFUSED_PATH_STATUS, (path, r.status_code, r.text)
    _assert_untouched(client, store_dir, ROBERT, before, events)


# ---------------------------------------------------------------- profile_edit of values.notes -> N3 -> gate (D7)

AMIRA_PARSE = {("prefer_attribute", "I love AI and new tech"), ("max_theme_weight", "I don't want everything in tech.")}
ROBERT_PARSE = {("prefer_attribute", "I prefer companies I understand."),
                ("avoid_attribute", "No hype stocks unless I can explain the business.")}


def test_profile_edit_of_values_notes_runs_n3_through_the_gate_with_the_mock_cache(client, store_dir):
    # D7 point 1 + §4 A2/A3: changed values.notes -> N3 (values_parser, recorded answer) -> gate: the quotes are exact
    # substrings of the new notes, every rule starts approved_by_user: false, the field is stamped llm:values_parser.
    before = _baseline(client, ROBERT)
    assert before["values"]["notes"] == ROBERT_NOTES
    body = _ok(client, ROBERT, "profile_edit", "Rewrote my notes.", _edit("values.notes", ROBERT_NOTES, AMIRA_NOTES))
    after = _profile(client, ROBERT)
    assert after["values"]["notes"] == AMIRA_NOTES
    parsed = after["values"]["parsed_notes"]
    assert parsed["by"] == "llm:values_parser"
    assert parsed["prompt_version"] == "values_parser.v1"
    assert parsed["model_id"] and parsed["updated_at"] and parsed["input_hash"]
    rules = parsed["value"]
    assert {(r["rule_kind"], r["quote"]) for r in rules} == AMIRA_PARSE        # the recorded N3 answer, nothing else
    assert all(r["quote"] in AMIRA_NOTES for r in rules)
    assert all(r["approved_by_user"] is False for r in rules)
    audit = _audit(client, body)
    calls = _n3_calls(audit)
    assert len(calls) == 1
    assert calls[0]["gate"] == "accepted"
    assert calls[0]["cache_hit"] is True
    assert calls[0]["prompt_version"] == "values_parser.v1"
    assert audit["llm_errors"] == []
    assert "values.notes" in {d["path"] for d in body["profile_diff"]}
    assert _version(body) == before["meta"]["version"] + 1


def test_unapproved_parsed_rules_are_not_promoted_into_limits_after_a_notes_edit(client, store_dir):
    # §6.2 N3 + inv. 2: only approved exclude_activity / max_theme_weight rules reach limits. The fresh parse is
    # unapproved, so limits equal the baseline's and still equal build_limits(profile) (no llm: input).
    before = _baseline(client, ROBERT)
    _ok(client, ROBERT, "profile_edit", "Rewrote my notes.", _edit("values.notes", ROBERT_NOTES, AMIRA_NOTES))
    after = _profile(client, ROBERT)
    assert after["limits"] == before["limits"]
    assert "max_theme_weight" not in after["limits"]
    assert after["limits_trace"] == before["limits_trace"]


def test_notes_edit_replaces_approved_rules_so_their_promoted_limit_is_rebuilt_away(client, store_dir):
    # §4 A5 on Amira: her approved "tech_heavy" rule (max_pct 50) gives limits.max_theme_weight. Re-parsing different
    # notes (Robert's, recorded) yields unapproved rules only, so the promoted limit is gone after the refresh.
    # SPEC-AMBIGUITY: approval is per parsed rule/quote; a rule that is no longer parsed keeps no approval (stricter:
    # no limit survives that no approved rule supports).
    before = _baseline(client, AMIRA)
    assert before["limits"]["max_theme_weight"] == {"tech_heavy": pytest.approx(0.5, abs=1e-9)}
    body = _ok(client, AMIRA, "profile_edit", "New notes.", _edit("values.notes", AMIRA_NOTES, ROBERT_NOTES))
    after = _profile(client, AMIRA)
    assert after["values"]["notes"] == ROBERT_NOTES
    rules = after["values"]["parsed_notes"]["value"]
    assert {(r["rule_kind"], r["quote"]) for r in rules} == ROBERT_PARSE
    assert all(r["approved_by_user"] is False for r in rules)
    assert "max_theme_weight" not in after["limits"]
    paths = {d["path"] for d in body["profile_diff"]}
    assert "values.notes" in paths and any(p.startswith("limits.max_theme_weight") for p in paths)
    calls = _n3_calls(_audit(client, body))
    assert [c["gate"] for c in calls] == ["accepted"] and calls[0]["cache_hit"] is True


def test_notes_edit_with_an_unrecorded_text_keeps_the_previous_parse_and_logs_the_failure(client, store_dir):
    # §1 inv. 4 / §4 A3: if N3 cannot produce a gated output (mock cache miss), the previous parsed value stays and
    # the failure goes in the audit log. The request itself still succeeds; nothing is invented.
    before = _baseline(client, ROBERT)
    body = _ok(client, ROBERT, "profile_edit", "Rewrote my notes.", _edit("values.notes", ROBERT_NOTES, UNRECORDED_NOTES))
    after = _profile(client, ROBERT)
    assert after["values"]["parsed_notes"] == before["values"]["parsed_notes"]
    assert after["limits"] == before["limits"]
    audit = _audit(client, body)
    assert not [c for c in _n3_calls(audit) if c["gate"] == "accepted"]
    assert any(e.get("node") == "values_parser" for e in audit["llm_errors"])


def test_notes_edit_with_a_stale_old_is_refused_and_n3_does_not_run(client, store_dir):
    # §3.4 old/new + §4 A2: a refused edit changes nothing, so there is no changed input for N3.
    before = _baseline(client, ROBERT)
    events = _events_file(store_dir, ROBERT).read_bytes()
    r = _post(client, ROBERT, "profile_edit", "Rewrote my notes.", _edit("values.notes", "something else", AMIRA_NOTES))
    assert r.status_code in (409, 422), r.text
    _assert_untouched(client, store_dir, ROBERT, before, events)


# ---------------------------------------------------------------- audit, determinism, fixtures

def test_flow_a_audit_lists_the_whole_event_log_in_order_and_is_deterministic(client, store_dir, tmp_path, monkeypatch):
    # §10: events_considered = the event ids the run looked at; §1 inv. 8: same inputs -> same refreshed profile.
    body = _ok(client, ROBERT, "quiz_result", "Quiz.", {"score": 0.5, "quiz_id": "kq_v1"})
    audit = _audit(client, body)
    ids = [e["event_id"] for e in _event_lines(_events_file(store_dir, ROBERT))]
    assert audit["events_considered"] == ids
    assert ids == ["ev_robert_0001", "ev_robert_0004", "ev_robert_0005", "ev_robert_0007", "ev_robert_0008"]
    first = _profile(client, ROBERT)
    # A second store replays the same event and reaches the same profile (timestamps aside).
    other = tmp_path / "store2"
    other.mkdir()
    monkeypatch.setenv("COHERENCE_ANALYZER_STORE_DIR", str(other))
    c2 = TestClient(api.app)
    _ok(c2, ROBERT, "quiz_result", "Quiz.", {"score": 0.5, "quiz_id": "kq_v1"})
    second = _profile(c2, ROBERT)
    for k in ("limits", "limits_trace"):
        assert second[k] == first[k]
    assert second["knowledge"]["quiz_score"] == first["knowledge"]["quiz_score"]
    assert second["knowledge"]["score"]["value"] == first["knowledge"]["score"]["value"]
    assert second["meta"]["version"] == first["meta"]["version"]


def test_d10_flow_a_response_and_audit_describe_never_prescribe(client, store_dir):
    # D10 / §1 inv. 7: every user-facing string in the response and the audit describes; none recommends a trade.
    body = _ok(client, ROBERT, "profile_edit", "Fee ceiling.", _edit("preferences.max_fee_mer_pct", 0.5, 0.4))
    text = (json.dumps(body) + json.dumps(_audit(client, body))).lower()
    for word in ("you should", "we recommend", "consider selling", "consider buying", "rebalance", "next step"):
        assert word not in text, word


def test_events_endpoint_for_flow_a_never_writes_the_fixtures_dir(client, store_dir):
    # CLAUDE.md / task: fixtures/ is never written. Run every Flow A event type, then compare the hashes.
    _ok(client, ROBERT, "question", "What is a bond ladder?")
    _ok(client, ROBERT, "note", "A note.")
    _ok(client, ROBERT, "quiz_result", "Quiz.", {"score": 0.6, "quiz_id": "kq_v1"})
    _ok(client, ROBERT, "profile_edit", "Edit.", _edit("preferences.max_fee_mer_pct", 0.5, 0.4))
    _ok(client, ROBERT, "profile_edit", "Notes.", _edit("values.notes", ROBERT_NOTES, AMIRA_NOTES))
    _ok(client, AMIRA, "profile_edit", "Notes.", _edit("values.notes", AMIRA_NOTES, ROBERT_NOTES))
    assert _hashes() == _FIXTURE_HASHES
