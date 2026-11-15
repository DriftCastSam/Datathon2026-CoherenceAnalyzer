"""FastAPI over the Flow C pipeline: profiles, intake, events, parsed-rule decisions, review, reports, audit (P1.4).

Sources: SPEC §12 (API), §3.4 (event log), §10 (persistence, audit), §1 invariants 2 and 6;
docs/spec-decisions.md D7 (Flow B is P2: `order_intent` -> 501, nothing appended; product focus = Flow C);
docs/spec-factors.md §20.6 (intake: `missing` / `next_question`, skip marker), §20.7, §20.9 (`profile_coherence`).

Everything runs offline with COHERENCE_ANALYZER_LLM=mock. `COHERENCE_ANALYZER_STORE_DIR` points at a tmp dir for
every test; the API copies a fixture dir into `store.root()/profiles/<profile_id>/` on first access and works there.
The last test checks that no file under `fixtures/` changed during the whole module.

Notes on shapes the spec leaves open (stricter reading chosen, tolerant only where the task text is silent):
the profile id in the URL is `meta.profile_id` (task text: working dir `store.root()/profiles/<profile_id>/`).
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

from coherence_analyzer import api, config, intake, pipeline  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
ROBERT_FIX = FIXTURES / "robert"
ROBERT_ID = "persona-robert"
FACTORS = ("objectif", "horizon", "risque", "contraintes", "preferences")
VERDICTS = ("coherent", "coherent_with_warnings", "incoherent")
ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")                     # SPEC §4: run_id is a ULID
ISO_TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")
FORBIDDEN_IN_ROUTES = ("buy", "sell", "order", "execute")           # SPEC §1 inv. 7: simulation only
NO_NUMBER_QUOTE = "No hype stocks unless I can explain the business."   # exact substring of Robert's notes


def _hashes() -> dict[str, str]:
    return {str(p.relative_to(FIXTURES)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(FIXTURES.rglob("*")) if p.is_file()}


_FIXTURE_HASHES = _hashes()                                         # taken before any test runs


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


@pytest.fixture()
def pid(client) -> str:
    return _robert_id(client)


def _robert_id(client: TestClient) -> str:
    r = client.get("/profiles")
    assert r.status_code == 200, r.text
    ids = [p["profile_id"] for p in r.json()]
    assert ROBERT_ID in ids, ids
    return ROBERT_ID


def _workdir(store_dir: Path, pid: str) -> Path:
    return store_dir / "profiles" / pid


def _seed(client: TestClient, store_dir: Path, pid: str, mutate) -> dict:
    """Touch the profile once (so the API copies the fixture), then rewrite the working profile.json."""
    assert client.get(f"/profiles/{pid}").status_code == 200
    path = _workdir(store_dir, pid) / "profile.json"
    assert path.exists(), "the API must copy profile.json into store.root()/profiles/<profile_id>/ on first access"
    p = json.loads(path.read_text(encoding="utf-8"))
    mutate(p)
    path.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _add_theme_rule(p: dict) -> None:
    p["values"]["parsed_notes"]["value"].append(
        {"rule_kind": "max_theme_weight", "params": {"theme": "tech_heavy"}, "applies_to": "all",
         "quote": NO_NUMBER_QUOTE, "approved_by_user": False})


def _theme_profile(client, store_dir, pid) -> str:
    _seed(client, store_dir, pid, _add_theme_rule)
    rules = client.get(f"/profiles/{pid}").json()["values"]["parsed_notes"]["value"]
    assert len(rules) == 3 and rules[2]["rule_kind"] == "max_theme_weight", "seeded profile.json must be what GET serves"
    return f"{intake.PARSED_RULES_PATH}[2].params.{intake.THEME_NUMBER_KEY}"


def _event_ids(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [json.loads(line)["event_id"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _event(**kw) -> dict:
    return {"type": "question", "text": "What does duration mean?", "ts": "2026-11-15T09:30:00-05:00",
            "source": "ui_chat", **kw}


def _mask(doc) -> str:
    """JSON text with run_id and ISO timestamps masked (the only allowed differences between two runs)."""
    return ISO_TS.sub("<TS>", json.dumps(doc, sort_keys=True, ensure_ascii=False))


def _paths(app) -> list[str]:
    return [getattr(r, "path", "") for r in app.routes]


def _local_review(store_dir_ref: Path, monkeypatch) -> dict:
    monkeypatch.setenv("COHERENCE_ANALYZER_STORE_DIR", str(store_dir_ref))
    return pipeline.review(ROBERT_FIX)


# ---------------------------------------------------------------- health, CORS, route names

def test_health_ok(client):
    # §12: GET /health
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_cors_allows_get_and_post_from_any_origin(client, pid):
    # Task: CORS middleware allowing GET/POST from any origin (local demo).
    r = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert r.headers.get("access-control-allow-origin") == "*"
    pre = client.options(f"/profiles/{pid}/events", headers={
        "Origin": "http://example.test", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type"})
    assert pre.status_code == 200
    assert pre.headers.get("access-control-allow-origin") == "*"
    allowed = {m.strip() for m in pre.headers.get("access-control-allow-methods", "").split(",")}
    assert {"GET", "POST"} <= allowed or allowed == {"*"}


def test_no_route_path_names_a_trade_action(client):
    # §1 inv. 7 / CLAUDE.md: no buy/sell recommendations, no execution. No route path contains buy, sell, order, execute.
    paths = _paths(api.app)
    assert any(p.startswith("/profiles") for p in paths)
    for p in paths:
        for word in FORBIDDEN_IN_ROUTES:
            assert word not in p.lower(), (p, word)
    spec_paths = client.get("/openapi.json").json()["paths"]
    for p in spec_paths:
        for word in FORBIDDEN_IN_ROUTES:
            assert word not in p.lower(), (p, word)


def test_expected_routes_exist(client):
    # §12 + task endpoint list: every endpoint is registered with the right method.
    spec_paths = client.get("/openapi.json").json()["paths"]
    expected = {
        ("get", "/health"), ("get", "/profiles"), ("get", "/profiles/{profile_id}"),
        ("get", "/profiles/{profile_id}/intake"), ("post", "/profiles/{profile_id}/events"),
        ("post", "/profiles/{profile_id}/intake/answer"), ("post", "/profiles/{profile_id}/intake/skip"),
        ("post", "/profiles/{profile_id}/review"), ("get", "/reports/{run_id}"), ("get", "/audit/{run_id}"),
    }
    # path parameter names are free: normalise {anything} to {x}
    have = {(m, re.sub(r"\{[^}]+\}", "{x}", p)) for p, ms in spec_paths.items() for m in ms}
    for m, p in expected:
        assert (m, re.sub(r"\{[^}]+\}", "{x}", p)) in have, (m, p)
    assert ("post", "/profiles/{x}/parsed-rules/{x}/decision") in have


# ---------------------------------------------------------------- profiles

def test_profiles_list_has_robert_with_id_and_name(client):
    # Task: GET /profiles -> [{profile_id, name}] for every fixture dir with profile.json + events.jsonl + portfolio.json.
    r = client.get("/profiles")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list) and body
    for item in body:
        assert set(item) >= {"profile_id", "name"}
        assert isinstance(item["profile_id"], str) and item["profile_id"]
        assert isinstance(item["name"], str) and item["name"]
    ids = [i["profile_id"] for i in body]
    assert len(set(ids)) == len(ids)
    assert ROBERT_ID in ids
    # Count is not hardcoded: it equals the number of fixture dirs that hold all three files.
    complete = [d for d in FIXTURES.iterdir()
                if d.is_dir() and all((d / f).exists() for f in ("profile.json", "events.jsonl", "portfolio.json"))]
    assert len(body) == len(complete)
    # Each listed id is the meta.profile_id of one of those fixtures.
    meta_ids = {json.loads((d / "profile.json").read_text(encoding="utf-8"))["meta"]["profile_id"] for d in complete}
    assert set(ids) == meta_ids


def test_get_profile_copies_fixture_into_store_and_serves_it(client, pid, store_dir):
    # Task: on first access copy the fixture dir into store.root()/profiles/<profile_id>/ and work there.
    r = client.get(f"/profiles/{pid}")
    assert r.status_code == 200
    body = r.json()
    fixture = json.loads((ROBERT_FIX / "profile.json").read_text(encoding="utf-8"))
    assert body["meta"]["profile_id"] == ROBERT_ID
    assert body["risk"] == fixture["risk"]
    assert body["objectives"] == fixture["objectives"]
    wd = _workdir(store_dir, pid)
    for name in ("profile.json", "events.jsonl", "portfolio.json"):
        assert (wd / name).exists(), name
    assert (wd / "events.jsonl").read_bytes() == (ROBERT_FIX / "events.jsonl").read_bytes()
    # Idempotent: a second access does not re-copy over later edits (same content here).
    assert client.get(f"/profiles/{pid}").json() == body


def test_get_unknown_profile_is_404(client):
    # Task: 404 unknown id (also for the other profile routes).
    assert client.get("/profiles/does-not-exist").status_code == 404
    assert client.get("/profiles/does-not-exist/intake").status_code == 404
    assert client.post("/profiles/does-not-exist/events", json=_event()).status_code == 404
    assert client.post("/profiles/does-not-exist/review").status_code == 404
    assert client.post("/profiles/does-not-exist/intake/answer",
                       json={"path": "preferences.prefers_dividends", "value": True}).status_code == 404
    assert client.post("/profiles/does-not-exist/intake/skip",
                       json={"path": "preferences.prefers_dividends"}).status_code == 404
    assert client.post("/profiles/does-not-exist/parsed-rules/0/decision", json={"approved": True}).status_code == 404


# ---------------------------------------------------------------- intake (§20.6)

def _drop_dividends(p: dict) -> None:
    del p["preferences"]["prefers_dividends"]


@pytest.mark.parametrize("locale", ["fr-CA", "en-CA"])
def test_intake_get_matches_intake_module_per_factor(client, store_dir, pid, locale):
    # §20.6: per factor {missing: intake.missing, next_question: intake.next_question}; no LLM writes questions.
    _seed(client, store_dir, pid, _drop_dividends)
    profile = client.get(f"/profiles/{pid}").json()
    r = client.get(f"/profiles/{pid}/intake", params={"locale": locale})
    assert r.status_code == 200, r.text
    body = r.json()
    # SPEC-AMBIGUITY: shape is not given; stricter reading = a JSON object keyed by the five factor ids.
    assert isinstance(body, dict) and set(body) == set(FACTORS)
    for f in FACTORS:
        assert set(body[f]) >= {"missing", "next_question"}, f
        assert body[f]["missing"] == intake.missing(profile, f), f
        assert body[f]["next_question"] == intake.next_question(profile, f, locale), f
    assert "preferences.prefers_dividends" in body["preferences"]["missing"]
    nq = body["preferences"]["next_question"]
    assert nq["path"] == body["preferences"]["missing"][0]
    assert nq["locale"] == locale
    assert nq["text"] == intake.QUESTIONS[nq["path"]][locale]


def test_intake_locale_changes_the_question_text_only(client, store_dir, pid):
    # §20.6: fixed templates per locale; the checklist itself does not depend on the locale.
    _seed(client, store_dir, pid, _drop_dividends)
    fr = client.get(f"/profiles/{pid}/intake", params={"locale": "fr-CA"}).json()
    en = client.get(f"/profiles/{pid}/intake", params={"locale": "en-CA"}).json()
    assert fr["preferences"]["missing"] == en["preferences"]["missing"]
    assert fr["preferences"]["next_question"]["text"] != en["preferences"]["next_question"]["text"]


def test_intake_rejects_unknown_locale(client, pid):
    # intake.next_question raises ValueError outside (fr-CA, en-CA): the API answers 4xx, never 500.
    r = client.get(f"/profiles/{pid}/intake", params={"locale": "de-DE"})
    assert r.status_code in (400, 422)


def test_intake_answer_stores_value_and_clears_missing(client, store_dir, pid):
    # §20.6 + task: intake.answer on the working profile, saved, returns the profile.
    _seed(client, store_dir, pid, _drop_dividends)
    path = "preferences.prefers_dividends"
    assert path in client.get(f"/profiles/{pid}/intake").json()["preferences"]["missing"]
    r = client.post(f"/profiles/{pid}/intake/answer", json={"path": path, "value": False})
    assert r.status_code == 200, r.text
    assert r.json()["preferences"]["prefers_dividends"] is False
    assert r.json()["meta"]["profile_id"] == ROBERT_ID
    saved = client.get(f"/profiles/{pid}").json()
    assert saved["preferences"]["prefers_dividends"] is False         # persisted, not only echoed
    assert path not in client.get(f"/profiles/{pid}/intake").json()["preferences"]["missing"]


def test_intake_skip_marks_path_skipped(client, store_dir, pid):
    # §20.6: a skipped path is {value: null, by: 'user:skipped'} and is no longer missing.
    _seed(client, store_dir, pid, _drop_dividends)
    path = "preferences.prefers_dividends"
    r = client.post(f"/profiles/{pid}/intake/skip", json={"path": path})
    assert r.status_code == 200, r.text
    assert r.json()["preferences"]["prefers_dividends"] == {"value": None, "by": "user:skipped"}
    assert client.get(f"/profiles/{pid}").json()["preferences"]["prefers_dividends"] == {"value": None,
                                                                                         "by": "user:skipped"}
    assert path not in client.get(f"/profiles/{pid}/intake").json()["preferences"]["missing"]


def test_intake_answer_and_skip_validate_the_body(client, pid):
    # FastAPI body validation: path is required, answer needs a value.
    assert client.post(f"/profiles/{pid}/intake/answer", json={"value": 1}).status_code == 422
    assert client.post(f"/profiles/{pid}/intake/answer", json={"path": "preferences.prefers_dividends"}).status_code == 422
    assert client.post(f"/profiles/{pid}/intake/skip", json={}).status_code == 422
    bad = client.post(f"/profiles/{pid}/intake/answer", json={"path": "not a path!", "value": 1})
    assert bad.status_code in (400, 422)


@pytest.mark.parametrize("path", ["limits.max_crypto_weight", "limits", "limits_trace.max_crypto_weight",
                                  "risk.behavioural", "knowledge.question_quality", "values.parsed_notes.by"])
def test_intake_cannot_write_limits_or_llm_fields(client, pid, path):
    # §1 inv. 2 + task: the request can never set limits or any llm: field. Limits are recomputed by the pipeline.
    before = client.get(f"/profiles/{pid}").json()
    for route, body in (("answer", {"path": path, "value": 0.99}), ("skip", {"path": path})):
        r = client.post(f"/profiles/{pid}/intake/{route}", json=body)
        assert r.status_code in (400, 403, 422), (route, path, r.status_code)
    assert client.get(f"/profiles/{pid}").json() == before


# ---------------------------------------------------------------- events (§3.4, D7)

def _events_path(store_dir: Path, pid: str) -> Path:
    return _workdir(store_dir, pid) / "events.jsonl"


def _last_event(path: Path) -> dict:
    return json.loads([line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()][-1])


def test_post_event_assigns_next_id_and_appends(client, store_dir, pid):
    # §3.4: one JSON object per line, append-only; ids look like ev_<name>_NNNN. Robert's last id is 0007 (0002, 0003
    # and 0006 are gaps), so the next id is 0008, then 0009.
    # SPEC-AMBIGUITY: "next" = highest existing number + 1 (count + 1 would give 0005, which already exists).
    assert client.get(f"/profiles/{pid}").status_code == 200
    path = _events_path(store_dir, pid)
    before = path.read_bytes()
    r = client.post(f"/profiles/{pid}/events", json=_event())
    assert r.status_code in (200, 201), r.text
    after = path.read_bytes()
    assert after.startswith(before)                                    # append-only: old lines byte-identical
    ev = _last_event(path)
    assert ev["event_id"] == "ev_robert_0008"
    assert ev["profile_id"] == ROBERT_ID
    assert ev["type"] == "question"
    assert ev["text"] == "What does duration mean?"
    assert ev["ts"] == "2026-11-15T09:30:00-05:00"
    body = r.json()
    assert (body.get("event_id") or body["event"]["event_id"]) == "ev_robert_0008"
    r2 = client.post(f"/profiles/{pid}/events", json=_event(type="note", text="Thinking about retirement."))
    assert r2.status_code in (200, 201), r2.text
    assert _last_event(path)["event_id"] == "ev_robert_0009"
    ids = _event_ids(path)
    assert len(ids) == len(set(ids))
    assert ids[-3:] == ["ev_robert_0007", "ev_robert_0008", "ev_robert_0009"]


@pytest.mark.parametrize("etype,payload", [
    ("question", None),
    ("note", None),
    ("quiz_result", {"score": 0.8, "quiz_id": "kq_v1"}),
    ("profile_edit", {"path": "risk.declared", "old": "low_moderate", "new": "moderate"}),
])
def test_post_event_accepts_the_non_order_types(client, store_dir, pid, etype, payload):
    # §3.4: type in question | order_intent | quiz_result | profile_edit | note | intent_confirmed.
    # (intent_confirmed needs a full confirmed intent, §3.6, and belongs to Flow B: not exercised here.)
    r = client.post(f"/profiles/{pid}/events", json=_event(type=etype, payload=payload, text="entry"))
    assert r.status_code in (200, 201), r.text
    ev = _last_event(_events_path(store_dir, pid))
    assert ev["type"] == etype
    assert ev["payload"] == payload


def test_order_intent_is_501_and_nothing_is_appended(client, store_dir, pid):
    # D7: Flow B is P2. order_intent -> 501 with a message saying so; the event log is untouched.
    assert client.get(f"/profiles/{pid}").status_code == 200
    path = _events_path(store_dir, pid)
    before = path.read_bytes()
    r = client.post(f"/profiles/{pid}/events",
                    json=_event(type="order_intent", text="I want to put 10% in NVDA, everyone's talking about it"))
    assert r.status_code == 501
    text = json.dumps(r.json())
    assert "Flow B" in text and "P2" in text
    assert path.read_bytes() == before


@pytest.mark.parametrize("body", [
    _event(type="bogus"),
    {"text": "no type", "ts": "2026-11-15T09:30:00-05:00"},
    {"type": "question", "ts": "2026-11-15T09:30:00-05:00"},
    _event(text=""),
    _event(ts="not-a-date"),
    _event(ts="yesterday"),
])
def test_invalid_events_are_422_and_not_appended(client, store_dir, pid, body):
    # Task: validates §3.4 (type enum, text, ts).
    assert client.get(f"/profiles/{pid}").status_code == 200
    path = _events_path(store_dir, pid)
    before = path.read_bytes()
    r = client.post(f"/profiles/{pid}/events", json=body)
    assert r.status_code == 422, r.text
    assert path.read_bytes() == before


def test_client_cannot_choose_the_event_id(client, store_dir, pid):
    # Task: the server assigns the next event_id. A client-supplied id is rejected or ignored, never stored.
    assert client.get(f"/profiles/{pid}").status_code == 200
    path = _events_path(store_dir, pid)
    r = client.post(f"/profiles/{pid}/events", json=_event(event_id="ev_robert_0001"))
    if r.status_code in (200, 201):
        assert _last_event(path)["event_id"] == "ev_robert_0008"
    else:
        assert r.status_code == 422
        assert _event_ids(path) == _event_ids(ROBERT_FIX / "events.jsonl")
    ids = _event_ids(path)
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------- parsed-rule decisions (D7 point 1, §1 inv. 2)

def _rules(client, pid) -> list[dict]:
    return client.get(f"/profiles/{pid}").json()["values"]["parsed_notes"]["value"]


def test_decision_reject_then_approve_round_trips(client, pid):
    # Task: approving then rejecting a parsed rule round-trips; only approved_by_user changes.
    before = client.get(f"/profiles/{pid}").json()
    rules0 = copy.deepcopy(before["values"]["parsed_notes"]["value"])
    assert rules0[0]["approved_by_user"] is True                     # Robert's fixture rules start approved

    r = client.post(f"/profiles/{pid}/parsed-rules/0/decision", json={"approved": False})
    assert r.status_code == 200, r.text
    assert r.json()["values"]["parsed_notes"]["value"][0]["approved_by_user"] is False
    mid = client.get(f"/profiles/{pid}").json()
    assert mid["values"]["parsed_notes"]["value"][0]["approved_by_user"] is False
    assert mid["values"]["parsed_notes"]["value"][1] == rules0[1]    # the other rule is untouched

    r = client.post(f"/profiles/{pid}/parsed-rules/0/decision", json={"approved": True})
    assert r.status_code == 200, r.text
    after = client.get(f"/profiles/{pid}").json()
    assert after["values"]["parsed_notes"]["value"] == rules0       # back to the original, field for field


def test_decision_does_not_touch_the_llm_stamp_or_other_fields(client, pid):
    # §1 inv. 1: parsed_notes stays an llm: object; the user's decision only flips the rule flag.
    before = client.get(f"/profiles/{pid}").json()
    assert client.post(f"/profiles/{pid}/parsed-rules/1/decision", json={"approved": False}).status_code == 200
    after = client.get(f"/profiles/{pid}").json()
    for k in ("by", "confidence", "prompt_version", "model_id", "input_hash"):
        assert after["values"]["parsed_notes"][k] == before["values"]["parsed_notes"][k], k
    assert after["values"]["notes"] == before["values"]["notes"]
    assert after["risk"] == before["risk"]
    assert after["objectives"] == before["objectives"]


@pytest.mark.parametrize("index", [2, 999])
def test_decision_bad_index_is_404(client, pid, index):
    # Task: 404 bad index (Robert has exactly two parsed rules).
    assert len(_rules(client, pid)) == 2
    assert client.post(f"/profiles/{pid}/parsed-rules/{index}/decision", json={"approved": True}).status_code == 404


def test_decision_non_integer_index_is_422(client, pid):
    assert client.post(f"/profiles/{pid}/parsed-rules/abc/decision", json={"approved": True}).status_code == 422


@pytest.mark.parametrize("body", [
    {},
    {"approved": "maybe"},
    {"max_pct": 10},
    {"approved": True, "limits": {"max_crypto_weight": 0.5}},
    {"approved": True, "limits_trace": {}},
    {"approved": True, "by": "llm:values_parser"},
    {"approved": True, "confidence": 1.0},
    {"approved": True, "rule_kind": "exclude_activity"},
    {"approved": True, "params": {"tag": "tobacco"}},
    {"approved": True, "values": {"parsed_notes": {"by": "user"}}},
])
def test_decision_body_is_strict_422_and_state_unchanged(client, pid, body):
    # Task: the request body can never set limits or any llm: field (422 on unknown keys); approved is a bool.
    before = client.get(f"/profiles/{pid}").json()
    r = client.post(f"/profiles/{pid}/parsed-rules/0/decision", json=body)
    assert r.status_code == 422, r.text
    assert client.get(f"/profiles/{pid}").json() == before


@pytest.mark.parametrize("bad", [-5, 150, 100.01])
def test_decision_max_pct_out_of_range_is_422(client, store_dir, pid, bad):
    # A theme share is a percentage in (0, 100]; out-of-range is rejected before anything is stored.
    _theme_profile(client, store_dir, pid)
    before = client.get(f"/profiles/{pid}").json()
    r = client.post(f"/profiles/{pid}/parsed-rules/2/decision", json={"approved": True, "max_pct": bad})
    assert r.status_code == 422, r.text
    assert client.get(f"/profiles/{pid}").json() == before


def test_theme_rule_number_comes_from_the_user_and_reaches_limits_only_after_approval(client, store_dir, pid):
    # D7 point 1 + §1 inv. 2: a parsed theme rule with no number asks for one (contraintes); the user's number and
    # approval make it a limit (percent -> fraction once), recomputed by the pipeline; a rejection removes it.
    max_pct_path = _theme_profile(client, store_dir, pid)
    assert max_pct_path in client.get(f"/profiles/{pid}/intake").json()["contraintes"]["missing"]
    assert client.get(f"/profiles/{pid}/intake").json()["contraintes"]["next_question"]["path"] == max_pct_path

    r = client.post(f"/profiles/{pid}/parsed-rules/2/decision", json={"approved": True, "max_pct": 15})
    assert r.status_code == 200, r.text
    rule = _rules(client, pid)[2]
    assert rule["approved_by_user"] is True
    assert rule["params"]["max_pct"] == 15
    assert rule["params"]["theme"] == "tech_heavy"                   # nothing else in the rule changed
    assert rule["quote"] == NO_NUMBER_QUOTE and rule["rule_kind"] == "max_theme_weight"
    assert max_pct_path not in client.get(f"/profiles/{pid}/intake").json()["contraintes"]["missing"]

    assert client.post(f"/profiles/{pid}/review").status_code == 200
    limits = client.get(f"/profiles/{pid}").json()["limits"]
    assert limits["max_theme_weight"] == pytest.approx({"tech_heavy": 0.15}, abs=1e-9)
    assert limits["by"].startswith("rule:")

    assert client.post(f"/profiles/{pid}/parsed-rules/2/decision", json={"approved": False}).status_code == 200
    assert _rules(client, pid)[2]["approved_by_user"] is False
    assert client.post(f"/profiles/{pid}/review").status_code == 200
    assert not client.get(f"/profiles/{pid}").json()["limits"].get("max_theme_weight")


# ---------------------------------------------------------------- review, reports, audit (§12, §10, §13)

def test_review_equals_pipeline_review_apart_from_run_id_and_timestamps(client, pid, tmp_path, monkeypatch):
    # Task: Robert review via the API equals pipeline.review on the fixture (verdict, sections, checks).
    api_store = Path(os.environ["COHERENCE_ANALYZER_STORE_DIR"])
    ref = _local_review(tmp_path / "ref_store", monkeypatch)
    monkeypatch.setenv("COHERENCE_ANALYZER_STORE_DIR", str(api_store))

    r = client.post(f"/profiles/{pid}/review")
    assert r.status_code == 200, r.text
    rep = r.json()
    assert ULID.match(rep["run_id"]) and rep["run_id"] != ref["run_id"]
    assert rep["flow"] == "C" and rep["intent"] is None
    assert rep["verdict"] in VERDICTS
    assert rep["verdict"] == ref["verdict"]
    assert rep["sections"] == ref["sections"]
    assert rep["checks_current"] == ref["checks_current"]
    assert [s["factor"] for s in rep["sections"]] == list(FACTORS)
    # Everything else too, once run_id and ISO timestamps are masked (the only allowed differences).
    a = _mask({k: v for k, v in rep.items() if k != "run_id"})
    b = _mask({k: v for k, v in ref.items() if k != "run_id"})
    assert a == b
    assert rep["disclaimer"] == config.DISCLAIMER["fr-CA"]            # SPEC 9.4: fixed text


def test_review_is_stored_and_served_by_run_id(client, pid, store_dir):
    # §12: GET /reports/{run_id}; §10: reports/<run_id>.json under the store root.
    rep = client.post(f"/profiles/{pid}/review").json()
    run_id = rep["run_id"]
    assert (store_dir / "reports" / f"{run_id}.json").exists()
    got = client.get(f"/reports/{run_id}")
    assert got.status_code == 200
    assert got.json() == rep


def test_audit_record_for_the_review_run(client, pid, store_dir):
    # §12: GET /audit/{run_id}; §10 audit record: flow, profile_id, verdict, events considered, llm calls.
    rep = client.post(f"/profiles/{pid}/review").json()
    r = client.get(f"/audit/{rep['run_id']}")
    assert r.status_code == 200
    audit = r.json()
    assert audit["run_id"] == rep["run_id"]
    assert audit["flow"] == "C"
    assert audit["profile_id"] == ROBERT_ID
    assert audit["verdict"] == rep["verdict"]
    assert audit["events_considered"] == _event_ids(ROBERT_FIX / "events.jsonl")
    assert audit["as_of"] == "2026-11-12"                              # D1: reference date = portfolio.as_of
    assert {c["node"] for c in audit["llm_calls"]} <= {"explainer", "values_parser", "profile_consistency"}
    assert (store_dir / "audit" / f"{rep['run_id']}.json").exists()


def test_review_considers_events_appended_through_the_api(client, pid):
    # Task: the review runs on the working dir, so an event posted before it is in the audit's events_considered.
    r = client.post(f"/profiles/{pid}/events", json=_event(text="Is XBB safe?"))
    assert r.status_code in (200, 201)
    rep = client.post(f"/profiles/{pid}/review").json()
    audit = client.get(f"/audit/{rep['run_id']}").json()
    assert audit["events_considered"][-1] == "ev_robert_0008"
    assert audit["events_considered"][:-1] == _event_ids(ROBERT_FIX / "events.jsonl")


def test_review_limits_are_recomputed_not_taken_from_the_request(client, store_dir, pid):
    # §1 inv. 2 + task: limits in returned profiles are always recomputed by the pipeline. A tampered limits block
    # in the working file is replaced after a review, and the request has no way to carry one.
    def tamper(p: dict) -> None:
        p["limits"]["max_crypto_weight"] = 0.99
    clean = json.loads((ROBERT_FIX / "profile.json").read_text(encoding="utf-8"))["limits"]["max_crypto_weight"]
    _seed(client, store_dir, pid, tamper)
    r = client.post(f"/profiles/{pid}/review", json={"limits": {"max_crypto_weight": 0.5}})
    assert r.status_code in (200, 422), r.text                       # a body, if accepted at all, is ignored
    if r.status_code == 422:
        r = client.post(f"/profiles/{pid}/review")
    assert r.status_code == 200
    limits = client.get(f"/profiles/{pid}").json()["limits"]
    assert limits["max_crypto_weight"] == pytest.approx(clean, abs=1e-9)
    assert limits["by"].startswith("rule:")


@pytest.mark.parametrize("route", ["reports", "audit"])
@pytest.mark.parametrize("run_id", ["01ARZ3NDEKTSV4RRFFQ69G5FAV", "nope"])
def test_unknown_run_id_is_404(client, route, run_id):
    # Task: stored docs, 404 if missing (no 500 from FileNotFoundError).
    assert client.get(f"/{route}/{run_id}").status_code == 404


def test_run_id_cannot_reach_other_store_files(client, pid):
    # A run id is an opaque key under reports/ and audit/, not a path.
    client.post(f"/profiles/{pid}/review")
    assert client.get(f"/reports/..%2Fprofiles%2F{pid}%2Fprofile.json").status_code in (404, 422)


# ---------------------------------------------------------------- fixtures stay read-only

def test_zz_fixtures_are_unchanged_after_the_whole_module():
    # Task: the API never mutates fixtures/. Runs last (file order): every file under fixtures/ keeps its sha256,
    # and no file appeared or disappeared.
    assert _hashes() == _FIXTURE_HASHES
