"""P6.CORRECT oracle: the feedback loop. The user corrects anything the AI read, in their own words; the analysis
re-runs and the AI reading updates.

Sources: docs/spec-decisions.md D11 (a numeric note rule is a provisional limit, capped at `warning`), D13 (the flow asks
nothing; owner review 2026-11-15, Q1: "a user can reprompt on anything noted by the AI ... and correct anything that is
wrong"), D10; SPEC §4 Flow A (A2/A3 N3 only when the notes changed, A5 limits, A6 version + audit) and Flow C, §6.2 N3,
§11 (the gate), §12 (API), §1 inv. 1-3 and 5, §9.3 (blocklist); docs/spec-factors.md §20.9 (N6 re-runs on the notes),
§7.4 S1 / S3.

Route pinned here: `POST /profiles/{id}/corrections`
  body     {target: {kind: "parsed_rule" | "profile_finding" | "behaviour" | "note_reading", ref}, text}
           parsed_rule ref = the index of the rule in values.parsed_notes.value (int); profile_finding ref = the id of a
           profile_coherence finding ("C1".."C5", "N6.k"); behaviour ref = a check id ("S1", "S3").
           Unknown keys -> 422 (no limits / limits_trace / llm: field can be sent, inv. 2); text non-blank, <= 500 chars.
  effect   appends ONE `note` event (source "ui", payload {target, text}), appends the text to `values.notes` on its own
           line `Correction (<kind> <ref>): <text>`, runs the existing Flow A refresh (N3 re-reads the notes through the
           gate) and the Flow C review.
  response {event, profile_diff, report, messages}; `messages` = plain statements (like the intake route's), non-empty
           when the reading could not be updated (gate rejection or no usable answer): the previous reading is kept.

UI hooks pinned in deploy/site/index.html (scanned as text, like the other dashboard oracles; no JS runtime):
  renderCorrectionControl(kind, ref)   the small control: `<form data-correct-kind data-correct-ref>` with a text box
                                       `text` (maxlength 500) and a submit button labelled « Corriger »; returns "" when
                                       there is no API base (static demo: hidden). Makes no request, asks nothing.
  sendCorrection(kind, ref, text)      `apiPost(\\`/profiles/${...}/corrections\\`, {target: {kind, ref}, text})`, then
                                       re-renders with the returned `report`.
Everything is offline (COHERENCE_ANALYZER_LLM=mock) against a tmp store; the N3 / N6 answers are a fake `bedrock.call_tool`
returning schema-valid output; fixtures/ must stay untouched.
"""
# STALE-ORACLE: tests/profile/test_limits_no_llm_inputs.py (test_llm_fields_do_not_change_limits,
# test_llm_fields_do_not_change_limits_without_rederive, test_unapproved_parsed_rules_do_not_appear_in_limits) perturb an
# UNAPPROVED numeric max_theme_weight rule and expect no limit. docs/spec-decisions.md D11 points 1, 2 and 5 (gated numeric
# note rules are promoted at once as provisional limits; "needs the owner's update") supersede that. Nothing below needs them.
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

from coherence_analyzer import api, config, data, gate  # noqa: E402
from coherence_analyzer.coherence import internal  # noqa: E402
from coherence_analyzer.llm import bedrock, extractors  # noqa: E402
from coherence_analyzer.rules import limits as limits_mod  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
META_DIR = ROOT / "data" / "meta"
INDEX = ROOT / "deploy" / "site" / "index.html"
AMIRA, ROBERT, MATEO = "persona-amira", "persona-robert", "persona-mateo"
FIXTURE_DIR = {AMIRA: "amira", ROBERT: "robert", MATEO: "mateo"}
VERDICTS = ("coherent", "coherent_with_warnings", "incoherent")
TECH_RULE = 1                                       # Amira: max_theme_weight tech_heavy, 50, approved (fixture)
TECH20 = "Actually my limit for tech is 20 % of the portfolio."
TECH30 = "Make that 30 % for tech, not less."
ROBERT_TEXT = "I changed my mind about small speculative companies."
ISO_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?$")
NOT_IN_NOTES = "This sentence is nowhere in the notes."

# SPEC §9.3 item 3, verbatim list (matched case- and accent-insensitively).
BLOCKLIST = (
    "tu devrais", "vous devriez", "tu pourrais", "vous pourriez", "envisage", "il serait judicieux",
    "il serait préférable", "je recommande", "nous recommandons", "je conseille", "nous conseillons",
    "je suggère", "rééquilibr", "you should", "you could", "you may want to", "you might want to", "consider",
    "it would be wise", "it would be better", "we recommend", "i recommend", "we suggest", "i suggest",
    "we advise", "rebalanc", "next step", "prochaine étape",
)
DISCLAIMER_FR = config.DISCLAIMER["fr-CA"]


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.replace("\u2019", "'").replace("\xa0", " ").replace("\u202f", " "))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", s)


def blocklist_hits(s: str) -> list[str]:
    low = norm(s)
    return [p for p in BLOCKLIST if norm(p) in low]


def _hashes(*roots: Path) -> dict[str, str]:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for r in roots if r.exists() for p in sorted(r.rglob("*")) if p.is_file() and "__pycache__" not in p.parts}


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


def _fixture_profile(pid: str) -> dict:
    return json.loads((FIXTURES / FIXTURE_DIR[pid] / "profile.json").read_text(encoding="utf-8"))


def _profile(client: TestClient, pid: str) -> dict:
    r = client.get(f"/profiles/{pid}")
    assert r.status_code == 200, r.text
    return r.json()


def _rules(profile: dict) -> list[dict]:
    return profile["values"]["parsed_notes"]["value"]


def _correct_raw(client: TestClient, pid: str, kind: str, ref, text: str):
    return client.post(f"/profiles/{pid}/corrections", json={"target": {"kind": kind, "ref": ref}, "text": text})


def _correct(client: TestClient, pid: str, kind: str, ref, text: str) -> dict:
    r = _correct_raw(client, pid, kind, ref, text)
    assert r.status_code == 200, r.text
    return r.json()


def _num(x):
    """A limit as a number: a plain number, or the one number of an object like {value, provisional}."""
    if isinstance(x, dict):
        nums = [v for v in x.values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
        assert len(nums) == 1, x
        return nums[0]
    return x


def _is_provisional(entry) -> bool:
    return isinstance(entry, dict) and entry.get("provisional") is True


def _rebuilt(profile: dict, pid: str) -> tuple[dict, dict]:
    """(limits, limits_trace) rebuilt from the profile by the rules module (inv. 2: no llm: input but the D11 notes)."""
    pf = data.load_portfolio(FIXTURES / FIXTURE_DIR[pid] / "portfolio.json", data.load_assets())
    lim, trace = limits_mod.build_limits(profile, pf["value_cad"])
    return json.loads(json.dumps(lim)), json.loads(json.dumps(trace))


# ---------------------------------------------------------------- the fake LLM (schema-valid N3 / N6 answers)

class FakeLLM:
    """Replaces `bedrock.call_tool`. N3 / N6 answers come from the given callables (payload -> output) and are checked
    against the node's schema; every other node falls through to the recorded mock cache (a miss stays a miss)."""

    def __init__(self, real, n3=None, n6=None):
        self.real, self.handlers, self.calls = real, {"values_parser": n3, "profile_consistency": n6}, []

    def __call__(self, node, schema, system, user_payload, prompt_version):
        self.calls.append((node, copy.deepcopy(user_payload)))
        handler = self.handlers.get(node)
        if handler is None:
            return self.real(node, schema, system, user_payload, prompt_version)
        out = handler(user_payload)
        problems = [e.message for e in Draft202012Validator(schema).iter_errors(out)]
        assert not problems, f"the fake {node} answer must be schema-valid: {problems}"
        log = {"node": node, "model_id": "fake:p6-correct", "prompt_version": prompt_version,
               "input_hash": bedrock.input_hash(user_payload), "cache_hit": False, "latency_ms": 0,
               "tokens_in": 0, "tokens_out": 0}
        return {"output": copy.deepcopy(out), "call_log": log}

    def payloads(self, node: str) -> list[dict]:
        return [p for n, p in self.calls if n == node]


def install(monkeypatch, n3=None, n6=None) -> FakeLLM:
    fake = FakeLLM(bedrock.call_tool, n3, n6)
    monkeypatch.setattr(bedrock, "call_tool", fake)
    return fake


def tech_reader(pcts: dict[str, float]):
    """N3 fake for Amira: keeps her first rule and reads the LAST correction present in the notes as a tech cap."""
    def n3(payload: dict) -> dict:
        notes = payload["notes"]
        text = max((t for t in pcts if t in notes), key=notes.rfind)
        rules = []
        if "I love AI and new tech" in notes:
            rules.append({"rule_kind": "prefer_attribute", "params": {"attribute": "ai_and_new_tech"},
                          "applies_to": "all", "quote": "I love AI and new tech"})
        rules.append({"rule_kind": "max_theme_weight", "params": {"theme": "tech_heavy", "max_pct": pcts[text]},
                      "applies_to": "all", "quote": text})
        return {"rules": rules}
    return n3


def bad_quote_reader(payload: dict) -> dict:
    """An N3 answer whose quote is not in the notes: the gate must drop it (rejected:evidence)."""
    return {"rules": [{"rule_kind": "max_theme_weight", "params": {"theme": "tech_heavy", "max_pct": 5},
                       "applies_to": "all", "quote": NOT_IN_NOTES}]}


# ---------------------------------------------------------------- 1. the route: event, notes, diff, report

def test_correction_returns_event_profile_diff_and_a_flow_c_report(client, monkeypatch):
    # §12 + task (1): the response is {event, profile_diff, report}; the report is the Flow C review (§13 schema).
    install(monkeypatch, n3=tech_reader({TECH20: 20}))
    body = _correct(client, AMIRA, "parsed_rule", TECH_RULE, TECH20)
    assert {"event", "profile_diff", "report"} <= set(body)
    rep = body["report"]
    schema = json.loads((config.SCHEMAS_DIR / "report.json").read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(rep)) == []
    assert rep["flow"] == "C" and rep["profile_id"] == AMIRA and rep["intent"] is None
    assert rep["verdict"] in VERDICTS
    assert rep["disclaimer"] == config.DISCLAIMER["fr-CA"]                       # §9.4 fixed text, never rewritten
    assert isinstance(body["profile_diff"], list)
    # the report served by GET /reports/{run_id} is the one returned (same run)
    assert client.get(f"/reports/{rep['run_id']}").json() == rep


def test_correction_appends_one_note_event_from_the_ui(client, store_dir, monkeypatch):
    # §3.4 + task (1): one `note` event, source "ui", payload {target, text}; append-only; the response carries it.
    install(monkeypatch, n3=tech_reader({TECH20: 20}))
    assert client.get(f"/profiles/{AMIRA}").status_code == 200                   # first access copies the fixture
    before = _events(store_dir, AMIRA)
    body = _correct(client, AMIRA, "parsed_rule", TECH_RULE, TECH20)
    events = _events(store_dir, AMIRA)
    assert len(events) == len(before) + 1
    assert events[:-1] == before
    ev = events[-1]
    assert body["event"] == ev
    assert ev["type"] == "note" and ev["source"] == "ui" and ev["profile_id"] == AMIRA
    assert ev["payload"] == {"target": {"kind": "parsed_rule", "ref": TECH_RULE}, "text": TECH20}
    assert ev["event_id"].startswith("ev_amira_") and ev["event_id"] not in {e["event_id"] for e in before}
    assert isinstance(ev["text"], str) and TECH20 in ev["text"]
    assert ISO_TS.match(ev["ts"])


def test_correction_text_joins_the_notes_as_the_users_own_words(client, monkeypatch):
    # §3.1 + D11: the text is appended to values.notes (user-declared), after what was there, on a separator line.
    # SPEC-AMBIGUITY: the task gives the separator as "e.g."; stricter reading: exactly that line format.
    install(monkeypatch, n3=tech_reader({TECH20: 20}))
    original = _fixture_profile(AMIRA)["values"]["notes"]
    body = _correct(client, AMIRA, "parsed_rule", TECH_RULE, TECH20)
    notes = _profile(client, AMIRA)["values"]["notes"]
    assert notes.startswith(original) and len(notes) > len(original)
    assert f"Correction (parsed_rule {TECH_RULE}): {TECH20}" in notes.splitlines()
    assert notes.index(TECH20) > len(original) - 1
    diff = {d["path"]: d for d in body["profile_diff"]}                          # the edit itself shows in the diff
    assert diff["values.notes"]["old"] == original and diff["values.notes"]["new"] == notes
    assert diff["values.notes"]["by"] == "user:declared"


def test_two_corrections_stack_in_order_and_the_latest_reading_wins(client, store_dir, monkeypatch):
    # §3.1: earlier words are never dropped; N3 re-reads all the notes, so the newest cap replaces the older one.
    install(monkeypatch, n3=tech_reader({TECH20: 20, TECH30: 30}))
    original = _fixture_profile(AMIRA)["values"]["notes"]
    _correct(client, AMIRA, "parsed_rule", TECH_RULE, TECH20)
    _correct(client, AMIRA, "parsed_rule", TECH_RULE, TECH30)
    notes = _profile(client, AMIRA)["values"]["notes"]
    assert notes.startswith(original)
    assert 0 < notes.index(TECH20) < notes.index(TECH30)
    assert [e["type"] for e in _events(store_dir, AMIRA)[-2:]] == ["note", "note"]
    limits = _profile(client, AMIRA)["limits"]
    assert _num(limits["max_theme_weight"]["tech_heavy"]) == pytest.approx(0.30, abs=1e-9)


# ---------------------------------------------------------------- 2. the AI re-reads through the gate (D11)

def test_the_parsed_rule_changes_and_the_provisional_limit_follows(client, monkeypatch):
    # §6.2 N3 + D11 points 1-2 + inv. 1: N3 re-reads the notes with the correction; the gated numeric rule becomes a
    # PROVISIONAL limit (flag, trace entry), never an approved one. The user's earlier approval of 50 % is gone.
    fake = install(monkeypatch, n3=tech_reader({TECH20: 20}))
    before = _profile(client, AMIRA)
    assert _num(before["limits"]["max_theme_weight"]["tech_heavy"]) == pytest.approx(0.5, abs=1e-9)
    assert not _is_provisional(before["limits"]["max_theme_weight"]["tech_heavy"])
    _correct(client, AMIRA, "parsed_rule", TECH_RULE, TECH20)
    assert any(TECH20 in p["notes"] for p in fake.payloads("values_parser"))     # N3 saw the corrected notes
    got = _profile(client, AMIRA)
    parsed = got["values"]["parsed_notes"]
    assert parsed["by"] == "llm:values_parser" and parsed["model_id"] == "fake:p6-correct"
    assert parsed["input_hash"] == bedrock.input_hash(extractors.n3_payload(got["values"]["notes"]))
    theme = [r for r in _rules(got) if r["rule_kind"] == "max_theme_weight"]
    assert len(theme) == 1
    assert theme[0]["params"] == {"theme": "tech_heavy", "max_pct": 20}
    assert theme[0]["quote"] == TECH20 and TECH20 in got["values"]["notes"]      # evidence: exact substring (§11)
    assert theme[0]["approved_by_user"] is False                                 # the gate starts every rule unapproved
    assert not any(r["params"].get("max_pct") == 50 for r in _rules(got))
    entry = got["limits"]["max_theme_weight"]["tech_heavy"]
    assert _num(entry) == pytest.approx(0.20, abs=1e-9)                          # percent -> fraction once
    assert _is_provisional(entry)                                                # D11 point 1
    assert "values.parsed_notes[provisional]" in got["limits_trace"]["max_theme_weight"]     # D11 point 1
    # inv. 2: limits are exactly what the rules module builds from the stored profile (no other path into them)
    limits, trace = _rebuilt(got, AMIRA)
    assert got["limits"] == limits and got["limits_trace"] == trace


def test_a_check_against_the_provisional_limit_is_capped_at_warning(client, monkeypatch):
    # D11 point 2 + inv. 3: Amira holds 38 % in tech_heavy (QQQ + NVDA + SHOP.TO); against a provisional 20 % the H13
    # check is a `warning`, never a `violation`, and no check anywhere gets `violation` from the AI reading.
    install(monkeypatch, n3=tech_reader({TECH20: 20}))
    base = client.post(f"/profiles/{AMIRA}/review").json()
    rep = _correct(client, AMIRA, "parsed_rule", TECH_RULE, TECH20)["report"]
    h13 = [c for c in rep["checks_current"] if c["id"] == "H13" and c["subject"] == "tech_heavy"]
    assert len(h13) == 1
    assert h13[0]["status"] == "warning"
    assert h13[0]["value"] == pytest.approx(0.38, abs=1e-9)
    assert _num(h13[0]["limit"]) == pytest.approx(0.20, abs=1e-9)
    violations = {c["id"] for c in rep["checks_current"] if c["status"] == "violation"}
    base_violations = {c["id"] for c in base["checks_current"] if c["status"] == "violation"}
    assert "H13" not in violations
    assert violations <= base_violations                                         # the reading added no violation
    assert [c for c in base["checks_current"] if c["id"] == "H13"][0]["status"] == "pass"   # 38 % <= 50 % (approved)


def test_correction_runs_flow_a_then_flow_c_and_both_are_audited(client, store_dir, monkeypatch):
    # §4 A2/A3/A6 + §10 + §11: the refresh audit names the new event and an accepted values_parser call (through the
    # gate); the review audit is retrievable by the report's run_id.
    install(monkeypatch, n3=tech_reader({TECH20: 20}))
    body = _correct(client, AMIRA, "parsed_rule", TECH_RULE, TECH20)
    eid = body["event"]["event_id"]
    audits = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((store_dir / "audit").glob("*.json"))]
    flow_a = [a for a in audits if a["flow"] == "A" and eid in a["events_considered"]]
    assert len(flow_a) == 1
    n3 = [c for c in flow_a[0]["llm_calls"] if c["node"] == "values_parser"]
    assert [c["gate"] for c in n3] == ["accepted"] and flow_a[0]["llm_errors"] == []
    assert "values.notes" in {d["path"] for d in flow_a[0]["profile_diff"]}
    review = client.get(f"/audit/{body['report']['run_id']}")
    assert review.status_code == 200 and review.json()["flow"] == "C"
    assert review.json()["profile_id"] == AMIRA


def test_every_llm_reading_of_a_correction_goes_through_the_gate(client, monkeypatch):
    # inv. 5 + §11: N3 (re-read of the notes) and N6 (profile consistency) outputs both pass gate.gate.
    install(monkeypatch, n3=tech_reader({TECH20: 20}), n6=lambda payload: {"abstain": True, "findings": []})
    seen: list[str] = []
    real_gate = gate.gate

    def spy(node, old_field, llm_output, context):
        seen.append(node)
        return real_gate(node, old_field, llm_output, context)

    monkeypatch.setattr(gate, "gate", spy)
    monkeypatch.setattr(internal, "gate", spy)      # internal.py binds `gate` by name (from ..gate import gate)
    _correct(client, AMIRA, "parsed_rule", TECH_RULE, TECH20)
    assert "values_parser" in seen and "profile_consistency" in seen


# ---------------------------------------------------------------- 3. a reading that fails the gate keeps the previous one (D10)

def _flow_a_audit(store_dir: Path, event_id: str) -> dict:
    audits = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((store_dir / "audit").glob("*.json"))]
    found = [a for a in audits if a["flow"] == "A" and event_id in a["events_considered"]]
    assert len(found) == 1
    return found[0]


@pytest.mark.parametrize("mode", ["gate_rejection", "no_usable_answer"])
def test_a_reading_that_cannot_be_used_keeps_the_previous_one_and_says_so(client, store_dir, monkeypatch, mode):
    # §11 + inv. 4/5 + D10: a quote that is not in the notes is dropped by the gate (rejected:evidence); a recorded
    # answer that is missing (mock mode, the notes changed) is no answer. Either way the previous reading and its
    # limits stay, the user's words are still saved, the review still runs, and the response says so in plain words.
    fake = install(monkeypatch, n3=bad_quote_reader) if mode == "gate_rejection" else None
    previous = _fixture_profile(AMIRA)
    body = _correct(client, AMIRA, "parsed_rule", TECH_RULE, TECH20)
    if fake is not None:
        assert fake.payloads("values_parser")                                    # N3 did run on the corrected notes
    got = _profile(client, AMIRA)
    assert got["values"]["parsed_notes"] == previous["values"]["parsed_notes"]   # same rules, same approvals, same stamp
    assert TECH20 in got["values"]["notes"]                                      # the user's words are kept as declared
    entry = got["limits"]["max_theme_weight"]["tech_heavy"]
    assert _num(entry) == pytest.approx(0.5, abs=1e-9) and not _is_provisional(entry)
    limits, trace = _rebuilt(got, AMIRA)
    assert got["limits"] == limits and got["limits_trace"] == trace
    assert [e["type"] for e in _events(store_dir, AMIRA)][-1] == "note"
    assert body["report"]["flow"] == "C" and body["report"]["verdict"] in VERDICTS
    # SPEC-AMBIGUITY: the spec says "the response says so in plain words" without a key; stricter reading: `messages`,
    # a non-empty list of non-blank strings like the intake routes' `intake.messages`, saying the previous reading is kept.
    msgs = body["messages"]
    assert isinstance(msgs, list) and msgs and all(isinstance(m, str) and m.strip() for m in msgs)
    assert any("previous" in m.lower() for m in msgs)
    assert blocklist_hits(" ".join(msgs)) == []
    audit = _flow_a_audit(store_dir, body["event"]["event_id"])
    if mode == "gate_rejection":
        assert [c["gate"] for c in audit["llm_calls"] if c["node"] == "values_parser"] == ["rejected:evidence"]
    else:
        assert audit["llm_calls"] == [] or all(c["node"] != "values_parser" for c in audit["llm_calls"])
        assert [e["node"] for e in audit["llm_errors"]] == ["values_parser"]


# ---------------------------------------------------------------- 4. a correction on an N6 finding re-runs N6 (§20.9)

def other_reader(text: str):
    def n3(payload: dict) -> dict:
        return {"rules": [{"rule_kind": "other", "params": {"note": "changed mind"}, "applies_to": "all",
                           "quote": text}]}
    return n3


def n6_tension(text: str, *, quote: str | None = None):
    """N6 fake: one tension between the first stated interest and the user's corrected words (or a made-up quote)."""
    def n6(payload: dict) -> dict:
        interest = payload["profile"]["preferences"]["interests"][0]
        return {"abstain": False, "findings": [{
            "kind": "tension", "fields": ["preferences.interests", "values.notes"],
            "quotes": [interest, quote or text],
            "reason": "The corrected note and the stated interest pull in different directions."}]}
    return n6


def test_a_correction_on_an_n6_finding_reruns_n6_with_the_new_notes(client, store_dir, monkeypatch):
    # §20.9 + task (3): N6's payload carries values.notes; after the correction the notes changed, so N6 runs again on
    # them (not a stale answer), through the gate; rules (C1) are code and stay. Limits do not move (inv. 2).
    base = client.post(f"/profiles/{ROBERT}/review").json()
    ids = [f["id"] for f in base["profile_coherence"]["findings"]]
    assert "N6.1" in ids and "C1" in ids
    old_quote = "No hype stocks unless I can explain the business."
    assert any(old_quote in (f.get("quotes") or []) for f in base["profile_coherence"]["findings"])
    limits_before = _profile(client, ROBERT)["limits"]
    fake = install(monkeypatch, n3=other_reader(ROBERT_TEXT), n6=n6_tension(ROBERT_TEXT))
    body = _correct(client, ROBERT, "profile_finding", "N6.1", ROBERT_TEXT)
    assert body["event"]["payload"] == {"target": {"kind": "profile_finding", "ref": "N6.1"}, "text": ROBERT_TEXT}
    runs = fake.payloads("profile_consistency")
    assert runs and all(ROBERT_TEXT in p["profile"]["values"]["notes"] for p in runs)
    findings = body["report"]["profile_coherence"]["findings"]
    llm = [f for f in findings if f["source"] == "llm:profile_consistency"]
    assert len(llm) == 1 and llm[0]["id"].startswith("N6.") and llm[0]["status"] == "info"   # tension -> info
    assert llm[0]["quotes"][1] == ROBERT_TEXT
    assert not any(old_quote in (f.get("quotes") or []) for f in findings)       # the stale finding is not shown again
    assert "C1" in [f["id"] for f in findings]                                   # a rule finding is not an AI reading
    got = _profile(client, ROBERT)
    assert [r["quote"] for r in _rules(got)] == [ROBERT_TEXT] and _rules(got)[0]["approved_by_user"] is False
    assert got["limits"] == limits_before


def test_an_n6_answer_with_a_made_up_quote_is_dropped_by_the_gate(client, monkeypatch):
    # §20.9 gate + inv. 5: a finding whose quote is not in the notes drops the whole N6 output; only the rules remain.
    install(monkeypatch, n3=other_reader(ROBERT_TEXT), n6=n6_tension(ROBERT_TEXT, quote="Nothing like this was written."))
    body = _correct(client, ROBERT, "profile_finding", "N6.1", ROBERT_TEXT)
    findings = body["report"]["profile_coherence"]["findings"]
    assert [f["id"] for f in findings if f["source"] == "llm:profile_consistency"] == []
    assert "C1" in [f["id"] for f in findings]


# ---------------------------------------------------------------- 5. the body: unknown keys, bad targets, size limits (inv. 2)

TARGET = {"kind": "parsed_rule", "ref": 0}
BAD_BODIES = {
    "extra_key": {"target": TARGET, "text": "ok", "extra": 1},
    "limits": {"target": TARGET, "text": "ok", "limits": {"max_theme_weight": {"tech_heavy": 0.9}}},
    "limits_trace": {"target": TARGET, "text": "ok", "limits_trace": {"max_theme_weight": []}},
    "llm_field": {"target": TARGET, "text": "ok", "llm:values_parser": {"value": []}},
    "extra_target_key": {"target": {**TARGET, "limits": 1}, "text": "ok"},
    "unknown_kind": {"target": {"kind": "limits", "ref": 0}, "text": "ok"},
    "missing_target": {"text": "ok"},
    "missing_text": {"target": TARGET},
    "blank_text": {"target": TARGET, "text": "   \n\t "},
    "empty_text": {"target": TARGET, "text": ""},
    "too_long": {"target": TARGET, "text": "x" * 501},
    "text_not_a_string": {"target": TARGET, "text": 5},
}


@pytest.mark.parametrize("name", sorted(BAD_BODIES))
def test_a_bad_body_is_a_422_and_nothing_is_written(client, store_dir, name):
    # §12 + inv. 2 (bodies forbid unknown keys; no limits / limits_trace / llm: field can be sent) + task (1).
    _profile(client, AMIRA)
    work = _work(store_dir, AMIRA)
    before = {p.name: p.read_bytes() for p in work.iterdir() if p.is_file()}
    audit_before = sorted(p.name for p in (store_dir / "audit").glob("*")) if (store_dir / "audit").exists() else []
    r = client.post(f"/profiles/{AMIRA}/corrections", json=BAD_BODIES[name])
    assert r.status_code == 422, r.text
    assert {p.name: p.read_bytes() for p in work.iterdir() if p.is_file()} == before
    audit_after = sorted(p.name for p in (store_dir / "audit").glob("*")) if (store_dir / "audit").exists() else []
    assert audit_after == audit_before                                           # no refresh, no review ran


def test_a_text_of_exactly_500_characters_is_accepted(client):
    text = "Cap: " + "t" * 495
    assert len(text) == 500
    body = _correct(client, AMIRA, "parsed_rule", 0, text)
    assert body["event"]["payload"]["text"] == text


def _from_the_route(r) -> bool:
    """True when the 4xx comes from the corrections route itself, not from FastAPI's \"no such route\" 404."""
    detail = r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else None
    return detail != "Not Found"


@pytest.mark.parametrize("kind,ref", [("parsed_rule", 99), ("parsed_rule", -1), ("parsed_rule", "abc"),
                                      ("profile_finding", "nope"), ("behaviour", "nope")])
def test_a_target_that_points_at_nothing_is_refused_and_nothing_is_written(client, store_dir, kind, ref):
    # SPEC-AMBIGUITY: the spec does not say what an unknown ref does; stricter reading: a correction must point at an
    # AI-derived item that can exist (a rule index in range, a C / N6 finding id, a check id), else 404 / 422, no write.
    _profile(client, AMIRA)
    before = (_work(store_dir, AMIRA) / "events.jsonl").read_bytes()
    r = _correct_raw(client, AMIRA, kind, ref, "ok")
    assert r.status_code in (404, 422) and _from_the_route(r), r.text
    assert (_work(store_dir, AMIRA) / "events.jsonl").read_bytes() == before


def test_an_unknown_profile_is_a_404(client, store_dir):
    r = _correct_raw(client, "persona-nobody", "parsed_rule", 0, "ok")
    assert r.status_code == 404 and "unknown profile" in r.json()["detail"]
    assert not (store_dir / "profiles" / "persona-nobody").exists()


def test_the_body_model_names_the_four_target_kinds(client):
    # task (1): target.kind is exactly parsed_rule | profile_finding | behaviour | note_reading (read from OpenAPI).
    spec = client.get("/openapi.json").json()
    op = spec["paths"]["/profiles/{profile_id}/corrections"]["post"]
    seen: set[str] = set()
    enums: list[list] = []

    def walk(node):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if ref and ref not in seen:
                seen.add(ref)
                walk(spec["components"]["schemas"][ref.split("/")[-1]])
            if isinstance(node.get("enum"), list):
                enums.append(node["enum"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(op["requestBody"])
    assert any(set(e) == {"parsed_rule", "profile_finding", "behaviour", "note_reading"} for e in enums)


@pytest.mark.parametrize("pid,kind,ref", [(AMIRA, "parsed_rule", 0), (ROBERT, "parsed_rule", 1),
                                          (ROBERT, "profile_finding", "C1"), (ROBERT, "behaviour", "S1"),
                                          (ROBERT, "behaviour", "S3"), (MATEO, "parsed_rule", 3)])
def test_every_ai_derived_item_can_be_corrected(client, store_dir, pid, kind, ref):
    # Owner Q1 (2026-11-15): "correct anything that is wrong" on a parsed rule, a profile finding, or a behaviour row.
    # No fake here: the recorded answers miss because the notes changed, which must never break the route (mock mode).
    text = "I want to put this in my own words."
    body = _correct(client, pid, kind, ref, text)
    assert body["event"]["payload"] == {"target": {"kind": kind, "ref": ref}, "text": text}
    assert f"Correction ({kind} {ref}): {text}" in _profile(client, pid)["values"]["notes"].splitlines()
    assert body["report"]["profile_id"] == pid and body["report"]["verdict"] in VERDICTS
    assert _events(store_dir, pid)[-1] == body["event"]


# ---------------------------------------------------------------- 6. fixtures, other profiles, the sources file

def test_fixtures_and_the_activity_tags_file_are_never_touched(client, store_dir, monkeypatch):
    # §10 + CLAUDE.md: seeds are read-only (the loop works on the store copy); activity_tags.csv gets no row from code.
    before = _hashes(FIXTURES, META_DIR)
    assert before
    install(monkeypatch, n3=tech_reader({TECH20: 20}))
    _correct(client, AMIRA, "parsed_rule", TECH_RULE, TECH20)
    _correct(client, ROBERT, "behaviour", "S1", ROBERT_TEXT)
    assert _hashes(FIXTURES, META_DIR) == before
    assert ROBERT_TEXT in (_work(store_dir, ROBERT) / "events.jsonl").read_text(encoding="utf-8")
    assert ROBERT_TEXT not in (FIXTURES / "robert" / "events.jsonl").read_text(encoding="utf-8")


def test_a_correction_does_not_change_another_persona_report(client, monkeypatch):
    # task (5): the persona reports stay as they are; one profile's correction reaches only its own working copy.
    def report(pid: str) -> dict:
        rep = client.post(f"/profiles/{pid}/review").json()
        rep.pop("run_id")
        return rep

    before = {pid: report(pid) for pid in (MATEO, ROBERT)}
    install(monkeypatch, n3=tech_reader({TECH20: 20}))
    _correct(client, AMIRA, "parsed_rule", TECH_RULE, TECH20)
    assert {pid: report(pid) for pid in (MATEO, ROBERT)} == before
    assert _profile(client, MATEO)["values"]["notes"] == _fixture_profile(MATEO)["values"]["notes"]


# ---------------------------------------------------------------- 7. UI: a passive « Corriger » control on every AI-derived item

def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# The JS scanner of the frozen Flow B UI oracle is reused, not copied. `deploy/site/index.html` (French,
# single-page) has been retired: the "Corriger" control is now three separate, per-screen pairs of
# (builder, submit-handler) functions in app.html's own modular JS, not one shared pair on one page.
H = _load_module("_ui_flow_b_helpers_for_correct", ROOT / "tests" / "dashboard" / "test_ui_flow_b.py")
function_source, string_literals, typed_digits, uses, norm = (
    H.function_source, H.string_literals, H.typed_digits, H.uses, H.norm)
D10_WORDS = H.D10_WORDS
DEPLOY = ROOT / "deploy"
PREFERENCES = DEPLOY / "site" / "js" / "screens" / "pillars" / "preferences.js"
COHERENCE_JS = DEPLOY / "site" / "js" / "screens" / "pillars" / "coherence.js"
RISK_JS = DEPLOY / "site" / "js" / "screens" / "pillars" / "risk.js"
APP_HTML = DEPLOY / "site" / "app.html"
# (file, builder function, submit-handler function, target kind)
CONTROLS = (
    (PREFERENCES, "correctionFormHtml", "handleCorrection", "parsed_rule"),
    (COHERENCE_JS, "correctionFormHtml", "submitCorrection", "profile_finding"),
    (RISK_JS, "correctionControlHtml", "submitCorrection", "behaviour"),
)


@pytest.fixture(scope="module")
def prefs_src() -> str:
    return PREFERENCES.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def coherence_src() -> str:
    return COHERENCE_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def risk_src() -> str:
    return RISK_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_html() -> str:
    return APP_HTML.read_text(encoding="utf-8")


_SRC_BY_FILE = {PREFERENCES: "prefs_src", COHERENCE_JS: "coherence_src", RISK_JS: "risk_src"}


def test_correction_hooks_are_defined_once_and_used(prefs_src, coherence_src, risk_src):
    by_file = {PREFERENCES: prefs_src, COHERENCE_JS: coherence_src, RISK_JS: risk_src}
    for path, builder, handler, _kind in CONTROLS:
        src = by_file[path]
        function_source(src, builder)                                            # exactly one definition
        function_source(src, handler)
        assert uses(src, builder) >= 2, f"{builder} ({path.name}) must be used somewhere besides its definition"
        assert uses(src, handler) >= 1, f"{handler} ({path.name}) must be called, not just defined"


def test_the_control_is_a_text_box_and_a_plain_verb_button_that_asks_nothing(prefs_src, coherence_src, risk_src):
    # D13 spirit: a passive affordance. A small form with a text box (<= 500 chars) and a submit button
    # labelled "Correct" (English UI): no prompt text, no question mark, no placeholder, no typed number.
    by_file = {PREFERENCES: prefs_src, COHERENCE_JS: coherence_src, RISK_JS: risk_src}
    for path, builder, _handler, _kind in CONTROLS:
        body = function_source(by_file[path], builder)
        texts = string_literals(body)
        literal = " ".join(texts)
        assert re.search(r"<form\b[^>]*\bdata-correct[-\w]*=", literal), (path.name, builder)
        assert re.search(r"<(textarea|input)\b[^>]*\bname=[\"']text[\"']", literal), (path.name, builder)
        assert re.search(r"<(textarea|input)\b[^>]*\bmaxlength=[\"']500[\"']", literal), (path.name, builder)
        assert re.search(r"<button\b[^>]*\btype=[\"']submit[\"'][^>]*>\s*Correct\s*</button>", literal), \
            (path.name, builder)
        assert "?" not in literal and "placeholder" not in literal.lower(), (path.name, builder)
        # "disabled" may appear only gated behind an in-flight ("sending") state, never on the control at rest.
        if re.search(r"\bdisabled\b", literal):
            assert re.search(r"sending", body), (path.name, builder, "disabled must be conditional on a sending state")
        assert not re.search(r"<textarea\b[^>]*>[^<]+</textarea>", literal), "the box starts empty"
        assert "fetch(" not in body, "rendering makes no request"
        assert not re.search(r"\b(prompt|confirm|alert)\s*\(", body)
        assert not typed_digits(literal)


def test_the_control_is_hidden_in_the_static_demo(prefs_src, coherence_src, risk_src, app_html):
    # No API base (the shipped page: empty <meta name="coherence-api-base">) -> the control renders nothing.
    by_file = {PREFERENCES: prefs_src, COHERENCE_JS: coherence_src, RISK_JS: risk_src}
    for path, builder, _handler, _kind in CONTROLS:
        body = function_source(by_file[path], builder)
        guard = re.search(r"if\s*\(\s*App\.source\.mode\s*!==\s*[\"']live[\"']\s*(\|\|[^)]*)?\)\s*return\s*(\"\"|''|``)",
                           body)
        assert guard, f"a static page (no live API) must get an empty string from {builder} in {path.name}"
        assert guard.start() < (body.index("<form") if "<form" in body else len(body))
    assert re.search(r"<meta\s+name=[\"']coherence-api-base[\"']\s+content=[\"']{2}\s*/?>", app_html)


def test_send_correction_posts_target_and_text_only_then_rerenders_with_the_returned_report(
        prefs_src, coherence_src, risk_src):
    # §12 + inv. 2: the body is exactly {target: {kind, ref}, text}, built by data_source.js's correction() (one
    # place, not duplicated per screen); each handler calls it, then shows the returned report instead of the
    # old one. A failure is shown, never swallowed.
    data_source_src = (DEPLOY / "site" / "js" / "core" / "data_source.js").read_text(encoding="utf-8")
    body = function_source(data_source_src, "correction")
    assert re.search(r"target\s*:\s*\{\s*kind\s*,\s*ref\s*\}", body)
    assert re.search(r"\btext\b", body)
    assert not re.search(r"limits|llm:|limits_trace", body)
    by_file = {PREFERENCES: prefs_src, COHERENCE_JS: coherence_src, RISK_JS: risk_src}
    for path, _builder, handler, kind in CONTROLS:
        src = by_file[path]
        hbody = function_source(src, handler)
        assert re.search(r"App\.source\.correction\(", hbody), (path.name, handler)
        assert re.search(rf"[\"']{re.escape(kind)}[\"']", hbody), (path.name, handler, kind)
        assert "App.setReport(" in hbody or "App.setReport(" in src, (path.name, handler)
        assert "catch" in hbody or re.search(r"\.then\([^,]*,\s*error\s*=>", hbody), (path.name, handler)


def test_correction_forms_are_bound_to_send_correction(prefs_src, coherence_src, risk_src):
    # The forms are plain markup after render(): each file binds their submit (preventDefault) to its own handler.
    by_file = {PREFERENCES: prefs_src, COHERENCE_JS: coherence_src, RISK_JS: risk_src}
    for path, _builder, handler, _kind in CONTROLS:
        src = by_file[path]
        m = re.search(r'addEventListener\(\s*[\"\']submit[\"\'].{0,500}?' + re.escape(handler) + r"\(", src, re.S)
        assert m, f"a submit listener must call {handler}( in {path.name}"
        window = src[max(0, m.start() - 200):m.end() + 100]
        assert "preventDefault" in window, (path.name, handler)


def test_every_ai_derived_item_gets_the_control(prefs_src, coherence_src, risk_src):
    # Each parsed note rule (with its quote and provisional flag), each profile_coherence finding, and the S1 /
    # S3 behaviour rows carry the control, each with its own target; the hard checks (H4/H7/H8/H14) do not.
    assert re.search(r"correctionFormHtml\(\s*index\s*,\s*notice\s*\)", prefs_src)
    assert "rule.quote" in prefs_src
    assert "provisional" in prefs_src.lower(), "the D11 provisional flag is shown on the rule"
    assert re.search(r"correctionFormHtml\(\s*finding\s*\)", coherence_src)
    assert "finding.id" in coherence_src
    detail = function_source(risk_src, "detailCard")
    assert "CORRECTABLE_CHECKS" in risk_src and "S1" in risk_src and "S3" in risk_src
    assert "correctionControlHtml(check)" in detail, "only gated checks (S1/S3) get the behaviour control"


def test_new_and_changed_ui_strings_pass_the_blocklist_and_d10_vocabulary(prefs_src, coherence_src, risk_src):
    # SPEC §9.3 item 3 + D10 items 1, 2, 4: no advisory phrase, nothing that proposes, ranks or labels a choice.
    by_file = {PREFERENCES: prefs_src, COHERENCE_JS: coherence_src, RISK_JS: risk_src}
    checked = {
        PREFERENCES: ("correctionFormHtml", "parsedRuleHtml", "parsedNotesHtml", "handleCorrection", "ruleSummary"),
        COHERENCE_JS: ("correctionFormHtml", "correctionNoteHtml", "findingCard", "submitCorrection"),
        RISK_JS: ("correctionControlHtml", "detailCard", "submitCorrection"),
    }
    for path, names in checked.items():
        src = by_file[path]
        for name in names:
            body = function_source(src, name)
            literal = " ".join(string_literals(body))
            assert blocklist_hits(literal) == [], (path.name, name)
            words = re.sub(r"</?(option|optgroup)\b", " ", literal)
            found = D10_WORDS.search(norm(words))
            assert found is None, (path.name, name, found and found.group(0))
    for path in (PREFERENCES, COHERENCE_JS, RISK_JS):
        assert blocklist_hits(by_file[path]) == [], path.name
