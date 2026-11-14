"""Resizer on Robert + NVDA (SPEC §8, §15 test_resizer; docs/spec-decisions.md D2 and D10).

D2: the resizer runs §8 exactly as written (cheap scan H1-H6 in 0.5 pp steps, then engine checks H7-H10 with
n=1000 + binary search, confirmed with n=5000) and we assert what the engine actually produces. This REPLACES the
§8/§15 expectation "5.0% binding H4 / 8.0% binding H3": on the starter data H9 (p5_T floor 360 000) binds first.

D10 (SPEC §8, §15): the resizer evaluates the user's OWN funding only. It never tries a funding source of its own
(no pro_rata_all / pro_rata_class candidates, no ranking, no top-3) and returns ONE result
`{funding, max_weight_pct, binding_check, metrics}` or `{none_fit: True, binding_checks}`. `pro_rata_*` stay valid
funding when the user states them in the intent; the golden values below are what the engine measures for each.

Measured on the starter data (Robert 400 000 CAD, horizon 48 months, seed 7, limits `low_moderate`):

    NVDA funded by       cheap scan stops   p5_T@5000 at 5.0%   golden max   p5_T@5000 at max / at max+0.5pp   binding
    ZST.TO (specific)    5.0 (H4 at 5.5)    345 618             1.5          360 503 / 358 818                 H9
    pro_rata_all         8.0 (H3 at 8.5)    348 463             2.0          360 101 / 357 698                 H9
    pro_rata_class etf   8.0 (H3 at 8.5)    349 941             2.0          360 630 / 358 531                 H9
    pro_rata_class eq    8.0 (H3 at 8.5)    346 804             1.5          361 047 / 359 322                 H9

(baseline Robert, no NVDA: p5_T = 366 719.) Robert + NVDA 10% from ZST.TO fails H3, H4 (cheap) and H7, H8, H9,
H10 (engine; D2 lists H8 and H10, H7 vol 9.83% vs band 5-9% is measured too).
"""
import copy
import json

import pytest

from coherence_analyzer import config, data, engine, intent as intent_mod, resizer
from coherence_analyzer.coherence import hard
from coherence_analyzer.rules import derive, limits as limits_mod

AS_OF = "2026-11-12"          # portfolio.as_of (docs/spec-decisions.md D1); no clock reads
VALUE = 400_000
HORIZON_M = 48                # SPEC §3.5 fixture horizon
FLOOR = 360_000.0
META_NAMES = ("asset_meta", "etf_sector_weights", "activity_tags")

TARGET = 10                   # in_0001: NVDA 10% from ZST.TO (SPEC §3.6)
SPECIFIC = {"kind": "specific", "ticker": "ZST.TO"}
PRO_RATA_ALL = {"kind": "pro_rata_all"}
CLASS_ETF = {"kind": "pro_rata_class", "asset_class": "etf_equity"}
CLASS_EQUITY = {"kind": "pro_rata_class", "asset_class": "equity"}
RESULT_KEYS = {"funding", "max_weight_pct", "binding_check", "metrics"}          # SPEC §8 step 4

# funding -> (golden max_weight_pct, p5_T at max (n=5000), p5_T at max + 0.5pp (n=5000)); measured, see module docstring
GOLDEN = {
    "specific": (SPECIFIC, 1.5, 360_503.0, 358_818.0),
    "pro_rata_all": (PRO_RATA_ALL, 2.0, 360_101.0, 357_698.0),
    "pro_rata_class_etf_equity": (CLASS_ETF, 2.0, 360_630.0, 358_531.0),
    "pro_rata_class_equity": (CLASS_EQUITY, 1.5, 361_047.0, 359_322.0),
}


def _intent(target: float = TARGET, funding: dict = SPECIFIC) -> dict:
    return {"intent_id": "in_0001", "event_id": "ev_robert_0007", "action": "buy", "ticker": "NVDA",
            "target_weight_pct": target, "funding": funding, "status": "confirmed",
            "confirmed_at": "2026-11-14T10:05:00-05:00"}


# ---------------------------------------------------------------- fixtures

@pytest.fixture(scope="module")
def profile(root) -> dict:
    raw = json.loads((root / "fixtures" / "robert" / "profile.json").read_text())
    p = derive.derive_all(raw, portfolio_value_cad=VALUE, as_of=AS_OF)
    lim, trace = limits_mod.build_limits(p, VALUE)
    p["limits"], p["limits_trace"] = lim, trace
    return p


@pytest.fixture(scope="module")
def assets():
    return data.load_assets()


@pytest.fixture(scope="module")
def meta() -> dict:
    return {n: data.load_meta(n) for n in META_NAMES}


@pytest.fixture(scope="module")
def portfolio(root, assets) -> dict:
    pf = data.load_portfolio(root / "fixtures" / "robert" / "portfolio.json", assets)
    # SPEC-AMBIGUITY: §8 gives the resizer no horizon argument; the engine horizon is the SPEC §3.5 fixture
    # value (48 months, same as ROBERT_NVDA in the golden test). Passed explicitly in the portfolio dict.
    pf["horizon_months"] = HORIZON_M
    return pf


@pytest.fixture(scope="module")
def result(portfolio, profile, market, meta) -> dict:
    return resizer.resize(portfolio, _intent(), profile, market, meta)


def _engine(market, weights_pct: dict, n: int) -> dict:
    return engine.analyse(market, {t: v for t, v in weights_pct.items() if v > 0}, HORIZON_M, VALUE, n=n)


def _violations(checks: list) -> set:
    return {c["id"] for c in checks if c["status"] == "violation"}


# ---------------------------------------------------------------- the engine-side facts behind the golden values

def test_robert_nvda_zst_at_5pct_cheap_checks_pass_but_floor_h9_breaks(market, profile, assets, meta):
    # D2: at 5% the cheap scan is still clean (H4: 22 + 8 = 30% >= 30%) but p5_T = 345 618 < 360 000.
    w = dict(engine.ROBERT, NVDA=5, **{"ZST.TO": 8})
    assert _violations(hard.run_cheap({t: v / 100 for t, v in w.items()}, profile, assets, meta)) == set()
    m = _engine(market, w, config.ENGINE_N)
    assert m["p5_T"] == pytest.approx(345_618.0, abs=1e-9)
    assert m["p5_T"] < FLOOR
    assert _violations(hard.run_engine(m, profile)) == {"H9"}


def test_robert_nvda_zst_cheap_scan_stops_at_5pct_on_h4(profile, assets, meta):
    # SPEC §8: fixed income = 22 + (13 - w) >= 30 -> w <= 5; the first failing 0.5 pp step (5.5) is H4 only.
    w = dict(engine.ROBERT, NVDA=5.5, **{"ZST.TO": 7.5})
    assert _violations(hard.run_cheap({t: v / 100 for t, v in w.items()}, profile, assets, meta)) == {"H4"}


def test_robert_nvda_10pct_from_zst_fails_h3_h4_and_engine_checks(market, profile, assets, meta):
    # D2 context (measured; H7 added to D2's H8/H10 list): the 10% intent fails both cheap and engine checks.
    w = engine.ROBERT_NVDA
    cheap = hard.run_cheap({t: v / 100 for t, v in w.items()}, profile, assets, meta)
    m = _engine(market, w, config.ENGINE_N)
    assert _violations(cheap) == {"H3", "H4"}
    assert _violations(hard.run_engine(m, profile)) == {"H7", "H8", "H9", "H10"}
    assert m["p5_T"] == pytest.approx(316_971.0, abs=1e-9)
    assert m["mdd"] == pytest.approx(-0.134, abs=1e-9)


@pytest.mark.parametrize("name", GOLDEN)
def test_golden_boundary_is_one_step_wide(name, market, profile):
    # The golden max passes H7-H10 at n=5000 and the next 0.5 pp step breaks the floor (H9): p5_T measured above.
    funding, mx, p5_at_max, p5_next = GOLDEN[name]
    for w, p5, ok in ((mx, p5_at_max, True), (mx + config.RESIZER_STEP_PP, p5_next, False)):
        weights, _ = intent_mod.apply(engine.ROBERT, _intent(w, funding), VALUE)
        m = _engine(market, weights, config.ENGINE_N)
        assert m["p5_T"] == pytest.approx(p5, abs=1e-9)
        assert (m["p5_T"] >= FLOOR) is ok
        assert _violations(hard.run_engine(m, profile)) == (set() if ok else {"H9"})


# ---------------------------------------------------------------- resize(): one result, the user's own funding

def test_robert_nvda_from_zst_is_below_5pct_and_binds_h9(result):
    # D2: expected below 5.0% with H9 binding (replaces SPEC §8's "5.0%, H4").
    assert not result.get("none_fit")
    assert result["max_weight_pct"] < 5.0
    assert result["binding_check"] == "H9"
    # golden (measured): 1.5%
    assert result["max_weight_pct"] == pytest.approx(1.5, abs=1e-9)
    assert result["metrics"]["p5_T"] < FLOOR + 1_000      # confirmed at n=5000: 360 503, just above the floor
    assert result["metrics"]["p5_T"] >= FLOOR


def test_result_is_one_object_with_exactly_the_section_8_keys_and_the_users_funding(result):
    # D10.2 + SPEC §8 step 4: ONE result {funding, max_weight_pct, binding_check, metrics}; `funding` is the
    # user's funding unchanged. No list of options / alternatives, no ranking, no extra key.
    # SPEC-AMBIGUITY: §8 lists exactly four keys; stricter reading: no other key (the old `binding_checks` list is gone).
    assert set(result) == RESULT_KEYS
    assert result["funding"] == SPECIFIC
    for gone in ("options", "alternatives", "candidates"):
        assert gone not in result


def test_the_resizer_evaluates_the_users_funding_only(portfolio, profile, market, meta, monkeypatch):
    # D10.2 / §15: "no other funding source is ever evaluated". Every funding that reaches intent.apply (the only
    # way a funding turns into weights) is the user's own: no pro_rata_all, no pro_rata_class of the resizer's own.
    # SPEC-AMBIGUITY: the spec names no hook; intent.apply is the one funding-to-weights function (resizer imports
    # the module), so spying on it sees every funding the resizer tries.
    seen: list[str] = []
    real = intent_mod.apply

    def spy(weights_pct, it, value):
        seen.append(json.dumps(it["funding"], sort_keys=True))
        return real(weights_pct, it, value)

    monkeypatch.setattr(intent_mod, "apply", spy)
    res = resizer.resize(portfolio, _intent(), profile, market, meta)
    assert seen and set(seen) == {json.dumps(SPECIFIC, sort_keys=True)}
    assert res["funding"] == SPECIFIC


@pytest.mark.parametrize("name", GOLDEN)
def test_a_funding_the_user_states_is_evaluated_as_given_and_returned_unchanged(name, portfolio, profile, market, meta):
    # D10.2: pro_rata_* stay valid when the intent states them; the result carries that funding, one result, golden.
    funding, mx, _, _ = GOLDEN[name]
    res = resizer.resize(portfolio, _intent(funding=funding), profile, market, meta)
    assert set(res) == RESULT_KEYS
    assert res["funding"] == funding
    assert res["max_weight_pct"] == pytest.approx(mx, abs=1e-9)
    # SPEC-AMBIGUITY: §8 says "binary-search downward on w" without a resolution; stricter reading is the
    # 0.5 pp grid of the scan (config.RESIZER_STEP_PP), so goldens are grid values.
    assert res["binding_check"] == "H9"
    assert res["max_weight_pct"] < 5.0


def test_result_metrics_are_the_n5000_engine_output_at_max_weight(result, market, profile):
    # SPEC §8 step 3: "Confirm the final w with full n=5000"; metrics carry the engine numbers for H7-H9.
    weights, value = intent_mod.apply(engine.ROBERT, _intent(result["max_weight_pct"], result["funding"]), VALUE)
    assert value == VALUE
    m = _engine(market, weights, config.ENGINE_N)
    for k in ("vol", "mdd", "p5_T"):
        assert result["metrics"][k] == pytest.approx(m[k], abs=1e-9), k
    assert _violations(hard.run_engine(m, profile)) == set()


def test_resize_is_deterministic(portfolio, profile, market, meta, result):
    again = resizer.resize(portfolio, _intent(), profile, market, meta)
    assert json.dumps(again, sort_keys=True, default=str) == json.dumps(result, sort_keys=True, default=str)


def test_resize_does_not_mutate_inputs(portfolio, profile, market, meta):
    pf, pr, it = copy.deepcopy(portfolio), copy.deepcopy(profile), _intent()
    it_before = copy.deepcopy(it)
    resizer.resize(portfolio, it, profile, market, meta)
    assert portfolio == pf and profile == pr and it == it_before


def test_intent_that_already_fits_is_capped_at_its_target(portfolio, profile, market, meta):
    # SPEC §8: the scan runs "from 0 to w*"; 1.0% passes every check (p5_T >= 363 150 at n=5000), so the max is
    # the target itself.
    res = resizer.resize(portfolio, _intent(target=1.0), profile, market, meta)
    assert not res.get("none_fit")
    assert res["max_weight_pct"] == pytest.approx(1.0, abs=1e-9)
    assert res["funding"] == SPECIFIC


# ---------------------------------------------------------------- none_fit

def test_none_fit_when_engine_floor_fails_even_at_zero(portfolio, profile, market, meta):
    # SPEC §8 step 4: baseline Robert has p5_T = 366 719; a floor of 370 000 is breached at w = 0.
    p = copy.deepcopy(profile)
    p["limits"]["floor_at_horizon"] = 370_000.0
    res = resizer.resize(portfolio, _intent(), p, market, meta)
    assert res["none_fit"] is True
    assert set(res) == {"none_fit", "binding_checks"}
    assert set(res["binding_checks"]) == {"H9"}


def test_none_fit_when_cheap_check_fails_even_at_zero(portfolio, profile, market, meta):
    # baseline fixed income is 35%; a 40% minimum fails H4 at w = 0.
    p = copy.deepcopy(profile)
    p["limits"]["min_fixed_income_weight"] = 0.40
    res = resizer.resize(portfolio, _intent(), p, market, meta)
    assert res["none_fit"] is True
    assert set(res) == {"none_fit", "binding_checks"}
    assert set(res["binding_checks"]) == {"H4"}
