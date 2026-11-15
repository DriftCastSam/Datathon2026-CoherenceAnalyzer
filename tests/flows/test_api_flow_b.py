"""API Flow B routes: order_intent event -> B1 draft intent; POST /intents/{intent_id}/confirm -> B3-B9 report (P2.APIFB).

Sources: SPEC §12 (API table), §4 Flow B (B1 N4 draft, B2 user confirms or edits, B3-B9), §3.4 (event log:
`order_intent`, `intent_confirmed` with `parent_event_id`), §3.6 (intent), §1 inv. 2 (no limits / llm: field from a
request), inv. 6 (numbers: the report is the pipeline's), inv. 7 + D10 (describe, never prescribe: nothing is executed,
no suggested ticker / size / funding), §10 (store layout, audit), §11 (gate), §13 (report schema), docs/spec-decisions.md
D7 (Flow B routes are P2.APIFB), D10.

Offline: COHERENCE_ANALYZER_LLM=mock, `COHERENCE_ANALYZER_STORE_DIR` = a tmp dir. The API works on the working copy
`store.root()/profiles/<profile_id>/`; `fixtures/` is never written (checked by the last test).

Scenario (module scoped, run once): Robert, `POST /profiles/persona-robert/events` with the recorded text of
ev_robert_0007 ("I want to put 10% in NVDA, everyone's talking about it", N4 mock-cache hit). The working copy already
holds ev_robert_0007, so the new event is ev_robert_0008. The recorded N4 answer carries `funding: pro_rata_all` and
lists `funding` in `ambiguous_fields`: the user did not state a funding, so under D10 the draft shows funding null
(nothing pre-filled). The user then confirms NVDA 10 % funded from ZST.TO (the same trade as `pipeline.check` on
fixtures/robert, in_0001): same verdict, hard checks, metrics and headroom as the reference run.

Notes on shapes the spec leaves open (stricter reading chosen where the task text speaks, tolerant elsewhere):
the response of the order_intent POST holds the event (under `event`, or as the top-level object) and the draft
(under `intent`, or `draft`).
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

from coherence_analyzer import api, config, explain, pipeline, store  # noqa: E402
from coherence_analyzer.llm import bedrock  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
ROBERT_FIX = FIXTURES / "robert"
ROBERT_ID = "persona-robert"
TAGS = ROOT / "data" / "meta" / "activity_tags.csv"
ORDER_TEXT = "I want to put 10% in NVDA, everyone's talking about it"     # recorded text of ev_robert_0007
ORDER_TS = "2026-11-15T10:00:00-05:00"
NEW_EVENT_ID = "ev_robert_0008"
CONFIRM_EVENT_ID = "ev_robert_0009"
SPECIFIC_ZST = {"kind": "specific", "ticker": "ZST.TO"}
GOOD_BODY = {"ticker": "NVDA", "target_weight_pct": 10, "funding": SPECIFIC_ZST}
ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
INTENT_ID = re.compile(r"^in_\d{4,}$")
VERDICTS = ("coherent", "coherent_with_warnings", "incoherent")
MODES = ("llm", "llm_retry", "template")
ALLOWED_NODES = {"intent_parser", "behaviour_classifier", "explainer", "values_parser", "profile_consistency"}
FR_DISCLAIMER = ("Simulation éducative avec des profils fictifs. Ceci n'est pas un conseil financier personnalisé. "
                 "Aucune transaction n'est exécutée.")                                   # SPEC §9.4
FORBIDDEN_IN_ROUTES = ("buy", "sell", "order", "execute")                                # SPEC §1 inv. 7
FORBIDDEN_KEYS = {"alternatives", "options", "recommendation", "recommendations", "next_step", "executed",
                  "execution", "order_id", "fills", "suggested", "suggestion"}
HARD_VIOLATIONS = {"H3", "H4", "H7", "H8", "H9", "H10"}                                  # tests/flows/test_flow_b.py


def _hashes() -> dict[str, str]:
    return {str(p.relative_to(FIXTURES)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(FIXTURES.rglob("*")) if p.is_file()}


_FIXTURE_HASHES = _hashes()                                         # taken before any test runs
_TAGS_BYTES = TAGS.read_bytes() if TAGS.exists() else None
_REAL_CALL_TOOL = bedrock.call_tool


# ---------------------------------------------------------------- helpers

def _workdir(store_dir: Path) -> Path:
    return Path(store_dir) / "profiles" / ROBERT_ID


def _lines(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _parts(body: dict) -> tuple[dict, dict]:
    """(event, draft intent) of the order_intent response."""
    # SPEC-AMBIGUITY: SPEC §12 says "returns the draft intent" without a shape; accept {event, intent|draft} and nothing else.
    event = body["event"] if isinstance(body.get("event"), dict) else body
    draft = body.get("intent") if isinstance(body.get("intent"), dict) else body.get("draft")
    assert isinstance(event, dict) and event.get("event_id"), body
    assert isinstance(draft, dict), f"no draft intent in {sorted(body)}"
    return event, draft


def _strings(node) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in _strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return []


def _keys(node) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {k for v in node.values() for k in _keys(v)}
    if isinstance(node, list):
        return {k for v in node for k in _keys(v)}
    return set()


def _records_with_intent_id(workdir: Path, intent_id: str) -> list[tuple[Path, dict]]:
    """Every dict carrying `intent_id` in a JSON file of the working copy."""
    out: list[tuple[Path, dict]] = []

    def walk(path: Path, node):
        if isinstance(node, dict):
            if node.get("intent_id") == intent_id:
                out.append((path, node))
            for v in node.values():
                walk(path, v)
        elif isinstance(node, list):
            for v in node:
                walk(path, v)

    for p in sorted(workdir.rglob("*.json")):
        try:
            walk(p, json.loads(p.read_text(encoding="utf-8")))
        except ValueError:
            continue
    return out


def _order_event(**kw) -> dict:
    return {"type": "order_intent", "text": ORDER_TEXT, "ts": ORDER_TS, "source": "ui_chat", **kw}


def _hard(checks: list[dict]) -> list[tuple]:
    return sorted((c["id"], c.get("subject") or "", c["status"]) for c in checks if c["kind"] == "hard")


class _Spy:
    """Records the node of every `bedrock.call_tool`; optional `patch(node, output) -> output` rewrites an answer."""

    def __init__(self, patch=None):
        self.nodes: list[str] = []
        self.patch = patch
        self.real = _REAL_CALL_TOOL

    def __call__(self, node, schema, system, user_payload, prompt_version):
        result = self.real(node, schema, system, user_payload, prompt_version)
        self.nodes.append(node)
        if self.patch is not None:
            result = dict(result, output=self.patch(node, copy.deepcopy(result["output"])))
        return result


# ---------------------------------------------------------------- fixtures

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
def client_nr() -> TestClient:
    """An unhandled server error becomes a 500 response (so a test can assert 'a 4xx, never a 500')."""
    return TestClient(api.app, raise_server_exceptions=False)


@pytest.fixture(scope="module")
def report_schema() -> dict:
    schema = json.loads((config.SCHEMAS_DIR / "report.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


@pytest.fixture(scope="module")
def scen(tmp_path_factory) -> dict:
    """order_intent -> draft -> confirm NVDA 10 % from ZST.TO, once; plus the reference `pipeline.check` (in_0001)."""
    base = tmp_path_factory.mktemp("apifb")
    api_store, ref_store = base / "api_store", base / "ref_store"
    api_store.mkdir()
    ref_store.mkdir()
    mp = pytest.MonkeyPatch()
    mp.setenv("COHERENCE_ANALYZER_LLM", "mock")
    mp.setenv("COHERENCE_ANALYZER_STORE_DIR", str(api_store))
    mp.chdir(base)
    out: dict = {"store": api_store, "workdir": _workdir(api_store)}
    try:
        client = TestClient(api.app)
        assert client.get(f"/profiles/{ROBERT_ID}").status_code == 200
        events_path = out["workdir"] / "events.jsonl"
        out["events_before"] = events_path.read_bytes()

        spy = _Spy()
        mp.setattr(bedrock, "call_tool", spy)
        r = client.post(f"/profiles/{ROBERT_ID}/events", json=_order_event())
        out["draft_resp"] = r
        out["draft_nodes"] = list(spy.nodes)
        out["events_after_draft"] = events_path.read_bytes()
        out["reports_after_draft"] = sorted((api_store / "reports").glob("*.json")) if (api_store / "reports").exists() else []
        out["audits_after_draft"] = sorted((api_store / "audit").glob("*.json")) if (api_store / "audit").exists() else []
        out["store_files_after_draft"] = {str(p): p.read_text(encoding="utf-8")
                                          for p in sorted(api_store.rglob("*")) if p.is_file()}
        if r.status_code not in (200, 201):
            return out
        _, draft = _parts(r.json())
        out["intent_id"] = draft["intent_id"]

        spy2 = _Spy()
        mp.setattr(bedrock, "call_tool", spy2)
        out["confirm_resp"] = client.post(f"/intents/{draft['intent_id']}/confirm", json=GOOD_BODY)
        out["confirm_nodes"] = list(spy2.nodes)
        out["events_after_confirm"] = events_path.read_bytes()
        mp.undo()

        mp = pytest.MonkeyPatch()
        mp.setenv("COHERENCE_ANALYZER_LLM", "mock")
        mp.setenv("COHERENCE_ANALYZER_STORE_DIR", str(ref_store))
        mp.chdir(base)
        out["ref"] = pipeline.check(ROBERT_FIX, "in_0001", auto_confirm=True)
    finally:
        mp.undo()
    return out


def _use_store(monkeypatch, scen: dict) -> TestClient:
    monkeypatch.setenv("COHERENCE_ANALYZER_STORE_DIR", str(scen["store"]))
    return TestClient(api.app)


# ---------------------------------------------------------------- routes (§12, inv. 7)

def test_confirm_route_exists_and_no_other_route_names_a_trade_action(client):
    # §12: POST /intents/{intent_id}/confirm is the only intent route; §1 inv. 7: no path contains buy/sell/order/execute.
    spec_paths = client.get("/openapi.json").json()["paths"]
    norm = {re.sub(r"\{[^}]+\}", "{x}", p): set(ms) for p, ms in spec_paths.items()}
    assert norm.get("/intents/{x}/confirm") == {"post"}
    assert [p for p in norm if "intent" in p.lower()] == ["/intents/{x}/confirm"]
    for p in list(norm) + [getattr(r, "path", "") for r in api.app.routes]:
        for word in FORBIDDEN_IN_ROUTES:
            assert word not in p.lower(), (p, word)
    assert norm["/reports/{x}"] == {"get"} and norm["/audit/{x}"] == {"get"}


# ---------------------------------------------------------------- B1 + B2: order_intent -> draft (D10: nothing pre-filled)

def test_order_intent_is_no_longer_refused_and_returns_event_plus_draft(scen):
    # §12 + task: order_intent is accepted (the P1.4 501 pin is gone) and returns the event and the draft intent.
    r = scen["draft_resp"]
    assert r.status_code in (200, 201), r.text
    event, draft = _parts(r.json())
    assert event["event_id"] == NEW_EVENT_ID                      # Robert's last id is 0007: next = highest + 1 (§3.4)
    assert event["profile_id"] == ROBERT_ID and event["type"] == "order_intent"
    assert event["text"] == ORDER_TEXT and event["ts"] == ORDER_TS
    assert INTENT_ID.match(draft["intent_id"]) and draft["intent_id"] != "in_0001"   # in_0001 is the fixture's intent
    assert draft["status"] == "draft"
    assert draft["event_id"] == NEW_EVENT_ID
    assert not draft.get("confirmed_at")                          # a draft is not confirmed (§3.6)


def test_draft_carries_only_what_the_user_stated_nothing_prefilled(scen):
    # D10.1 + §4 B2 + task: "10% in NVDA" is stated; the funding is not (the recorded N4 answer says pro_rata_all and
    # lists `funding` as ambiguous): the draft shows ticker NVDA, weight 10, funding null. No default, no suggestion.
    # SPEC-AMBIGUITY: SPEC §3.6 says the default pro_rata_all is "shown as the default before they confirm"; D10 wins
    # (stricter reading): funding is null in the draft.
    body = scen["draft_resp"].json()
    _, draft = _parts(body)
    assert draft["ticker"] == "NVDA"
    assert draft["target_weight_pct"] == 10
    assert draft["funding"] is None
    assert draft.get("action") in ("buy", None)                  # "put in" = buy; unstated is also accepted
    assert "pro_rata_all" not in json.dumps(body)
    if "ambiguous_fields" in draft:
        assert "funding" in draft["ambiguous_fields"]
    assert not any(any(w in k.lower() for w in ("suggest", "default", "recommend")) for k in _keys(body))
    assert not (FORBIDDEN_KEYS & _keys(body))


def test_draft_strings_are_descriptive_only(scen):
    # D10.4: no advisory wording in anything the route says (case- and accent-insensitive blocklist).
    assert scen["draft_resp"].status_code in (200, 201), scen["draft_resp"].text
    for text in _strings(scen["draft_resp"].json()):
        assert explain.advisory_hits(text) == [], text


def test_order_intent_appends_one_event_and_runs_b1_only(scen):
    # §3.4 append-only + §4 B1/B2: one line appended (old bytes untouched); N4 is the only LLM call; no engine run,
    # no report, no audit (the user has not confirmed; nothing is analysed or executed before B2).
    before, after = scen["events_before"], scen["events_after_draft"]
    assert after.startswith(before) and len(after) > len(before)
    appended = [json.loads(x) for x in after[len(before):].decode("utf-8").splitlines() if x.strip()]
    assert [e["event_id"] for e in appended] == [NEW_EVENT_ID]
    assert appended[0]["type"] == "order_intent" and appended[0]["text"] == ORDER_TEXT
    assert scen["draft_nodes"] == ["intent_parser"]
    assert scen["reports_after_draft"] == []
    assert scen["audits_after_draft"] == []


def test_draft_is_stored_in_the_working_copy_never_under_fixtures(scen):
    # Task: the draft intent lives in the working copy (status 'draft', nothing pre-filled); fixtures/ is untouched.
    found = _records_with_intent_id(scen["workdir"], scen["intent_id"])
    assert found, "the draft intent must be stored under store.root()/profiles/<profile_id>/"
    assert any(rec.get("status") in ("draft", "confirmed") for _, rec in found)
    for path, _ in found:
        assert scen["workdir"] in path.parents
    for path, text in scen["store_files_after_draft"].items():
        assert "pro_rata_all" not in text, path                   # D10: the recorded default is never persisted as a draft
    assert not _records_with_intent_id(FIXTURES, scen["intent_id"])
    assert _hashes() == _FIXTURE_HASHES


def test_two_order_intents_get_two_distinct_intent_ids(client):
    # §3.6: an intent id names one intent; a second order_intent event does not reuse it.
    a = client.post(f"/profiles/{ROBERT_ID}/events", json=_order_event())
    b = client.post(f"/profiles/{ROBERT_ID}/events", json=_order_event(ts="2026-11-15T11:00:00-05:00"))
    assert a.status_code in (200, 201) and b.status_code in (200, 201), (a.text, b.text)
    (ea, da), (eb, db) = _parts(a.json()), _parts(b.json())
    assert (ea["event_id"], eb["event_id"]) == (NEW_EVENT_ID, CONFIRM_EVENT_ID)
    assert da["intent_id"] != db["intent_id"]
    assert da["event_id"] == ea["event_id"] and db["event_id"] == eb["event_id"]


def _assert_unparsed_draft_or_4xx(r) -> None:
    """An N4 miss / gate failure: a draft with every unparsed field null, or a 4xx with a reason. Never a guess."""
    # SPEC-AMBIGUITY: the task allows both outcomes; either is accepted, a 5xx or a guessed field never is.
    assert r.status_code < 500, r.text
    if r.status_code in (200, 201):
        _, draft = _parts(r.json())
        assert draft["status"] == "draft"
        assert draft["ticker"] is None and draft["target_weight_pct"] is None and draft["funding"] is None
        assert "pro_rata_all" not in json.dumps(r.json())
    else:
        assert r.json().get("detail")


def test_n4_cache_miss_gives_an_unparsed_draft_or_a_4xx_never_a_guess(client_nr, store_dir, monkeypatch):
    # §6.2 N4 / D10 + task: a text the mock cache has never seen is an LLMCacheMiss: nothing is guessed from the text.
    spy = _Spy()
    monkeypatch.setattr(bedrock, "call_tool", spy)
    r = client_nr.post(f"/profiles/{ROBERT_ID}/events", json=_order_event(text="Put 3% into XOM and 4% in SHOP.TO"))
    assert spy.nodes == []                                        # the mock raised before returning: no answer was used
    _assert_unparsed_draft_or_4xx(r)
    assert "XOM" not in json.dumps(r.json().get("intent") or r.json().get("draft") or {})
    assert not (store_dir / "reports").exists() or not list((store_dir / "reports").glob("*.json"))


@pytest.mark.parametrize("patch", [
    lambda out: {**out, "ticker": "ZZZZ"},
    lambda out: {**out, "target_weight_pct": 150},
    lambda out: {**out, "funding": {"kind": "margin"}},
], ids=["ticker outside the universe", "weight out of range", "unknown funding kind"])
def test_n4_draft_failing_the_gate_is_dropped_not_partly_used(client_nr, monkeypatch, patch):
    # §11 / inv. 4 + task: N4 goes through the gate; a draft that fails it (rewritten after the real mock-cache read)
    # is rejected as a whole: no ticker, size or funding survives (the pipeline drops the whole draft).
    spy = _Spy(patch=lambda node, out: patch(out) if node == "intent_parser" else out)
    monkeypatch.setattr(bedrock, "call_tool", spy)
    r = client_nr.post(f"/profiles/{ROBERT_ID}/events", json=_order_event())
    assert spy.nodes == ["intent_parser"]
    _assert_unparsed_draft_or_4xx(r)
    if r.status_code in (200, 201):
        draft = _parts(r.json())[1]
        assert "ZZZZ" not in json.dumps(draft) and "margin" not in json.dumps(draft)


# ---------------------------------------------------------------- confirm: B3-B9 -> Flow B report

def test_confirm_returns_the_flow_b_report_schema_valid(scen, report_schema):
    # §12 + §13 + task: the confirm response is the Flow B report (same shape as pipeline.check with auto_confirm).
    r = scen["confirm_resp"]
    assert r.status_code == 200, r.text
    rep, ref = r.json(), scen["ref"]
    errors = sorted(Draft202012Validator(report_schema).iter_errors(rep), key=lambda e: list(e.absolute_path))
    assert errors == [], [e.message for e in errors]
    assert set(rep) == set(ref)
    assert ULID.match(rep["run_id"]) and rep["run_id"] != ref["run_id"]
    assert rep["flow"] == "B" and rep["profile_id"] == ROBERT_ID and rep["portfolio_id"] == ref["portfolio_id"]
    assert rep["checks_proposed"] and rep["metrics_proposed"] and rep["diff"]           # unlike Flow C
    assert rep["data_window"] == ref["data_window"] == ["2022-01-31", "2026-10-31"]
    assert [s["factor"] for s in rep["sections"]] == [s["factor"] for s in ref["sections"]]
    assert rep["explanation"]["mode"] in MODES
    assert json.loads(json.dumps(rep)) == rep
    assert not (FORBIDDEN_KEYS & _keys(rep))                                            # D10.2: no alternatives / options


def test_confirm_report_carries_the_confirmed_user_intent(scen):
    # §3.6 + §4 B2: the report's intent is the confirmed one: user values (NVDA 10 %, specific ZST.TO), linked to the new
    # order_intent event and to the draft's intent_id; the draft's null funding did not leak in as a default.
    it = scen["confirm_resp"].json()["intent"]
    assert it["intent_id"] == scen["intent_id"] and it["event_id"] == NEW_EVENT_ID
    assert it["action"] == "buy" and it["ticker"] == "NVDA" and it["target_weight_pct"] == 10
    assert it["funding"] == SPECIFIC_ZST
    assert it["status"] == "confirmed"
    assert isinstance(it["confirmed_at"], str) and it["confirmed_at"]


def test_confirm_matches_pipeline_check_on_the_same_trade(scen):
    # §4 B3-B9 + task: same verdict, hard checks (id, subject, status and numbers), engine metrics (1e-9) and headroom as
    # `pipeline.check(fixtures/robert, in_0001, auto_confirm=True)`: the API adds no math of its own (golden unchanged).
    rep, ref = scen["confirm_resp"].json(), scen["ref"]
    assert rep["verdict"] == ref["verdict"] == "incoherent"
    for side in ("checks_current", "checks_proposed"):
        assert _hard(rep[side]) == _hard(ref[side]), side
        ref_by = {(c["id"], c.get("subject") or "", c["status"]): c for c in ref[side] if c["kind"] == "hard"}
        for c in rep[side]:
            if c["kind"] != "hard":
                continue
            e = ref_by[(c["id"], c.get("subject") or "", c["status"])]
            for f in ("value", "limit"):
                if isinstance(e.get(f), (int, float)):
                    assert c[f] == pytest.approx(e[f], abs=1e-9), (side, c["id"], f)
    assert {c["id"] for c in rep["checks_proposed"] if c["status"] == "violation"} == HARD_VIOLATIONS
    assert all(c["kind"] == "hard" for c in rep["checks_proposed"] if c["status"] == "violation")   # inv. 3
    for side in ("metrics_current", "metrics_proposed"):
        for k in ("vol", "mdd", "loss_prob", "width", "p5_T", "p50_T", "p95_T"):
            assert rep[side][k] == pytest.approx(ref[side][k], abs=1e-9), (side, k)
    assert rep["metrics_proposed"]["p5_T"] == pytest.approx(316_971.0, abs=1e-9)           # D2 context (test_flow_b)
    assert rep["diff"]["flipped"] == ref["diff"]["flipped"]
    assert rep["unknowns"] == [] or {u["id"] for u in rep["unknowns"]} <= {c["id"] for c in rep["checks_proposed"]}


def test_confirm_headroom_is_the_users_own_funding_only_or_null(scen):
    # §8 + D10.2 + §13: `headroom` is null | object; here the proposed side has violations, so it is the object for the
    # funding the user stated (ZST.TO, 1.5 %, binding H9: the D2 golden), never another funding.
    hr = scen["confirm_resp"].json()["headroom"]
    assert hr is None or isinstance(hr, dict)
    assert isinstance(hr, dict) and hr
    assert hr["funding"] == SPECIFIC_ZST
    assert hr["max_weight_pct"] == pytest.approx(1.5, abs=1e-9)
    assert hr["binding_check"] == "H9"
    assert not {"options", "alternatives"} & set(hr)


def test_confirm_report_disclaimer_is_the_fixed_text_and_nothing_is_advisory(scen):
    # §9.4 + inv. 7 + D10: fixed FR disclaimer (Robert is fr-CA); report prose carries no advisory wording.
    rep = scen["confirm_resp"].json()
    assert rep["disclaimer"] == config.DISCLAIMER["fr-CA"] == FR_DISCLAIMER
    assert rep["explanation"]["disclaimer"] == FR_DISCLAIMER
    prose = [rep["explanation"]["headline"]] + [s["text"] for s in rep["explanation"]["segments"]]
    for text in prose:
        assert explain.advisory_hits(text) == [], text


def test_confirm_appends_intent_confirmed_event_linked_to_the_order_intent(scen):
    # §3.4 + §3.6 + task: an `intent_confirmed` event whose payload is the confirmed intent, `parent_event_id` = the
    # order_intent event; append-only; exactly one new event (the id after the order_intent's).
    before, after = scen["events_after_draft"], scen["events_after_confirm"]
    assert after.startswith(before) and len(after) > len(before)
    appended = [json.loads(x) for x in after[len(before):].decode("utf-8").splitlines() if x.strip()]
    assert len(appended) == 1
    ev = appended[0]
    assert ev["event_id"] == CONFIRM_EVENT_ID and ev["profile_id"] == ROBERT_ID
    assert ev["type"] == "intent_confirmed" and ev["parent_event_id"] == NEW_EVENT_ID
    assert ev["payload"] == scen["confirm_resp"].json()["intent"]
    assert ev["payload"]["status"] == "confirmed" and ev["payload"]["funding"] == SPECIFIC_ZST
    assert isinstance(ev["ts"], str) and ev["ts"]
    assert ev.get("text") is None or explain.advisory_hits(ev["text"]) == []


def test_confirm_runs_b3_to_b9_without_re_parsing_and_never_executes(scen):
    # §12: confirm = B3-B9 (B1 ran at the order_intent POST): no second N4 call; N1 / N5 / N6 only (all gated nodes).
    assert "intent_parser" not in scen["confirm_nodes"]
    assert set(scen["confirm_nodes"]) <= ALLOWED_NODES


def test_report_and_audit_of_the_confirm_run_are_served_by_run_id(scen, monkeypatch):
    # §12 + §10: GET /reports/{run_id} = the confirm response; GET /audit/{run_id} is the Flow B audit record:
    # events considered end at the order_intent event (B3: "up to and including"), every LLM call is a known node.
    client = _use_store(monkeypatch, scen)
    rep = scen["confirm_resp"].json()
    got = client.get(f"/reports/{rep['run_id']}")
    assert got.status_code == 200 and got.json() == rep
    r = client.get(f"/audit/{rep['run_id']}")
    assert r.status_code == 200, r.text
    audit = r.json()
    assert audit["run_id"] == rep["run_id"] and audit["flow"] == "B" and audit["profile_id"] == ROBERT_ID
    assert audit["verdict"] == rep["verdict"] == "incoherent"
    assert audit["events_considered"][-1] == NEW_EVENT_ID
    assert CONFIRM_EVENT_ID not in audit["events_considered"]
    assert audit["as_of"] == "2026-11-12"                                               # D1
    assert {c["node"] for c in audit["llm_calls"]} <= ALLOWED_NODES
    assert audit["engine"]["seed"] == config.ENGINE_SEED == 7
    assert audit.get("intent_id") == scen["intent_id"]
    assert (scen["store"] / "reports" / f"{rep['run_id']}.json").exists()
    assert (scen["store"] / "audit" / f"{rep['run_id']}.json").exists()


def test_limits_stay_rule_computed_after_a_confirm(scen, monkeypatch):
    # §1 inv. 2: the working profile's limits are still `rule:` computed after the run; nothing from the intent text or
    # the N4 draft reached them.
    client = _use_store(monkeypatch, scen)
    limits = client.get(f"/profiles/{ROBERT_ID}").json()["limits"]
    assert limits["by"].startswith("rule:")
    fixture = json.loads((ROBERT_FIX / "profile.json").read_text(encoding="utf-8"))["limits"]
    assert limits["max_single_stock_weight"] == pytest.approx(fixture["max_single_stock_weight"], abs=1e-9)


# ---------------------------------------------------------------- confirm: errors, strict bodies, edits, repeat

def _draft(client) -> dict:
    r = client.post(f"/profiles/{ROBERT_ID}/events", json=_order_event())
    assert r.status_code in (200, 201), r.text
    return _parts(r.json())[1]


def _state(store_dir: Path) -> tuple[bytes, list[Path], bytes]:
    wd = _workdir(store_dir)
    reports = sorted((store_dir / "reports").glob("*.json")) if (store_dir / "reports").exists() else []
    return (wd / "events.jsonl").read_bytes(), reports, (wd / "profile.json").read_bytes()


def test_confirm_unknown_intent_id_is_404_and_changes_nothing(client_nr, store_dir):
    # Task: unknown intent_id -> 404 (also when the profile was never touched); nothing is appended or saved.
    r = client_nr.post("/intents/in_9999/confirm", json=GOOD_BODY)
    assert r.status_code == 404, r.text
    assert isinstance(r.json().get("detail"), str) and explain.advisory_hits(r.json()["detail"]) == []
    assert client_nr.get(f"/profiles/{ROBERT_ID}").status_code == 200       # the copy now exists
    events, reports, _ = _state(store_dir)
    assert client_nr.post("/intents/in_9999/confirm", json=GOOD_BODY).status_code == 404
    assert _state(store_dir)[0] == events and reports == []
    assert client_nr.post("/intents/..%2Fin_0002/confirm", json=GOOD_BODY).status_code in (404, 422)


@pytest.mark.parametrize("extra", [
    {"limits": {"max_crypto_weight": 0.5}},
    {"limits_trace": {"max_crypto_weight": "user"}},
    {"llm:intent_parser": {"ticker": "NVDA"}},
    {"by": "llm:intent_parser"},
    {"confidence": 0.9},
    {"status": "confirmed"},
    {"intent_id": "in_0001"},
    {"action": "sell"},
    {"suggested_funding": {"kind": "pro_rata_all"}},
], ids=lambda e: next(iter(e)))
def test_confirm_body_forbids_unknown_keys_and_limits_or_llm_fields(client_nr, store_dir, extra):
    # §1 inv. 2 + task: body = ticker, target_weight_pct, funding only; limits / limits_trace / llm: fields (and any
    # other unknown key) are rejected (422), and the request changes nothing: no event, no report, profile untouched.
    draft = _draft(client_nr)
    before = _state(store_dir)
    r = client_nr.post(f"/intents/{draft['intent_id']}/confirm", json={**GOOD_BODY, **extra})
    assert 400 <= r.status_code < 500, r.text
    if "action" not in extra:
        assert r.status_code == 422, r.text
    assert _state(store_dir) == before


@pytest.mark.parametrize("body", [
    {"target_weight_pct": 10, "funding": SPECIFIC_ZST},
    {"ticker": "NVDA", "funding": SPECIFIC_ZST},
    {"ticker": "NVDA", "target_weight_pct": 10},
    {"ticker": "NVDA", "target_weight_pct": 10, "funding": None},
    {"ticker": "ZZZZ", "target_weight_pct": 10, "funding": SPECIFIC_ZST},
    {"ticker": "NVDA", "target_weight_pct": 0, "funding": SPECIFIC_ZST},
    {"ticker": "NVDA", "target_weight_pct": -5, "funding": SPECIFIC_ZST},
    {"ticker": "NVDA", "target_weight_pct": 150, "funding": SPECIFIC_ZST},
    {"ticker": "NVDA", "target_weight_pct": "ten", "funding": SPECIFIC_ZST},
    {"ticker": "NVDA", "target_weight_pct": 10, "funding": {"kind": "margin"}},
    {"ticker": "NVDA", "target_weight_pct": 10, "funding": {"kind": "specific"}},
    {"ticker": "NVDA", "target_weight_pct": 10, "funding": {"kind": "specific", "ticker": "NOPE"}},
    {"ticker": "NVDA", "target_weight_pct": 10, "funding": {"kind": "pro_rata_class"}},
    {"ticker": "NVDA", "target_weight_pct": 10, "funding": {"kind": "cash_contribution"}},
    {"ticker": "NVDA", "target_weight_pct": 10, "funding": {"kind": "cash_contribution", "amount_cad": -100}},
    {"ticker": "NVDA", "target_weight_pct": 10, "funding": {"kind": "specific", "ticker": "ZST.TO", "limits": {}}},
], ids=lambda b: json.dumps(b, sort_keys=True)[:70])
def test_confirm_invalid_or_incomplete_intent_is_a_4xx_and_changes_nothing(client_nr, store_dir, body):
    # §3.6 + D10: ticker in the universe, weight in (0, 100], a funding the user states (kind + its parameter). Nothing
    # is defaulted for the user: a missing ticker / weight / funding is refused. No event, no report.
    # SPEC-AMBIGUITY: §3.6 defaults a missing funding to pro_rata_all; D10 (nothing pre-filled) wins: stricter, 4xx.
    draft = _draft(client_nr)
    before = _state(store_dir)
    r = client_nr.post(f"/intents/{draft['intent_id']}/confirm", json=body)
    assert 400 <= r.status_code < 500, r.text
    assert _state(store_dir) == before


@pytest.mark.parametrize("body", [
    {"ticker": "XIU.TO", "target_weight_pct": 10, "funding": SPECIFIC_ZST},            # below the 18 % held: not a buy
    {"ticker": "NVDA", "target_weight_pct": 30, "funding": SPECIFIC_ZST},              # ZST.TO holds 13 %, 30 needed
    {"ticker": "NVDA", "target_weight_pct": 10, "funding": {"kind": "specific", "ticker": "NVDA"}},   # funds itself
    {"ticker": "NVDA", "target_weight_pct": 10, "funding": {"kind": "specific", "ticker": "AAPL"}},   # not held
], ids=["below current weight", "pool too small", "self funding", "source not held"])
def test_confirm_trade_that_cannot_be_applied_is_a_4xx_not_a_500(client_nr, store_dir, body):
    # §3.6 / intent.apply raises ValueError for these: the API answers 4xx with a reason, appends no intent_confirmed
    # event (stricter reading: the trade is validated before anything is written) and saves no report.
    draft = _draft(client_nr)
    before = _state(store_dir)
    r = client_nr.post(f"/intents/{draft['intent_id']}/confirm", json=body)
    assert 400 <= r.status_code < 500, r.text
    assert r.json().get("detail")
    assert _state(store_dir) == before


@pytest.mark.parametrize("body", [
    {"ticker": "MSFT", "target_weight_pct": 5, "funding": {"kind": "specific", "ticker": "XBB.TO"}},
    {"ticker": "NVDA", "target_weight_pct": 10, "funding": {"kind": "pro_rata_all"}},
], ids=["other ticker, size and funding", "funding the user states (pro_rata_all)"])
def test_user_edits_win_over_the_draft(client, store_dir, body):
    # §4 B2 + task: the confirmed intent is user-declared data: the report, the intent_confirmed payload and the headroom
    # use what the user sent, whatever the N4 draft said (draft: NVDA 10, funding null).
    draft = _draft(client)
    assert draft["ticker"] == "NVDA" and draft["funding"] is None
    r = client.post(f"/intents/{draft['intent_id']}/confirm", json=body)
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["flow"] == "B" and rep["verdict"] in VERDICTS
    it = rep["intent"]
    assert (it["ticker"], it["target_weight_pct"], it["funding"]) == (body["ticker"], body["target_weight_pct"], body["funding"])
    assert it["intent_id"] == draft["intent_id"] and it["event_id"] == NEW_EVENT_ID and it["status"] == "confirmed"
    assert rep["headroom"] is None or rep["headroom"]["funding"] == body["funding"]
    if body["ticker"] != "NVDA":
        assert "NVDA" not in {c.get("subject") for c in rep["checks_proposed"]}
    events = _lines(_workdir(store_dir) / "events.jsonl")
    assert [e["event_id"] for e in events][-2:] == [NEW_EVENT_ID, CONFIRM_EVENT_ID]
    assert events[-1]["type"] == "intent_confirmed" and events[-1]["parent_event_id"] == NEW_EVENT_ID
    assert events[-1]["payload"]["ticker"] == body["ticker"] and events[-1]["payload"]["funding"] == body["funding"]
    assert client.get(f"/reports/{rep['run_id']}").json() == rep


def test_confirming_twice_is_a_409_or_the_same_run_and_never_a_second_event(scen, monkeypatch):
    # Task: "confirming twice -> 409 or the same run, document which". Pinned: both are accepted, but a repeat never
    # creates a second intent_confirmed event or a second run. 409 -> advisory-free reason; 200 -> the same report.
    client = _use_store(monkeypatch, scen)
    first = scen["confirm_resp"].json()
    events_before = (scen["workdir"] / "events.jsonl").read_bytes()
    runs_before = sorted(p.name for p in (scen["store"] / "reports").glob("*.json"))
    r = client.post(f"/intents/{scen['intent_id']}/confirm", json=GOOD_BODY)
    assert r.status_code in (200, 409), r.text
    if r.status_code == 409:
        assert isinstance(r.json().get("detail"), str) and explain.advisory_hits(r.json()["detail"]) == []
    else:
        assert r.json() == first
    assert (scen["workdir"] / "events.jsonl").read_bytes() == events_before
    assert sorted(p.name for p in (scen["store"] / "reports").glob("*.json")) == runs_before
    assert [e["type"] for e in _lines(scen["workdir"] / "events.jsonl")].count("intent_confirmed") == 1


def test_confirm_does_not_write_the_human_only_tag_file_nor_fixtures():
    # CLAUDE.md invariant: data/meta/activity_tags.csv is written by humans only; §10: fixtures/ is never a store.
    # Runs after the scenario (file order): the whole module left both unchanged.
    assert (TAGS.read_bytes() if TAGS.exists() else None) == _TAGS_BYTES


def test_zz_fixtures_are_unchanged_after_the_whole_module():
    # Task: the API never writes under fixtures/ (drafts, events, intents live in the working copy). Runs last.
    assert _hashes() == _FIXTURE_HASHES
