"""P3.FREETEXT: intake answers and skips are events; a free-text answer goes through N3 and the gate (Flow A).

Sources: docs/spec-factors.md §20.6 ("The answer is an event; free-text answers go through the existing N2/N3
extractors and the gate (§11)"; skip = `{"value": null, "by": "user:skipped"}`), SPEC §3.4 (`profile_edit` payload
`{path, old, new}`), §4 Flow A (A2 N3 only if `values.notes` changed, A3 gate, A5 limits, A6 audit), §6.2 N3, §11
(gate), §12 (API), docs/spec-decisions.md D7 point 1 (free text `values.notes` -> N3 -> gate; parsed rules start
`approved_by_user: false`), D10 (describe, never prescribe), §1 inv. 1 (limits from `rule:` fields and user-declared
values only), inv. 2/3 (no `llm:` field feeds limits; no request writes an `llm:` field), inv. 4 (gate rejection keeps
the previous value, the failure is reported).

Everything runs offline with COHERENCE_ANALYZER_LLM=mock on a temp store. The notes used for N3 are the recorded ones
(Robert's and Amira's, fixtures/llm_cache) so the mock cache hits. Response shape of the intake routes (spec leaves it
open): structured answers keep returning the profile (frozen tests/flows/test_api.py); these tests read events from the
working copy's events.jsonl and the profile from GET, and only look for text in the response when a rejection must be
explained.

The event shape is the existing `profile_edit` one (P3.FLOWA, tests/flows/test_flow_a_events.py): `event_id`
(server-assigned, next number after the highest in the log), `profile_id`, `ts` (server clock, ISO 8601), `type`
`profile_edit`, `text`, `payload` `{path, old, new}`, `source` `intake`.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

from coherence_analyzer import api, data, gate as gate_mod, intake  # noqa: E402
from coherence_analyzer.llm import bedrock, extractors  # noqa: E402
from coherence_analyzer.rules import limits as limits_mod  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
TAGS_CSV = ROOT / "data" / "meta" / "activity_tags.csv"
ROBERT = "persona-robert"
AMIRA = "persona-amira"
ROBERT_NOTES = "I prefer companies I understand. No hype stocks unless I can explain the business."
AMIRA_NOTES = "I love AI and new tech but I don't want everything in tech."
UNRECORDED_NOTES = "Prefer Canadian dividend payers; nothing that depends on a single product launch."
AMIRA_PARSE = {("prefer_attribute", "I love AI and new tech"), ("max_theme_weight", "I don't want everything in tech.")}
ROBERT_PARSE = {("prefer_attribute", "I prefer companies I understand."),
                ("avoid_attribute", "No hype stocks unless I can explain the business.")}
NO_NUMBER_QUOTE = "No hype stocks unless I can explain the business."     # exact substring of Robert's notes
SKIPPED = {"value": None, "by": "user:skipped"}
LLM_PREFIXES = ("values.parsed_notes", "risk.behavioural", "knowledge.question_quality")
# The user-approved subset of the parsed rules is a user declaration (§6.2 N3, §7.2) and is the one parsed_notes path the
# trace may name (rules/limits.APPROVED_PARSED_RULES; also pinned by test_intake.py and test_personas.py).
APPROVED_PATH = "values.parsed_notes[approved_by_user]"


def _llm_deps(deps: list[str]) -> list[str]:
    return [d for d in deps if d.startswith(LLM_PREFIXES) and d != APPROVED_PATH]


def _hashes(base: Path) -> dict[str, str]:
    return {str(p.relative_to(base)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(base.rglob("*")) if p.is_file()}


_FIXTURE_HASHES = _hashes(FIXTURES)                                   # taken before any test runs
_TAGS_HASH = hashlib.sha256(TAGS_CSV.read_bytes()).hexdigest() if TAGS_CSV.exists() else None


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


def _events_file(store_dir: Path, pid: str) -> Path:
    return store_dir / "profiles" / pid / "events.jsonl"


def _events(store_dir: Path, pid: str) -> list[dict]:
    p = _events_file(store_dir, pid)
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def _profile(client: TestClient, pid: str) -> dict:
    r = client.get(f"/profiles/{pid}")
    assert r.status_code == 200, r.text
    return r.json()


def _answer(client: TestClient, pid: str, path: str, value):
    return client.post(f"/profiles/{pid}/intake/answer", json={"path": path, "value": value})


def _skip(client: TestClient, pid: str, path: str):
    return client.post(f"/profiles/{pid}/intake/skip", json={"path": path})


def _ok_answer(client: TestClient, pid: str, path: str, value) -> dict:
    r = _answer(client, pid, path, value)
    assert r.status_code == 200, r.text
    return r.json()


def _new_events(store_dir: Path, pid: str, before: list[dict]) -> list[dict]:
    after = _events(store_dir, pid)
    assert after[:len(before)] == before, "the existing log must be left untouched (append only)"
    return after[len(before):]


def _limits_for(profile: dict, pid: str) -> dict:
    """Limits rebuilt from a profile by the rules module (inv. 1/2: rule: fields and declared values only)."""
    fixture = {ROBERT: "robert", AMIRA: "amira"}[pid]
    pf = data.load_portfolio(FIXTURES / fixture / "portfolio.json", data.load_assets())
    lim, _ = limits_mod.build_limits(profile, pf["value_cad"])
    return json.loads(json.dumps(lim))


def _assert_untouched(client, store_dir, pid, before_profile: dict, before_events: bytes) -> None:
    assert _events_file(store_dir, pid).read_bytes() == before_events, "a refused answer must not append an event"
    assert _profile(client, pid) == before_profile, "a refused answer must not change the working profile"


def _start(client: TestClient, pid: str, store_dir: Path) -> tuple[dict, list[dict]]:
    """Touch the profile once (the API copies the fixture) and return it with its event log."""
    return _profile(client, pid), _events(store_dir, pid)


def _check_intake_event(ev: dict, pid: str, path: str, old, new) -> None:
    assert ev["type"] == "profile_edit"
    assert ev["source"] == "intake"
    assert ev["profile_id"] == pid
    assert ev["payload"] == {"path": path, "old": old, "new": new}
    assert isinstance(ev["text"], str) and ev["text"].strip()
    assert isinstance(ev["ts"], str)
    datetime.fromisoformat(ev["ts"].replace("Z", "+00:00"))            # server-assigned ISO 8601 timestamp
    assert re.fullmatch(r"ev_[a-z0-9_]+_\d{4}", ev["event_id"])


def _spy_gate(monkeypatch) -> list[dict]:
    """Record every gate.gate call as {node, old, out, new, status}; the real gate still decides."""
    calls: list[dict] = []
    real = gate_mod.gate

    def spy(node, old_field, llm_output, context):
        new_field, status = real(node, old_field, llm_output, context)
        calls.append({"node": node, "old": copy.deepcopy(old_field), "out": copy.deepcopy(llm_output),
                      "new": copy.deepcopy(new_field), "status": status})
        return new_field, status

    monkeypatch.setattr(gate_mod, "gate", spy)
    return calls


def _no_llm(monkeypatch) -> list[str]:
    """Any Bedrock/mock-cache call made while this is active is recorded and fails the test."""
    seen: list[str] = []

    def boom(node, *a, **kw):
        seen.append(node)
        raise AssertionError(f"structured intake answers must not call an LLM node ({node})")

    monkeypatch.setattr(bedrock, "call_tool", boom)
    return seen


# ---------------------------------------------------------------- structured answers: one event, no LLM (§20.6, §3.4)

def test_structured_answer_appends_one_profile_edit_event_with_source_intake(client, store_dir):
    # §20.6 "The answer is an event" + §3.4 profile_edit {path, old, new}: one event per answer, appended to the
    # working copy's events.jsonl, server-assigned id (next after ev_robert_0007), source `intake`.
    before, log = _start(client, ROBERT, store_dir)
    assert [e["event_id"] for e in log][-1] == "ev_robert_0007"
    path = "preferences.prefers_dividends"
    assert before["preferences"]["prefers_dividends"] is True
    _ok_answer(client, ROBERT, path, False)
    (ev,) = _new_events(store_dir, ROBERT, log)
    assert ev["event_id"] == "ev_robert_0008"
    _check_intake_event(ev, ROBERT, path, True, False)
    assert _profile(client, ROBERT)["preferences"]["prefers_dividends"] is False


def test_each_answer_gets_its_own_sequential_event_and_chains_old_to_new(client, store_dir):
    # §3.4: old is the value the user saw. Two answers on one path -> two events, ids ev_..._0008 and _0009, the
    # second event's `old` equals the first event's `new`; ids are server-assigned, never repeated.
    _, log = _start(client, ROBERT, store_dir)
    path = "risk.max_acceptable_drawdown_pct"
    _ok_answer(client, ROBERT, path, -20)
    _ok_answer(client, ROBERT, path, -8)
    first, second = _new_events(store_dir, ROBERT, log)
    assert (first["event_id"], second["event_id"]) == ("ev_robert_0008", "ev_robert_0009")
    _check_intake_event(first, ROBERT, path, -12, -20)
    _check_intake_event(second, ROBERT, path, -20, -8)


@pytest.mark.parametrize("extra", [{"event_id": "ev_robert_9999"}, {"ts": "2026-11-15T09:30:00-05:00"},
                                   {"source": "ui_chat"}, {"type": "note"}])
def test_event_fields_are_server_assigned_not_client_supplied(client, store_dir, extra):
    # §12 / task: the server assigns the event id (and the rest); a request cannot choose them (extra keys -> 4xx).
    before, log = _start(client, ROBERT, store_dir)
    raw = _events_file(store_dir, ROBERT).read_bytes()
    r = client.post(f"/profiles/{ROBERT}/intake/answer",
                    json={"path": "preferences.prefers_dividends", "value": False, **extra})
    assert r.status_code == 422, r.text
    _assert_untouched(client, store_dir, ROBERT, before, raw)


@pytest.mark.parametrize("path,old,new", [
    ("preferences.prefers_dividends", True, False),            # bool
    ("risk.max_acceptable_drawdown_pct", -12, -20),            # number
    ("situation.monthly_expenses", 6500, 7000),                # number
    ("identity.employer_sector", "Health Care", "Energy"),     # a plain string that is not free text for N3
    ("preferences.asset_types_refused", ["crypto", "options", "leveraged_etf", "margin"], ["crypto"]),   # list
])
def test_structured_answer_is_validated_stored_and_never_calls_an_llm(client, store_dir, monkeypatch, path, old, new):
    # task (3): structured answers keep today's behaviour: the validated value at the path, no LLM, no N3/N2/N1.
    # Only `values.notes` is free text for N3 (§6.2 N3 input = values.notes); a string like employer_sector is a
    # declared value.
    _, log = _start(client, ROBERT, store_dir)
    llm_nodes = _no_llm(monkeypatch)
    gate_calls = _spy_gate(monkeypatch)
    _ok_answer(client, ROBERT, path, new)
    node = _profile(client, ROBERT)
    for part in path.split("."):
        node = node[part]
    assert node == new and not isinstance(node, dict)               # plain declared value (inv. 1)
    (ev,) = _new_events(store_dir, ROBERT, log)
    _check_intake_event(ev, ROBERT, path, old, new)
    assert llm_nodes == [] and gate_calls == []


def test_answer_event_is_in_the_log_the_pipeline_reads(client, store_dir):
    # §4 / §10: the audit trail of a later refresh lists the intake event, because it is in events.jsonl.
    _, log = _start(client, ROBERT, store_dir)
    _ok_answer(client, ROBERT, "preferences.home_bias_ok", False)
    r = client.post(f"/profiles/{ROBERT}/events",
                    json={"type": "note", "text": "Checking the log.", "ts": "2026-11-15T09:30:00-05:00",
                          "source": "ui_chat", "payload": None})
    assert r.status_code in (200, 201), r.text
    ids = [e["event_id"] for e in _events(store_dir, ROBERT)]
    assert ids == [e["event_id"] for e in log] + ["ev_robert_0008", "ev_robert_0009"]
    audit = client.get(f"/audit/{r.json()['run_id']}").json()
    assert "ev_robert_0008" in audit["events_considered"]


def test_declared_risk_answer_rebuilds_limits_from_rule_and_declared_fields_only(client, store_dir):
    # §1 inv. 1/2 + §4 A1/A5: an answer is a declared value; limits are (re)built by the rules module from rule:
    # fields and declared values, so after the answer limits == build_limits(profile) and no limits_trace entry
    # depends on an llm: field.
    # SPEC-AMBIGUITY: §20.6 does not say a structured answer refreshes the profile; stricter reading (the task
    # says to reuse the Flow A machinery) = it does, so risk.effective and limits follow the declared level.
    before, _ = _start(client, ROBERT, store_dir)
    assert before["risk"]["declared"] == "low_moderate"
    _ok_answer(client, ROBERT, "risk.declared", "low")
    after = _profile(client, ROBERT)
    assert after["risk"]["declared"] == "low"
    assert after["risk"]["effective"]["value"] == "low"
    assert after["risk"]["effective"]["by"].startswith("rule:")
    for k, v in _limits_for(after, ROBERT).items():
        if k != "_note":
            assert after["limits"][k] == v, k
    assert after["limits"] != before["limits"]
    for deps in after["limits_trace"].values():
        assert not _llm_deps(deps), deps


# ---------------------------------------------------------------- skips are events too

def test_skip_appends_one_event_whose_new_value_is_the_skip_marker(client, store_dir):
    # §20.6 skip -> {"value": null, "by": "user:skipped"}; the audit trail shows the skip as a profile_edit event
    # from source `intake` with new == the marker.
    _, log = _start(client, ROBERT, store_dir)
    path = "preferences.prefers_large_caps"
    r = _skip(client, ROBERT, path)
    assert r.status_code == 200, r.text
    (ev,) = _new_events(store_dir, ROBERT, log)
    assert ev["event_id"] == "ev_robert_0008"
    _check_intake_event(ev, ROBERT, path, True, SKIPPED)
    assert _profile(client, ROBERT)["preferences"]["prefers_large_caps"] == SKIPPED
    assert path not in intake.missing(_profile(client, ROBERT), "preferences")


def test_answer_after_skip_records_the_marker_as_old(client, store_dir):
    # §3.4 old = what the user saw: after a skip the field shows the marker; the later answer's old is that marker.
    _, log = _start(client, ROBERT, store_dir)
    path = "preferences.prefers_large_caps"
    assert _skip(client, ROBERT, path).status_code == 200
    _ok_answer(client, ROBERT, path, False)
    skip_ev, answer_ev = _new_events(store_dir, ROBERT, log)
    _check_intake_event(answer_ev, ROBERT, path, SKIPPED, False)
    assert (skip_ev["event_id"], answer_ev["event_id"]) == ("ev_robert_0008", "ev_robert_0009")
    assert _profile(client, ROBERT)["preferences"]["prefers_large_caps"] is False


def test_skip_never_calls_an_llm(client, store_dir, monkeypatch):
    # §20.6: a skip is deterministic (the related checks report unknown); no N1/N2/N3 call.
    _start(client, ROBERT, store_dir)
    llm_nodes = _no_llm(monkeypatch)
    assert _skip(client, ROBERT, "situation.emergency_fund").status_code == 200
    assert llm_nodes == []


# ---------------------------------------------------------------- refused answers append nothing

@pytest.mark.parametrize("route,path,value", [
    ("answer", "limits.max_crypto_weight", 0.9),                  # inv. 2: limits are never written by a request
    ("answer", "limits_trace.max_fee_mer_pct", []),
    ("answer", "risk.behavioural", "low"),                        # llm: fields only change through the gate (inv. 3/4)
    ("answer", "knowledge.question_quality", 1.0),
    ("answer", "values.parsed_notes", []),
    ("answer", "values.parsed_notes.by", "user:declared"),
    ("answer", "risk.effective", "high"),                         # rule: fields are computed
    ("answer", "preferences.prefers_dividends", None),            # null is a skip, not an answer
    ("skip", "limits", None),
    ("skip", "risk.behavioural", None),
    ("skip", "values.notes", None),                               # notes are free text; they are not skippable here
])
def test_refused_answer_or_skip_appends_no_event_and_changes_nothing(client, store_dir, route, path, value):
    # §20.6 + inv. 1-4: a refused request leaves events.jsonl and the profile byte-identical.
    # SPEC-AMBIGUITY: skipping values.notes is not described anywhere; stricter reading = refused.
    before, _ = _start(client, ROBERT, store_dir)
    raw = _events_file(store_dir, ROBERT).read_bytes()
    r = (_answer(client, ROBERT, path, value) if route == "answer" else _skip(client, ROBERT, path))
    assert r.status_code in (400, 403, 422), (route, path, r.status_code, r.text)
    _assert_untouched(client, store_dir, ROBERT, before, raw)


# ---------------------------------------------------------------- free text: values.notes -> N3 -> gate (D7 point 1)

def test_notes_answer_appends_event_runs_n3_through_the_gate_and_stores_the_gated_result(client, store_dir,
                                                                                         monkeypatch):
    # §20.6 + D7 point 1 + §4 A2/A3: a free-text answer for values.notes is an event AND goes through N3
    # (values_parser, recorded answer) and the gate: every quote is an exact substring of the new notes, the field is
    # stamped llm:values_parser, every rule starts approved_by_user: false, and what is stored is exactly the
    # gate's returned field, never the raw LLM output.
    before, log = _start(client, ROBERT, store_dir)
    assert before["values"]["notes"] == ROBERT_NOTES
    gate_calls = _spy_gate(monkeypatch)
    n3_calls: list[str] = []
    real_n3 = extractors.values_parser_call

    def n3(notes):
        n3_calls.append(notes)
        return real_n3(notes)

    monkeypatch.setattr(extractors, "values_parser_call", n3)
    _ok_answer(client, ROBERT, "values.notes", AMIRA_NOTES)
    after = _profile(client, ROBERT)
    assert after["values"]["notes"] == AMIRA_NOTES
    parsed = after["values"]["parsed_notes"]
    assert parsed["by"] == "llm:values_parser"
    assert parsed["prompt_version"] == extractors.prompt_version("values_parser")
    assert parsed["model_id"] and parsed["updated_at"] and parsed["input_hash"]
    rules = parsed["value"]
    assert {(r["rule_kind"], r["quote"]) for r in rules} == AMIRA_PARSE          # the recorded N3 answer, nothing else
    assert all(r["quote"] in AMIRA_NOTES for r in rules)
    assert all(r["approved_by_user"] is False for r in rules)
    assert n3_calls == [AMIRA_NOTES]                                               # N3 input = values.notes only
    n3_gate = [c for c in gate_calls if c["node"] == "values_parser"]
    assert len(n3_gate) == 1 and n3_gate[0]["status"] == "accepted"
    assert parsed["value"] == n3_gate[0]["new"]["value"]                           # stored = gated, not raw
    assert [r["quote"] for r in n3_gate[0]["out"]["rules"]] == [r["quote"] for r in rules]
    (ev,) = _new_events(store_dir, ROBERT, log)
    assert ev["event_id"] == "ev_robert_0008"
    _check_intake_event(ev, ROBERT, "values.notes", ROBERT_NOTES, AMIRA_NOTES)


def test_notes_answer_unapproved_rules_do_not_reach_limits(client, store_dir):
    # §6.2 N3 + §1 inv. 1/2: only approved exclude_activity / max_theme_weight rules are promoted into limits. The
    # fresh parse is unapproved, so Amira's approved tech_heavy limit disappears when her notes are replaced, limits
    # equal build_limits(profile) and no limits_trace dependency is an llm: field.
    # SPEC-AMBIGUITY: approval is per parsed rule; a re-parse of different notes carries no approval (as in the
    # frozen Flow A test for a profile_edit of values.notes); stricter reading = no limit survives without one.
    before, _ = _start(client, AMIRA, store_dir)
    assert before["limits"]["max_theme_weight"] == {"tech_heavy": pytest.approx(0.5, abs=1e-9)}
    _ok_answer(client, AMIRA, "values.notes", ROBERT_NOTES)
    after = _profile(client, AMIRA)
    assert {(r["rule_kind"], r["quote"]) for r in after["values"]["parsed_notes"]["value"]} == ROBERT_PARSE
    assert all(r["approved_by_user"] is False for r in after["values"]["parsed_notes"]["value"])
    assert "max_theme_weight" not in after["limits"]
    for k, v in _limits_for(after, AMIRA).items():
        if k != "_note":
            assert after["limits"][k] == v, k
    for deps in after["limits_trace"].values():
        assert not _llm_deps(deps), deps


def test_gate_rejection_keeps_the_previous_parse_and_the_response_says_why(client, store_dir, monkeypatch):
    # §1 inv. 4/5 + §11 step 2: an N3 output whose quote is not a substring of the notes is `rejected:evidence`; the
    # previous parsed_notes stay, the invented quote never reaches the profile, and the response gives the reason.
    # The answer itself is still an event (the user did answer).
    # SPEC-AMBIGUITY: 200 with the reason in the body (as Flow A does for a failed N3) rather than a 4xx; and the
    # reason is the gate status string "rejected:evidence" (the §10 audit vocabulary).
    before, log = _start(client, ROBERT, store_dir)
    invented = "a quote the investor never wrote"

    def bad_n3(notes):
        return {"output": {"rules": [{"rule_kind": "other", "params": {"note": "x"}, "applies_to": "all",
                                      "quote": invented}]},
                "call_log": {"node": "values_parser", "model_id": "fixture:hand-written",
                             "prompt_version": extractors.prompt_version("values_parser"),
                             "input_hash": "sha256:" + "0" * 64, "cache_hit": True, "latency_ms": 0,
                             "tokens_in": 0, "tokens_out": 0}}

    monkeypatch.setattr(extractors, "values_parser_call", bad_n3)
    gate_calls = _spy_gate(monkeypatch)
    r = _answer(client, ROBERT, "values.notes", AMIRA_NOTES)
    assert r.status_code == 200, r.text
    assert [c["status"] for c in gate_calls if c["node"] == "values_parser"] == ["rejected:evidence"]
    after = _profile(client, ROBERT)
    assert after["values"]["parsed_notes"] == before["values"]["parsed_notes"]
    assert invented not in json.dumps(after)
    assert "rejected:evidence" in json.dumps(r.json())
    assert after["limits"] == before["limits"]
    (ev,) = _new_events(store_dir, ROBERT, log)
    _check_intake_event(ev, ROBERT, "values.notes", ROBERT_NOTES, AMIRA_NOTES)


def test_unrecorded_notes_keep_the_previous_parse_and_the_response_says_why(client, store_dir):
    # §1 inv. 4 + §6.1 (mock mode raises on a cache miss): no gated N3 output -> the previous parsed_notes stay, the
    # failure is reported, nothing is invented. The event is still appended.
    # SPEC-AMBIGUITY: the response names the node (`values_parser`) that failed; 200 as in Flow A.
    before, log = _start(client, ROBERT, store_dir)
    r = _answer(client, ROBERT, "values.notes", UNRECORDED_NOTES)
    assert r.status_code == 200, r.text
    after = _profile(client, ROBERT)
    assert after["values"]["parsed_notes"] == before["values"]["parsed_notes"]
    assert after["limits"] == before["limits"]
    assert "values_parser" in json.dumps(r.json())
    (ev,) = _new_events(store_dir, ROBERT, log)
    _check_intake_event(ev, ROBERT, "values.notes", ROBERT_NOTES, UNRECORDED_NOTES)


@pytest.mark.parametrize("bad", ["", "   ", 5, ["I prefer cheap funds"], {"text": "x"}, True])
def test_notes_answer_must_be_non_blank_text(client, store_dir, bad):
    # §20.6: a free-text answer is a string; an empty answer is not an answer (the user skips instead).
    # SPEC-AMBIGUITY: clearing the notes through intake is not described; stricter reading = refused, nothing logged.
    before, _ = _start(client, ROBERT, store_dir)
    raw = _events_file(store_dir, ROBERT).read_bytes()
    r = _answer(client, ROBERT, "values.notes", bad)
    assert r.status_code in (400, 422), r.text
    _assert_untouched(client, store_dir, ROBERT, before, raw)


def test_notes_answer_equal_to_the_current_notes_runs_no_n3(client, store_dir, monkeypatch):
    # §4 A2: N3 runs only if values.notes changed. The answer is still an event (every answer is), the profile's
    # parsed rules (approved by the user in the fixture) are untouched and no LLM call is made.
    before, log = _start(client, ROBERT, store_dir)
    llm_nodes = _no_llm(monkeypatch)
    _ok_answer(client, ROBERT, "values.notes", ROBERT_NOTES)
    after = _profile(client, ROBERT)
    assert after["values"]["parsed_notes"] == before["values"]["parsed_notes"]
    assert llm_nodes == []
    (ev,) = _new_events(store_dir, ROBERT, log)
    _check_intake_event(ev, ROBERT, "values.notes", ROBERT_NOTES, ROBERT_NOTES)


# ---------------------------------------------------------------- the theme number (Amira's "pas tout en tech")

def test_theme_number_answer_is_an_event_without_llm_and_stays_out_of_limits_until_approved(client, store_dir,
                                                                                            monkeypatch):
    # D7 point 1 + §20.6: the number of a parsed max_theme_weight rule is a user-declared value; the answer is an
    # event, needs no LLM, and the unapproved rule is not promoted into limits (inv. 1/2; approval is a separate
    # user action, POST .../parsed-rules/{i}/decision).
    # SPEC-AMBIGUITY: the rule has no `max_pct` key before the answer; JSON has no "absent", so `old` is null.
    assert client.get(f"/profiles/{ROBERT}").status_code == 200
    wp = store_dir / "profiles" / ROBERT / "profile.json"
    p = json.loads(wp.read_text(encoding="utf-8"))
    p["values"]["parsed_notes"]["value"].append(
        {"rule_kind": "max_theme_weight", "params": {"theme": "tech_heavy"}, "applies_to": "all",
         "quote": NO_NUMBER_QUOTE, "approved_by_user": False})
    wp.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    path = f"{intake.PARSED_RULES_PATH}[2].params.{intake.THEME_NUMBER_KEY}"
    assert path in intake.missing(_profile(client, ROBERT), "contraintes")
    log = _events(store_dir, ROBERT)
    llm_nodes = _no_llm(monkeypatch)
    _ok_answer(client, ROBERT, path, 15)
    after = _profile(client, ROBERT)
    assert after["values"]["parsed_notes"]["value"][2]["params"]["max_pct"] == 15
    assert after["values"]["parsed_notes"]["value"][2]["approved_by_user"] is False
    assert "max_theme_weight" not in after["limits"]
    assert llm_nodes == []
    (ev,) = _new_events(store_dir, ROBERT, log)
    _check_intake_event(ev, ROBERT, path, None, 15)


# ---------------------------------------------------------------- D10, fixtures, tag CSV

def test_d10_intake_event_text_and_responses_describe_never_prescribe(client, store_dir):
    # D10 / §1 inv. 7: the event text and every response string describe what was recorded; none recommends a trade
    # or a next step.
    _, log = _start(client, ROBERT, store_dir)
    bodies = [json.dumps(_ok_answer(client, ROBERT, "preferences.prefers_dividends", False)),
              _skip(client, ROBERT, "preferences.home_bias_ok").text,
              json.dumps(_ok_answer(client, ROBERT, "values.notes", AMIRA_NOTES))]
    assert len(_new_events(store_dir, ROBERT, log)) == 3
    text = (json.dumps(_new_events(store_dir, ROBERT, log)) + " ".join(bodies)).lower()
    for word in ("you should", "we recommend", "consider selling", "consider buying", "rebalance", "next step"):
        assert word not in text, word


def test_intake_events_never_write_fixtures_or_the_activity_tags_csv(client, store_dir):
    # CLAUDE.md: fixtures/ is never written (the working copy is) and data/meta/activity_tags.csv is human-only.
    _start(client, ROBERT, store_dir)
    _ok_answer(client, ROBERT, "preferences.prefers_dividends", False)
    assert _skip(client, ROBERT, "preferences.home_bias_ok").status_code == 200
    _ok_answer(client, ROBERT, "values.notes", AMIRA_NOTES)
    _ok_answer(client, AMIRA, "values.notes", ROBERT_NOTES)
    assert _hashes(FIXTURES) == _FIXTURE_HASHES
    assert (hashlib.sha256(TAGS_CSV.read_bytes()).hexdigest() if TAGS_CSV.exists() else None) == _TAGS_HASH
