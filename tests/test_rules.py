"""Derived fields + limits on the Robert fixture (SPEC §3.3, §7.1, §7.2, §15 test_rules).

The rules read the v1 Robert fixture for now (P0.3 converts it to schema v2).
"""
import copy
import json
from dataclasses import replace

import pytest

from coherence_analyzer.rules import derive, limits as limits_mod, registry

AS_OF = "2026-11-14"          # Robert's profile meta.updated_at / order_intent date; fixed, no clock reads
ROBERT_VALUE = 400000         # fixtures/robert/portfolio.json value_cad
META_KEYS = {"_note", "by"}   # descriptive keys of the example limits block, not limits themselves

EFFECTIVE_CHAIN = [
    "risk.effective", "risk.declared", "risk.capacity",
    "objectives.effective_horizon_years", "objectives.horizon_years", "situation.planned_withdrawals",
    "situation.emergency_fund_months", "situation.emergency_fund", "situation.monthly_expenses",
    "situation.income_stability", "identity.employment_type",
]

# SPEC §7.2 table (fractions; max DD in percent like risk.max_acceptable_drawdown_pct)
SPEC_TABLE = {
    "low":           dict(vol=(0.02, 0.06), dd=-8,  fi=0.50, ss=0.05, sector=0.25, crypto=0.00, fx=0.30, floor=0.95),
    "low_moderate":  dict(vol=(0.05, 0.09), dd=-12, fi=0.30, ss=0.08, sector=0.30, crypto=0.00, fx=0.40, floor=0.90),
    "moderate":      dict(vol=(0.08, 0.12), dd=-20, fi=0.20, ss=0.10, sector=0.35, crypto=0.02, fx=0.50, floor=0.85),
    "moderate_high": dict(vol=(0.11, 0.16), dd=-28, fi=0.10, ss=0.12, sector=0.40, crypto=0.05, fx=0.60, floor=0.75),
    "high":          dict(vol=(0.14, 0.22), dd=-40, fi=0.00, ss=0.15, sector=0.45, crypto=0.10, fx=0.70, floor=0.60),
}


@pytest.fixture()
def robert(root) -> dict:
    return json.loads((root / "fixtures" / "robert" / "profile.json").read_text())


def _derive(profile: dict, value: float = ROBERT_VALUE) -> dict:
    return derive.derive_all(profile, portfolio_value_cad=value, as_of=AS_OF)


def _limits(profile: dict, value: float = ROBERT_VALUE) -> tuple[dict, dict]:
    return limits_mod.build_limits(_derive(profile, value), value)


def _strip(block: dict) -> dict:
    return {k: v for k, v in block.items() if k not in META_KEYS}


def _get(profile: dict, path: str):
    node = profile
    for part in path.split("."):
        node = node[part]
    return node


def _val(profile: dict, path: str):
    node = _get(profile, path)
    return node["value"] if isinstance(node, dict) and "value" in node else node


# ---------------------------------------------------------------- Robert expectations (§15)

def test_robert_derived_fields(robert):
    d = _derive(robert)
    assert _val(d, "identity.life_stage") == "pre_retirement"
    assert _val(d, "situation.income_stability") == "high"
    assert _val(d, "situation.emergency_fund_months") == pytest.approx(4.6, abs=1e-9)
    assert _val(d, "objectives.effective_horizon_years") == pytest.approx(3.6, abs=1e-9)
    assert _val(d, "risk.capacity") == "low_moderate"
    assert _val(d, "risk.effective") == "low_moderate"
    assert _val(d, "knowledge.score") == pytest.approx(74, abs=1e-9)


def test_derived_fields_are_rule_objects(robert):
    """§1 invariant 1: computed fields are {value, by, confidence} with by = rule:<name>."""
    d = _derive(robert)
    for path in ["identity.life_stage", "situation.income_stability", "situation.emergency_fund_months",
                 "objectives.effective_horizon_years", "risk.capacity", "risk.effective", "knowledge.score"]:
        node = _get(d, path)
        assert isinstance(node, dict), path
        assert {"value", "by", "confidence"} <= set(node), path
        assert node["by"].startswith("rule:"), path
    # §7.1: knowledge.score mixes an llm: input -> rule: field with confidence < 1
    assert _get(d, "knowledge.score")["confidence"] < 1


def test_derive_all_is_pure(robert):
    before = copy.deepcopy(robert)
    _derive(robert)
    assert robert == before


def test_derive_all_is_deterministic(robert):
    assert _derive(copy.deepcopy(robert)) == _derive(copy.deepcopy(robert))


def test_robert_limits_equal_fixture_block(robert):
    lim, _ = _limits(robert)
    expected = _strip(robert["limits"])
    got = _strip(lim)
    assert set(got) == set(expected)
    for key, exp in expected.items():
        if key in ("excluded_asset_types", "excluded_activities"):
            # SPEC-AMBIGUITY: §7.2 defines these as set unions; order is not specified, so compare as sorted lists.
            assert sorted(got[key]) == sorted(exp), key
            assert len(got[key]) == len(set(got[key])), key
        elif key == "vol_band_annual":
            assert list(got[key]) == pytest.approx(exp, abs=1e-9)
        else:
            assert got[key] == pytest.approx(exp, abs=1e-9), key


# ---------------------------------------------------------------- §7.1 rules, unit level

@pytest.mark.parametrize("age,target,expected", [
    (34, 63, "early_accumulation"),
    (35, 63, "accumulation"),
    (54, 63, "accumulation"),
    (55, 63, "pre_retirement"),
    (59, 63, "pre_retirement"),
    (62, 63, "pre_retirement"),
    (63, 63, "retired"),
    (70, 63, "retired"),
    (50, 50, "accumulation"),     # age < 55 is checked before the retirement target
    (30, 30, "early_accumulation"),
])
def test_life_stage(age, target, expected):
    assert derive.life_stage(age, target) == expected


@pytest.mark.parametrize("employment,expected", [
    ("salaried", "high"),
    ("self_employed", "medium"),
    ("contract", "low"),
    ("commission", "low"),
    ("unemployed", "low"),
])
def test_income_stability(employment, expected):
    assert derive.income_stability(employment) == expected


def test_emergency_fund_months_one_decimal():
    assert derive.emergency_fund_months(30000, 6500) == pytest.approx(4.6, abs=1e-9)
    assert derive.emergency_fund_months(10000, 3000) == pytest.approx(3.3, abs=1e-9)
    assert derive.emergency_fund_months(0, 3000) == pytest.approx(0.0, abs=1e-9)


def test_effective_horizon_robert():
    # 2026-11 -> 2030-06 = 43 months -> 3.58 -> 3.6 (SPEC §7.1)
    w = [{"date": "2030-06", "amount": 60000}]
    assert derive.effective_horizon_years(4, w, AS_OF) == pytest.approx(3.6, abs=1e-9)


def test_effective_horizon_no_withdrawals_is_horizon():
    assert derive.effective_horizon_years(10, [], AS_OF) == pytest.approx(10, abs=1e-9)


def test_effective_horizon_withdrawal_after_horizon_is_horizon():
    w = [{"date": "2040-01", "amount": 10000}]
    assert derive.effective_horizon_years(4, w, AS_OF) == pytest.approx(4, abs=1e-9)


def test_effective_horizon_uses_earliest_withdrawal():
    # SPEC-AMBIGUITY: "first planned_withdrawal" read as earliest by date, not first in list order (stricter).
    w = [{"date": "2032-01", "amount": 40000}, {"date": "2030-06", "amount": 60000}]
    assert derive.effective_horizon_years(10, w, AS_OF) == pytest.approx(3.6, abs=1e-9)


def _cap(eh, ef=6.0, income="high", withdrawals=(), value=ROBERT_VALUE):
    return derive.capacity(effective_horizon_years=eh, ef_months=ef, income_stability=income,
                           planned_withdrawals=list(withdrawals), portfolio_value_cad=value, as_of=AS_OF)


# SPEC-AMBIGUITY: bands "3–5", "5–10", "10–15" share endpoints; read as half-open [lo, hi), consistent with
# "< 3 low" and ">= 15 high".
@pytest.mark.parametrize("eh,expected", [
    (0.0, "low"), (2.9, "low"),
    (3.0, "low_moderate"), (3.6, "low_moderate"), (4.9, "low_moderate"),
    (5.0, "moderate"), (9.9, "moderate"),
    (10.0, "moderate_high"), (14.9, "moderate_high"),
    (15.0, "high"), (30.0, "high"),
])
def test_capacity_base_from_horizon(eh, expected):
    assert _cap(eh) == expected


def test_capacity_robert():
    assert _cap(3.6, ef=4.6, income="high", withdrawals=[{"date": "2030-06", "amount": 60000}]) == "low_moderate"


def test_capacity_notch_low_emergency_fund():
    assert _cap(20, ef=2.9) == "moderate_high"
    assert _cap(20, ef=3.0) == "high"          # strict "< 3"


def test_capacity_notch_low_income_stability():
    assert _cap(20, income="low") == "moderate_high"
    assert _cap(20, income="medium") == "high"


def test_capacity_notch_near_withdrawals():
    near_big = [{"date": "2027-11", "amount": 50000}]     # 12 months away, 12.5% of 400k
    near_small = [{"date": "2027-11", "amount": 40000}]   # exactly 10% -> not "> 10%"
    far_big = [{"date": "2030-06", "amount": 200000}]     # 43 months away
    assert _cap(20, withdrawals=near_big) == "moderate_high"
    assert _cap(20, withdrawals=near_small) == "high"
    assert _cap(20, withdrawals=far_big) == "high"


def test_capacity_near_withdrawals_are_summed():
    # SPEC-AMBIGUITY: "withdrawals within 24 months > 10%" read as the sum of those withdrawals (stricter).
    two = [{"date": "2027-05", "amount": 24000}, {"date": "2028-05", "amount": 24000}]  # 6% + 6%
    assert _cap(20, withdrawals=two) == "moderate_high"


def test_capacity_notches_stack():
    near_big = [{"date": "2027-11", "amount": 50000}]
    assert _cap(20, ef=1.0, income="low", withdrawals=near_big) == "moderate"


def test_capacity_floor_is_low():
    near_big = [{"date": "2027-11", "amount": 50000}]
    assert _cap(1.0, ef=1.0, income="low", withdrawals=near_big) == "low"
    assert _cap(3.6, ef=1.0, income="low") == "low"


@pytest.mark.parametrize("declared,cap,expected", [
    ("high", "low_moderate", "low_moderate"),
    ("low", "low_moderate", "low"),
    ("moderate", "moderate", "moderate"),
    ("moderate_high", "high", "moderate_high"),
])
def test_effective_risk_is_min(declared, cap, expected):
    assert derive.effective_risk(declared, cap) == expected


def test_knowledge_score_robert_formula():
    # 0.4*0.8*100 + 0.3*78 + 0.3*0.62*100 = 32 + 23.4 + 18.6 = 74
    assert derive.knowledge_score(0.8, "advanced", 0.62, None) == pytest.approx(74, abs=1e-9)


@pytest.mark.parametrize("level,mapped", [("novice", 20), ("intermediate", 50), ("advanced", 78), ("expert", 95)])
def test_knowledge_score_declared_map(level, mapped):
    assert derive.knowledge_score(0.0, level, 0.0, None) == pytest.approx(0.3 * mapped, abs=1e-9)


# SPEC-AMBIGUITY: "previous run" = the profile's stored knowledge.score (74); quiz and declared are unchanged,
# so the question-quality term delta equals the score delta and is clamped to ±5 either way.
@pytest.mark.parametrize("qq,expected", [(1.0, 79), (0.0, 69), (0.65, 74.9)])
def test_knowledge_score_llm_term_clamped_to_5_points(robert, qq, expected):
    p = copy.deepcopy(robert)
    p["knowledge"]["question_quality"]["value"] = qq
    d = _derive(p)
    assert _val(d, "knowledge.score") == pytest.approx(expected, abs=1e-9)


def test_knowledge_score_history_appended(robert):
    # SPEC-AMBIGUITY: "Append to history" checked on an update that changes the score.
    p = copy.deepcopy(robert)
    p["knowledge"]["question_quality"]["value"] = 1.0
    before = len(p["knowledge"]["score"]["history"])
    hist = _get(_derive(p), "knowledge.score")["history"]
    assert len(hist) == before + 1
    assert hist[-1]["value"] == pytest.approx(79, abs=1e-9)


# ---------------------------------------------------------------- §7.2 limits

def _limits_at(robert: dict, level: str, value: float = ROBERT_VALUE) -> dict:
    p = _derive(robert, value)
    p["risk"]["effective"]["value"] = level
    p["risk"]["max_acceptable_drawdown_pct"] = -100   # let the table value bind
    lim, _ = limits_mod.build_limits(p, value)
    return lim


@pytest.mark.parametrize("level", list(SPEC_TABLE))
def test_limits_table_rows(robert, level):
    row = SPEC_TABLE[level]
    lim = _limits_at(robert, level)
    assert list(lim["vol_band_annual"]) == pytest.approx(list(row["vol"]), abs=1e-9)
    assert lim["max_drawdown_pct"] == pytest.approx(row["dd"], abs=1e-9)
    assert lim["min_fixed_income_weight"] == pytest.approx(row["fi"], abs=1e-9)
    assert lim["max_single_stock_weight"] == pytest.approx(row["ss"], abs=1e-9)
    assert lim["max_sector_risk_share"] == pytest.approx(row["sector"], abs=1e-9)
    assert lim["max_crypto_weight"] == pytest.approx(row["crypto"], abs=1e-9)
    assert lim["max_foreign_currency_weight"] == pytest.approx(row["fx"], abs=1e-9)
    assert lim["floor_at_horizon"] == pytest.approx(ROBERT_VALUE * row["floor"], abs=1e-6)
    assert lim["max_employer_sector_weight"] == pytest.approx(0.15, abs=1e-9)
    assert lim["max_fee_mer_pct"] == pytest.approx(0.5, abs=1e-9)


def test_limits_table_matches_spec():
    for level, row in SPEC_TABLE.items():
        t = limits_mod.LIMITS_TABLE[level]
        assert tuple(t["vol_band"]) == pytest.approx(row["vol"], abs=1e-9)
        assert t["max_dd_pct"] == row["dd"]
        assert t["min_fixed_income"] == pytest.approx(row["fi"], abs=1e-9)
        assert t["max_single_stock"] == pytest.approx(row["ss"], abs=1e-9)
        assert t["max_sector_risk_share"] == pytest.approx(row["sector"], abs=1e-9)
        assert t["max_crypto"] == pytest.approx(row["crypto"], abs=1e-9)
        assert t["max_foreign_ccy"] == pytest.approx(row["fx"], abs=1e-9)
        assert t["floor_ratio"] == pytest.approx(row["floor"], abs=1e-9)
    assert limits_mod.MAX_EMPLOYER_SECTOR_WEIGHT == pytest.approx(0.15, abs=1e-9)


def test_max_drawdown_is_less_negative_of_table_and_user(robert):
    p = copy.deepcopy(robert)
    p["risk"]["max_acceptable_drawdown_pct"] = -5
    assert _limits(p)[0]["max_drawdown_pct"] == pytest.approx(-5, abs=1e-9)
    p["risk"]["max_acceptable_drawdown_pct"] = -30
    assert _limits(p)[0]["max_drawdown_pct"] == pytest.approx(-12, abs=1e-9)


def test_floor_scales_with_portfolio_value(robert):
    assert _limits(robert)[0]["floor_at_horizon"] == pytest.approx(360000, abs=1e-6)
    assert _limits(robert, 100000)[0]["floor_at_horizon"] == pytest.approx(90000, abs=1e-6)


def test_crypto_excluded_when_cap_is_zero_even_if_not_refused(robert):
    p = copy.deepcopy(robert)
    p["preferences"]["asset_types_refused"] = ["options"]
    lim = _limits(p)   # effective low_moderate -> max_crypto 0
    assert sorted(lim["excluded_asset_types"]) == ["crypto", "options"]


def test_crypto_not_excluded_when_cap_positive_and_not_refused(robert):
    p = copy.deepcopy(robert)
    p["preferences"]["asset_types_refused"] = ["options"]
    lim = _limits_at(p, "moderate")   # max_crypto 0.02
    assert sorted(lim["excluded_asset_types"]) == ["options"]


def test_declared_above_capacity_does_not_loosen_limits(robert):
    base, _ = _limits(robert)
    p = copy.deepcopy(robert)
    p["risk"]["declared"] = "high"
    d = _derive(p)
    assert _val(d, "risk.effective") == "low_moderate"
    assert _strip(limits_mod.build_limits(d, ROBERT_VALUE)[0]) == _strip(base)


def test_low_emergency_fund_tightens_limits_end_to_end(robert):
    p = copy.deepcopy(robert)
    p["situation"]["emergency_fund"] = 15000     # 15000 / 6500 = 2.3 months -> capacity down one notch
    d = _derive(p)
    assert _val(d, "situation.emergency_fund_months") == pytest.approx(2.3, abs=1e-9)
    assert _val(d, "risk.capacity") == "low"
    assert _val(d, "risk.effective") == "low"
    lim, _ = limits_mod.build_limits(d, ROBERT_VALUE)
    assert lim["min_fixed_income_weight"] == pytest.approx(0.50, abs=1e-9)
    assert lim["max_single_stock_weight"] == pytest.approx(0.05, abs=1e-9)
    assert lim["floor_at_horizon"] == pytest.approx(380000, abs=1e-6)
    assert lim["max_drawdown_pct"] == pytest.approx(-8, abs=1e-9)


def _with_parsed(robert: dict, approved: bool) -> dict:
    p = copy.deepcopy(robert)
    p["values"]["notes"] = "No oil producers. I love AI and new tech but I don't want everything in tech."
    p["values"]["parsed_notes"]["value"] = [
        {"rule_kind": "exclude_activity", "params": {"tag": "fossil_fuel_producer"}, "applies_to": "all",
         "quote": "No oil producers.", "approved_by_user": approved},
        {"rule_kind": "max_theme_weight", "params": {"theme": "tech_heavy", "max_pct": 50}, "applies_to": "all",
         "quote": "I don't want everything in tech", "approved_by_user": approved},
    ]
    return p


def test_approved_parsed_rules_are_promoted(robert):
    lim, _ = _limits(_with_parsed(robert, approved=True))
    assert sorted(lim["excluded_activities"]) == ["accounting_fraud_last_5y", "fossil_fuel_producer", "tobacco"]
    # SPEC-AMBIGUITY: §7.2 doesn't fix the shape of max_theme_weight; strict reading = {theme: fraction},
    # with the percent max_pct converted once to a fraction (CLAUDE.md conventions).
    assert lim["max_theme_weight"] == {"tech_heavy": pytest.approx(0.50, abs=1e-9)}


def test_unapproved_parsed_rules_are_not_promoted(robert):
    lim, _ = _limits(_with_parsed(robert, approved=False))
    assert sorted(lim["excluded_activities"]) == ["accounting_fraud_last_5y", "tobacco"]
    assert not lim.get("max_theme_weight")
    assert "tech_heavy" not in json.dumps(lim, default=str)
    assert "fossil_fuel_producer" not in json.dumps(lim, default=str)


def test_other_rule_kinds_never_promoted(robert):
    """§6.2 N3: only exclude_activity / max_theme_weight are promoted; others stay soft (S4)."""
    p = copy.deepcopy(robert)
    p["values"]["parsed_notes"]["value"] = [
        {"rule_kind": "prefer_attribute", "params": {"attribute": "understandable_business"},
         "applies_to": "single_stocks", "quote": "I prefer companies I understand.", "approved_by_user": True},
    ]
    lim, _ = _limits(p)
    assert _strip(lim) == _strip(_limits(robert)[0])
    assert "understandable_business" not in json.dumps(lim, default=str)


# ---------------------------------------------------------------- limits_trace (§3.3 item 5, §7.2)

def test_limits_trace_min_fixed_income_matches_spec_example(robert):
    _, trace = _limits(robert)
    # SPEC-AMBIGUITY: §7.2 gives an ordered example; strict reading pins the exact list, order included.
    assert trace["min_fixed_income_weight"] == EFFECTIVE_CHAIN


def test_limits_trace_covers_every_limit(robert):
    lim, trace = _limits(robert)
    assert set(_strip(lim)) <= set(trace)
    for name, paths in trace.items():
        assert isinstance(paths, list), name
        assert all(isinstance(x, str) for x in paths), name
        assert len(paths) == len(set(paths)), name


@pytest.mark.parametrize("name", ["vol_band_annual", "min_fixed_income_weight", "max_single_stock_weight",
                                  "max_sector_risk_share", "max_crypto_weight", "max_foreign_currency_weight",
                                  "max_drawdown_pct", "floor_at_horizon", "excluded_asset_types"])
def test_table_driven_limits_trace_through_effective_risk(robert, name):
    _, trace = _limits(robert)
    assert set(EFFECTIVE_CHAIN) <= set(trace[name])


def test_limits_trace_user_inputs(robert):
    _, trace = _limits(robert)
    assert "risk.max_acceptable_drawdown_pct" in trace["max_drawdown_pct"]
    assert "preferences.asset_types_refused" in trace["excluded_asset_types"]
    assert "preferences.max_fee_mer_pct" in trace["max_fee_mer_pct"]
    for pillar in ("environment", "social", "governance"):
        assert f"values.{pillar}.exclusions" in trace["excluded_activities"]


def test_limits_trace_is_fully_expanded(robert):
    """Every registered rule output in a trace has all its transitive inputs in the same trace."""
    _, trace = _limits(robert)
    for name, paths in trace.items():
        for p in paths:
            if p in registry.REGISTRY:
                assert set(registry.expand(p)) <= set(paths), (name, p)


def test_limits_trace_is_generated_from_registry(robert, monkeypatch):
    """Changing a rule's declared inputs changes the trace: it can't be hand-written."""
    cap = registry.REGISTRY["risk.capacity"]
    monkeypatch.setitem(registry.REGISTRY, "risk.capacity",
                        replace(cap, inputs=cap.inputs + ("situation.__probe__",)))
    _, trace = _limits(robert)
    assert "situation.__probe__" in trace["min_fixed_income_weight"]
    assert "situation.__probe__" in trace["max_single_stock_weight"]
