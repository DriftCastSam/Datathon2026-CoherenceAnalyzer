"""Amira + Mateo fixtures reviewed end-to-end offline (P1.1; SPEC §16 amira/ mateo/, §3.3-§3.5, §6.2 N3, §7.1, §7.2,
§7.3; docs/spec-decisions.md D1, D3, D7 points 1 and 4; docs/spec-factors.md §20.6 showcase, §20.9 N6).

Offline and deterministic: COHERENCE_ANALYZER_LLM=mock (recorded / hand-written responses only), fixed engine seed, the
reference date is `portfolio.as_of` = 2026-11-12 (D1), never the wall clock. Reviews run in a temp working directory,
so the store (reports/, audit/, profiles/) never touches the repo.

Everything about the fictional figures (income, expenses, emergency fund, goals, withdrawals) is a team assumption; the
tests only pin what SPEC §16 and the rules say, and assert properties wherever the spec gives no number.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import re
from datetime import datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

from coherence_analyzer import cli, config, data, gate, pipeline, store  # noqa: E402
from coherence_analyzer.coherence import hard, internal, report as report_mod  # noqa: E402
from coherence_analyzer.llm import bedrock, extractors  # noqa: E402
from coherence_analyzer.rules import derive, limits as limits_mod  # noqa: E402
from coherence_analyzer.rules.limits import LIMITS_TABLE  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
AS_OF = "2026-11-12"                      # portfolio.as_of (D1)
PERSONAS = ("amira", "mateo")
PROFILE_IDS = {"amira": "persona-amira", "mateo": "persona-mateo"}
PORTFOLIO_VALUES = {"amira": 60_000, "mateo": 150_000}      # SPEC §3.5 / §16
LEVELS = ("low", "low_moderate", "moderate", "moderate_high", "high")    # SPEC §3.3
VERDICTS = ("coherent", "coherent_with_warnings", "incoherent")           # D3
NOTES = {
    "amira": "I love AI and new tech but I don't want everything in tech.",
    "mateo": ("Avoid companies with major environmental or social controversies. "
              "Open to new tech if it fits, nothing speculative."),
}
ENV_TAG, SOC_TAG = "significant_environmental_controversy", "significant_social_controversy"
APPROVED_PATH = "values.parsed_notes[approved_by_user]"
MOCK_MODEL = "fixture:hand-written"
N3, N6 = "values_parser", "profile_consistency"
EVENT_TYPES = {"question", "order_intent", "quiz_result", "profile_edit", "note", "intent_confirmed"}   # §3.4
EVENT_KEYS = {"event_id", "profile_id", "ts", "type", "text", "payload", "source"}
META_KEYS = {"_note", "by"}               # descriptive keys of a limits block, not limits themselves


# ---------------------------------------------------------------- helpers

@pytest.fixture(scope="module", autouse=True)
def _mock_mode():
    """The suite must run on recorded responses whatever the local .env says."""
    mp = pytest.MonkeyPatch()
    mp.setattr(bedrock, "COHERENCE_ANALYZER_LLM", "mock")
    yield
    mp.undo()


def _dir(name: str) -> Path:
    return FIXTURES / name


def _read(name: str, file: str) -> dict:
    return json.loads((_dir(name) / file).read_text(encoding="utf-8"))


def _events(name: str) -> list[dict]:
    lines = (_dir(name) / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(ln) for ln in lines if ln.strip()]


def _val(node):
    return node["value"] if isinstance(node, dict) and "value" in node else node


def _get(profile: dict, path: str):
    node = profile
    for part in path.split("."):
        node = node[part]
    return node


def _ruled(profile: dict, value: float) -> dict:
    """The profile after Flow A's rules (derive -> limits + trace), exactly as the pipeline builds it."""
    p = derive.derive_all(copy.deepcopy(profile), portfolio_value_cad=value, as_of=AS_OF)
    lim, trace = limits_mod.build_limits(p, value)
    p["limits"], p["limits_trace"] = lim, trace
    return p


@pytest.fixture(scope="module")
def raw() -> dict:
    return {n: _read(n, "profile.json") for n in PERSONAS}


@pytest.fixture(scope="module")
def ruled(raw) -> dict:
    return {n: _ruled(raw[n], PORTFOLIO_VALUES[n]) for n in PERSONAS}


@pytest.fixture(scope="module")
def assets():
    return data.load_assets()


def _months_between(a: str, b: str) -> int:
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7])


def _expected_rules(profile: dict, value: float) -> dict:
    """SPEC §7.1 written out independently of rules/derive.py (month granularity, as_of = portfolio.as_of, D1)."""
    ident, sit, obj, risk = profile["identity"], profile["situation"], profile["objectives"], profile["risk"]
    age = ident["age"]
    life = ("early_accumulation" if age < 35 else "accumulation" if age < 55
            else "pre_retirement" if age < ident["retirement_target_age"] else "retired")
    stability = {"salaried": "high", "self_employed": "medium", "contract": "low", "commission": "low",
                 "unemployed": "low", "retired": "low"}[ident["employment_type"]]
    ef = round(sit["emergency_fund"] / sit["monthly_expenses"], 1)
    withdrawals = sit.get("planned_withdrawals") or []
    horizon = float(obj["horizon_years"])
    if withdrawals:
        first = min(max(0, _months_between(AS_OF, w["date"])) for w in withdrawals)
        horizon = min(horizon, first / 12)
    eh = round(horizon, 1)
    base = 0 if eh < 3 else 1 if eh < 5 else 2 if eh < 10 else 3 if eh < 15 else 4
    near = sum(w["amount"] for w in withdrawals if 0 <= _months_between(AS_OF, w["date"]) <= 24)
    notches = (ef < 3) + (stability == "low") + (near > 0.10 * value)
    capacity = LEVELS[max(0, base - notches)]
    effective = LEVELS[min(LEVELS.index(risk["declared"]), LEVELS.index(capacity))]
    return {"life_stage": life, "income_stability": stability, "ef_months": ef, "effective_horizon": eh,
            "capacity": capacity, "effective": effective}


def _walk_llm(node, path: str = ""):
    """(path, obj) for every dict whose `by` starts with `llm:` (SPEC §1 invariant 1)."""
    if isinstance(node, dict):
        if isinstance(node.get("by"), str) and node["by"].startswith("llm:"):
            yield path, node
        for k, v in node.items():
            yield from _walk_llm(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_llm(v, f"{path}[{i}]")


def _parsed(profile: dict) -> list[dict]:
    return profile["values"]["parsed_notes"]["value"]


def _declared_exclusions(profile: dict) -> list[str]:
    out: list[str] = []
    for pillar in profile["values"].values():
        if isinstance(pillar, dict) and isinstance(pillar.get("exclusions"), list):
            out.extend(pillar["exclusions"])
    return out


def _cache_entry(node: str, payload: dict) -> dict:
    key = bedrock.cache_key(node, extractors.prompt_version(node), MOCK_MODEL, payload)
    path = config.LLM_CACHE_DIR / f"{node}.{key}.json"
    assert path.is_file(), f"missing hand-written {node} cache entry for this input: {path.name}"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- fixtures exist, validate, ids match (§3.3, §3.5, §16)

@pytest.mark.parametrize("name", PERSONAS)
def test_profile_validates_and_profile_ids_match_the_portfolio_and_events(name, raw):
    """§3.3 + §16: profile.json validates against schemas/profile.v2.json; meta.profile_id = portfolio.json's
    (persona-amira / persona-mateo) = every event's profile_id; portfolio is as_of 2026-11-12 with the §3.5 value."""
    for f in ("profile.json", "events.jsonl", "portfolio.json"):
        assert (_dir(name) / f).is_file(), f"fixtures/{name}/{f} is missing"
    profile = raw[name]
    pipeline.validate_profile(profile, f"fixtures/{name}/profile.json")          # raises ValueError when invalid
    schema = json.loads((config.SCHEMAS_DIR / "profile.v2.json").read_text(encoding="utf-8"))
    errors = [e.message for e in Draft202012Validator(schema).iter_errors(profile)]
    assert errors == []
    pf = _read(name, "portfolio.json")
    assert profile["meta"]["profile_id"] == pf["profile_id"] == PROFILE_IDS[name]
    assert profile["meta"]["schema_version"] == 2
    assert pf["as_of"] == AS_OF and pf["value_cad"] == PORTFOLIO_VALUES[name]
    assert {e["profile_id"] for e in _events(name)} == {PROFILE_IDS[name]}
    loaded = data.load_portfolio(_dir(name) / "portfolio.json", data.load_assets())    # known tickers, sum 100 +- 0.01
    assert sum(loaded["weights"].values()) == pytest.approx(1.0, abs=1e-4)


@pytest.mark.parametrize("name", PERSONAS)
def test_llm_fields_are_stamped_hand_written_and_limits_are_generated(name, raw, ruled):
    """§3.3 items 1, 5 + §16 (hand-written until Bedrock is recorded): every `llm:` object carries the stamp with
    model_id fixture:hand-written; the stored limits_trace is the generated one (never hand-written, §7.2)."""
    found = list(_walk_llm(raw[name]))
    assert "values.parsed_notes" in {p for p, _ in found}
    for path, obj in found:
        assert obj["model_id"] == MOCK_MODEL, path
        assert re.search(r"v\d+$", obj["prompt_version"]), path
        assert obj["input_hash"].startswith("sha256:"), path
    # SPEC-AMBIGUITY: §3.3 item 5 adds limits_trace to the profile; stricter reading (as Robert's fixture and
    # test_profile_schema): the fixture stores limits + limits_trace and they equal what rules/limits.py generates.
    assert "limits" in raw[name] and "limits_trace" in raw[name]
    assert {k: v for k, v in raw[name]["limits"].items() if k not in META_KEYS} == \
           {k: v for k, v in ruled[name]["limits"].items() if k not in META_KEYS}
    assert set(raw[name]["limits_trace"]) == set(ruled[name]["limits_trace"])
    for limit, paths in ruled[name]["limits_trace"].items():
        assert sorted(raw[name]["limits_trace"][limit]) == sorted(paths), limit


# ---------------------------------------------------------------- declared fields (§16)

def test_amira_declared_profile_matches_section_16(raw):
    """§16 amira/: 26, salaried software engineer (employer_sector Information Technology), single, no dependants,
    intermediate, horizon 15+, declared high, notes verbatim; fictional income/expenses/emergency fund are present."""
    p = raw["amira"]
    assert p["identity"]["age"] == 26
    assert p["identity"]["employment_type"] == "salaried"
    assert "software engineer" in p["identity"]["occupation"].lower()
    assert p["identity"]["employer_sector"] == "Information Technology"       # H6 matters with NVDA / SHOP.TO
    assert p["identity"]["marital_status"] == "single"
    assert p["identity"]["dependants"] == 0
    assert p["knowledge"]["declared_level"] == "intermediate"
    assert p["objectives"]["horizon_years"] >= 15
    assert p["risk"]["declared"] == "high"
    assert p["values"]["notes"] == NOTES["amira"]                              # verbatim
    sit = p["situation"]
    assert sit["annual_income_band"] and sit["monthly_expenses"] > 0 and sit["emergency_fund"] > 0


def test_mateo_declared_profile_matches_section_16(raw):
    """§16 mateo/: 41, operations manager, partner + 2 children, intermediate, horizon 10, declared moderate;
    values.environment {5, [controversy env]}, values.social {4, [controversy social]}; notes verbatim."""
    p = raw["mateo"]
    assert p["identity"]["age"] == 41
    assert "operations manager" in p["identity"]["occupation"].lower()
    # SPEC-AMBIGUITY: §16 says "partner" without a marital_status value; any partnered status is accepted.
    assert isinstance(p["identity"]["marital_status"], str) and p["identity"]["marital_status"] != "single"
    assert p["identity"]["dependants"] == 2
    assert p["knowledge"]["declared_level"] == "intermediate"
    assert p["objectives"]["horizon_years"] == 10
    assert p["risk"]["declared"] == "moderate"
    assert p["values"]["environment"] == {"importance": 5, "exclusions": [ENV_TAG]}
    assert p["values"]["social"] == {"importance": 4, "exclusions": [SOC_TAG]}
    assert p["values"]["notes"] == NOTES["mateo"]                              # verbatim
    sit = p["situation"]
    assert sit["annual_income_band"] and sit["monthly_expenses"] > 0 and sit["emergency_fund"] > 0


# ---------------------------------------------------------------- rules: derive (§7.1, D1)

def test_amira_rules_give_capacity_high_and_effective_high(ruled, raw):
    """§7.1 + §16: age 26 -> early_accumulation; salaried -> income_stability high; ef_months >= 3 (so capacity does
    not drop); effective horizon >= 15 y -> capacity high; effective = min(declared high, capacity) = high."""
    p = ruled["amira"]
    exp = _expected_rules(raw["amira"], PORTFOLIO_VALUES["amira"])
    assert _val(p["identity"]["life_stage"]) == exp["life_stage"] == "early_accumulation"
    assert _val(p["situation"]["income_stability"]) == exp["income_stability"] == "high"
    ef = _val(p["situation"]["emergency_fund_months"])
    assert ef == pytest.approx(exp["ef_months"], abs=1e-9)
    assert ef >= 3
    assert _val(p["objectives"]["effective_horizon_years"]) == pytest.approx(exp["effective_horizon"], abs=1e-9)
    assert _val(p["objectives"]["effective_horizon_years"]) >= 15
    assert _val(p["risk"]["capacity"]) == "high"
    assert _val(p["risk"]["effective"]) == "high"
    for path in ("identity.life_stage", "situation.income_stability", "situation.emergency_fund_months",
                 "objectives.effective_horizon_years", "risk.capacity", "risk.effective"):
        assert _get(p, path)["by"].startswith("rule:"), path                  # inv. 1: computed by code


def test_mateo_rules_give_effective_moderate(ruled, raw):
    """§7.1 + §16: age 41 -> accumulation; capacity is what the rules give (checked against SPEC §7.1 written out in
    the test); declared moderate and capacity at least moderate -> effective moderate."""
    p = ruled["mateo"]
    exp = _expected_rules(raw["mateo"], PORTFOLIO_VALUES["mateo"])
    assert _val(p["identity"]["life_stage"]) == exp["life_stage"] == "accumulation"
    assert _val(p["situation"]["income_stability"]) == exp["income_stability"]
    assert _val(p["situation"]["emergency_fund_months"]) == pytest.approx(exp["ef_months"], abs=1e-9)
    assert _val(p["objectives"]["effective_horizon_years"]) == pytest.approx(exp["effective_horizon"], abs=1e-9)
    assert _val(p["objectives"]["effective_horizon_years"]) <= 10             # min(horizon 10, first withdrawal)
    assert _val(p["risk"]["capacity"]) == exp["capacity"]
    assert LEVELS.index(_val(p["risk"]["capacity"])) >= LEVELS.index("moderate")
    assert _val(p["risk"]["effective"]) == exp["effective"] == "moderate"
    for path in ("risk.capacity", "risk.effective", "objectives.effective_horizon_years"):
        assert _get(p, path)["by"].startswith("rule:"), path


# ---------------------------------------------------------------- rules: limits (§7.2)

def test_amira_limits_are_the_high_row_of_the_table_applied_as_section_7_2(ruled, raw):
    """§7.2 + §16: effective high -> max_crypto 0.10, max_single_stock 0.15, min fixed income 0.00, floor
    60 000 x 0.60 = 36 000; the rest is the `high` row; crypto is not auto-excluded (max_crypto != 0)."""
    lim = ruled["amira"]["limits"]
    row = LIMITS_TABLE["high"]
    assert lim["max_crypto_weight"] == pytest.approx(0.10, abs=1e-9)
    assert lim["max_single_stock_weight"] == pytest.approx(0.15, abs=1e-9)
    assert lim["min_fixed_income_weight"] == pytest.approx(0.0, abs=1e-9)
    assert lim["floor_at_horizon"] == pytest.approx(60_000 * 0.60, abs=1e-9)
    assert lim["floor_at_horizon"] == pytest.approx(36_000, abs=1e-9)
    assert lim["vol_band_annual"] == pytest.approx(list(row["vol_band"]), abs=1e-9)
    assert lim["max_sector_risk_share"] == pytest.approx(row["max_sector_risk_share"], abs=1e-9)
    assert lim["max_foreign_currency_weight"] == pytest.approx(row["max_foreign_ccy"], abs=1e-9)
    assert lim["max_employer_sector_weight"] == pytest.approx(0.15, abs=1e-9)  # all levels
    declared_dd = raw["amira"]["risk"].get("max_acceptable_drawdown_pct")
    expected_dd = row["max_dd_pct"] if declared_dd is None else max(row["max_dd_pct"], declared_dd)   # less negative
    assert lim["max_drawdown_pct"] == pytest.approx(expected_dd, abs=1e-9)
    refused = raw["amira"]["preferences"].get("asset_types_refused", [])
    assert "crypto" not in refused                                             # §4 matrix: crypto must be held-able
    assert set(lim["excluded_asset_types"]) == set(refused)                    # refused U (crypto if max_crypto = 0)
    assert lim["max_fee_mer_pct"] == raw["amira"]["preferences"]["max_fee_mer_pct"]


def test_mateo_limits_are_the_moderate_row_of_the_table_applied_as_section_7_2(ruled, raw):
    """§7.2: effective moderate -> the `moderate` row; floor 150 000 x 0.85 = 127 500; max_crypto 0.02 != 0, so
    excluded_asset_types is exactly preferences.asset_types_refused."""
    lim = ruled["mateo"]["limits"]
    row = LIMITS_TABLE["moderate"]
    assert lim["max_crypto_weight"] == pytest.approx(0.02, abs=1e-9)
    assert lim["max_single_stock_weight"] == pytest.approx(0.10, abs=1e-9)
    assert lim["min_fixed_income_weight"] == pytest.approx(0.20, abs=1e-9)
    assert lim["floor_at_horizon"] == pytest.approx(150_000 * 0.85, abs=1e-9)
    assert lim["vol_band_annual"] == pytest.approx(list(row["vol_band"]), abs=1e-9)
    assert lim["max_sector_risk_share"] == pytest.approx(0.35, abs=1e-9)
    assert lim["max_foreign_currency_weight"] == pytest.approx(0.50, abs=1e-9)
    declared_dd = raw["mateo"]["risk"].get("max_acceptable_drawdown_pct")
    expected_dd = row["max_dd_pct"] if declared_dd is None else max(row["max_dd_pct"], declared_dd)
    assert lim["max_drawdown_pct"] == pytest.approx(expected_dd, abs=1e-9)
    assert set(lim["excluded_asset_types"]) == set(raw["mateo"]["preferences"].get("asset_types_refused", []))


def test_amira_approved_theme_rule_becomes_the_theme_limit_with_trace(ruled, raw):
    """§20.6 showcase + §6.2 N3 + §7.2: the parsed `max_theme_weight {theme: tech_heavy}` has no number from N3; the
    fixture holds it approved_by_user true with max_pct 50 (she answered the intake question), so
    limits.max_theme_weight == {tech_heavy: 0.50} (percent -> fraction once) and limits_trace cites the approval."""
    theme_rules = [r for r in _parsed(raw["amira"]) if r["rule_kind"] == "max_theme_weight"]
    assert len(theme_rules) == 1
    rule = theme_rules[0]
    assert rule["params"]["theme"] == "tech_heavy"
    assert rule["params"]["max_pct"] == 50
    assert rule["approved_by_user"] is True
    assert rule["quote"] in NOTES["amira"]
    assert set(ruled["amira"]["limits"]["max_theme_weight"]) == {"tech_heavy"}
    assert ruled["amira"]["limits"]["max_theme_weight"]["tech_heavy"] == pytest.approx(0.50, abs=1e-9)
    assert APPROVED_PATH in ruled["amira"]["limits_trace"]["max_theme_weight"]
    assert APPROVED_PATH in raw["amira"]["limits_trace"]["max_theme_weight"]   # and in the stored fixture


def test_amira_theme_limit_exists_only_through_the_users_approval(raw):
    """Invariant 2 (SPEC §1, §6.2 N3): the `llm:` parsed rule feeds `limits` only once the user approved it, and a
    rule without her number promotes nothing. Un-approving it removes the limit."""
    p = copy.deepcopy(raw["amira"])
    for r in _parsed(p):
        r["approved_by_user"] = False
    assert not _ruled(p, PORTFOLIO_VALUES["amira"])["limits"].get("max_theme_weight")
    q = copy.deepcopy(raw["amira"])
    for r in _parsed(q):
        r["params"].pop("max_pct", None)
    assert not _ruled(q, PORTFOLIO_VALUES["amira"])["limits"].get("max_theme_weight")


def test_mateo_excluded_activities_hold_both_controversy_tags_and_no_theme_limit(ruled, raw):
    """§16 + §7.2: excluded_activities = union of values.*.exclusions + approved exclude_activity rules, so it holds
    both controversy tags; the stored trace lists the exclusion paths and the approval path."""
    lim = ruled["mateo"]["limits"]
    assert {ENV_TAG, SOC_TAG} <= set(lim["excluded_activities"])
    approved_tags = {r["params"]["tag"] for r in _parsed(raw["mateo"])
                     if r["rule_kind"] == "exclude_activity" and r["approved_by_user"]}
    assert set(lim["excluded_activities"]) == set(_declared_exclusions(raw["mateo"])) | approved_tags
    trace = ruled["mateo"]["limits_trace"]["excluded_activities"]
    assert "values.environment.exclusions" in trace and "values.social.exclusions" in trace
    assert APPROVED_PATH in trace
    assert not lim.get("max_theme_weight")                                     # no theme rule in his notes


def test_mateo_parsed_rules_are_unapproved_except_where_the_user_declared_them(raw):
    """§6.2 N3 + §16: parsed rules start approved_by_user false. The only ones that may be true are the
    exclude_activity rules whose tag the user declared in values.*.exclusions; `avoid_attribute speculative` is a
    soft reminder (S4) and stays false. The exclusions come from the declared values, not from the LLM (inv. 2)."""
    rules = _parsed(raw["mateo"])
    declared = set(_declared_exclusions(raw["mateo"]))
    for r in rules:
        if r["approved_by_user"]:
            assert r["rule_kind"] == "exclude_activity" and r["params"]["tag"] in declared, r
    spec = [r for r in rules if r["rule_kind"] == "avoid_attribute" and r["params"].get("attribute") == "speculative"]
    assert len(spec) == 1 and spec[0]["approved_by_user"] is False
    # un-approving everything leaves the declared exclusions in the limits
    p = copy.deepcopy(raw["mateo"])
    for r in _parsed(p):
        r["approved_by_user"] = False
    lim = _ruled(p, PORTFOLIO_VALUES["mateo"])["limits"]
    assert {ENV_TAG, SOC_TAG} <= set(lim["excluded_activities"])


# ---------------------------------------------------------------- N3 values_parser: hand-written mock entries (§6.2 N3, §16)

def _n3_context(name: str, entry: dict) -> dict:
    return {"event_ids": set(), "notes": NOTES[name], "model_id": entry["model_id"],
            "prompt_version": entry["prompt_version"], "input_hash": entry["input_hash"]}


@pytest.mark.parametrize("name", PERSONAS)
def test_n3_mock_entry_is_hand_written_schema_valid_exact_quotes_and_gate_accepted(name):
    """§16 llm_cache + §6.2 N3 + §11: the N3 entry for the persona's notes exists (model fixture:hand-written), its
    input is exactly the notes, the mock call returns it, it validates against schemas/values_parser.json, every
    quote is an exact substring of the notes, and the gate accepts it with every rule starting unapproved."""
    notes = NOTES[name]
    payload = extractors.n3_payload(notes)
    assert payload == {"notes": notes}                                         # the notes only, no profile field
    entry = _cache_entry(N3, payload)
    assert entry["model_id"] == MOCK_MODEL and entry["node"] == N3
    assert entry["user_payload"] == payload
    call = extractors.values_parser_call(notes)                                # mock mode: raises LLMCacheMiss on a miss
    assert call["output"] == entry["output"]
    assert call["call_log"]["cache_hit"] is True and call["call_log"]["model_id"] == MOCK_MODEL
    Draft202012Validator(extractors.load_schema(N3)).validate(call["output"])
    assert call["output"]["rules"], "the notes contain rules to parse"
    assert all(r["quote"] in notes for r in call["output"]["rules"])           # exact substrings
    new, status = gate.gate(N3, None, call["output"], _n3_context(name, entry))
    assert status == "accepted"
    assert new["by"] == "llm:values_parser" and new["model_id"] == MOCK_MODEL
    assert all(r["approved_by_user"] is False for r in new["value"])           # §6.2: parsed rules start unapproved


def test_n3_amira_entry_parses_a_tech_theme_rule_with_no_number():
    """§16 amira/ + §20.6: N3 parses `max_theme_weight {theme: tech_heavy}` and gives NO max_pct (she never said a
    number; the intake form asks for it)."""
    rules = extractors.values_parser(NOTES["amira"])["rules"]
    theme = [r for r in rules if r["rule_kind"] == "max_theme_weight"]
    assert len(theme) == 1
    assert theme[0]["params"] == {"theme": "tech_heavy"}                       # no max_pct
    assert theme[0]["quote"] in NOTES["amira"]


def test_n3_mateo_entry_excludes_both_controversy_tags_and_avoids_speculative():
    """§16 mateo/: N3 -> exclude_activity for both controversy tags plus avoid_attribute speculative."""
    rules = extractors.values_parser(NOTES["mateo"])["rules"]
    assert {r["params"]["tag"] for r in rules if r["rule_kind"] == "exclude_activity"} == {ENV_TAG, SOC_TAG}
    avoid = [r for r in rules if r["rule_kind"] == "avoid_attribute"]
    assert [r["params"].get("attribute") for r in avoid] == ["speculative"]
    assert not [r for r in rules if r["rule_kind"] == "max_theme_weight"]
    source = (ROOT / "scripts" / "handwrite_llm_cache.py").read_text(encoding="utf-8")
    assert "mateo" in source.lower()                                           # written through the script (P1.1)


@pytest.mark.parametrize("name", PERSONAS)
def test_profile_parsed_notes_are_the_n3_output_plus_the_users_approvals(name, raw):
    """§3.3 item 4 + §6.2 N3: the fixture's values.parsed_notes holds exactly the N3 rules (same kind, applies_to,
    exact quote; params may only gain the user's number) with `approved_by_user` booleans; and the notes count as
    unchanged, so a refresh does not re-run N3 and drop the user's approvals."""
    profile = raw[name]
    cached = extractors.values_parser(NOTES[name])["rules"]
    parsed = _parsed(profile)
    assert len(parsed) == len(cached)
    for c in cached:
        twins = [r for r in parsed if (r["rule_kind"], r["applies_to"], r["quote"]) ==
                 (c["rule_kind"], c["applies_to"], c["quote"]) and c["params"].items() <= r["params"].items()]
        assert len(twins) == 1, c
        assert type(twins[0]["approved_by_user"]) is bool
    node = profile["values"]["parsed_notes"]
    assert node["by"] == "llm:values_parser" and node["prompt_version"] == extractors.prompt_version(N3)
    assert node["model_id"] == MOCK_MODEL and 0 <= node["confidence"] <= 1
    assert pipeline.notes_changed(profile) is False


@pytest.mark.parametrize("name", PERSONAS)
def test_refresh_keeps_the_approved_rules_and_does_not_call_n3(name, raw, ruled, tmp_path, monkeypatch):
    """§4 Flow A + D7 point 1: refreshing an unchanged profile calls no LLM node, and the limits are those of the
    rules (Amira: max_theme_weight {tech_heavy: 0.50}; Mateo: both controversy tags)."""
    monkeypatch.chdir(tmp_path)
    nodes: list[str] = []
    real = bedrock.call_tool

    def spy(node, schema, system, user_payload, prompt_version):
        nodes.append(node)
        return real(node, schema, system, user_payload, prompt_version)

    monkeypatch.setattr(bedrock, "call_tool", spy)
    out = pipeline.refresh(_dir(name))
    p = out.get("profile", out)
    assert N3 not in nodes
    assert p["limits"] == ruled[name]["limits"] and p["limits_trace"] == ruled[name]["limits_trace"]
    assert _parsed(p) == _parsed(raw[name])                                    # approvals untouched
    if name == "amira":
        assert p["limits"]["max_theme_weight"] == {"tech_heavy": pytest.approx(0.50, abs=1e-9)}
        assert APPROVED_PATH in p["limits_trace"]["max_theme_weight"]
    else:
        assert {ENV_TAG, SOC_TAG} <= set(p["limits"]["excluded_activities"])


# ---------------------------------------------------------------- events.jsonl (§3.4, §16)

@pytest.mark.parametrize("name", PERSONAS)
def test_events_are_wellformed_dated_up_to_as_of_and_include_a_quiz_result(name, raw):
    """§3.4: one JSON object per line with the §3.4 keys, ids ev_<persona>_0001.. unique and in order, valid type,
    tz-aware non-decreasing timestamps, all dated <= 2026-11-12; a quiz_result whose score is the profile's quiz_score."""
    events = _events(name)
    assert events
    ids = [e["event_id"] for e in events]
    assert len(set(ids)) == len(ids)
    assert all(re.fullmatch(rf"ev_{name}_\d{{4}}", i) for i in ids), ids
    # SPEC-AMBIGUITY: §16 writes "ev_<persona>_0001.."; stricter reading = numbering starts at 0001 and increases
    # in file order (Robert's skips numbers, so gaps are allowed).
    assert ids[0] == f"ev_{name}_0001" and ids == sorted(ids)
    stamps = []
    for e in events:
        assert EVENT_KEYS <= set(e), e
        assert e["profile_id"] == PROFILE_IDS[name]
        assert e["type"] in EVENT_TYPES
        assert isinstance(e["source"], str) and e["source"]
        ts = datetime.fromisoformat(e["ts"])
        assert ts.tzinfo is not None, f"ts must carry a UTC offset: {e['ts']}"
        assert e["ts"][:10] <= AS_OF, f"{e['event_id']} is dated after portfolio.as_of"
        stamps.append(ts)
        if e["type"] in ("question", "order_intent", "note"):
            assert isinstance(e["text"], str) and e["text"].strip()
    assert stamps == sorted(stamps)                                            # append-only log
    quizzes = [e for e in events if e["type"] == "quiz_result"]
    assert quizzes
    for q in quizzes:
        assert set(q["payload"]) == {"score", "quiz_id"} and isinstance(q["payload"]["quiz_id"], str)
        assert 0 <= q["payload"]["score"] <= 1
    assert raw[name]["knowledge"]["quiz_score"] == quizzes[-1]["payload"]["score"]


# SPEC-AMBIGUITY: §16 describes the event content in words ("long-term framing", "crypto FOMO", "ESG / controversies").
# The classifiers below are deliberately broad keyword tests: they pin the intent of the fixture, not its wording.
LONG_TERM = re.compile(r"long[- ]?term|long run|decade|retire|horizon|compound|years? from now|"
                       r"\b\d{1,2}\+?\s*(?:-\s*)?years?\b|next\s+(?:\w+\s+)?years|for the long|buy and hold|"
                       r"\b20[3-9]\d\b", re.I)
CRYPTO_WORD = re.compile(r"bitcoin|btc|ethereum|\beth\b|crypto|doge|altcoin|\bcoins?\b", re.I)
FOMO_CUE = re.compile(r"everyone|everybody|miss(?:ed|ing)?\b|fomo|too late|rall(?:y|ies)|pump|moon|skyrocket|surg|"
                      r"all[- ]time|explod|going up|buy now|jump|hype|friends|getting rich|\bviral\b|trending", re.I)
ESG_WORD = re.compile(r"esg|controvers|environment|social|sustainab|ethic|scandal|green|emission|fossil|oil\b|"
                      r"climate|governance|pollut|lawsuit|labou?r|human rights|responsib|exclu", re.I)


def _questions(name: str) -> list[dict]:
    return [e for e in _events(name) if e["type"] == "question"]


def test_amira_events_show_long_term_framing_and_one_crypto_fomo_question():
    """§16 amira/: a quiz_result, >= 2 questions framed on the long term, and a crypto FOMO question (one or more,
    distinct from the long-term ones)."""
    questions = _questions("amira")
    fomo = [q for q in questions if CRYPTO_WORD.search(q["text"]) and FOMO_CUE.search(q["text"])]
    assert fomo, "a crypto FOMO question is missing"
    long_term = [q for q in questions if LONG_TERM.search(q["text"]) and q not in fomo]
    assert len(long_term) >= 2, [q["text"] for q in questions]
    assert any(e["type"] == "quiz_result" for e in _events("amira"))


def test_mateo_events_show_a_quiz_result_and_two_esg_or_controversy_questions():
    """§16 mateo/: a quiz_result and >= 2 questions about ESG / controversies."""
    esg = [q for q in _questions("mateo") if ESG_WORD.search(q["text"])]
    assert len(esg) >= 2, [q["text"] for q in _questions("mateo")]
    assert any(e["type"] == "quiz_result" for e in _events("mateo"))


# ---------------------------------------------------------------- N6 profile_consistency: hand-written mock entries (§20.9)

@pytest.mark.parametrize("name", PERSONAS)
def test_n6_mock_entry_exists_for_the_refreshed_profile_and_is_gate_valid(name, ruled):
    """§20.9 + §16: the N6 entry is keyed by the payload the pipeline builds (declared fields + notes + rule hits of
    the refreshed profile); it is hand-written, schema-valid, does not abstain, and the gate accepts it (declared
    paths, one quote per path equal to its value, reason <= 200 chars without digits). Rules never see `violation`."""
    profile = ruled[name]
    payload = internal.n6_payload(profile, internal.rule_findings(profile, AS_OF))
    entry = _cache_entry(N6, payload)
    assert entry["model_id"] == MOCK_MODEL and entry["node"] == N6
    call = extractors.profile_consistency_call(payload)                       # LLMCacheMiss here = no recording
    assert call["call_log"]["cache_hit"] is True and call["call_log"]["model_id"] == MOCK_MODEL
    out = call["output"]
    Draft202012Validator(extractors.load_schema(N6)).validate(out)
    assert out["abstain"] is False
    assert len(out["findings"]) <= 5
    context = {"event_ids": set(), "notes": profile["values"]["notes"], "profile": profile,
               "model_id": call["call_log"]["model_id"], "prompt_version": extractors.prompt_version(N6),
               "input_hash": call["call_log"]["input_hash"]}
    new, status = gate.gate(N6, None, out, context)
    assert status == "accepted"
    assert new["by"] == "llm:profile_consistency"
    for f in out["findings"]:
        assert f["kind"] in ("tension", "contradiction")                       # -> info | warning, never violation


# ---------------------------------------------------------------- hard.run_cheap on the committed weights (§7.3)

def _cheap(name: str, ruled: dict, assets) -> list[dict]:
    pf = data.load_portfolio(_dir(name) / "portfolio.json", assets)
    return hard.run_cheap(pf["weights"], ruled[name], assets, {})              # meta={}: no look-through, no tags


def test_amira_cheap_checks_h2_violation_h6_per_the_starter_sectors(ruled, assets):
    """§15 test_hard + §16: Amira's crypto 0.15 > 0.10 at `high` -> H2 violation. H6 (employer sector Information
    Technology): NVDA 0.08 + SHOP.TO 0.08 are the equities in that sector in the starter universe = 0.16 > 0.15 ->
    violation; the equity ETFs are unverifiable without look-through -> an extra `unknown`. Verdict `incoherent`."""
    checks = _cheap("amira", ruled, assets)
    h2 = [c for c in checks if c["id"] == "H2"]
    assert len(h2) == 1 and h2[0]["status"] == "violation" and h2[0]["kind"] == "hard"
    assert h2[0]["value"] == pytest.approx(0.15, abs=1e-9)
    assert h2[0]["limit"] == pytest.approx(0.10, abs=1e-9)
    # what the starter data gives for the employer sector
    w = data.load_portfolio(_dir("amira") / "portfolio.json", assets)["weights"]
    it = {t: v for t, v in w.items() if assets.loc[t, "asset_class"] == "equity"
          and assets.loc[t, "sector"] == "Information Technology"}
    assert set(it) == {"NVDA", "SHOP.TO"}
    assert sum(it.values()) == pytest.approx(0.16, abs=1e-9)
    h6 = [c for c in checks if c["id"] == "H6"]
    direct = [c for c in h6 if c["status"] != "unknown"]
    assert len(direct) == 1
    assert direct[0]["subject"] == "Information Technology"
    assert direct[0]["value"] == pytest.approx(0.16, abs=1e-9)
    assert direct[0]["limit"] == pytest.approx(0.15, abs=1e-9)
    assert direct[0]["status"] == "violation"                                  # 0.16 > 0.15
    assert [c for c in h6 if c["status"] == "unknown"], "ETF part of H6 must be reported unknown (no look-through)"
    assert report_mod.verdict(checks) == "incoherent"


def test_amira_other_cheap_checks_follow_from_the_data_and_her_limits(ruled, assets):
    """§7.3: H1 pass (crypto is not refused); H3 pass for NVDA and SHOP.TO (0.08 <= 0.15); H4 pass (0.10 >= 0.00);
    H5 = the non-CAD weight computed from the universe, compared with the `high` cap; only hard checks, statuses
    from the hard set (inv. 3)."""
    checks = _cheap("amira", ruled, assets)
    by = lambda cid: [c for c in checks if c["id"] == cid]  # noqa: E731
    assert [c["status"] for c in by("H1")] == ["pass"]
    h3 = {c["subject"]: c for c in by("H3")}
    assert {"NVDA", "SHOP.TO"} <= set(h3)
    assert all(c["status"] == "pass" and c["limit"] == pytest.approx(0.15, abs=1e-9) for c in h3.values())
    assert h3["NVDA"]["value"] == pytest.approx(0.08, abs=1e-9)
    h4 = by("H4")
    assert len(h4) == 1 and h4[0]["status"] == "pass"
    assert h4[0]["value"] == pytest.approx(0.10, abs=1e-9) and h4[0]["limit"] == pytest.approx(0.0, abs=1e-9)
    w = data.load_portfolio(_dir("amira") / "portfolio.json", assets)["weights"]
    foreign = sum(v for t, v in w.items() if assets.loc[t, "currency"] != "CAD")
    h5 = by("H5")
    assert len(h5) == 1 and h5[0]["value"] == pytest.approx(foreign, abs=1e-9)
    assert h5[0]["limit"] == pytest.approx(0.70, abs=1e-9)
    assert h5[0]["status"] == ("violation" if foreign > 0.70 + 1e-9 else "pass")
    assert {c["status"] for c in checks} <= {"pass", "violation", "info", "unknown"}


def test_mateo_cheap_checks_h1_to_h5_pass_at_moderate(ruled, assets):
    """§7.2 `moderate` row on the §3.5 Mateo portfolio: no crypto (<= 0.02), each single stock 0.05 <= 0.10, fixed
    income XBB.TO 0.20 + AGG 0.05 = 0.25 >= 0.20, foreign currency 0.45 <= 0.50. No violation among H1-H5."""
    checks = _cheap("mateo", ruled, assets)
    for cid in ("H1", "H2", "H3", "H4", "H5"):
        got = [c for c in checks if c["id"] == cid]
        assert got and all(c["status"] == "pass" for c in got), (cid, got)
    h2 = next(c for c in checks if c["id"] == "H2")
    assert h2["value"] == pytest.approx(0.0, abs=1e-9) and h2["limit"] == pytest.approx(0.02, abs=1e-9)
    h4 = next(c for c in checks if c["id"] == "H4")
    assert h4["value"] == pytest.approx(0.25, abs=1e-9) and h4["limit"] == pytest.approx(0.20, abs=1e-9)
    h5 = next(c for c in checks if c["id"] == "H5")
    assert h5["value"] == pytest.approx(0.45, abs=1e-9) and h5["limit"] == pytest.approx(0.50, abs=1e-9)


# ---------------------------------------------------------------- Flow C review end-to-end (§4, §13, D3, D7, §20.9)

_FIXTURE_SNAPSHOT = {p: p.read_bytes() for n in PERSONAS for p in sorted(_dir(n).glob("*")) if p.is_file()}
ALLOWED_NODES = {"explainer", N3, N6}                                         # D7: no N1 / N2 / N4


@pytest.fixture(scope="module")
def reviews(tmp_path_factory):
    """`pipeline.review(fixtures/<persona>)` in mock mode, in a temp working directory (the store writes there)."""
    mp = pytest.MonkeyPatch()
    mp.chdir(tmp_path_factory.mktemp("personas"))
    try:
        out = {}
        for name in PERSONAS:
            rep = pipeline.review(_dir(name))
            out[name] = {"report": rep, "audit": store.load_audit(rep["run_id"])}
    finally:
        mp.undo()
    return out


@pytest.fixture(scope="module")
def report_schema() -> dict:
    schema = json.loads((config.SCHEMAS_DIR / "report.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


@pytest.mark.parametrize("name", PERSONAS)
def test_review_returns_a_schema_valid_flow_c_report_with_profile_coherence(name, reviews, report_schema):
    """§4 Flow C + §13 + §20.9: the report validates against schemas/report.json; flow C, no intent, no proposed
    side; `profile_coherence` is present (status pass|info|warning, never violation) and separate from the verdict."""
    rep = reviews[name]["report"]
    errors = [e.message for e in Draft202012Validator(report_schema).iter_errors(rep)]
    assert errors == []
    assert rep["flow"] == "C"
    assert rep["profile_id"] == PROFILE_IDS[name]
    assert rep["portfolio_id"] == _read(name, "portfolio.json")["portfolio_id"]
    assert rep["intent"] is None and rep["checks_proposed"] is None
    pc = rep["profile_coherence"]
    assert pc["status"] in ("pass", "info", "warning")
    assert isinstance(pc["findings"], list)
    worst = max((f["status"] for f in pc["findings"]), key=("pass", "info", "warning").index, default="pass")
    assert pc["status"] == worst                                               # §20.9: worst finding status or pass
    locale = _read(name, "profile.json")["meta"].get("locale", "fr-CA")
    assert rep["disclaimer"] == config.DISCLAIMER[locale]                     # SPEC §9.4 fixed text, not LLM-written


def test_amira_review_is_incoherent_because_crypto_exceeds_the_high_tier_cap(reviews):
    """§4 persona matrix: \"high risk != anything goes\". Verdict incoherent; H2 violation 0.15 vs 0.10 is in the
    report and sits in the contraintes section, which is therefore `violation`."""
    rep = reviews["amira"]["report"]
    assert rep["verdict"] == "incoherent"
    h2 = [c for c in rep["checks_current"] if c["id"] == "H2"]
    assert len(h2) == 1 and h2[0]["status"] == "violation"
    assert h2[0]["value"] == pytest.approx(0.15, abs=1e-9) and h2[0]["limit"] == pytest.approx(0.10, abs=1e-9)
    sections = {s["factor"]: s for s in rep["sections"]}
    assert sections["contraintes"]["status"] == "violation" and "H2" in sections["contraintes"]["checks"]
    assert reviews["amira"]["audit"]["verdict"] == "incoherent"


def test_mateo_review_verdict_is_one_of_the_three_and_unknowns_never_change_it(reviews):
    """D3 + §7.5: Mateo's verdict is one of the three; it equals the verdict derived from the checks and from the
    sections; `unknown` checks are listed in `unknowns` and never move the verdict (only unknowns -> coherent)."""
    rep = reviews["mateo"]["report"]
    assert rep["verdict"] in VERDICTS
    assert rep["verdict"] == report_mod.verdict(rep["checks_current"]) == report_mod.sections_verdict(rep["sections"])
    statuses = {c["status"] for c in rep["checks_current"]}
    if not statuses & {"violation", "warning"}:
        assert rep["verdict"] == "coherent"
    assert [u["id"] for u in rep["unknowns"]] == [c["id"] for c in rep["checks_current"] if c["status"] == "unknown"]
    assert reviews["mateo"]["audit"]["verdict"] == rep["verdict"]


@pytest.mark.parametrize("name", PERSONAS)
def test_audit_has_no_n3_or_n6_cache_miss_and_n6_passed_the_gate(name, reviews):
    """§10 + §16 llm_cache: no LLMCacheMiss for N3 / N6 in the audit (an explainer miss falls back to the template,
    which is fine); the N6 call is in `llm_calls` as a cache hit accepted by the gate; no N1 / N2 / N4 (D7)."""
    audit = reviews[name]["audit"]
    assert audit["flow"] == "C" and audit["profile_id"] == PROFILE_IDS[name]
    assert [e for e in audit["llm_errors"] if e["node"] in (N3, N6)] == []
    nodes = {c["node"] for c in audit["llm_calls"]}
    assert nodes <= ALLOWED_NODES, nodes
    n6 = [c for c in audit["llm_calls"] if c["node"] == N6]
    assert len(n6) == 1 and n6[0]["cache_hit"] is True and n6[0]["gate"] == "accepted"
    assert n6[0]["model_id"] == MOCK_MODEL
    assert AS_OF in json.dumps(audit)                                          # D1: the reference date used


@pytest.mark.parametrize("name", PERSONAS)
def test_review_leaves_the_persona_fixtures_unchanged(name, reviews):
    """Flow C reads fixtures and writes versions to the store only (§10): no fixture file is rewritten or added."""
    now = {p: p.read_bytes() for p in sorted(_dir(name).glob("*")) if p.is_file()}
    assert now == {p: b for p, b in _FIXTURE_SNAPSHOT.items() if p.parent == _dir(name)}


def test_demo_now_reviews_three_complete_personas(tmp_path, monkeypatch, report_schema):
    """D7 acceptance + §15 test_pipeline_offline: `cli demo` finds robert, amira and mateo complete, writes a
    schema-valid Flow C report for each (no \"incomplete\" notice), and Amira's verdict is incoherent."""
    monkeypatch.chdir(tmp_path)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(["demo"])
    text = out.getvalue()
    assert "incomplete" not in text.lower()
    reports = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((tmp_path / "reports").glob("*.json"))]
    assert {r["profile_id"] for r in reports} == {"persona-robert", "persona-amira", "persona-mateo"}
    validator = Draft202012Validator(report_schema)
    for r in reports:
        assert validator.is_valid(r) and r["flow"] == "C" and r["verdict"] in VERDICTS
        assert r["verdict"] in text                                            # §12: the demo prints verdicts
    amira = [r for r in reports if r["profile_id"] == "persona-amira"]
    assert [r["verdict"] for r in amira] == ["incoherent"]
    assert config.DISCLAIMER["fr-CA"] in text


# ---------------------------------------------------------------- README Assumptions (CLAUDE.md conventions)

def _assumptions() -> str:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    m = re.search(r"^## Assumptions\s*$(.*?)(?=^## )", text, re.S | re.M)
    assert m, "README has no '## Assumptions' section"
    return m.group(1)


def _squash(s: str) -> str:
    s = s.lower().replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"[\s\u00a0\u202f,$]", "", s)


def _figures(profile: dict) -> list:
    """Every fictional money figure of a profile: expenses, emergency fund, contribution, debts, other assets,
    planned withdrawals, goal amounts (numbers >= 100) and the income band."""
    sit, obj = profile["situation"], profile["objectives"]
    out: list = [sit["annual_income_band"]]
    nums = [sit["monthly_expenses"], sit["emergency_fund"], sit.get("monthly_contribution")]
    nums += list((sit.get("debt") or {}).values()) + list((sit.get("other_assets") or {}).values())
    nums += [w["amount"] for w in sit.get("planned_withdrawals") or []]
    nums += [g["amount"] for g in obj.get("goals") or [] if "amount" in g]
    return out + [n for n in nums if isinstance(n, (int, float)) and not isinstance(n, bool) and n >= 100]


def test_readme_assumptions_list_the_fictional_figures_of_amira_and_mateo(raw):
    """CLAUDE.md conventions + SPEC §16: every fictional figure (income band, expenses, emergency fund, goals,
    withdrawals, ...) is listed in README -> Assumptions, which also names both personas."""
    section = _assumptions()
    assert "Amira" in section and "Mateo" in section
    squashed = _squash(section)
    for name in PERSONAS:
        for fig in _figures(raw[name]):
            token = _squash(fig) if isinstance(fig, str) else str(int(fig)) if float(fig).is_integer() else str(fig)
            assert token in squashed, f"{name}: fictional figure {fig!r} is not in README -> Assumptions"
