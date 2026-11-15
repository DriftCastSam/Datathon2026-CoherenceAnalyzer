"""P4.DECISION: approving / rejecting a parsed rule records an event and runs the Flow A refresh.

Sources: docs/spec-decisions.md D7 (the user approves / rejects each parsed rule; notes -> N3 -> gate -> user decision
-> Flow A refresh), SPEC §6.2 N3 (parsed rules start unapproved; only approved exclude_activity / max_theme_weight
rules are promoted), §7.2 (limits from rule: fields and user-declared values only), §3.4 (event log, profile_edit
payload {path, old, new}), §4 Flow A (A5 limits + limits_trace, A6 bump meta.version, save, audit), §12 (API),
§1 inv. 2 (no llm: field feeds limits; a rule is promoted only after the user's approval).

Audit gap this pins down: POST /profiles/{id}/parsed-rules/{i}/decision used to flip `approved_by_user` and save.
Rejecting Amira's approved tech_heavy rule left limits.max_theme_weight = {"tech_heavy": 0.5}, the version at 1 and
no event / audit record. Now: a profile_edit event (path values.parsed_notes.value[i].approved_by_user, old, new,
source "ui") is appended, Flow A runs, and the response and GET /profiles/{id} show limits rebuilt from the approved
rules only, the version bumped, limits_trace consistent.

Everything is offline (COHERENCE_ANALYZER_LLM=mock), against a tmp store; fixtures/ must stay untouched.
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
AMIRA = "persona-amira"
ROBERT = "persona-robert"
FIXTURE_DIR = {AMIRA: "amira", ROBERT: "robert"}
TS = "2026-11-15T09:30:00-05:00"
NO_NUMBER_QUOTE = "No hype stocks unless I can explain the business."          # exact substring of Robert's notes
TECH_RULE = 1                                                                 # Amira: max_theme_weight tech_heavy, 50, approved
PREFER_RULE = 0                                                               # Amira: prefer_attribute, unapproved


def _hashes() -> dict[str, str]:
    return {str(p.relative_to(FIXTURES)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(FIXTURES.rglob("*")) if p.is_file()}


_FIXTURE_HASHES = _hashes()                                                   # taken before any test runs


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


def _events(store_dir: Path, pid: str) -> list[dict]:
    path = _work(store_dir, pid) / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _audit_ids(store_dir: Path) -> set[str]:
    d = store_dir / "audit"
    return {p.stem for p in d.glob("*.json")} if d.exists() else set()


def _profile(client: TestClient, pid: str) -> dict:
    r = client.get(f"/profiles/{pid}")
    assert r.status_code == 200, r.text
    return r.json()


def _rules(profile: dict) -> list[dict]:
    return profile["values"]["parsed_notes"]["value"]


def _decide(client: TestClient, pid: str, index: int, approved: bool, **extra) -> dict:
    r = client.post(f"/profiles/{pid}/parsed-rules/{index}/decision", json={"approved": approved, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def _baseline(client: TestClient, pid: str) -> dict:
    """A first Flow A run (a note event) so the stored profile is already refreshed (fixture limits == rebuilt)."""
    r = client.post(f"/profiles/{pid}/events", json={"type": "note", "text": "Starting a session.", "ts": TS,
                                                     "source": "ui_chat", "payload": None})
    assert r.status_code in (200, 201), r.text
    return _profile(client, pid)


def _rebuilt(profile: dict, pid: str) -> tuple[dict, dict]:
    """(limits, limits_trace) rebuilt from the profile by the rules module (invariant 2: no llm: input)."""
    pf = data.load_portfolio(FIXTURES / FIXTURE_DIR[pid] / "portfolio.json", data.load_assets())
    lim, trace = limits_mod.build_limits(profile, pf["value_cad"])
    return json.loads(json.dumps(lim)), json.loads(json.dumps(trace))


def _seed(client: TestClient, store_dir: Path, pid: str, mutate) -> None:
    """Touch the profile once (so the API copies the fixture), then rewrite the working profile.json."""
    assert client.get(f"/profiles/{pid}").status_code == 200
    path = _work(store_dir, pid) / "profile.json"
    p = json.loads(path.read_text(encoding="utf-8"))
    mutate(p)
    path.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")


def _add_theme_rule(p: dict) -> None:
    p["values"]["parsed_notes"]["value"].append(
        {"rule_kind": "max_theme_weight", "params": {"theme": "tech_heavy"}, "applies_to": "all",
         "quote": NO_NUMBER_QUOTE, "approved_by_user": False})


def _add_exclusion_rule(p: dict) -> None:
    p["values"]["parsed_notes"]["value"].append(
        {"rule_kind": "exclude_activity", "params": {"tag": "fossil_fuel_producer"}, "applies_to": "all",
         "quote": NO_NUMBER_QUOTE, "approved_by_user": False})


def _decision_events(events: list[dict], index: int) -> list[dict]:
    path = f"values.parsed_notes.value[{index}].approved_by_user"
    return [e for e in events if e["type"] == "profile_edit" and (e.get("payload") or {}).get("path") == path]


# ---------------------------------------------------------------- the audit gap: a rejection reaches limits

def test_rejecting_amiras_approved_tech_rule_removes_the_theme_limit_from_response_and_get(client):
    # D7 + §6.2 N3 + §1 inv. 2: limits come from approved rules only. Rejecting the approved tech_heavy rule must
    # drop limits.max_theme_weight at once, in the response and in GET /profiles (it used to stay {"tech_heavy": 0.5}).
    before = _baseline(client, AMIRA)
    assert before["limits"]["max_theme_weight"] == {"tech_heavy": pytest.approx(0.5, abs=1e-9)}
    assert _rules(before)[TECH_RULE]["approved_by_user"] is True
    body = _decide(client, AMIRA, TECH_RULE, False)
    assert _rules(body)[TECH_RULE]["approved_by_user"] is False
    assert not body["limits"].get("max_theme_weight")
    got = _profile(client, AMIRA)
    assert _rules(got)[TECH_RULE]["approved_by_user"] is False
    assert not got["limits"].get("max_theme_weight")


def test_response_equals_get_and_limits_and_trace_are_rebuilt_from_the_decided_profile(client):
    # §4 A5 + §7.2 + inv. 2: after a decision the stored limits and limits_trace equal build_limits(profile), which
    # reads rule: fields, user-declared values and approved rules only; limits.by stays a rule: stamp.
    _baseline(client, AMIRA)
    body = _decide(client, AMIRA, TECH_RULE, False)
    got = _profile(client, AMIRA)
    assert got == body                                                    # one source of truth, no stale copy
    limits, trace = _rebuilt(got, AMIRA)
    assert got["limits"] == limits
    assert got["limits_trace"] == trace
    assert got["limits"]["by"].startswith("rule:")
    assert "max_theme_weight" in got["limits_trace"]                      # the trace still names where the limit would come from


def test_decision_bumps_the_profile_version_once(client):
    # §4 A6: the refresh bumps meta.version when the profile changed (here: the flag and the rebuilt limits).
    before = _baseline(client, AMIRA)
    body = _decide(client, AMIRA, TECH_RULE, False)
    assert body["meta"]["version"] == before["meta"]["version"] + 1
    assert _profile(client, AMIRA)["meta"]["version"] == before["meta"]["version"] + 1


def test_decision_appends_one_profile_edit_event_with_path_old_new_and_ui_source(client, store_dir):
    # §3.4 + D7: the decision is an event: profile_edit, payload {path, old, new}, source "ui", appended to events.jsonl.
    _baseline(client, AMIRA)
    ids_before = [e["event_id"] for e in _events(store_dir, AMIRA)]
    _decide(client, AMIRA, TECH_RULE, False)
    events = _events(store_dir, AMIRA)
    assert len(events) == len(ids_before) + 1
    ev = events[-1]
    assert ev["type"] == "profile_edit"
    assert ev["payload"] == {"path": "values.parsed_notes.value[1].approved_by_user", "old": True, "new": False}
    assert ev["source"] == "ui"
    assert ev["profile_id"] == AMIRA
    assert ev["event_id"].startswith("ev_amira_") and ev["event_id"] not in ids_before
    assert isinstance(ev["text"], str) and ev["text"].strip()
    assert "T" in ev["ts"]
    assert [e["event_id"] for e in events[:-1]] == ids_before             # append-only: earlier events untouched


def test_decision_runs_flow_a_and_leaves_an_audit_record_without_llm_calls(client, store_dir):
    # §4 A6 + §10: Flow A saves an audit record (flow "A", versions in/out, diff, events considered). The notes did not
    # change, so N3 does not run (A2: only extractors whose inputs changed) and nothing from an LLM enters the run.
    before = _baseline(client, AMIRA)
    seen = _audit_ids(store_dir)
    _decide(client, AMIRA, TECH_RULE, False)
    new = _audit_ids(store_dir) - seen
    assert len(new) == 1, "exactly one Flow A run per decision"
    audit = json.loads((store_dir / "audit" / f"{next(iter(new))}.json").read_text(encoding="utf-8"))
    assert audit["flow"] == "A"
    assert audit["profile_id"] == AMIRA
    assert audit["profile_version_in"] == before["meta"]["version"]
    assert audit["profile_version_out"] == before["meta"]["version"] + 1
    assert audit["llm_calls"] == [] and audit["llm_errors"] == []
    assert _events(store_dir, AMIRA)[-1]["event_id"] in audit["events_considered"]
    assert any(d["path"].startswith("limits.max_theme_weight") for d in audit["profile_diff"])


def test_approving_the_rule_again_restores_the_theme_limit_and_logs_both_decisions(client, store_dir):
    # D7: a decision is reversible; limits follow the latest decision, every decision is its own event and version.
    before = _baseline(client, AMIRA)
    _decide(client, AMIRA, TECH_RULE, False)
    body = _decide(client, AMIRA, TECH_RULE, True)
    assert body["limits"]["max_theme_weight"] == {"tech_heavy": pytest.approx(0.5, abs=1e-9)}
    assert _profile(client, AMIRA)["limits"] == body["limits"]
    assert body["meta"]["version"] == before["meta"]["version"] + 2
    evs = _decision_events(_events(store_dir, AMIRA), TECH_RULE)
    assert [(e["payload"]["old"], e["payload"]["new"]) for e in evs] == [(True, False), (False, True)]
    assert len({e["event_id"] for e in evs}) == 2
    limits, trace = _rebuilt(body, AMIRA)
    assert body["limits"] == limits and body["limits_trace"] == trace


# ---------------------------------------------------------------- approvals promote, other rules stay out of limits

def test_approving_a_theme_rule_with_the_users_number_promotes_it_at_once(client, store_dir):
    # D7 + §6.2 N3 (max_pct only when the user gave a number) + §1 inv. 2: the body's max_pct keeps working and the
    # approved rule shows in limits immediately (percent -> fraction once), without a separate /review call.
    _seed(client, store_dir, ROBERT, _add_theme_rule)
    before = _baseline(client, ROBERT)
    assert "max_theme_weight" not in before["limits"] or not before["limits"]["max_theme_weight"]
    body = _decide(client, ROBERT, 2, True, max_pct=15)
    rule = _rules(body)[2]
    assert rule["approved_by_user"] is True and rule["params"] == {"theme": "tech_heavy", "max_pct": 15}
    assert body["limits"]["max_theme_weight"] == {"tech_heavy": pytest.approx(0.15, abs=1e-9)}
    got = _profile(client, ROBERT)
    assert got == body
    limits, trace = _rebuilt(got, ROBERT)
    assert got["limits"] == limits and got["limits_trace"] == trace
    assert got["meta"]["version"] == before["meta"]["version"] + 1
    # SPEC-AMBIGUITY: whether the number is also logged as its own event is open; the approval itself must be logged.
    approvals = _decision_events(_events(store_dir, ROBERT), 2)
    assert [(e["payload"]["old"], e["payload"]["new"]) for e in approvals] == [(False, True)]
    assert approvals[0]["source"] == "ui" and approvals[0]["type"] == "profile_edit"


def test_approving_a_theme_rule_without_a_number_changes_no_limit_but_is_still_recorded(client, store_dir):
    # §7.2: an approved theme rule with no number has nothing to promote (limits stay as rebuilt by build_limits);
    # the decision is still an event and a version.
    _seed(client, store_dir, ROBERT, _add_theme_rule)
    before = _baseline(client, ROBERT)
    body = _decide(client, ROBERT, 2, True)
    assert _rules(body)[2]["approved_by_user"] is True
    assert not body["limits"].get("max_theme_weight")
    assert body["limits"] == _rebuilt(body, ROBERT)[0]
    assert body["meta"]["version"] == before["meta"]["version"] + 1
    assert len(_decision_events(_events(store_dir, ROBERT), 2)) == 1


def test_approving_and_rejecting_an_exclusion_rule_moves_excluded_activities(client, store_dir):
    # §6.2 N3 + §7.2: an approved exclude_activity rule adds its tag to limits.excluded_activities; rejecting removes
    # it again. The declared exclusions (tobacco, accounting_fraud_last_5y) stay in both cases.
    _seed(client, store_dir, ROBERT, _add_exclusion_rule)
    before = _baseline(client, ROBERT)
    declared = before["limits"]["excluded_activities"]
    assert "fossil_fuel_producer" not in declared
    approved = _decide(client, ROBERT, 2, True)
    assert approved["limits"]["excluded_activities"] == [*declared, "fossil_fuel_producer"]
    assert _profile(client, ROBERT)["limits"]["excluded_activities"] == [*declared, "fossil_fuel_producer"]
    assert approved["limits"] == _rebuilt(approved, ROBERT)[0]
    rejected = _decide(client, ROBERT, 2, False)
    assert rejected["limits"]["excluded_activities"] == declared
    assert _profile(client, ROBERT)["limits"] == rejected["limits"]
    assert rejected["limits_trace"] == _rebuilt(rejected, ROBERT)[1]
    assert rejected["meta"]["version"] == before["meta"]["version"] + 2


def test_deciding_a_preference_rule_changes_no_limit_but_is_recorded(client, store_dir):
    # §6.2 N3: only exclude_activity / max_theme_weight reach limits. Approving Amira's prefer_attribute rule changes
    # no limit (the other approved rule keeps its 0.5), yet it is an event, a Flow A run and a version bump.
    before = _baseline(client, AMIRA)
    assert _rules(before)[PREFER_RULE]["rule_kind"] == "prefer_attribute" and not _rules(before)[PREFER_RULE]["approved_by_user"]
    body = _decide(client, AMIRA, PREFER_RULE, True)
    assert _rules(body)[PREFER_RULE]["approved_by_user"] is True
    assert body["limits"] == before["limits"]
    assert body["limits_trace"] == before["limits_trace"]
    assert body["meta"]["version"] == before["meta"]["version"] + 1
    ev = _decision_events(_events(store_dir, AMIRA), PREFER_RULE)
    assert [e["payload"] for e in ev] == [{"path": "values.parsed_notes.value[0].approved_by_user", "old": False, "new": True}]


# ---------------------------------------------------------------- what a decision must not touch

def test_decision_changes_only_the_flag_the_limit_it_feeds_and_the_version(client):
    # §1 inv. 1 + inv. 2 + D7: parsed_notes keeps its llm: stamp; the user's own fields and every other limit are
    # unchanged; only the rule flag, limits.max_theme_weight, the derived trace and meta move.
    before = _baseline(client, AMIRA)
    after = _decide(client, AMIRA, TECH_RULE, False)
    for k in ("by", "confidence", "prompt_version", "model_id", "input_hash", "updated_at"):
        assert after["values"]["parsed_notes"][k] == before["values"]["parsed_notes"][k], k
    expected_rules = copy.deepcopy(_rules(before))
    expected_rules[TECH_RULE]["approved_by_user"] = False
    assert _rules(after) == expected_rules
    for section in ("identity", "situation", "objectives", "risk", "knowledge", "preferences"):
        assert after[section] == before[section], section
    assert after["values"]["notes"] == before["values"]["notes"]
    drop = lambda lim: {k: v for k, v in lim.items() if k != "max_theme_weight"}
    assert drop(after["limits"]) == drop(before["limits"])


@pytest.mark.parametrize("pid,index,body", [
    (AMIRA, 7, {"approved": False}),                      # no such rule
    (AMIRA, 0, {"approved": True, "max_pct": 10}),        # max_pct only applies to a max_theme_weight rule
    (AMIRA, TECH_RULE, {"approved": True, "max_pct": 10}),  # the rule already has a number
    (AMIRA, TECH_RULE, {"approved": "maybe"}),            # strict body
    (AMIRA, TECH_RULE, {"approved": False, "limits": {"max_crypto_weight": 0.5}}),   # a body can never set limits
])
def test_refused_decisions_write_no_event_no_audit_and_do_not_change_the_profile(client, store_dir, pid, index, body):
    # §3.4 / §4: nothing is appended and no run is stored unless the decision is accepted.
    before = _baseline(client, pid)
    events_file = _work(store_dir, pid) / "events.jsonl"
    events_bytes, audits = events_file.read_bytes(), _audit_ids(store_dir)
    r = client.post(f"/profiles/{pid}/parsed-rules/{index}/decision", json=body)
    assert r.status_code in (404, 409, 422), r.text
    assert events_file.read_bytes() == events_bytes
    assert _audit_ids(store_dir) == audits
    assert _profile(client, pid) == before


def test_decision_never_writes_under_fixtures(client, store_dir):
    # §10: the API works on a copy under the store root; a decision (event + refresh) never writes fixtures/.
    _baseline(client, AMIRA)
    _decide(client, AMIRA, TECH_RULE, False)
    _decide(client, AMIRA, TECH_RULE, True)
    assert _hashes() == _FIXTURE_HASHES
