"""intent.apply for every funding.kind (SPEC §3.6, §8; docs/spec-decisions.md D2).

Contract (coherence_analyzer/intent.py): apply(weights_pct, intent, value_cad) -> (new weights_pct, new value_cad).
Weights are percent in and out. Invariants checked for every funding kind: sum to 100, never negative,
the target ticker lands on the target weight, the input dict is not mutated. Robert fixture: SPEC §3.5.
"""
import copy
import math

import pytest

from coherence_analyzer import engine, intent as intent_mod

ROBERT = engine.ROBERT            # SPEC §3.5: XIU.TO 18, RY.TO 7, ENB.TO 6, FTS.TO 7, JNJ 5, PG 5, SPY 12, XBB.TO 22, ZST.TO 13, GLD 5
VALUE = 400_000                   # fixtures/robert/portfolio.json value_cad
TOL = 1e-9

EQUITY_POOL = ("RY.TO", "ENB.TO", "FTS.TO", "JNJ", "PG")      # asset_class == equity (excluding NVDA), 30%
ETF_EQUITY_POOL = ("XIU.TO", "SPY")                            # asset_class == etf_equity, 30%


def _intent(target: float, funding: dict, ticker: str = "NVDA") -> dict:
    """SPEC §3.6 intent shape."""
    return {"intent_id": "in_test", "event_id": "ev_robert_0007", "action": "buy", "ticker": ticker,
            "target_weight_pct": target, "funding": funding, "status": "confirmed",
            "confirmed_at": "2026-11-14T10:05:00-05:00"}


SPECIFIC = {"kind": "specific", "ticker": "ZST.TO"}
PRO_RATA_ALL = {"kind": "pro_rata_all"}
CLASS_EQUITY = {"kind": "pro_rata_class", "asset_class": "equity"}
CLASS_ETF_EQUITY = {"kind": "pro_rata_class", "asset_class": "etf_equity"}


def _w(weights: dict, t: str) -> float:
    return weights.get(t, 0.0)     # a zero-weight ticker may be kept or dropped


def _check_invariants(weights: dict, ticker: str | None = None, target: float | None = None) -> None:
    assert math.fsum(weights.values()) == pytest.approx(100, abs=TOL)
    for t, v in weights.items():
        assert v >= 0, f"negative weight {t}={v}"
    if ticker is not None:
        assert _w(weights, ticker) == pytest.approx(target, abs=TOL)


# ------------------------------------------------------------------ specific

def test_specific_robert_nvda_from_zst_equals_engine_fixture():
    # SPEC §3.6: "ROBERT_NVDA corresponds to NVDA 10%, funding specific ZST.TO"
    new, value = intent_mod.apply(ROBERT, _intent(10, SPECIFIC), VALUE)
    assert value == VALUE
    _check_invariants(new, "NVDA", 10)
    assert {t: _w(new, t) for t in set(new) | set(engine.ROBERT_NVDA)} == pytest.approx(
        {t: engine.ROBERT_NVDA.get(t, 0) for t in set(new) | set(engine.ROBERT_NVDA)}, abs=TOL)
    assert _w(new, "ZST.TO") == pytest.approx(3, abs=TOL)


def test_specific_only_touches_target_and_funding_ticker():
    new, _ = intent_mod.apply(ROBERT, _intent(7.5, SPECIFIC), VALUE)
    for t, v in ROBERT.items():
        if t != "ZST.TO":
            assert _w(new, t) == pytest.approx(v, abs=TOL), t
    assert _w(new, "ZST.TO") == pytest.approx(13 - 7.5, abs=TOL)


def test_specific_full_drain_never_negative():
    # target == funding ticker's whole weight: funding ticker goes to exactly 0, not below
    new, _ = intent_mod.apply(ROBERT, _intent(13, SPECIFIC), VALUE)
    _check_invariants(new, "NVDA", 13)
    assert _w(new, "ZST.TO") >= 0
    assert _w(new, "ZST.TO") == pytest.approx(0, abs=TOL)


def test_specific_insufficient_funding_never_produces_negative_weights():
    # SPEC-AMBIGUITY: SPEC §3.6 does not say what happens when the funding ticker (13%) is smaller than
    # the needed 20%. Stricter reading: either refuse (ValueError) or return a fully valid portfolio; never negative.
    try:
        new, _ = intent_mod.apply(ROBERT, _intent(20, SPECIFIC), VALUE)
    except ValueError:
        return
    _check_invariants(new)


# ------------------------------------------------------------------ pro_rata_all

def test_pro_rata_all_scales_every_other_holding():
    new, value = intent_mod.apply(ROBERT, _intent(10, PRO_RATA_ALL), VALUE)
    assert value == VALUE
    _check_invariants(new, "NVDA", 10)
    for t, v in ROBERT.items():
        assert _w(new, t) == pytest.approx(v * 0.9, abs=TOL), t      # (100-10)/100 on every holding
    # fixed income: 35% -> 31.5%
    assert _w(new, "XBB.TO") + _w(new, "ZST.TO") == pytest.approx(31.5, abs=TOL)


def test_pro_rata_all_existing_holding_increase():
    # SPY 12 -> 20: the other holdings share the 8pp, scaled by (100-20)/(100-12)
    new, _ = intent_mod.apply(ROBERT, _intent(20, PRO_RATA_ALL, ticker="SPY"), VALUE)
    _check_invariants(new, "SPY", 20)
    for t, v in ROBERT.items():
        if t != "SPY":
            assert _w(new, t) == pytest.approx(v * 80 / 88, abs=TOL), t


# ------------------------------------------------------------------ pro_rata_class

def test_pro_rata_class_equity_leaves_fixed_income_untouched():
    # SPEC §8: "with pro-rata funding from equities, fixed income stays at 35%"
    new, value = intent_mod.apply(ROBERT, _intent(8, CLASS_EQUITY), VALUE)
    assert value == VALUE
    _check_invariants(new, "NVDA", 8)
    assert _w(new, "XBB.TO") + _w(new, "ZST.TO") == pytest.approx(35, abs=TOL)
    for t in set(ROBERT) - set(EQUITY_POOL):
        assert _w(new, t) == pytest.approx(ROBERT[t], abs=TOL), t
    for t in EQUITY_POOL:      # 30% pool shrinks to 22%, pro rata
        assert _w(new, t) == pytest.approx(ROBERT[t] * 22 / 30, abs=TOL), t


def test_pro_rata_class_etf_equity():
    new, _ = intent_mod.apply(ROBERT, _intent(5, CLASS_ETF_EQUITY), VALUE)
    _check_invariants(new, "NVDA", 5)
    for t in set(ROBERT) - set(ETF_EQUITY_POOL):
        assert _w(new, t) == pytest.approx(ROBERT[t], abs=TOL), t
    for t in ETF_EQUITY_POOL:  # 30% pool -> 25%
        assert _w(new, t) == pytest.approx(ROBERT[t] * 25 / 30, abs=TOL), t


def test_pro_rata_class_equity_excludes_target_from_its_own_pool():
    # SPEC-AMBIGUITY: NVDA is itself asset_class equity. Stricter reading: the target never funds itself,
    # so an already-held NVDA ends exactly on the target weight.
    start = dict(ROBERT, NVDA=4, **{"ZST.TO": 9})
    assert math.fsum(start.values()) == pytest.approx(100, abs=TOL)
    new, _ = intent_mod.apply(start, _intent(10, CLASS_EQUITY), VALUE)
    _check_invariants(new, "NVDA", 10)
    for t in set(start) - set(EQUITY_POOL) - {"NVDA"}:
        assert _w(new, t) == pytest.approx(start[t], abs=TOL), t
    for t in EQUITY_POOL:      # 30% pool funds the extra 6pp -> 24%
        assert _w(new, t) == pytest.approx(start[t] * 24 / 30, abs=TOL), t


def test_pro_rata_class_with_empty_pool_is_refused_or_valid():
    # SPEC-AMBIGUITY: Robert holds no crypto, so there is nothing to fund from. Stricter reading:
    # refuse (ValueError) or return a valid portfolio; never negative weights, never a sum != 100.
    try:
        new, _ = intent_mod.apply(ROBERT, _intent(5, {"kind": "pro_rata_class", "asset_class": "crypto"}), VALUE)
    except ValueError:
        return
    _check_invariants(new)


# ------------------------------------------------------------------ cash_contribution

def test_cash_contribution_exact_amount_reaches_target():
    # amount chosen so NVDA is exactly 10% of the enlarged portfolio: a = V * t / (1 - t)
    amount = VALUE * 0.10 / 0.90
    new, value = intent_mod.apply(ROBERT, _intent(10, {"kind": "cash_contribution", "amount_cad": amount}), VALUE)
    assert value == pytest.approx(VALUE + amount, abs=1e-6)
    _check_invariants(new, "NVDA", 10)
    for t, v in ROBERT.items():          # nothing sold: each existing holding keeps its CAD value
        assert _w(new, t) * value / 100 == pytest.approx(v * VALUE / 100, abs=1e-6), t


def test_cash_contribution_smaller_amount_keeps_existing_cad_values():
    # SPEC-AMBIGUITY: SPEC §3.6 leaves under-funded cash contributions open. Stricter reading: new value
    # = old + amount, weights valid, existing holdings keep their CAD value (nothing sold), the ticker
    # rises but does not exceed the target.
    amount = 20_000
    new, value = intent_mod.apply(ROBERT, _intent(10, {"kind": "cash_contribution", "amount_cad": amount}), VALUE)
    assert value == pytest.approx(VALUE + amount, abs=1e-6)
    _check_invariants(new)
    for t, v in ROBERT.items():
        assert _w(new, t) * value / 100 == pytest.approx(v * VALUE / 100, abs=1e-6), t
    assert 0 < _w(new, "NVDA") <= 10 + TOL


# ------------------------------------------------------------------ cross-cutting

KINDS = {
    "specific": SPECIFIC,
    "pro_rata_all": PRO_RATA_ALL,
    "pro_rata_class_equity": CLASS_EQUITY,
    "pro_rata_class_etf_equity": CLASS_ETF_EQUITY,
}


@pytest.mark.parametrize("target", [0.5, 2.5, 5, 8, 10, 12.5])
@pytest.mark.parametrize("kind", KINDS)
def test_all_funding_kinds_sum_to_100_and_non_negative(kind, target):
    new, value = intent_mod.apply(ROBERT, _intent(target, KINDS[kind]), VALUE)
    assert value == VALUE            # only cash_contribution changes the portfolio value
    _check_invariants(new, "NVDA", target)


@pytest.mark.parametrize("amount", [1_000, 20_000, 44_444.44, 100_000])
def test_cash_contribution_sums_to_100_non_negative_and_value(amount):
    new, value = intent_mod.apply(ROBERT, _intent(10, {"kind": "cash_contribution", "amount_cad": amount}), VALUE)
    assert value == pytest.approx(VALUE + amount, abs=1e-6)
    _check_invariants(new)


@pytest.mark.parametrize("kind", KINDS)
def test_apply_does_not_mutate_inputs(kind):
    w, it = copy.deepcopy(ROBERT), _intent(8, KINDS[kind])
    it_before = copy.deepcopy(it)
    intent_mod.apply(w, it, VALUE)
    assert w == ROBERT
    assert it == it_before


@pytest.mark.parametrize("kind", KINDS)
def test_target_equal_to_current_weight_is_a_no_op(kind):
    new, value = intent_mod.apply(ROBERT, _intent(12, KINDS[kind], ticker="SPY"), VALUE)
    assert value == VALUE
    for t in set(new) | set(ROBERT):
        assert _w(new, t) == pytest.approx(ROBERT.get(t, 0), abs=TOL), t


def test_funding_kinds_constant_matches_spec():
    # SPEC §3.6: funding.kind ∈ specific | pro_rata_all | pro_rata_class | cash_contribution
    assert set(intent_mod.FUNDING_KINDS) == {"specific", "pro_rata_all", "pro_rata_class", "cash_contribution"}
