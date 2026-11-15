"""P5.NOPF: a new user answers the intake BEFORE setting a portfolio; setting the portfolio runs the Flow A refresh.

Sources: SPEC §4 Flow A (every question / profile_edit / note event triggers a refresh: derive -> limits + trace ->
validate -> save -> audit), docs/spec-factors.md §20.6 (an unanswered or skipped input is null, never a default; the
checks that need it report `unknown`), docs/spec-decisions.md D1 (reference date = `portfolio.as_of`, never the wall
clock; the audit record carries the date used) and D3 (unknowns never change the verdict), SPEC §7.1 (derived fields),
§7.2 (limits = LIMITS_TABLE[risk.effective] x declared values; floor = portfolio.value_cad x floor_ratio), §1 inv. 1-3
(limits come from `rule:` fields and declared values only; only hard checks give `violation`), §3.5, §12 (API), §13
(report schema), D10 (no prescribing wording in user-facing strings).

Bug pinned: POST /profiles creates a user with no portfolio.json; every intake answer runs `pipeline.refresh`, which
raised FileNotFoundError (HTTP 500) on the first answer, and `derive_all(portfolio_value_cad=None)` /
`build_limits(.., None)` raised TypeError. Contract:
  1. Flow A runs with no portfolio. Every derived field and limit that needs the portfolio value or the reference
     date is null and the trace names the missing input; everything else is derived as usual; the audit `as_of` is null.
  2. PUT /profiles/{id}/portfolio, after writing portfolio.json and its event, runs the same refresh, so the stored
     profile (GET /profiles/{id}) holds limits computed from the new value and date.
  3. create -> answer every `intake.REQUIRED` path -> PUT portfolio -> review gives a schema-valid report with a
     verdict (mock LLM, tmp store). A review before any portfolio is a 4xx with a message, never a 500.

# SPEC-AMBIGUITY (whole file): "needs the portfolio value or the reference date" is read from the signatures of the
# rules (`capacity(.., portfolio_value_cad, as_of)`, `effective_horizon_years(.., as_of)`): risk.capacity,
# risk.effective and objectives.effective_horizon_years are null whenever EITHER input is None, even for a profile
# whose withdrawals list is empty (the stricter reading: no value is computed from an input that does not exist).
# SPEC-AMBIGUITY (whole file): the trace format for a missing portfolio is not fixed; the stricter reading is one entry
# that contains "portfolio" and a word saying it is missing / not set (like "<path> (skipped)" in P3.SKIP).
# SPEC-AMBIGUITY (whole file): `identity.employment_type` is outside `intake.REQUIRED`, so an API-only user has no
# income_stability, hence no risk.capacity and no table limit even after the portfolio is set (reported in
# tests/flows/test_any_user.py). Numeric limits are therefore asserted only for a user whose employment_type was
# declared through POST /profiles/{id}/events (a `profile_edit`, the documented way to set a declared field).

Offline and deterministic: COHERENCE_ANALYZER_LLM=mock, a tmp store per test, fixed timestamps.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

from coherence_analyzer import api, config, data, intake, pipeline, store  # noqa: E402
from coherence_analyzer.llm import bedrock  # noqa: E402
from coherence_analyzer.rules import derive, limits as limits_mod  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
ROBERT_FIX = ROOT / "fixtures" / "robert"
AS_OF = "2026-11-12"
ROBERT_VALUE = 400_000
FACTORS = tuple(intake.FACTORS)
VERDICTS = ("coherent", "coherent_with_warnings", "incoherent")
TS = "2026-11-15T09:30:00-05:00"
FORBIDDEN_WORDS = ("consider", "you could", "rebalance", "next step", "vous pourriez", "envisagez")     # D10
MISSING_WORDS = ("missing", "not set", "no portfolio", "absent", "unset", "without", "skip")

# Limits read from the LIMITS_TABLE row of risk.effective (all need the portfolio date/value through risk.capacity).
TABLE_LIMITS = ("vol_band_annual", "floor_at_horizon", "max_drawdown_pct", "min_fixed_income_weight",
                "max_single_stock_weight", "max_sector_risk_share", "max_crypto_weight",
                "max_foreign_currency_weight", "excluded_asset_types")

ANSWERS: dict[str, object] = {
    "objectives.primary": "capital_preservation_with_growth",
    "objectives.goals": [{"name": "Retire at 65", "date": "2036-06", "amount": 300000, "priority": 1}],
    "objectives.horizon_years": 10,
    "identity.retirement_target_age": 65,
    "situation.planned_withdrawals": [{"date": "2030-06", "amount": 20000, "purpose": "renovation"}],
    "risk.declared": "moderate",
    "risk.max_acceptable_drawdown_pct": -15,
    "risk.reaction_to_20pct_drop": "sell_some",
    "situation.emergency_fund": 30000,
    "situation.monthly_expenses": 6500,
    "identity.employer_sector": "Health Care",
    "preferences.asset_types_refused": [],
    "preferences.prefers_dividends": True,
    "preferences.prefers_large_caps": False,
    "preferences.home_bias_ok": True,
}
PF = {"as_of": "2026-11-12", "value_cad": 120000, "weights_pct": {"XIU.TO": 30, "XBB.TO": 30, "SPY": 25, "GLD": 15}}

_PROFILE_SCHEMA = Draft202012Validator(json.loads((config.SCHEMAS_DIR / "profile.v2.json").read_text("utf-8")))
_REPORT_SCHEMA = Draft202012Validator(json.loads((config.SCHEMAS_DIR / "report.json").read_text("utf-8")))
_ROBERT = json.loads((ROBERT_FIX / "profile.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- helpers

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
    # a server error must show up as a status code (500), not as a re-raised exception
    return TestClient(api.app, raise_server_exceptions=False)


def _create(client: TestClient, locale: str = "en-CA") -> str:
    r = client.post("/profiles", json={"locale": locale})
    assert r.status_code in (200, 201), r.text
    return r.json()["meta"]["profile_id"]


def _answer_all(client: TestClient, pid: str) -> None:
    assert set(ANSWERS) == {p for paths in intake.REQUIRED.values() for p in paths}     # every REQUIRED path, once
    for path, value in ANSWERS.items():
        r = client.post(f"/profiles/{pid}/intake/answer", json={"path": path, "value": value})
        assert r.status_code == 200, (path, r.status_code, r.text)


def _declare_employment(client: TestClient, pid: str) -> None:
    body = {"type": "profile_edit", "text": "Employment type declared.", "ts": TS, "source": "ui",
            "payload": {"path": "identity.employment_type", "old": None, "new": "salaried"}}
    r = client.post(f"/profiles/{pid}/events", json=body)
    assert r.status_code == 200, r.text


def _profile(client: TestClient, pid: str) -> dict:
    r = client.get(f"/profiles/{pid}")
    assert r.status_code == 200, r.text
    return r.json()


def _put(client: TestClient, pid: str, pf: dict = PF):
    return client.put(f"/profiles/{pid}/portfolio", json=pf)


def _val(node):
    return node["value"] if isinstance(node, dict) and "value" in node else node


def _at(profile: dict, path: str):
    node = profile
    for part in path.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    return node


def _names_missing_portfolio(entries) -> bool:
    return any(isinstance(e, str) and "portfolio" in e.lower() and any(w in e.lower() for w in MISSING_WORDS)
               for e in entries)


def _audits(store_dir: Path, pid: str) -> dict[str, dict]:
    out = {}
    for p in sorted((store_dir / "audit").glob("*.json")) if (store_dir / "audit").is_dir() else []:
        a = json.loads(p.read_text(encoding="utf-8"))
        if a.get("profile_id") == pid:
            out[a["run_id"]] = a
    return out


def _events(store_dir: Path, pid: str) -> list[dict]:
    return store.read_events(store_dir / "profiles" / pid / "events.jsonl")


def _baseline() -> tuple[dict, dict, dict]:
    """Robert's profile derived and limited WITH his portfolio (value 400 000, as_of 2026-11-12): the reference."""
    p = derive.derive_all(copy.deepcopy(_ROBERT), portfolio_value_cad=ROBERT_VALUE, as_of=AS_OF)
    lim, trace = limits_mod.build_limits(p, ROBERT_VALUE)
    return p, lim, trace


def _robert_workdir(tmp_path: Path, with_portfolio: bool) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    shutil.copy(ROBERT_FIX / "profile.json", work / "profile.json")
    shutil.copy(ROBERT_FIX / "events.jsonl", work / "events.jsonl")
    if with_portfolio:
        shutil.copy(ROBERT_FIX / "portfolio.json", work / "portfolio.json")
    return work


# ---------------------------------------------------------------- (1) rules without a portfolio value / date (§7.1, D1)

def test_derive_all_without_value_and_date_nulls_only_the_dependent_fields():
    # §7.1 + D1: no TypeError; capacity / effective risk / effective horizon are null (rule: stamp kept, schema
    # `ruleField`), everything that needs neither input equals the full derivation.
    base, _, _ = _baseline()
    before = copy.deepcopy(_ROBERT)
    out = derive.derive_all(_ROBERT, portfolio_value_cad=None, as_of=None)
    assert _ROBERT == before                                                    # input not mutated
    for path in ("objectives.effective_horizon_years", "risk.capacity", "risk.effective"):
        node = _at(out, path)
        assert isinstance(node, dict) and node["value"] is None, (path, node)
        assert str(node["by"]).startswith("rule:"), (path, node)                  # limits only read rule: fields (inv. 1)
    for path in ("identity.life_stage", "situation.income_stability", "situation.emergency_fund_months",
                 "knowledge.score"):
        assert _val(_at(out, path)) == _val(_at(base, path)), path
    assert _val(_at(out, "situation.emergency_fund_months")) == pytest.approx(4.6, abs=1e-9)   # §7.1 Robert
    assert _val(_at(out, "situation.income_stability")) == "high"


@pytest.mark.parametrize("value, as_of, horizon", [(None, AS_OF, 3.6), (ROBERT_VALUE, None, None)])
def test_derive_all_with_only_one_of_value_and_date_missing(value, as_of, horizon):
    # D1: the horizon needs the date only (Robert 2026-11 -> 2030-06 = 43 months = 3.6 y), the capacity needs both.
    out = derive.derive_all(copy.deepcopy(_ROBERT), portfolio_value_cad=value, as_of=as_of)
    eh = _val(_at(out, "objectives.effective_horizon_years"))
    if horizon is None:
        assert eh is None
    else:
        assert eh == pytest.approx(horizon, abs=1e-9)
    assert _val(_at(out, "risk.capacity")) is None
    assert _val(_at(out, "risk.effective")) is None


def test_derive_all_empty_withdrawals_still_needs_the_inputs_it_declares():
    # SPEC-AMBIGUITY: strict reading (see the module note): an empty withdrawals list does not make capacity computable.
    p = copy.deepcopy(_ROBERT)
    p["situation"]["planned_withdrawals"] = []
    out = derive.derive_all(p, portfolio_value_cad=None, as_of=None)
    assert _val(_at(out, "risk.capacity")) is None and _val(_at(out, "risk.effective")) is None
    full = derive.derive_all(p, portfolio_value_cad=ROBERT_VALUE, as_of=AS_OF)
    assert _val(_at(full, "objectives.effective_horizon_years")) == pytest.approx(4.0, abs=1e-9)


# ---------------------------------------------------------------- (1) limits without a portfolio value (§7.2)

def test_build_limits_without_value_nulls_only_the_floor_and_traces_the_missing_input():
    # §7.2: floor_at_horizon = value x floor_ratio needs the value; every other limit is what the full profile gives.
    base_p, base_lim, base_trace = _baseline()
    lim, trace = limits_mod.build_limits(base_p, None)
    assert lim.get("floor_at_horizon") is None
    assert base_lim["floor_at_horizon"] == pytest.approx(360_000, abs=1e-9)
    for name, value in base_lim.items():
        if name != "floor_at_horizon":
            assert lim.get(name) == value, name
    assert set(trace) == set(base_trace)
    assert _names_missing_portfolio(trace["floor_at_horizon"]), trace["floor_at_horizon"]
    for name, entries in trace.items():
        if name != "floor_at_horizon":
            assert entries == base_trace[name], name
            assert not _names_missing_portfolio(entries), name


def test_build_limits_on_a_profile_derived_without_portfolio_has_no_table_limit():
    # §7.2 + inv. 1: no risk.effective -> no LIMITS_TABLE row -> null (never a default row, never an llm: input).
    base_p, base_lim, base_trace = _baseline()
    p = derive.derive_all(copy.deepcopy(_ROBERT), portfolio_value_cad=None, as_of=None)
    lim, trace = limits_mod.build_limits(p, None)
    for name in TABLE_LIMITS:
        assert lim.get(name) is None, (name, lim.get(name))
        assert _names_missing_portfolio(trace[name]) or _names_missing_portfolio(
            [str(_at(p, f"risk.{k}")["by"]) for k in ("capacity", "effective")]), (name, trace[name])
    assert _names_missing_portfolio(trace["floor_at_horizon"]), trace["floor_at_horizon"]
    for name in ("max_employer_sector_weight", "excluded_activities", "max_fee_mer_pct", "max_theme_weight"):
        if name in base_lim:
            assert lim.get(name) == base_lim[name], name                             # needs neither input
            assert not _names_missing_portfolio(trace[name]), name
    assert set(trace) == set(base_trace)
    assert all(isinstance(v, list) and all(isinstance(e, str) for e in v) for v in trace.values())


# ---------------------------------------------------------------- (1) Flow A (pipeline.refresh) with no portfolio.json

def test_refresh_runs_without_a_portfolio_and_records_a_null_reference_date(tmp_path, store_dir):
    # §4 Flow A + D1: no FileNotFoundError; the audit record has as_of null (the date used), flow A, every event.
    work = _robert_workdir(tmp_path, with_portfolio=False)
    res = pipeline.refresh(work)
    audit = res["audit"]
    assert audit["flow"] == "A" and audit["profile_id"] == "persona-robert"
    assert "as_of" in audit and audit["as_of"] is None
    assert audit["events_considered"] == [e["event_id"] for e in store.read_events(work / "events.jsonl")]
    assert store.load_audit(res["run_id"])["as_of"] is None                    # and so is the stored record
    assert not (work / "portfolio.json").exists()                              # nothing invented on disk
    pipeline.validate_profile(res["profile"], "refreshed profile without portfolio")      # §3.3: valid on every save


def test_refresh_without_a_portfolio_nulls_the_dependent_fields_and_keeps_the_rest(tmp_path):
    # §7.1/§7.2 + P3.SKIP convention: null + a trace entry naming the missing input; the rest as the full profile.
    base_p, base_lim, base_trace = _baseline()
    res = pipeline.refresh(_robert_workdir(tmp_path, with_portfolio=False))
    p = res["profile"]
    for path in ("objectives.effective_horizon_years", "risk.capacity", "risk.effective"):
        assert _val(_at(p, path)) is None, path
        assert str(_at(p, path)["by"]).startswith("rule:"), path
    for path in ("identity.life_stage", "situation.income_stability", "situation.emergency_fund_months",
                 "knowledge.score"):
        assert _val(_at(p, path)) == _val(_at(base_p, path)), path
    limits, trace = p["limits"], p["limits_trace"]
    for name in TABLE_LIMITS:
        assert limits.get(name) is None, (name, limits.get(name))
    assert _names_missing_portfolio(trace["floor_at_horizon"]), trace["floor_at_horizon"]
    for name in TABLE_LIMITS:
        assert _names_missing_portfolio(trace[name]) or _names_missing_portfolio(
            [str(_at(p, "risk.capacity")["by"]), str(_at(p, "risk.effective")["by"])]), (name, trace[name])
    for name in ("max_employer_sector_weight", "excluded_activities", "max_fee_mer_pct", "max_theme_weight"):
        if name in base_lim:
            assert limits.get(name) == base_lim[name], name
    assert set(trace) == set(base_trace)


def test_refresh_with_a_portfolio_is_unchanged_for_a_persona(tmp_path):
    # Control (personas always have a portfolio): D1 date recorded, Robert's §7.1/§7.2 numbers.
    res = pipeline.refresh(_robert_workdir(tmp_path, with_portfolio=True))
    assert res["audit"]["as_of"] == AS_OF
    p = res["profile"]
    assert _val(_at(p, "objectives.effective_horizon_years")) == pytest.approx(3.6, abs=1e-9)
    assert _val(_at(p, "risk.effective")) == "low_moderate"
    assert p["limits"]["floor_at_horizon"] == pytest.approx(360_000, abs=1e-9)
    assert p["limits"]["max_single_stock_weight"] == pytest.approx(0.08, abs=1e-9)


# ---------------------------------------------------------------- (1) the API: intake answers before any portfolio

@pytest.mark.parametrize("locale", ["fr-CA", "en-CA"])
def test_every_intake_answer_before_a_portfolio_is_a_200_and_one_event(client, store_dir, locale):
    # The reported 500: create -> answer. Each answer: 200, one `intake` profile_edit event, valid profile, no number
    # invented for a limit whose inputs are missing.
    pid = _create(client, locale)
    assert not (store_dir / "profiles" / pid / "portfolio.json").exists()
    for n, (path, value) in enumerate(ANSWERS.items(), start=1):
        r = client.post(f"/profiles/{pid}/intake/answer", json={"path": path, "value": value})
        assert r.status_code == 200, (path, r.status_code, r.text)
        events = _events(store_dir, pid)
        assert len(events) == n and events[-1]["type"] == "profile_edit" and events[-1]["source"] == "intake"
        assert events[-1]["payload"]["path"] == path
        assert _PROFILE_SCHEMA.is_valid(_profile(client, pid)), path
    profile = _profile(client, pid)
    for factor in FACTORS:
        assert intake.missing(profile, factor) == [], factor                   # §20.6: the checklist is complete
    for path, value in ANSWERS.items():
        assert _at(profile, path) == value, path                               # declared values stored as given
    limits = profile.get("limits") or {}
    for name in TABLE_LIMITS:
        assert limits.get(name) is None, (name, limits.get(name))             # no portfolio: no table limit, no floor
    assert not (store_dir / "profiles" / pid / "portfolio.json").exists()


def test_skip_a_note_and_a_quiz_result_before_a_portfolio_are_not_server_errors(client, store_dir):
    # Flow A triggers (SPEC §4): skip, note and quiz_result events run the refresh on a user with no portfolio.
    pid = _create(client, "en-CA")
    r = client.post(f"/profiles/{pid}/intake/skip", json={"path": "risk.declared"})
    assert r.status_code == 200, r.text
    assert _at(_profile(client, pid), "risk.declared") == {"value": None, "by": intake.SKIPPED_BY}
    r = client.post(f"/profiles/{pid}/events", json={"type": "note", "text": "I may need cash in 2030.", "ts": TS})
    assert r.status_code == 200, r.text
    r = client.post(f"/profiles/{pid}/events", json={"type": "quiz_result", "text": "Quiz done.", "ts": TS,
                                                     "payload": {"score": 0.6, "quiz_id": "kq_v1"}})
    assert r.status_code == 200, r.text
    assert [e["type"] for e in _events(store_dir, pid)] == ["profile_edit", "note", "quiz_result"]
    assert _PROFILE_SCHEMA.is_valid(_profile(client, pid))
    audits = _audits(store_dir, pid)
    assert len(audits) >= 3 and all(a["flow"] == "A" and a["as_of"] is None for a in audits.values())   # D1


def test_before_the_portfolio_the_audit_has_no_date_and_derived_inputs_are_computed(client, store_dir):
    # (1): everything not needing the portfolio is derived as usual from the declared answers (§7.1), the trace names
    # the missing portfolio for every limit that needs it, and each refresh audit says as_of null (D1).
    pid = _create(client, "en-CA")
    _declare_employment(client, pid)
    _answer_all(client, pid)
    p = _profile(client, pid)
    assert _val(p["situation"]["emergency_fund_months"]) == pytest.approx(4.6, abs=1e-9)    # 30 000 / 6 500
    assert _val(p["situation"]["income_stability"]) == "high"                               # salaried
    for path in ("objectives.effective_horizon_years", "risk.capacity", "risk.effective"):
        assert _val(_at(p, path)) is None, path
    for name in TABLE_LIMITS:
        assert (p["limits"] or {}).get(name) is None, name
        assert _names_missing_portfolio(p["limits_trace"][name]) or _names_missing_portfolio(
            [str(p["risk"]["capacity"]["by"]), str(p["risk"]["effective"]["by"])]), (name, p["limits_trace"][name])
    assert _names_missing_portfolio(p["limits_trace"]["floor_at_horizon"])
    assert (p["limits"] or {}).get("max_employer_sector_weight") == pytest.approx(0.15, abs=1e-9)   # §7.2, every level
    audits = _audits(store_dir, pid)
    assert len(audits) == 1 + len(ANSWERS)                                      # one refresh per answered question
    assert all(a["flow"] == "A" and a["as_of"] is None for a in audits.values())


# ---------------------------------------------------------------- (2) PUT portfolio runs the refresh

def test_put_portfolio_computes_the_limits_from_the_new_value_and_date(client, store_dir):
    # (2) + §7.2 table row low_moderate (declared moderate, capacity low_moderate from the 3.6-year effective horizon):
    # floor = 120 000 x 0.90, min fixed income 0.30, single stock 0.08, sector share 0.30, crypto 0.00, foreign 0.40,
    # vol band 0.05-0.09, drawdown = the less negative of -12 (table) and -15 (declared).
    pid = _create(client, "en-CA")
    _declare_employment(client, pid)
    _answer_all(client, pid)
    before = _profile(client, pid)
    audits_before = set(_audits(store_dir, pid))
    r = _put(client, pid)
    assert r.status_code == 200, r.text
    p = _profile(client, pid)                                                  # GET serves the refreshed profile
    assert _PROFILE_SCHEMA.is_valid(p)
    assert p["meta"]["version"] > before["meta"]["version"]
    assert _val(p["objectives"]["effective_horizon_years"]) == pytest.approx(3.6, abs=1e-9)   # 2026-11 -> 2030-06
    assert _val(p["risk"]["capacity"]) == "low_moderate" and _val(p["risk"]["effective"]) == "low_moderate"
    lim = p["limits"]
    assert lim["floor_at_horizon"] == pytest.approx(120_000 * 0.90, abs=1e-9)
    assert lim["min_fixed_income_weight"] == pytest.approx(0.30, abs=1e-9)
    assert lim["max_single_stock_weight"] == pytest.approx(0.08, abs=1e-9)
    assert lim["max_sector_risk_share"] == pytest.approx(0.30, abs=1e-9)
    assert lim["max_crypto_weight"] == pytest.approx(0.0, abs=1e-9)
    assert lim["max_foreign_currency_weight"] == pytest.approx(0.40, abs=1e-9)
    assert lim["vol_band_annual"] == pytest.approx([0.05, 0.09], abs=1e-9)
    assert lim["max_drawdown_pct"] == -12
    assert "crypto" in lim["excluded_asset_types"]                             # max_crypto = 0 -> crypto excluded
    assert not any(_names_missing_portfolio(v) for v in p["limits_trace"].values())      # nothing missing any more
    # D1: the refresh after the PUT is audited with the date it used; it saw the portfolio event.
    new = {k: a for k, a in _audits(store_dir, pid).items() if k not in audits_before}
    assert len(new) == 1
    audit = next(iter(new.values()))
    assert audit["flow"] == "A" and audit["as_of"] == PF["as_of"]
    events = _events(store_dir, pid)
    assert audit["events_considered"] == [e["event_id"] for e in events]
    assert events[-1]["type"] == "profile_edit" and events[-1]["payload"]["path"] == "portfolio"


def test_put_portfolio_without_any_intake_answer_is_fine_and_limits_stay_null(client, store_dir):
    # (2) + P3.SKIP: a refresh with every required answer missing is not a crash and invents no limit.
    pid = _create(client, "fr-CA")
    r = _put(client, pid)
    assert r.status_code == 200, r.text
    p = _profile(client, pid)
    assert _PROFILE_SCHEMA.is_valid(p)
    for name in TABLE_LIMITS:
        assert (p.get("limits") or {}).get(name) is None, name
    audits = list(_audits(store_dir, pid).values())
    assert audits and all(a["flow"] == "A" and a["as_of"] == PF["as_of"] for a in audits)


def test_put_portfolio_on_a_persona_refreshes_limits_from_the_new_value_and_date(client, store_dir):
    # (2) + D1 + §7.1/§7.2 on Robert (fixtures/ stay untouched, the store copy takes the edit):
    # 2026-11-12 / 200 000: effective horizon 3.6 y -> low_moderate, floor = 200 000 x 0.90.
    # 2028-01-15 / 200 000: 29 months to 2030-06 = 2.4 y -> capacity low, effective low, floor = 200 000 x 0.95.
    pid = "persona-robert"
    first = {"as_of": "2026-11-12", "value_cad": 200000, "weights_pct": {"XIU.TO": 50, "XBB.TO": 50}}
    assert _put(client, pid, first).status_code == 200
    p = _profile(client, pid)
    assert p["limits"]["floor_at_horizon"] == pytest.approx(180_000, abs=1e-9)
    assert _val(p["risk"]["effective"]) == "low_moderate"
    second = {"as_of": "2028-01-15", "value_cad": 200000, "weights_pct": {"XIU.TO": 50, "XBB.TO": 50}}
    assert _put(client, pid, second).status_code == 200
    p = _profile(client, pid)
    assert _val(p["objectives"]["effective_horizon_years"]) == pytest.approx(2.4, abs=1e-9)
    assert _val(p["risk"]["capacity"]) == "low" and _val(p["risk"]["effective"]) == "low"
    assert p["limits"]["floor_at_horizon"] == pytest.approx(190_000, abs=1e-9)
    assert p["limits"]["min_fixed_income_weight"] == pytest.approx(0.50, abs=1e-9)
    assert _PROFILE_SCHEMA.is_valid(p)
    assert _audits(store_dir, pid)                                              # each PUT left a Flow A audit


# ---------------------------------------------------------------- (3) the full order, and review before a portfolio

@pytest.mark.parametrize("with_employment", [False, True])
@pytest.mark.parametrize("locale", ["fr-CA", "en-CA"])
def test_full_order_create_answer_put_review_gives_a_valid_report(client, store_dir, locale, with_employment):
    # §4 Flow C + §13 + §9.4: the natural order works end to end in mock mode.
    pid = _create(client, locale)
    if with_employment:
        _declare_employment(client, pid)
    _answer_all(client, pid)
    assert _put(client, pid).status_code == 200
    r = client.post(f"/profiles/{pid}/review")
    assert r.status_code == 200, r.text
    rep = r.json()
    errors = [e.message for e in _REPORT_SCHEMA.iter_errors(rep)]
    assert errors == [], errors[:3]
    assert rep["flow"] == "C" and rep["profile_id"] == pid and rep["verdict"] in VERDICTS
    assert [s["factor"] for s in rep["sections"]] == list(FACTORS)
    assert rep["disclaimer"] == config.DISCLAIMER[locale]                       # fixed text (SPEC §9.4)
    assert client.get(f"/reports/{rep['run_id']}").json() == rep
    audit = client.get(f"/audit/{rep['run_id']}").json()
    assert audit["flow"] == "C" and audit["as_of"] == PF["as_of"] and audit["verdict"] == rep["verdict"]
    assert _PROFILE_SCHEMA.is_valid(_profile(client, pid))
    for u in rep["unknowns"]:
        assert u["status"] == "unknown"                                        # D3: unknowns are listed
    text = " ".join(seg["text"] for seg in rep["explanation"]["segments"]).lower()
    assert not any(w in text for w in FORBIDDEN_WORDS)                         # D10
    if not with_employment:
        # SPEC-AMBIGUITY: without employment_type no table limit exists; inv. 3 + D3: no table check can be a violation.
        assert [c for c in rep["checks_current"] if c["status"] == "violation"
                and c["id"] in ("H1", "H2", "H3", "H4", "H5", "H7", "H8", "H9", "H10")] == []
    else:
        floor = [c for c in rep["checks_current"] if c["id"] == "H9"]
        assert floor and all(c["status"] != "unknown" for c in floor)           # the floor now exists: H9 is decided


@pytest.mark.parametrize("answered", [False, True])
def test_review_before_any_portfolio_is_a_clear_4xx_and_writes_nothing(client, store_dir, answered):
    # (3): 4xx with a message that says what is missing; never a 500; no report, no audit, no event.
    pid = _create(client, "en-CA")
    if answered:
        _answer_all(client, pid)
    events_before = _events(store_dir, pid)
    audits_before = set(_audits(store_dir, pid))
    r = client.post(f"/profiles/{pid}/review")
    assert 400 <= r.status_code < 500, (r.status_code, r.text)
    detail = r.json().get("detail")
    assert isinstance(detail, str) and "portfolio" in detail.lower()
    assert not any(w in detail.lower() for w in FORBIDDEN_WORDS)               # D10 on a user-facing string
    assert not (store_dir / "reports").exists() or list((store_dir / "reports").glob("*.json")) == []
    assert _events(store_dir, pid) == events_before
    assert set(_audits(store_dir, pid)) == audits_before


def test_a_refused_review_does_not_stop_the_user_from_setting_the_portfolio_afterwards(client, store_dir):
    pid = _create(client, "en-CA")
    _answer_all(client, pid)
    assert 400 <= client.post(f"/profiles/{pid}/review").status_code < 500
    assert _put(client, pid).status_code == 200
    assert client.post(f"/profiles/{pid}/review").status_code == 200


def test_seed_fixtures_are_untouched_by_this_module():
    # fixtures/ is seed data: the persona test above wrote the store copy only.
    assert json.loads((ROBERT_FIX / "portfolio.json").read_text(encoding="utf-8"))["value_cad"] == ROBERT_VALUE
    assert json.loads((ROBERT_FIX / "profile.json").read_text(encoding="utf-8")) == _ROBERT
