"""What-if for a user-typed CAD amount (deploy/whatif.py amount_intent / scenario_for_amount).

The amount is the CAD put into the chosen asset. Reallocation keeps the portfolio value; "cash" adds the amount.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures" / "robert"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def whatif():
    return _load("deploy_whatif_amount", ROOT / "deploy" / "whatif.py")


@pytest.fixture(scope="module")
def ctx(whatif, tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("COHERENCE_ANALYZER_STORE_DIR", str(tmp_path_factory.mktemp("store")))
    try:
        yield whatif.context(FIXTURE, "en-CA")
    finally:
        mp.undo()


def _held(ctx) -> str:
    return max(ctx["w0"], key=lambda t: ctx["w0"][t])


def _unheld(ctx) -> str:
    return next(t for t in sorted(ctx["assets"].index) if ctx["w0"].get(t, 0.0) == 0.0)


def test_reallocation_amount_matches_the_precomputed_target_scenario(whatif, ctx):
    t = _unheld(ctx)
    amount = ctx["value"] * 0.05
    from_amount = whatif.scenario_for_amount(ctx, t, amount, "pro_rata_all")
    precomputed = whatif.scenarios(ctx, [t])[t]["scenarios"]["5|pro_rata_all"]
    assert from_amount["weights_pct"] == precomputed["weights_pct"]
    assert from_amount["metrics"] == precomputed["metrics"]
    assert from_amount["value_cad"] == pytest.approx(ctx["value"], abs=0.01)


def test_reallocation_on_a_held_asset_adds_only_the_amount(whatif, ctx):
    t = _held(ctx)
    intent = whatif.amount_intent(ctx, t, 1000.0, "pro_rata_all")
    assert intent["target_weight_pct"] == pytest.approx(ctx["w0"][t] + 1000.0 / ctx["value"] * 100, abs=1e-9)


def test_cash_contribution_grows_the_portfolio_and_buys_exactly_the_amount(whatif, ctx):
    t, amount = _held(ctx), 2500.0
    s = whatif.scenario_for_amount(ctx, t, amount, "cash")
    value_after = ctx["value"] + amount
    assert s["value_cad"] == pytest.approx(value_after, abs=0.01)
    from coherence_analyzer import intent as intent_mod   # unrounded weights (the payload rounds to 3 dp)
    w1, v1 = intent_mod.apply(ctx["w0"], whatif.amount_intent(ctx, t, amount, "cash"), ctx["value"])
    assert v1 == pytest.approx(value_after, abs=1e-6)
    bought = w1[t] / 100 * v1 - ctx["w0"][t] / 100 * ctx["value"]
    assert bought == pytest.approx(amount, abs=0.01)
    assert math.fsum(s["weights_pct"].values()) == pytest.approx(100.0, abs=0.05)


@pytest.mark.parametrize("amount", [0.0, -5.0])
def test_non_positive_amount_is_rejected(whatif, ctx, amount):
    with pytest.raises(ValueError):
        whatif.amount_intent(ctx, _unheld(ctx), amount, "pro_rata_all")


def test_unknown_funding_or_ticker_is_rejected(whatif, ctx):
    with pytest.raises(ValueError):
        whatif.amount_intent(ctx, _unheld(ctx), 100.0, "specific:NOPE")
    with pytest.raises(ValueError):
        whatif.amount_intent(ctx, "NOT_A_TICKER", 100.0, "pro_rata_all")


def test_amount_above_what_the_funding_holds_is_rejected(whatif, ctx):
    with pytest.raises(ValueError):
        whatif.scenario_for_amount(ctx, _unheld(ctx), ctx["value"] * 2, "pro_rata_all")
    specific = [fid for fid, _, _ in ctx["fundings"] if fid.startswith("specific:")]
    if specific:
        src = specific[0].split(":", 1)[1]
        too_much = ctx["w0"][src] / 100 * ctx["value"] * 1.5
        with pytest.raises(ValueError):
            whatif.scenario_for_amount(ctx, _unheld(ctx), too_much, specific[0])


def test_amount_scenario_has_the_precomputed_shape_and_neutral_narrative(whatif, ctx):
    d10 = _load("d10_site_for_amount", ROOT / "tests" / "dashboard" / "test_d10_site.py")
    t = _unheld(ctx)
    s = whatif.scenario_for_amount(ctx, t, 3000.0, "cash")
    precomputed = whatif.scenarios(ctx, [t])[t]["scenarios"]["5|pro_rata_all"]
    assert set(s) == set(precomputed)
    for sec in s["sections"]:
        for seg in sec["segments"]:
            low = d10.norm(seg["text"])
            assert [p for p in d10.NORM_BLOCKLIST if p in low] == [], seg["text"]
            assert d10.FORBIDDEN_WORD.search(seg["text"]) is None, seg["text"]


def test_cash_funding_is_live_only(whatif, ctx):
    base = whatif.universe(ctx)["base"]
    assert whatif.CASH_FUNDING not in [f["id"] for f in base["fundings"]]
    assert [f["id"] for f in base["live_fundings"]] == [whatif.CASH_FUNDING]
