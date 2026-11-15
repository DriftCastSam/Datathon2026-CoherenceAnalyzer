"""P5.USERS: the app works for any user, not only the three persona fixtures (owner request, 2026-11-14).

Sources: SPEC §12 (API), §3.3 (profile v2, `schemas/profile.v2.json` = `config.SCHEMAS_DIR / "profile.v2.json"`),
§3.4 (event log, `profile_edit` payload `{path, old, new}`), §3.5 (portfolio: weights in percent summing to 100 +- 0.01,
tickers in the universe), §10 (store layout `profiles/<profile_id>/`), docs/spec-factors.md §20.6 (intake: `REQUIRED`,
`missing`, `next_question`; an unanswered value is null / `unknown`, never a default), §1 inv. 1-2 (no client-written
`limits`, `limits_trace` or `llm:` field; limits come from `rule:` fields and declared values only), docs/spec-decisions.md
D1 (reference date = `portfolio.as_of`), D3 (unknowns never change the verdict), D7 (Flow C), D10 + SPEC §9.4 (fixed
disclaimer).

Contract pinned here:
  1. A profile is any directory `<store>/profiles/<profile_id>/` holding profile.json + events.jsonl + portfolio.json.
     `fixtures/` stays seed data copied on first access and is never written. GET /profiles lists seeds and store-only users.
  2. POST /profiles {locale, display_name?} -> a profile v2 with every `intake.REQUIRED` path missing, a server-assigned id,
     an empty events.jsonl, empty parsed notes, no computed limits. Unknown keys are 422.
  3. PUT /profiles/{id}/portfolio {as_of, value_cad, weights_pct} validates and writes portfolio.json (with the profile_id)
     and appends one `profile_edit` event.
  4. POST /profiles/{id}/review works end to end in mock mode for such a user (sections without a recorded N5 answer end
     in the template).

# SPEC-AMBIGUITY (whole file): the task text does not say whether a profile created by POST /profiles but without a
# portfolio is already listed by GET /profiles ("a profile is a directory with all three files"). Listing is therefore
# asserted after the portfolio is set; GET /profiles/{id} and GET /profiles/{id}/intake are asserted right after POST.
# SPEC-AMBIGUITY (whole file): with `identity.employment_type` outside `intake.REQUIRED`, a new user's `risk.capacity`
# (and so every table limit) cannot be derived even when all 15 REQUIRED paths are answered. No test here asserts that
# limits become numeric after intake; the gap is reported to the owner, not pinned.

Offline and deterministic: COHERENCE_ANALYZER_LLM=mock, a tmp store per test, fixed ids for the hand-made user.
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
from jsonschema import Draft202012Validator

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

from coherence_analyzer import api, config, data, intake, store  # noqa: E402
from coherence_analyzer.llm import bedrock  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
ROBERT_FIX = FIXTURES / "robert"
PERSONA_IDS = {"persona-robert", "persona-amira", "persona-mateo"}
FACTORS = ("objectif", "horizon", "risque", "contraintes", "preferences")
VERDICTS = ("coherent", "coherent_with_warnings", "incoherent")
ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
USER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")        # safe as a URL segment and as a directory name
EVENT_ID = re.compile(r"^ev_.+_\d{4}$")
ISO_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
# Limits that come from the LIMITS_TABLE row of `risk.effective` (rules/limits.py); all of them need declared risk,
# horizon, withdrawals, emergency fund and expenses (tests/profile/test_skipped_fields.py AFFECTED_LIMITS).
TABLE_LIMITS = {"vol_band_annual": "H7", "floor_at_horizon": "H9", "max_drawdown_pct": "H8",
                "min_fixed_income_weight": "H4", "max_single_stock_weight": "H3", "max_sector_risk_share": "H10",
                "max_crypto_weight": "H2", "max_foreign_currency_weight": "H5", "excluded_asset_types": "H1"}

PF = {"as_of": "2026-11-12", "value_cad": 120000,
      "weights_pct": {"XIU.TO": 30, "XBB.TO": 30, "SPY": 25, "GLD": 15}}
PF2 = {"as_of": "2026-11-13", "value_cad": 150000,
       "weights_pct": {"XIU.TO": 20, "XBB.TO": 40, "SPY": 30, "GLD": 10}}


def _hashes() -> dict[str, str]:
    return {str(p.relative_to(FIXTURES)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(FIXTURES.rglob("*")) if p.is_file()}


_FIXTURE_HASHES = _hashes()                                      # taken before any test runs
_SCHEMA = Draft202012Validator(json.loads((config.SCHEMAS_DIR / "profile.v2.json").read_text(encoding="utf-8")))


# ---------------------------------------------------------------- fixtures and helpers

@pytest.fixture(autouse=True)
def store_dir(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "store"
    d.mkdir()
    monkeypatch.setenv("COHERENCE_ANALYZER_STORE_DIR", str(d))
    monkeypatch.setenv("COHERENCE_ANALYZER_LLM", "mock")
    monkeypatch.setattr(bedrock, "COHERENCE_ANALYZER_LLM", "mock")
    return d


@pytest.fixture()
def client() -> TestClient:
    return TestClient(api.app)


def _create(client: TestClient, locale: str = "en-CA", **extra) -> tuple[str, dict]:
    """POST /profiles. Returns (profile_id, response body)."""
    r = client.post("/profiles", json={"locale": locale, **extra})
    assert r.status_code in (200, 201), r.text
    body = r.json()
    pid = body["meta"]["profile_id"] if "meta" in body else body["profile_id"]
    assert isinstance(pid, str) and pid
    return pid, body


def _put_pf(client: TestClient, pid: str, pf: dict = PF):
    return client.put(f"/profiles/{pid}/portfolio", json=pf)


def _user_dir(store_dir: Path, pid: str) -> Path:
    return store_dir / "profiles" / pid


def _events(store_dir: Path, pid: str) -> list[dict]:
    return store.read_events(_user_dir(store_dir, pid) / "events.jsonl")


def _profile_dirs(store_dir: Path) -> set[str]:
    root = store_dir / "profiles"
    return {p.name for p in root.iterdir()} if root.exists() else set()


def _has_number(node) -> bool:
    if isinstance(node, bool):
        return False
    if isinstance(node, (int, float)):
        return True
    if isinstance(node, dict):
        return any(_has_number(v) for v in node.values())
    if isinstance(node, (list, tuple)):
        return any(_has_number(v) for v in node)
    return False


def _walk(node):
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _make_store_user(store_dir: Path, pid: str = "user-handmade") -> Path:
    """A user that exists only in the store: Robert's three files with the ids rewritten. No API call involved."""
    d = _user_dir(store_dir, pid)
    d.mkdir(parents=True)
    prof = json.loads((ROBERT_FIX / "profile.json").read_text(encoding="utf-8"))
    prof["meta"]["profile_id"] = pid
    (d / "profile.json").write_text(json.dumps(prof, ensure_ascii=False, indent=2), encoding="utf-8")
    pf = json.loads((ROBERT_FIX / "portfolio.json").read_text(encoding="utf-8"))
    pf.update(profile_id=pid, portfolio_id=f"{pid}-main")
    (d / "portfolio.json").write_text(json.dumps(pf), encoding="utf-8")
    lines = []
    for line in (ROBERT_FIX / "events.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            lines.append(json.dumps({**json.loads(line), "profile_id": pid}, ensure_ascii=False))
    (d / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return d


# ---------------------------------------------------------------- (2) POST /profiles

def test_post_profiles_creates_a_schema_valid_v2_profile_in_the_store(client, store_dir):
    # §12 + task (2): server-assigned id, profile v2 valid against schemas/profile.v2.json, files in the store.
    persona_dirs_before = _profile_dirs(store_dir)
    pid, body = _create(client, "en-CA", display_name="Jordan Tremblay")
    assert USER_ID.match(pid), pid
    assert pid not in PERSONA_IDS
    assert pid not in persona_dirs_before
    work = _user_dir(store_dir, pid)
    assert (work / "profile.json").is_file() and (work / "events.jsonl").is_file()
    stored = json.loads((work / "profile.json").read_text(encoding="utf-8"))
    errors = [e.message for e in _SCHEMA.iter_errors(stored)]
    assert errors == [], errors                                         # §3.3: validate on every save
    assert stored["meta"]["profile_id"] == pid
    assert stored["meta"]["schema_version"] == 2                        # §3.3 item 6
    assert stored["meta"]["locale"] == "en-CA"
    assert stored["meta"].get("version", 1) == 1
    r = client.get(f"/profiles/{pid}")
    assert r.status_code == 200, r.text
    assert r.json() == stored                                            # the API serves what is stored
    if "meta" in body:
        assert body == stored                                            # POST returns the created profile
    assert not (FIXTURES / pid).exists()                                 # seeds are never written (task 5)


def test_new_profile_has_empty_events_and_empty_parsed_notes(client, store_dir):
    # task (2): empty events.jsonl, empty parsed notes (no N3 result, nothing for the user to approve).
    pid, _ = _create(client, "fr-CA")
    work = _user_dir(store_dir, pid)
    assert (work / "events.jsonl").read_text(encoding="utf-8").strip() == ""
    assert store.read_events(work / "events.jsonl") == []
    profile = json.loads((work / "profile.json").read_text(encoding="utf-8"))
    values = profile.get("values") or {}
    assert not values.get("notes")
    parsed = values.get("parsed_notes")
    rules = parsed.get("value") if isinstance(parsed, dict) else parsed
    assert not rules                                                     # absent, null or []
    for node in _walk(profile):                                          # nothing was inferred by an LLM yet (inv. 1)
        if isinstance(node, dict) and isinstance(node.get("by"), str):
            assert not node["by"].startswith("llm:"), node


@pytest.mark.parametrize("locale", ["fr-CA", "en-CA"])
def test_new_profile_asks_for_every_required_path(client, locale):
    # §20.6 + task (2): every intake.REQUIRED path is missing, so GET /intake asks for it (fixed template question).
    pid, _ = _create(client, locale)
    profile = client.get(f"/profiles/{pid}").json()
    for factor in FACTORS:
        assert intake.missing(profile, factor) == list(intake.REQUIRED[factor]), factor
    r = client.get(f"/profiles/{pid}/intake")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == set(FACTORS)
    for factor in FACTORS:
        assert body[factor]["missing"] == list(intake.REQUIRED[factor]), factor
        nq = body[factor]["next_question"]
        assert nq == intake.next_question(profile, factor, locale)       # default locale = the profile's locale
        assert nq["path"] == intake.REQUIRED[factor][0] and nq["locale"] == locale
    other = "en-CA" if locale == "fr-CA" else "fr-CA"
    r = client.get(f"/profiles/{pid}/intake", params={"locale": other})
    assert r.json()["objectif"]["next_question"]["locale"] == other


def test_new_profile_has_no_computed_limits_and_no_default_answers(client, store_dir):
    # task (2) + P3.SKIP: limits are not computed until answered: absent, null or empty, never a number.
    pid, _ = _create(client, "en-CA")
    profile = json.loads((_user_dir(store_dir, pid) / "profile.json").read_text(encoding="utf-8"))
    limits = {k: v for k, v in (profile.get("limits") or {}).items() if k not in ("by", "_note")}
    assert not _has_number(limits), limits
    for name in TABLE_LIMITS:
        assert limits.get(name) in (None, [], {}), name
    # No REQUIRED path holds a made-up value: each is absent or null (intake.missing already says so; here the raw node).
    for path in (p for paths in intake.REQUIRED.values() for p in paths):
        node = profile
        for part in path.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        assert node is None, (path, node)


def test_post_profiles_assigns_distinct_server_side_ids(client, store_dir):
    # task (2): the id is assigned by the server; two creations never collide and never reuse a persona id.
    a, _ = _create(client, "fr-CA")
    b, _ = _create(client, "fr-CA")
    assert a != b and USER_ID.match(a) and USER_ID.match(b)
    assert not ({a, b} & PERSONA_IDS)
    assert {a, b} <= _profile_dirs(store_dir)
    r = client.post("/profiles", json={"locale": "fr-CA", "profile_id": "persona-robert"})
    assert r.status_code == 422                                          # a client cannot choose the id


@pytest.mark.parametrize("body", [
    {},                                                                  # locale is required (SPEC-AMBIGUITY: strict)
    {"display_name": "No locale"},
    {"locale": "es-ES"},
    {"locale": "en-CA", "display_name": 123},
    {"locale": "en-CA", "limits": {"max_single_stock_weight": 0.5}},     # inv. 2
    {"locale": "en-CA", "limits_trace": {"max_single_stock_weight": []}},
    {"locale": "en-CA", "llm:behaviour_classifier": {"value": "high"}},
    {"locale": "en-CA", "risk": {"declared": "high"}},                   # unknown key: answers go through intake
    {"locale": "en-CA", "meta": {"profile_id": "x"}},
    {"locale": "en-CA", "unexpected": True},
])
def test_post_profiles_rejects_bad_bodies_and_writes_nothing(client, store_dir, body):
    before = _profile_dirs(store_dir)
    r = client.post("/profiles", json=body)
    assert r.status_code == 422, (body, r.status_code, r.text)
    assert _profile_dirs(store_dir) == before


def test_post_profiles_with_a_non_object_body_is_422(client, store_dir):
    assert client.post("/profiles", content=b"not json", headers={"content-type": "application/json"}).status_code == 422
    assert client.post("/profiles", json=["en-CA"]).status_code == 422
    assert _profile_dirs(store_dir) == set()


# ---------------------------------------------------------------- (3) PUT /profiles/{id}/portfolio

def test_put_portfolio_writes_portfolio_json_and_one_audit_event(client, store_dir):
    # §3.5 + task (3): portfolio.json carries the profile_id; a profile_edit event (§3.4) is appended.
    pid, _ = _create(client, "en-CA")
    r = _put_pf(client, pid)
    assert r.status_code == 200, r.text
    work = _user_dir(store_dir, pid)
    pf = json.loads((work / "portfolio.json").read_text(encoding="utf-8"))
    assert pf["profile_id"] == pid
    assert pf["as_of"] == PF["as_of"] and pf["value_cad"] == PF["value_cad"]
    assert pf["weights_pct"] == PF["weights_pct"]
    assert isinstance(pf["portfolio_id"], str) and pf["portfolio_id"]    # §3.5 / pipeline.review needs it
    loaded = data.load_portfolio(work / "portfolio.json", data.load_assets())   # the engine's own validation accepts it
    assert loaded["weights"] == {t: w / 100 for t, w in PF["weights_pct"].items()}
    events = _events(store_dir, pid)
    assert len(events) == 1
    e = events[0]
    assert e["type"] == "profile_edit" and e["profile_id"] == pid
    assert EVENT_ID.match(e["event_id"]), e["event_id"]
    assert ISO_TS.match(e["ts"]) and isinstance(e["source"], str) and e["source"]
    assert isinstance(e["text"], str) and e["text"].strip()
    p = e["payload"]
    assert set(p) >= {"path", "old", "new"}                              # §3.4 profile_edit payload
    assert isinstance(p["path"], str) and "portfolio" in p["path"]
    assert p["old"] is None                                              # nothing was held before
    # SPEC-AMBIGUITY: the audit trail must say what was set; `new` carries the submitted holdings.
    assert p["new"]["weights_pct"] == PF["weights_pct"] and p["new"]["value_cad"] == PF["value_cad"]
    assert p["new"]["as_of"] == PF["as_of"]
    assert not (FIXTURES / pid).exists()


def test_put_portfolio_replaces_holdings_and_records_the_previous_ones(client, store_dir):
    # §3.4 profile_edit {path, old, new}: a second PUT replaces portfolio.json and audits old -> new.
    pid, _ = _create(client, "en-CA")
    assert _put_pf(client, pid, PF).status_code == 200
    assert _put_pf(client, pid, PF2).status_code == 200
    pf = json.loads((_user_dir(store_dir, pid) / "portfolio.json").read_text(encoding="utf-8"))
    assert pf["weights_pct"] == PF2["weights_pct"] and pf["as_of"] == PF2["as_of"]
    assert pf["value_cad"] == PF2["value_cad"] and pf["profile_id"] == pid
    events = _events(store_dir, pid)
    assert [e["type"] for e in events] == ["profile_edit", "profile_edit"]
    assert len({e["event_id"] for e in events}) == 2
    assert events[1]["payload"]["old"]["weights_pct"] == PF["weights_pct"]
    assert events[1]["payload"]["new"]["weights_pct"] == PF2["weights_pct"]


def test_put_portfolio_accepts_a_sum_within_the_tolerance(client, store_dir):
    # §3.5: weights sum to 100 +- 0.01. 100.005 is inside.
    pid, _ = _create(client, "en-CA")
    pf = {**PF, "weights_pct": {"XIU.TO": 33.335, "XBB.TO": 33.335, "SPY": 33.335}}
    assert _put_pf(client, pid, pf).status_code == 200


@pytest.mark.parametrize("pf", [
    {**PF, "weights_pct": {"XIU.TO": 50, "FAKE.TO": 50}},                # ticker not in the universe
    {**PF, "weights_pct": {"XIU.TO": 50, "XBB.TO": 49.5}},               # sums to 99.5
    {**PF, "weights_pct": {"XIU.TO": 50, "XBB.TO": 50.02}},              # 100.02: outside +- 0.01
    {**PF, "weights_pct": {"XIU.TO": 50, "XBB.TO": 49.98}},              # 99.98: outside +- 0.01
    {**PF, "weights_pct": {}},
    {**PF, "weights_pct": {"XIU.TO": 120, "XBB.TO": -20}},               # SPEC-AMBIGUITY: no negative weight (no shorts)
    {**PF, "weights_pct": {"XIU.TO": "50", "XBB.TO": "50"}},
    {**PF, "value_cad": 0},
    {**PF, "value_cad": -5000},
    {**PF, "value_cad": "120000"},
    {**PF, "as_of": "12 November 2026"},
    {**PF, "as_of": "2026-13-45"},
    {k: v for k, v in PF.items() if k != "as_of"},
    {k: v for k, v in PF.items() if k != "value_cad"},
    {k: v for k, v in PF.items() if k != "weights_pct"},
    {**PF, "limits": {"max_single_stock_weight": 0.5}},                  # inv. 2
    {**PF, "limits_trace": {}},
    {**PF, "llm:values_parser": {}},
    {**PF, "profile_id": "persona-robert"},                              # the id comes from the URL
    {**PF, "unexpected": 1},
])
def test_put_portfolio_rejects_invalid_bodies_and_changes_nothing(client, store_dir, pf):
    pid, _ = _create(client, "en-CA")
    r = _put_pf(client, pid, pf)
    assert r.status_code == 422, (pf, r.status_code, r.text)
    assert not (_user_dir(store_dir, pid) / "portfolio.json").exists()
    assert _events(store_dir, pid) == []


def test_a_rejected_put_keeps_the_previous_portfolio(client, store_dir):
    pid, _ = _create(client, "en-CA")
    assert _put_pf(client, pid, PF).status_code == 200
    work = _user_dir(store_dir, pid)
    before = ((work / "portfolio.json").read_bytes(), (work / "events.jsonl").read_bytes())
    assert _put_pf(client, pid, {**PF2, "weights_pct": {"XIU.TO": 60, "FAKE.TO": 40}}).status_code == 422
    assert ((work / "portfolio.json").read_bytes(), (work / "events.jsonl").read_bytes()) == before


def test_unknown_profile_is_404_and_creates_nothing_in_the_store(client, store_dir):
    # task (1): an id that is neither a seed nor a store directory is a 404 on every route, with no side effect.
    event = {"type": "question", "text": "What is duration?", "ts": "2026-11-15T09:30:00-05:00"}
    for method, path, kw in (("get", "/profiles/nobody-here", {}), ("get", "/profiles/nobody-here/intake", {}),
                             ("put", "/profiles/nobody-here/portfolio", {"json": PF}),
                             ("post", "/profiles/nobody-here/review", {}),
                             ("post", "/profiles/nobody-here/events", {"json": event})):
        r = getattr(client, method)(path, **kw)
        assert r.status_code == 404, (method, path, r.status_code)
    assert "nobody-here" not in _profile_dirs(store_dir)


def test_put_portfolio_on_a_seed_persona_writes_the_store_copy_only(client, store_dir):
    # task (5): fixtures/ is read-only seed data; the persona's working copy takes the edit.
    pid = "persona-robert"
    assert client.get(f"/profiles/{pid}").status_code == 200
    work = _user_dir(store_dir, pid)
    before = store.read_events(work / "events.jsonl")
    pf = {"as_of": "2026-11-12", "value_cad": 400000, "weights_pct": {"XIU.TO": 50, "XBB.TO": 50}}
    r = _put_pf(client, pid, pf)
    assert r.status_code == 200, r.text
    stored = json.loads((work / "portfolio.json").read_text(encoding="utf-8"))
    assert stored["profile_id"] == pid and stored["weights_pct"] == pf["weights_pct"]
    after = store.read_events(work / "events.jsonl")
    assert after[:len(before)] == before and len(after) == len(before) + 1
    assert after[-1]["type"] == "profile_edit" and after[-1]["profile_id"] == pid
    assert _hashes() == _FIXTURE_HASHES


# ---------------------------------------------------------------- (1) any directory under <store>/profiles/ is a profile

def test_profiles_list_has_seeds_and_store_only_users_once_each(client, store_dir):
    # task (1): GET /profiles lists the seeds (fixtures) and the users that exist only in the store.
    seeds = {json.loads((d / "profile.json").read_text(encoding="utf-8"))["meta"]["profile_id"]
             for d in FIXTURES.iterdir()
             if d.is_dir() and all((d / f).exists() for f in ("profile.json", "events.jsonl", "portfolio.json"))}
    assert PERSONA_IDS <= seeds
    listed = {p["profile_id"] for p in client.get("/profiles").json()}
    assert listed == seeds                                               # an empty store: seeds only (unchanged)
    uid, _ = _create(client, "en-CA", display_name="Sam Roy")
    assert _put_pf(client, uid).status_code == 200
    _make_store_user(store_dir, "user-handmade")
    assert client.get("/profiles/persona-robert").status_code == 200     # robert is now also a store directory
    r = client.get("/profiles")
    assert r.status_code == 200
    body = r.json()
    ids = [p["profile_id"] for p in body]
    assert len(set(ids)) == len(ids), ids                                # a seed copied into the store is not listed twice
    assert set(ids) == seeds | {uid, "user-handmade"}
    for item in body:
        assert set(item) >= {"profile_id", "name"}
        assert isinstance(item["name"], str) and item["name"].strip()
    names = {p["profile_id"]: p["name"] for p in body}
    assert names[uid] == "Sam Roy"                                       # display_name


def test_a_user_without_display_name_still_has_a_listed_name(client):
    uid, _ = _create(client, "fr-CA")
    assert _put_pf(client, uid).status_code == 200
    names = {p["profile_id"]: p["name"] for p in client.get("/profiles").json()}
    assert isinstance(names[uid], str) and names[uid].strip()


def test_listing_skips_directories_that_are_not_profiles(client, store_dir):
    # task (1): a profile = profile.json + events.jsonl + portfolio.json, readable. Anything else is skipped, not a 500.
    root = store_dir / "profiles"
    (root / "empty-dir").mkdir(parents=True)
    (root / "only-notes").mkdir()
    (root / "only-notes" / "notes.txt").write_text("not a profile", encoding="utf-8")
    broken = _make_store_user(store_dir, "user-broken")
    (broken / "profile.json").write_text("{not json", encoding="utf-8")
    no_pf = _make_store_user(store_dir, "user-nopf")
    (no_pf / "portfolio.json").unlink()
    ok = _make_store_user(store_dir, "user-fine")
    assert ok.is_dir()
    r = client.get("/profiles")
    assert r.status_code == 200, r.text
    ids = {p["profile_id"] for p in r.json()}
    assert "user-fine" in ids
    assert not ids & {"empty-dir", "only-notes", "user-broken", "user-nopf"}


def test_store_only_directory_user_is_served_and_reviewed(client, store_dir):
    # task (1) + (4): nothing about the user comes from fixtures/: the store directory alone is enough.
    _make_store_user(store_dir, "user-handmade")
    r = client.get("/profiles/user-handmade")
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["profile_id"] == "user-handmade"
    r = client.get("/profiles/user-handmade/intake")
    assert r.status_code == 200 and set(r.json()) == set(FACTORS)
    rep = client.post("/profiles/user-handmade/review")
    assert rep.status_code == 200, rep.text
    body = rep.json()
    assert body["profile_id"] == "user-handmade" and body["verdict"] in VERDICTS
    assert not (FIXTURES / "user-handmade").exists()


def test_a_seed_persona_is_copied_to_the_store_on_first_access_and_fixtures_stay_untouched(client, store_dir):
    # task (1) "unchanged behaviour" + (5).
    assert not (store_dir / "profiles" / "persona-amira").exists()
    r = client.get("/profiles/persona-amira")
    assert r.status_code == 200
    work = store_dir / "profiles" / "persona-amira"
    for name in ("profile.json", "events.jsonl", "portfolio.json"):
        assert (work / name).read_bytes() == (FIXTURES / "amira" / name).read_bytes(), name
    assert _hashes() == _FIXTURE_HASHES


# ---------------------------------------------------------------- (4) review end to end, mock mode

def test_review_before_any_portfolio_is_a_client_error(store_dir):
    # No holdings yet: there is nothing to review. A 4xx with a message, never a server error.
    c = TestClient(api.app, raise_server_exceptions=False)
    pid, _ = _create(c, "en-CA")
    r = c.post(f"/profiles/{pid}/review")
    assert 400 <= r.status_code < 500, (r.status_code, r.text)
    assert not (store_dir / "reports").exists() or list((store_dir / "reports").glob("*.json")) == []


@pytest.mark.parametrize("locale", ["en-CA", "fr-CA"])
def test_review_works_end_to_end_for_a_new_user(client, store_dir, locale):
    # task (4): POST /profiles -> PUT portfolio -> POST review; sections with no recorded N5 answer end in the template.
    pid, _ = _create(client, locale, display_name="Alex Roy")
    assert _put_pf(client, pid).status_code == 200
    r = client.post(f"/profiles/{pid}/review")
    assert r.status_code == 200, r.text
    rep = r.json()
    assert ULID.match(rep["run_id"])
    assert rep["flow"] == "C" and rep["profile_id"] == pid and rep["intent"] is None
    assert rep["verdict"] in VERDICTS
    assert [s["factor"] for s in rep["sections"]] == list(FACTORS)
    assert rep["disclaimer"] == config.DISCLAIMER[locale]                # SPEC §9.4: the fixed text, in the user's locale
    assert rep["explanation"]["disclaimer"] == config.DISCLAIMER[locale]
    for s in rep["sections"]:
        if s["status"] != "pass":
            assert s["narrative"]["mode"] == "template", s["factor"]    # no recorded answer for this user
    assert rep["explanation"]["mode"] == "template"
    assert client.get(f"/reports/{rep['run_id']}").json() == rep
    audit = client.get(f"/audit/{rep['run_id']}").json()
    assert audit["profile_id"] == pid and audit["flow"] == "C"
    assert audit["as_of"] == PF["as_of"]                                 # D1: the reference date is portfolio.as_of
    assert any(e["type"] == "profile_edit" for e in _events(store_dir, pid))
    assert audit["events_considered"] == [e["event_id"] for e in _events(store_dir, pid)]


def test_review_of_a_new_user_reports_unanswered_limits_as_unknown_never_violation(client):
    # P3.SKIP + D3 + inv. 3: no limit exists yet, so no hard check can be a violation and the table-limit checks are
    # `unknown`; unknowns do not change the verdict.
    pid, _ = _create(client, "en-CA")
    heavy = {"as_of": "2026-11-12", "value_cad": 50000,
             "weights_pct": {"NVDA": 40, "QQQ": 30, "BTC-USD": 20, "SHOP.TO": 10}}   # would breach any table limit
    assert _put_pf(client, pid, heavy).status_code == 200
    rep = client.post(f"/profiles/{pid}/review").json()
    checks = rep["checks_current"]
    assert [c for c in checks if c["status"] == "violation"] == []
    for cid in sorted(set(TABLE_LIMITS.values())):
        rows = [c for c in checks if c["id"] == cid]
        assert rows, cid
        assert {c["status"] for c in rows} == {"unknown"}, (cid, rows)
    assert rep["verdict"] in ("coherent", "coherent_with_warnings")      # D3: no violation -> never `incoherent`
    limits = client.get(f"/profiles/{pid}").json().get("limits") or {}
    for name in TABLE_LIMITS:
        assert limits.get(name) in (None, [], {}), (name, limits.get(name))


def test_intake_answers_work_for_a_new_user_and_are_audited(client, store_dir):
    # §20.6: an answer is one profile_edit event from source `intake`, the path leaves `missing`; limits stay a
    # function of declared values (a number the user did not give is never invented).
    pid, _ = _create(client, "en-CA")
    assert _put_pf(client, pid).status_code == 200
    before = len(_events(store_dir, pid))
    answers = {"risk.declared": "moderate", "objectives.horizon_years": 10, "preferences.prefers_dividends": True}
    for path, value in answers.items():
        r = client.post(f"/profiles/{pid}/intake/answer", json={"path": path, "value": value})
        assert r.status_code == 200, (path, r.text)
    profile = client.get(f"/profiles/{pid}").json()
    assert profile["risk"]["declared"] == "moderate"
    assert profile["objectives"]["horizon_years"] == 10
    assert profile["preferences"]["prefers_dividends"] is True
    assert "risk.declared" not in intake.missing(profile, "risque")
    assert "objectives.horizon_years" not in intake.missing(profile, "horizon")
    assert "preferences.prefers_dividends" not in intake.missing(profile, "preferences")
    assert intake.missing(profile, "risque") == ["risk.max_acceptable_drawdown_pct", "risk.reaction_to_20pct_drop"]
    events = _events(store_dir, pid)[before:]
    assert [e["type"] for e in events] == ["profile_edit"] * 3
    assert [e["source"] for e in events] == ["intake"] * 3
    assert [e["payload"]["path"] for e in events] == list(answers)
    assert all(e["payload"]["old"] is None for e in events)
    assert _SCHEMA.is_valid(profile)
    assert client.post("/profiles/" + pid + "/intake/answer",
                       json={"path": "limits.max_single_stock_weight", "value": 0.5}).status_code == 422   # inv. 2


def test_zz_fixtures_are_unchanged_after_the_whole_module():
    # task (5): seed personas are never written under fixtures/ (no new file, no changed byte, no new directory).
    assert _hashes() == _FIXTURE_HASHES
