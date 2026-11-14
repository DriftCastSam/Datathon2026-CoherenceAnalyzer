"""P2.14 (SPEC §17 step 14): monthly contributions + goal_probs in the engine (SPEC §5), check G1 (SPEC §7.3).

Oracles come from the spec and from values computed independently of the implementation:
- defaults (monthly_contribution=0, goals=None) leave every engine output unchanged (golden behaviour, fixed seed);
- a zero-volatility synthetic market, where every bootstrap path is the same deterministic annuity
  NAV_m = S (1+r)^m + c ((1+r)^m - 1) / r  (c added at the end of each month, D1 reference date = portfolio.as_of);
- on the real data, the closed form NAV_t = S G_t + c Σ_{k<=t} G_t / G_k applied to the contribution-free paths of the
  same seed (the draws do not depend on c), so the recursion is checked against a formula, not against itself;
- G1 is always `info` (invariant 3: only hard limit checks block; G1 has no limit), carries assumption labels, never
  touches `limits` (invariant 2) and never changes the verdict.

Decision recorded here (spec-factors §20.8 vs §7.3): §20.8 / frozen test_pipeline_offline pin Robert's objectif section
at `pass` and his checks_current at the hard checks of `hard.run_all`, so the review report carries G1 in its own
`goals` block (like `profile_coherence`, §20.9) and the objectif section shows it in `facts.goals`, without entering
the section status or the verdict.
"""
from __future__ import annotations

import copy
import json
import math

import numpy as np
import pandas as pd
import pytest
from jsonschema import Draft202012Validator

from coherence_analyzer import config, engine, factors
from coherence_analyzer.coherence import hard, report as report_mod
from coherence_analyzer.factors import fmt, objectif
from coherence_analyzer.rules import derive, limits as limits_mod

AS_OF = "2026-11-12"
S = 400_000
H = 48
C = 1_500


@pytest.fixture(scope="module")
def fix_dir(root):
    return root / "fixtures" / "robert"


# ---------------------------------------------------------------- synthetic zero-volatility market

def _flat_market(r: float, months: int = 30) -> engine.Market:
    """Two CAD assets with the same constant monthly return r: every bootstrap path is deterministic."""
    idx = pd.date_range("2022-01-31", periods=months, freq="ME")
    tick = ["SPY", "XBB.TO"]
    r_cad = pd.DataFrame(r, index=idx, columns=tick)
    assets = pd.DataFrame({"name": ["Equity", "Bonds"], "asset_class": ["etf_equity", "etf_bond"],
                           "currency": ["CAD", "CAD"], "sector": ["Broad market", "Bonds"]},
                          index=pd.Index(tick, name="ticker"))
    return engine.Market(r_cad=r_cad, r_loc=r_cad.copy(), fxr=pd.Series(0.0, index=idx), assets=assets)


def _annuity(s: float, c: float, r: float, m: int) -> float:
    g = (1 + r) ** m
    return s * g + (c * m if r == 0 else c * (g - 1) / r)


W_FLAT = {"SPY": 60, "XBB.TO": 40}


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
@pytest.mark.parametrize("r", [0.0, 0.004])
@pytest.mark.parametrize("c", [0.0, 250.0])
def test_zero_vol_paths_follow_the_annuity_formula(r, c):
    """SPEC §5: contributions are added to NAV at each step, paths in CAD (not normalised)."""
    mk = _flat_market(r)
    out = engine.analyse(mk, W_FLAT, 24, 10_000, n=50, drift="historical", monthly_contribution=c,
                         return_paths=True)
    paths = np.asarray(out["paths"])
    assert paths.shape == (50, 24)
    for m in (1, 12, 24):
        assert paths[:, m - 1] == pytest.approx(np.full(50, _annuity(10_000, c, r, m)), rel=1e-12)
    for p in ("p5", "p25", "p50", "p75", "p95"):
        assert out["cone"][p][-1] == pytest.approx(_annuity(10_000, c, r, 24), abs=1)
    assert out["p5_T"] == pytest.approx(_annuity(10_000, c, r, 24), abs=1)
    assert out["p50_T"] == out["p5_T"] == out["p95_T"]
    assert out["width"] == 0


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_zero_vol_goal_probs_are_zero_or_one_at_the_goal_month():
    """SPEC §5: goal_probs = share of paths at or above `amount` at the goal's month (month from portfolio.as_of, D1)."""
    r, c, s = 0.004, 250.0, 10_000
    mk = _flat_market(r)
    m12 = _annuity(s, c, r, 12)                      # 2027-11 is 12 months after 2026-11
    goals = [{"name": "just below", "date": "2027-11", "amount": m12 - 1},
             {"name": "just above", "date": "2027-11", "amount": m12 + 1},
             {"name": "exact", "date": "2027-11-30", "amount": round(m12, 6) - 1e-6}]
    out = engine.analyse(mk, W_FLAT, 24, s, n=50, drift="historical", monthly_contribution=c, goals=goals, as_of=AS_OF)
    gp = out["goal_probs"]
    assert [g["name"] for g in gp] == ["just below", "just above", "exact"]
    assert [g["prob"] for g in gp] == [1.0, 0.0, 1.0]
    assert all(g["month"] == 12 for g in gp)
    for g, src in zip(gp, goals):
        assert g["date"] == src["date"] and g["amount"] == src["amount"]


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_zero_vol_loss_prob_counts_against_money_put_in():
    """With contributions, a 'loss' means ending below the capital put in (start + contributions), not below the start."""
    mk = _flat_market(0.0)
    out = engine.analyse(mk, W_FLAT, 24, 10_000, n=50, drift="historical", monthly_contribution=100.0)
    assert out["p50_T"] == pytest.approx(10_000 + 2_400, abs=1)
    assert out["loss_prob"] == 0.0                   # ends exactly at the money put in: not a loss
    mk_neg = _flat_market(-0.001)
    out_neg = engine.analyse(mk_neg, W_FLAT, 24, 10_000, n=50, drift="historical", monthly_contribution=100.0)
    assert out_neg["p50_T"] > 10_000                 # above the start ...
    assert out_neg["loss_prob"] == 1.0               # ... but below start + 24 x 100


# ---------------------------------------------------------------- real data: defaults unchanged

def _eq(a, b):
    if isinstance(a, dict):
        assert set(a) == set(b)
        for k in a:
            _eq(a[k], b[k])
    elif isinstance(a, (list, tuple)):
        assert len(a) == len(b)
        for x, y in zip(a, b):
            _eq(x, y)
    elif isinstance(a, float) and math.isnan(a):
        assert isinstance(b, float) and math.isnan(b)
    else:
        assert a == b


@pytest.fixture(scope="module")
def base(market):
    return engine.analyse(market, engine.ROBERT, H, S, return_paths=True)


@pytest.fixture(scope="module")
def contrib(market):
    return engine.analyse(market, engine.ROBERT, H, S, monthly_contribution=C, return_paths=True)


def test_defaults_are_bit_for_bit_today(market, base):
    """SPEC §5: default 0 / None keeps the current behaviour (same seed, same draws, same outputs)."""
    explicit = engine.analyse(market, engine.ROBERT, H, S, monthly_contribution=0.0, goals=None, return_paths=True)
    empty = engine.analyse(market, engine.ROBERT, H, S, monthly_contribution=0, goals=[], return_paths=True)
    for o in (explicit, empty):
        _eq({k: v for k, v in o.items() if k != "paths"}, {k: v for k, v in base.items() if k != "paths"})
        assert np.array_equal(o["paths"], base["paths"])
    assert base["goal_probs"] == []


def test_goals_without_contribution_change_nothing_else(market, base):
    goals = [{"name": "g", "date": "2028-11", "amount": 420_000}]
    o = engine.analyse(market, engine.ROBERT, H, S, goals=goals, as_of=AS_OF, return_paths=True)
    _eq({k: v for k, v in o.items() if k not in ("paths", "goal_probs")},
        {k: v for k, v in base.items() if k not in ("paths", "goal_probs")})
    assert np.array_equal(o["paths"], base["paths"])
    # zero contribution: the paths are the start value compounded, so the share is read on those same paths
    assert o["goal_probs"][0]["prob"] == pytest.approx(round(float((base["paths"][:, 23] >= 420_000).mean()), 4), abs=0)


# ---------------------------------------------------------------- real data: contributions

def test_contribution_paths_match_the_closed_form_on_the_same_draws(base, contrib):
    """NAV_t = S G_t + c Σ_{k=1..t} G_t/G_k, with G the growth of the contribution-free path (same seed, same draws)."""
    g = np.asarray(base["paths"]) / S                       # (n, H) cumulative growth
    t_idx = np.arange(H)
    expected = np.empty_like(g)
    for t in t_idx:
        expected[:, t] = S * g[:, t] + C * (g[:, [t]] / g[:, : t + 1]).sum(axis=1)
    assert np.allclose(np.asarray(contrib["paths"]), expected, rtol=1e-9, atol=1e-6)


def test_contributions_raise_every_path_and_every_percentile(base, contrib):
    assert (np.asarray(contrib["paths"]) > np.asarray(base["paths"])).all()
    for p in ("p5", "p25", "p50", "p75", "p95"):
        assert all(x > y for x, y in zip(contrib["cone"][p], base["cone"][p])), p
    assert contrib["p5_T"] == contrib["cone"]["p5"][-1]
    assert contrib["p50_T"] == contrib["cone"]["p50"][-1]
    assert contrib["p95_T"] == contrib["cone"]["p95"][-1]
    paths = np.asarray(contrib["paths"])
    assert contrib["p50_T"] == pytest.approx(round(float(np.percentile(paths[:, -1], 50)), 0), abs=1)
    assert contrib["width"] == pytest.approx(contrib["p95_T"] - contrib["p5_T"], abs=1)


def test_contribution_keeps_the_risk_metrics(base, contrib):
    """Contributions change the paths, not the historical / covariance metrics."""
    for k in ("vol", "vol_local", "mdd", "mdd_date", "groups", "drivers", "corr_eq_bond"):
        _eq(contrib[k], base[k])


def test_contribution_loss_prob_is_against_money_put_in(contrib):
    paths = np.asarray(contrib["paths"])
    assert contrib["loss_prob"] == pytest.approx(round(float((paths[:, -1] < S + C * H).mean()), 4), abs=0)


def test_contribution_is_labelled_in_assumptions(base, contrib):
    """Inv. 9: the contribution is an input the user declared; it is labelled, and the default list is unchanged."""
    assert set(base["assumptions"]) < set(contrib["assumptions"])
    extra = [a for a in contrib["assumptions"] if a not in base["assumptions"]]
    assert any("contribution" in a.lower() and "1500" in a.replace(" ", "").replace(",", "") for a in extra)


def test_seed_is_fixed(market, contrib):
    again = engine.analyse(market, engine.ROBERT, H, S, monthly_contribution=C, return_paths=True)
    assert np.array_equal(again["paths"], contrib["paths"])
    _eq({k: v for k, v in again.items() if k != "paths"}, {k: v for k, v in contrib.items() if k != "paths"})


def test_negative_contribution_is_rejected(market):
    with pytest.raises(ValueError):
        engine.analyse(market, engine.ROBERT, H, S, monthly_contribution=-1)


# ---------------------------------------------------------------- real data: goal_probs

GOALS = [
    {"name": "two years", "date": "2028-11", "amount": 450_000},      # month 24
    {"name": "at horizon", "date": "2030-11", "amount": 450_000},     # month 48 = H
    {"name": "after horizon", "date": "2030-12", "amount": 10_000},   # month 49: outside
    {"name": "this month", "date": "2026-11", "amount": 10_000},      # month 0: nothing simulated yet
    {"name": "past", "date": "2025-01", "amount": 10_000},
    {"name": "zero", "date": "2027-05", "amount": 0},                 # month 6
]


@pytest.fixture(scope="module")
def with_goals(market):
    return engine.analyse(market, engine.ROBERT, H, S, monthly_contribution=C, goals=GOALS, as_of=AS_OF,
                          return_paths=True)


def test_goal_probs_only_within_the_horizon_in_input_order(with_goals):
    gp = with_goals["goal_probs"]
    assert [g["name"] for g in gp] == ["two years", "at horizon", "zero"]
    assert [g["month"] for g in gp] == [24, 48, 6]
    for g in gp:
        assert 0.0 <= g["prob"] <= 1.0
        assert {"name", "date", "amount", "month", "prob"} <= set(g)


def test_goal_probs_are_the_share_of_paths_at_or_above_the_amount(with_goals):
    paths = np.asarray(with_goals["paths"])
    by = {g["name"]: g for g in with_goals["goal_probs"]}
    assert by["two years"]["prob"] == pytest.approx(round(float((paths[:, 23] >= 450_000).mean()), 4), abs=0)
    assert by["at horizon"]["prob"] == pytest.approx(round(float((paths[:, -1] >= 450_000).mean()), 4), abs=0)
    assert by["zero"]["prob"] == 1.0
    assert 0.0 < by["two years"]["prob"] < 1.0                                       # a real spread, not a degenerate 0/1


def test_goal_probs_do_not_change_the_paths(with_goals, contrib):
    assert np.array_equal(with_goals["paths"], contrib["paths"])
    _eq({k: v for k, v in with_goals.items() if k not in ("paths", "goal_probs")},
        {k: v for k, v in contrib.items() if k not in ("paths", "goal_probs")})


def test_goal_probs_decrease_with_the_amount(market):
    amounts = [300_000, 400_000, 450_000, 500_000, 700_000]
    goals = [{"name": str(a), "date": "2030-06", "amount": a} for a in amounts]
    gp = engine.analyse(market, engine.ROBERT, H, S, monthly_contribution=C, goals=goals, as_of=AS_OF)["goal_probs"]
    probs = [g["prob"] for g in gp]
    assert probs == sorted(probs, reverse=True)
    assert probs[0] > probs[-1]


def test_goals_need_the_reference_date(market):
    """D1: the goal month counts from portfolio.as_of; the engine never reads the wall clock."""
    with pytest.raises(ValueError):
        engine.analyse(market, engine.ROBERT, H, S, goals=GOALS[:1])


# ---------------------------------------------------------------- G1 (SPEC §7.3)

@pytest.fixture(scope="module")
def robert_profile(fix_dir):
    raw = json.loads((fix_dir / "profile.json").read_text(encoding="utf-8"))
    p = derive.derive_all(copy.deepcopy(raw), portfolio_value_cad=S, as_of=AS_OF)
    p["limits"], p["limits_trace"] = limits_mod.build_limits(p, S)
    return p


@pytest.fixture(scope="module")
def robert_goal_metrics(market, robert_profile):
    goals = robert_profile["objectives"]["goals"]
    return engine.analyse(market, engine.ROBERT, H, S, monthly_contribution=C, goals=goals, as_of=AS_OF)


def test_g1_one_info_check_per_goal_with_assumption_labels(robert_goal_metrics, robert_profile):
    checks = hard.g1(robert_goal_metrics, robert_profile)
    goals = robert_profile["objectives"]["goals"]
    assert [c["subject"] for c in checks] == [g["name"] for g in goals]
    for c in checks:
        assert c["id"] == "G1" and c["kind"] == "hard" and c["status"] == "info"
        assert factors.CHECK_FACTOR["G1"] == "objectif"
        assert c["source"].startswith("rule:")
        assert c["limit"] is None                                            # G1 compares to nothing the user must meet
        assert not any(p.startswith("limits") for p in c["derived_from"])    # inv. 2: G1 never reads or feeds limits
        assert "objectives.goals" in c["derived_from"]
        assert isinstance(c["assumptions"], list) and c["assumptions"]
        assert all(isinstance(a, str) and a for a in c["assumptions"])
        low = " ".join(c["assumptions"]).lower()
        assert "contribution" in low and "withdrawal" in low and "not a forecast" in low
    # Retire at 63 (2030-06, month 43) is inside the 48-month horizon; the 2032-01 goal is not
    inside, outside = checks
    gp = robert_goal_metrics["goal_probs"]
    assert len(gp) == 1 and gp[0]["month"] == 43
    assert inside["value"] == gp[0]["prob"] and inside["unit"] == "probability"
    assert outside["value"] is None and outside["message_key"] == "goal_outside_horizon"


@pytest.mark.parametrize("prob", [0.0, 0.01, 0.5, 1.0])
def test_g1_is_always_info_whatever_the_probability(robert_profile, prob):
    m = {"goal_probs": [{"name": "Retire at 63", "date": "2030-06", "amount": 450000, "month": 43, "prob": prob}],
         "assumptions": ["x"]}
    checks = hard.g1(m, robert_profile)
    assert {c["status"] for c in checks} == {"info"}
    assert report_mod.verdict(checks) == "coherent"


def test_g1_without_goals_is_empty(robert_profile):
    p = copy.deepcopy(robert_profile)
    p["objectives"]["goals"] = []
    assert hard.g1({"goal_probs": [], "assumptions": []}, p) == []
    p["objectives"].pop("goals")
    assert hard.g1({"goal_probs": [], "assumptions": []}, p) == []


def test_g1_does_not_need_limits(robert_goal_metrics, robert_profile):
    p = copy.deepcopy(robert_profile)
    p.pop("limits")
    p.pop("limits_trace")
    assert [c["status"] for c in hard.g1(robert_goal_metrics, p)] == ["info", "info"]


def test_g1_never_changes_the_verdict(robert_goal_metrics, robert_profile, market):
    from coherence_analyzer import data
    m = engine.analyse(market, engine.ROBERT_NVDA, H, S)
    base = hard.run_all({t: v / 100 for t, v in engine.ROBERT_NVDA.items()}, m, robert_profile, data.load_assets(),
                        {n: data.load_meta(n) for n in ("asset_meta", "etf_sector_weights", "activity_tags")})
    g = hard.g1(robert_goal_metrics, robert_profile)
    assert report_mod.verdict(base + g) == report_mod.verdict(base)
    assert report_mod.verdict(g) == "coherent"


def test_goals_and_contribution_never_feed_limits(robert_profile):
    """Inv. 2: limits come from rule: fields and declared values; goals and contributions are not inputs."""
    p = copy.deepcopy(robert_profile)
    p["objectives"]["goals"] = [{"name": "x", "date": "2027-01", "amount": 9_999_999}]
    p["situation"]["monthly_contribution"] = 99_999
    lim, trace = limits_mod.build_limits(p, S)
    assert lim == robert_profile["limits"] and trace == robert_profile["limits_trace"]
    for paths in trace.values():
        assert not any(x.startswith(("objectives.goals", "situation.monthly_contribution")) for x in paths)


# ---------------------------------------------------------------- objectif section shows G1

def _ctx(profile, goal_checks, locale="fr-CA"):
    return factors.SectionContext(profile=profile, limits=profile["limits"], limits_trace=profile["limits_trace"],
                                  checks_current=[], goal_checks=goal_checks, locale=locale)


@pytest.mark.parametrize("locale", ["fr-CA", "en-CA"])
def test_objectif_facts_show_the_goals_preformatted(robert_goal_metrics, robert_profile, locale):
    g = report_mod.tag_checks(hard.g1(robert_goal_metrics, robert_profile))
    facts = objectif.facts(_ctx(robert_profile, g, locale))
    assert facts["status"] == "pass"                       # G1 is shown, it does not set the section status (§20.8)
    rows = facts["goals"]
    assert [r["name"] for r in rows] == ["Retire at 63", "Help children with a down payment"]
    prob = robert_goal_metrics["goal_probs"][0]["prob"]
    assert rows[0]["probability"] == fmt.percent(prob, locale)
    assert rows[0]["amount"] == fmt.amount(450_000, locale)
    assert rows[0]["date"] == "2030-06" and rows[0]["status"] == "info" and rows[0]["check"] == "G1"
    assert rows[1]["probability"] is None                  # outside the horizon: no number invented
    assert facts["goal_assumptions"] and all(isinstance(a, str) for a in facts["goal_assumptions"])


def test_objectif_facts_unchanged_without_goal_checks(robert_profile):
    facts = objectif.facts(_ctx(robert_profile, []))
    assert "goals" not in facts and "goal_assumptions" not in facts


# ---------------------------------------------------------------- Flow C review wiring

@pytest.fixture(scope="module")
def review_current(fix_dir, tmp_path_factory):
    from coherence_analyzer import pipeline
    from coherence_analyzer.llm import bedrock
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(bedrock, "COHERENCE_ANALYZER_LLM", "mock")
        mp.setenv("COHERENCE_ANALYZER_LLM", "mock")
        mp.setenv("COHERENCE_ANALYZER_STORE_DIR", str(tmp_path_factory.mktemp("store")))
        return pipeline.review(fix_dir)


def test_review_report_carries_g1_in_its_goals_block(review_current, market, robert_profile):
    rep = review_current
    block = rep["goals"]
    assert block["monthly_contribution"] == C
    assert block["horizon_months"] == H
    exp = engine.analyse(market, engine.ROBERT, H, S, monthly_contribution=C,
                         goals=robert_profile["objectives"]["goals"], as_of=AS_OF)
    assert block["goal_probs"] == exp["goal_probs"]
    assert [c["id"] for c in block["checks"]] == ["G1", "G1"]
    assert all(c["status"] == "info" and c["factor"] == "objectif" for c in block["checks"])
    assert block["checks"][0]["value"] == exp["goal_probs"][0]["prob"]
    assert block["assumptions"] and all(isinstance(a, str) and a for a in block["assumptions"])


def test_review_g1_does_not_move_metrics_checks_or_verdict(review_current, market):
    rep = review_current
    exp = engine.analyse(market, engine.ROBERT, H, S)
    for k in ("vol", "mdd", "p5_T", "p50_T", "p95_T", "loss_prob", "width"):
        assert rep["metrics_current"][k] == exp[k], k               # H9 still reads the contribution-free cone
    assert "G1" not in {c["id"] for c in rep["checks_current"]}
    assert rep["verdict"] == "coherent"                              # D7 point 5: Robert's current portfolio
    by = {s["factor"]: s for s in rep["sections"]}
    assert by["objectif"]["status"] == "pass"
    assert [r["name"] for r in by["objectif"]["facts"]["goals"]] == ["Retire at 63", "Help children with a down payment"]


def test_review_report_with_goals_is_schema_valid(review_current):
    schema = json.loads((config.SCHEMAS_DIR / "report.json").read_text(encoding="utf-8"))
    errors = [e.message for e in Draft202012Validator(schema).iter_errors(review_current)]
    assert errors == []
    assert "goals" in schema["properties"]
