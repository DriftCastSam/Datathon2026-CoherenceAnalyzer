"""P5.INTAKEFULL: the intake asks every user-declared input the rules and limits read.

Sources: docs/spec-factors.md §20.3 (`REQUIRED` per factor module), §20.2 (which factor owns which field), §20.6
(intake loop: fixed template questions, skip = `{value: null, by: user:skipped}`), SPEC §3.1 (profile paths), §7.1
(derived fields), §7.2 (limits from `rule:` fields and declared values only), §1 inv. 1 and 2, docs/spec-decisions.md
D3 (unknowns never change the verdict), D7 point 1 (intake asks, no LLM writes a question), D10 (describe, never
prescribe: question wording).

Bug pinned (P5.USERS end-to-end finding): a user created with POST /profiles who answers every intake question and
sets a portfolio still got `risk.capacity`, `risk.effective` and `identity.life_stage` null, so every table limit was
null. `rules/registry.REGISTRY` and `rules/limits.LIMIT_INPUTS` read `identity.age`, `identity.employment_type` and
`knowledge.declared_level`, which `intake.REQUIRED` never asked.

# STALE-ORACLE: tests/profile/test_skipped_fields.py::test_the_checklist_has_the_15_required_paths_and_every_path_is_
#   covered_by_the_tables_below pins `len(REQUIRED_PATHS) == 15` (docs/spec-factors.md §20.6 fixes no list; the owner
#   request of 2026-11-14 says the app must work for any user).
# STALE-ORACLE: tests/flows/test_new_user_no_portfolio.py::_answer_all asserts `set(ANSWERS) == every REQUIRED path`
#   (15 answers); its SPEC-AMBIGUITY note already records that employment_type is outside REQUIRED.
# Both are named in .claude/loop/BLOCKED.md; this file keeps the spec-driven expectation.

Offline and deterministic: COHERENCE_ANALYZER_LLM=mock, no network, no clock (reference date = portfolio.as_of, D1).
"""
from __future__ import annotations

import copy
import json
import os
import re
import unicodedata
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

os.environ.setdefault("COHERENCE_ANALYZER_LLM", "mock")

from coherence_analyzer import config, explain, intake  # noqa: E402
from coherence_analyzer.rules import derive, limits as limits_mod, registry  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FACTORS = ("objectif", "horizon", "risque", "contraintes", "preferences")
LOCALES = ("fr-CA", "en-CA")
PERSONAS = ("robert", "amira", "mateo")
AS_OF = "2026-11-12"          # portfolio.as_of (D1)
VALUE = 120_000               # an intake-only user's portfolio.value_cad
PILLARS = ("environment", "social", "governance")

# The 15 paths the checklist asked before P5.INTAKEFULL (used only to tell new questions from old ones).
OLD_REQUIRED = {
    "objectives.primary", "objectives.goals", "objectives.horizon_years", "identity.retirement_target_age",
    "situation.planned_withdrawals", "risk.declared", "risk.max_acceptable_drawdown_pct",
    "risk.reaction_to_20pct_drop", "situation.emergency_fund", "situation.monthly_expenses",
    "identity.employer_sector", "preferences.asset_types_refused", "preferences.prefers_dividends",
    "preferences.prefers_large_caps", "preferences.home_bias_ok",
}

# Declared inputs that MUST be asked (their absence nulls risk.capacity / risk.effective / life_stage / knowledge.score),
# each under the factor that owns the derived field it feeds in docs/spec-factors.md §20.2:
#   identity.age -> identity.life_stage (objectif); identity.employment_type -> situation.income_stability and
#   knowledge.declared_level -> knowledge.score (both contraintes).
# SPEC-AMBIGUITY: §20.2 lists no factor for `identity.age` itself; the stricter reading is the factor that shows the
# field it feeds (life_stage, objectif).
MANDATORY = {
    "identity.age": "objectif",
    "identity.employment_type": "contraintes",
    "knowledge.declared_level": "contraintes",
}

# Declared inputs that may stay outside REQUIRED (the explicit OPTIONAL list the task asks for). Reason, one line each:
#   preferences.max_fee_mer_pct: an absent fee cap means "no cap"; limits only gains the key when it is declared.
#   values.<pillar>.exclusions: Amira and Mateo declare none for some pillars; an absent list means "no exclusion".
OPTIONAL = {"preferences.max_fee_mer_pct", *(f"values.{p}.exclusions" for p in PILLARS)}
# If one of these is asked anyway, §20.2 puts it under contraintes (exclusions are limits, ESG split).
OPTIONAL_OWNER = "contraintes"

# Not intake questions: fed by events (quiz result, N2) or by an llm: field (SPEC §3.3, §6.2).
EVENT_FED = {"knowledge.quiz_score", "knowledge.question_quality"}
LLM_FIELDS = {"risk.behavioural", "knowledge.question_quality", "values.parsed_notes"}

# Plausible answers (fictional user, 45, salaried). Used for every REQUIRED path.
ANSWERS: dict[str, object] = {
    "objectives.primary": "capital_preservation_with_growth",
    "objectives.goals": [{"name": "Retire at 65", "date": "2046-06", "amount": 300000, "priority": 1}],
    "objectives.horizon_years": 10,
    "identity.age": 45,
    "identity.retirement_target_age": 65,
    "identity.employment_type": "salaried",
    "situation.planned_withdrawals": [],
    "risk.declared": "moderate",
    "risk.max_acceptable_drawdown_pct": -15,
    "risk.reaction_to_20pct_drop": "sell_some",
    "situation.emergency_fund": 30000,
    "situation.monthly_expenses": 6500,
    "identity.employer_sector": "Health Care",
    "knowledge.declared_level": "intermediate",
    "preferences.asset_types_refused": [],
    "preferences.max_fee_mer_pct": 0.5,
    "preferences.prefers_dividends": True,
    "preferences.prefers_large_caps": False,
    "preferences.home_bias_ok": True,
    **{f"values.{p}.exclusions": [] for p in PILLARS},
}

# An empty profile v2, as POST /profiles builds it (api._new_user_profile): every checklist key present and null.
EMPTY_V2: dict = {
    "meta": {"profile_id": "user-9999", "schema_version": 2, "version": 1, "locale": "fr-CA",
             "updated_at": "2026-11-15"},
    "identity": {"age": None, "retirement_target_age": None, "employment_type": None, "employer_sector": None},
    "situation": {"monthly_expenses": None, "emergency_fund": None, "planned_withdrawals": None},
    "objectives": {"primary": None, "goals": None, "horizon_years": None},
    "risk": {"declared": None, "max_acceptable_drawdown_pct": None, "reaction_to_20pct_drop": None},
    "knowledge": {}, "values": {},
    "preferences": {"asset_types_refused": None, "prefers_dividends": None, "prefers_large_caps": None,
                    "home_bias_ok": None},
}

TABLE_LIMITS = ("vol_band_annual", "floor_at_horizon", "max_drawdown_pct", "min_fixed_income_weight",
                "max_single_stock_weight", "max_sector_risk_share", "max_crypto_weight",
                "max_foreign_currency_weight", "excluded_asset_types")


# ---------------------------------------------------------------- helpers

def _required_paths() -> list[str]:
    return [p for f in FACTORS for p in intake.REQUIRED[f]]


def _owner(path: str) -> str | None:
    return next((f for f in FACTORS if path in intake.REQUIRED[f]), None)


def _persona(name: str) -> dict:
    return json.loads((ROOT / "fixtures" / name / "profile.json").read_text(encoding="utf-8"))


def _portfolio(name: str) -> dict:
    return json.loads((ROOT / "fixtures" / name / "portfolio.json").read_text(encoding="utf-8"))


def _node(profile: dict, path: str):
    node = profile
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _answer(profile: dict, path: str) -> dict:
    assert path in ANSWERS, f"no plausible answer in the oracle for the new REQUIRED path {path!r}"
    return intake.answer(profile, path, ANSWERS[path])


def _intake_only(extra: tuple[str, ...] = ()) -> dict:
    """Empty v2 profile -> answer every REQUIRED path (factor order), plus `extra` paths."""
    p = copy.deepcopy(EMPTY_V2)
    for path in _required_paths():
        p = _answer(p, path)
    for path in extra:
        if path not in _required_paths():
            p = _answer(p, path)
    return p


def _declared_inputs() -> set[str]:
    """User-declared paths read by the rules and the limits (REGISTRY + LIMIT_INPUTS, expanded), without rule
    outputs, llm: fields, event-fed fields and the virtual approved-rules path."""
    outputs = set(registry.REGISTRY)
    found: set[str] = set()
    for r in registry.REGISTRY.values():
        found |= set(r.inputs)
    for inputs in limits_mod.LIMIT_INPUTS.values():
        for p in inputs:
            found.add(p)
            found |= set(registry.expand(p))
    found = {p for p in found if "[" not in p}                  # values.parsed_notes[approved_by_user]
    return found - outputs - LLM_FIELDS - EVENT_FED


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).casefold()


def _derive_limits(profile: dict) -> tuple[dict, dict, dict]:
    derived = derive.derive_all(profile, portfolio_value_cad=VALUE, as_of=AS_OF)
    limits, trace = limits_mod.build_limits(derived, VALUE)
    return derived, limits, trace


def _val(node):
    return node["value"] if isinstance(node, dict) and "value" in node else node


# ---------------------------------------------------------------- (2) the checklist covers the declared inputs

def test_declared_inputs_computed_from_the_registry_and_limit_inputs_include_the_three_missing_ones():
    # §7.1 / §7.2: the declared inputs are what the rules and limits read, minus rule outputs, llm: and event-fed paths.
    declared = _declared_inputs()
    assert set(MANDATORY) <= declared
    assert {"risk.declared", "objectives.horizon_years", "situation.planned_withdrawals", "situation.emergency_fund",
            "situation.monthly_expenses", "identity.retirement_target_age", "risk.max_acceptable_drawdown_pct",
            "preferences.asset_types_refused"} <= declared
    assert OPTIONAL <= declared
    assert not (declared & (EVENT_FED | LLM_FIELDS | set(registry.REGISTRY)))


@pytest.mark.parametrize("name", PERSONAS)
def test_declared_inputs_are_plain_user_values_in_the_persona_fixtures(name):
    # §3.3 convention: a declared input is a plain value (no `by`), never a rule: / llm: field (inv. 1, 2).
    profile = _persona(name)
    for path in sorted(_declared_inputs()):
        node = _node(profile, path)
        if node is None:        # Amira / Mateo leave some optional lists out
            assert path in OPTIONAL, (name, path)
            continue
        assert not (isinstance(node, dict) and "by" in node), (name, path)


def test_every_declared_input_is_in_required_or_in_the_explicit_optional_list():
    # Task (2): the checklist must cover every declared input, or the input is explicitly optional.
    required = set(_required_paths())
    uncovered = _declared_inputs() - required - OPTIONAL
    assert not uncovered, f"declared inputs the intake never asks and that are not listed OPTIONAL: {sorted(uncovered)}"


@pytest.mark.parametrize("path,factor", sorted(MANDATORY.items()))
def test_the_inputs_that_null_the_derived_fields_are_required_under_their_owning_factor(path, factor):
    # §20.2 / §20.3: each path lives under the factor that owns it, once.
    assert path in intake.REQUIRED[factor]
    assert _required_paths().count(path) == 1


def test_required_paths_stay_unique_dotted_and_user_declared_only():
    paths = _required_paths()
    assert len(paths) == len(set(paths))
    assert OLD_REQUIRED <= set(paths)                      # nothing that was asked is dropped
    for path in paths:
        assert re.match(r"^[a-z_][a-z_0-9]*(\.[a-z_][a-z_0-9]*)+$", path), path
        assert path not in registry.REGISTRY, path         # never a rule: output
        assert path not in LLM_FIELDS | EVENT_FED, path    # never an llm: or event-fed field


def test_an_optional_input_that_is_asked_belongs_to_contraintes():
    for path in OPTIONAL & set(_required_paths()):
        assert _owner(path) == OPTIONAL_OWNER, path


def test_optional_inputs_and_new_questions_are_documented_in_the_readme_intake_assumption():
    # Task (1): README states which optional inputs are asked ('none' = no constraint) and which stay optional.
    # SPEC-AMBIGUITY: the README block is the "Intake and N3 (P0.10)" assumption bullet; the stricter reading
    # requires the new paths and every optional input to be named inside it.
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    start = text.index("**Intake and N3 (P0.10):**")
    end = text.find("\n- **", start + 1)
    block = text[start:end if end != -1 else None]
    for path in MANDATORY:
        assert path in block, path
    assert "max_fee_mer_pct" in block
    assert "exclusions" in block
    assert re.search(r"optional|no constraint|aucune contrainte", block, re.IGNORECASE)


# ---------------------------------------------------------------- (1) fixed questions, no LLM, D10 wording

def test_every_required_path_has_a_fixed_non_empty_question_in_both_locales():
    for path in _required_paths():
        assert path in intake.QUESTIONS, path
        for loc in LOCALES:
            text = intake.QUESTIONS[path][loc]
            assert isinstance(text, str) and text.strip(), (path, loc)
        assert intake.QUESTIONS[path]["fr-CA"] != intake.QUESTIONS[path]["en-CA"], path
    texts = [intake.QUESTIONS[p][loc] for p in _required_paths() for loc in LOCALES]
    assert len(set(texts)) == len(texts)                   # one distinct question per path and locale


def test_new_questions_describe_and_never_prescribe_d10():
    # D10 / SPEC §9.3 item 3: the wide advisory blocklist (folded both sides) never matches a new question.
    new = [p for p in _required_paths() if p not in OLD_REQUIRED]
    assert set(MANDATORY) <= set(new)
    for path in new:
        for loc in LOCALES:
            low = _fold(intake.QUESTIONS[path][loc])
            hits = [b for b in explain.ADVISORY_BLOCKLIST if _fold(b) in low]
            assert not hits, (path, loc, hits)


@pytest.mark.parametrize("locale", LOCALES)
def test_next_question_on_an_empty_profile_is_the_fixed_text_of_the_first_required_path(locale):
    for factor in FACTORS:
        assert intake.missing(EMPTY_V2, factor) == list(intake.REQUIRED[factor])
        q = intake.next_question(EMPTY_V2, factor, locale)
        first = intake.REQUIRED[factor][0]
        assert q["path"] == first and q["text"] == intake.QUESTIONS[first][locale]
        assert q == intake.next_question(copy.deepcopy(EMPTY_V2), factor, locale)      # no LLM: same input, same text


def test_answering_each_required_path_in_order_empties_every_factor():
    p = copy.deepcopy(EMPTY_V2)
    for factor in FACTORS:
        for path in intake.REQUIRED[factor]:
            assert intake.next_question(p, factor)["path"] == path
            p = _answer(p, path)
        assert intake.missing(p, factor) == []
        assert intake.next_question(p, factor) is None


def test_a_none_answer_to_an_optional_list_question_is_an_answer_not_missing():
    # Task (1): an optional list input that is asked takes [] as "no constraint"; [] is an answer (§20.6), not missing.
    # SPEC-AMBIGUITY: the 'none' of preferences.max_fee_mer_pct (null or skip marker) is the implementer's call and
    # is not pinned here; a fee cap of 0 would mean "no product allowed", never "no constraint".
    for path in sorted(OPTIONAL & set(_required_paths())):
        if not path.endswith(".exclusions"):
            continue
        p = intake.answer(EMPTY_V2, path, [])
        assert path not in intake.missing(p, _owner(path)), path


# ---------------------------------------------------------------- (3) a profile built only from intake answers

def test_intake_only_profile_validates_against_the_profile_schema():
    schema = json.loads((config.SCHEMAS_DIR / "profile.v2.json").read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(_intake_only()))
    assert not errors, [e.message for e in errors]


def test_intake_only_profile_has_every_derived_field_set():
    # The bug: life_stage, risk.capacity and risk.effective were null for a user who answered the whole intake.
    p = _intake_only()
    for factor in FACTORS:
        assert intake.missing(p, factor) == []
    derived, _, _ = _derive_limits(p)
    # §7.1 rules on the answers above (age 45, retires at 65, salaried, 30000 / 6500 = 4.6 months, 10 years, no
    # withdrawal, declared moderate): life_stage accumulation, stability high, base capacity moderate_high, no notch.
    assert _val(derived["identity"]["life_stage"]) == "accumulation"
    assert _val(derived["situation"]["income_stability"]) == "high"
    assert _val(derived["situation"]["emergency_fund_months"]) == pytest.approx(4.6, abs=1e-9)
    assert _val(derived["objectives"]["effective_horizon_years"]) == pytest.approx(10.0, abs=1e-9)
    assert _val(derived["risk"]["capacity"]) == "moderate_high"
    assert _val(derived["risk"]["effective"]) == "moderate"
    for path in ("identity.life_stage", "situation.income_stability", "risk.capacity", "risk.effective"):
        node = _node(derived, path)
        assert node["by"].startswith("rule:") and node["value"] is not None, path


def test_intake_only_profile_gets_real_limits_and_no_null_limit():
    # §7.2: limits = LIMITS_TABLE[risk.effective] x declared values; nothing is null when nothing was skipped.
    _, limits, trace = _derive_limits(_intake_only())
    row = limits_mod.LIMITS_TABLE["moderate"]
    assert limits["vol_band_annual"] == list(row["vol_band"])
    assert limits["floor_at_horizon"] == pytest.approx(VALUE * row["floor_ratio"], abs=1e-9)
    assert limits["max_drawdown_pct"] == max(row["max_dd_pct"], -15)        # the less negative of table and declared
    assert limits["min_fixed_income_weight"] == row["min_fixed_income"]
    assert limits["max_single_stock_weight"] == row["max_single_stock"]
    assert limits["max_sector_risk_share"] == row["max_sector_risk_share"]
    assert limits["max_crypto_weight"] == row["max_crypto"]
    assert limits["max_foreign_currency_weight"] == row["max_foreign_ccy"]
    assert limits["excluded_asset_types"] == []
    assert limits["max_employer_sector_weight"] == limits_mod.MAX_EMPLOYER_SECTOR_WEIGHT
    nulls = [k for k, v in limits.items() if v is None]
    assert nulls == []
    for name, inputs in limits_mod.LIMIT_INPUTS.items():
        if not inputs:          # max_employer_sector_weight is a constant (§7.2): no input, empty trace
            continue
        assert trace[name], name
        assert not any("(skipped)" in t or "(not set)" in t for t in trace[name]), name
    assert "identity.employment_type" in trace["vol_band_annual"]            # the trace now reaches the new input


def test_intake_only_limits_follow_the_declared_answers_and_are_deterministic():
    # Same declared values, same limits: the intake path adds no hidden default (§7.2, §20.6). Declaring a higher
    # tolerance than the capacity (moderate_high from the 10-year horizon) is capped by the capacity (§7.1).
    p = _intake_only()
    assert _derive_limits(p)[1] == _derive_limits(copy.deepcopy(p))[1]
    bolder = intake.answer(p, "risk.declared", "high")
    derived, limits, _ = _derive_limits(bolder)
    assert _val(derived["risk"]["effective"]) == "moderate_high"
    assert limits["vol_band_annual"] == list(limits_mod.LIMITS_TABLE["moderate_high"]["vol_band"])


@pytest.mark.parametrize("path", sorted(set(ANSWERS)))
def test_a_skipped_input_nulls_only_the_limits_that_name_it_in_their_trace(path):
    # §20.6 + D3: a limit is null only when one of its inputs was skipped, and the trace names that input.
    base = _intake_only(extra=tuple(MANDATORY))
    skipped = intake.skip(base, path)
    derived, limits, trace = _derive_limits(skipped)                          # never raises
    for name in limits_mod.LIMIT_INPUTS:
        if name in limits and limits[name] is None:
            assert f"{path} (skipped)" in trace[name], (path, name)
    # the table limits are exactly the ones that need risk.effective: they are null iff the skipped path feeds it
    feeds_table = path in registry.expand("risk.effective") or path in {
        "risk.declared", "identity.employment_type"}
    for name in TABLE_LIMITS:
        if name in ("max_drawdown_pct", "excluded_asset_types"):
            continue
        assert (limits[name] is None) == feeds_table, (path, name)


@pytest.mark.parametrize("path", ["identity.age", "knowledge.declared_level"])
def test_age_and_declared_level_feed_no_limit(path):
    # §7.1: age feeds life_stage only, declared_level feeds knowledge.score only (S2): skipping either keeps every limit.
    base = _intake_only(extra=tuple(MANDATORY))
    full = _derive_limits(base)[1]
    skipped_derived, limits, trace = _derive_limits(intake.skip(base, path))
    assert limits == full
    assert not any(f"{path} (skipped)" in t for t in trace.values())
    if path == "identity.age":
        assert _val(skipped_derived["identity"]["life_stage"]) is None


def test_a_never_answered_employment_type_is_what_nulled_every_table_limit():
    # Regression of the finding: with employment_type unanswered the chain income_stability -> capacity -> effective
    # is null and so is every table limit; answering it (the intake now asks it) fixes all of them.
    base = _intake_only(extra=tuple(MANDATORY))
    unanswered = copy.deepcopy(base)
    unanswered["identity"]["employment_type"] = None
    derived, limits, _ = _derive_limits(unanswered)
    assert _val(derived["risk"]["capacity"]) is None and _val(derived["risk"]["effective"]) is None
    assert all(limits[n] is None for n in TABLE_LIMITS)
    assert intake.missing(unanswered, MANDATORY_FACTOR_OF["identity.employment_type"]) == [
        "identity.employment_type"]
    assert all(v is not None for v in _derive_limits(base)[1].values())


MANDATORY_FACTOR_OF = dict(MANDATORY)


# ---------------------------------------------------------------- (4) personas unchanged

@pytest.mark.parametrize("name", PERSONAS)
@pytest.mark.parametrize("factor", FACTORS)
def test_persona_intake_missing_stays_empty(name, factor):
    # The fixtures already hold the new declared fields; no persona is asked a new question.
    profile = _persona(name)
    assert intake.missing(profile, factor) == []
    assert intake.next_question(profile, factor) is None


@pytest.mark.parametrize("name", PERSONAS)
def test_persona_limits_and_trace_are_unchanged_by_the_new_checklist(name):
    # Reports are built from the stored profile; rebuilding derived fields + limits must give exactly what the
    # fixture stores (the golden the persona reports rest on), whatever intake.REQUIRED holds.
    profile, pf = _persona(name), _portfolio(name)
    derived = derive.derive_all(profile, portfolio_value_cad=pf["value_cad"], as_of=pf["as_of"])
    limits, trace = limits_mod.build_limits(derived, pf["value_cad"])
    assert limits == profile["limits"]
    assert trace == profile["limits_trace"]
    for path in ("identity.life_stage", "risk.capacity", "risk.effective"):
        assert _node(derived, path)["value"] is not None, path
        assert _node(derived, path)["value"] == _node(profile, path)["value"], path


@pytest.mark.parametrize("fid", FACTORS)
def test_factor_modules_expose_the_same_required_tuple_as_the_intake(fid):
    # §20.3: a factor module's REQUIRED is a view of the intake checklist, so new paths show up there too.
    import importlib
    module = importlib.import_module(f"coherence_analyzer.factors.{fid}")
    assert tuple(module.REQUIRED) == tuple(intake.REQUIRED[fid])
