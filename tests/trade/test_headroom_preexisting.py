"""Headroom counts only what the TRADE does, not breaches the current portfolio already has (P3.RESIZE).

Sources: SPEC §8 ("how far can *this* trade ... go before it crosses one of my own limits", step 4: `none_fit` when
"even the smallest step crosses a limit"), §4 Flow B B7 ("if proposed has violations -> resizer"), §7.5 (diff = checks
whose status changed between current and proposed), docs/spec-decisions.md D2 (Robert + NVDA golden numbers stay
as they are) and D10 (headroom is a measurement, never a suggestion).

Audit gap reproduced on the starter data: Amira (60 000 CAD, horizon 240 months) already breaches H2 (crypto 15 % vs 10 %),
H5 (foreign currency 76 % vs 70 %), H6 (employer sector 16 % vs 15 %) and H9 before any trade. "Buy 2 % MSFT funded from SPY" gave
`{'none_fit': True, 'binding_checks': ['H2', 'H5', 'H6']}` and the template said the trade crosses H2 "from the
first step", although the trade does not touch crypto or the foreign-currency weight (MSFT and SPY are both USD).

D19: with real ETF look-through data, Amira also already breaches H10 (Information Technology risk share 45.41 %
vs a 45 % limit -- extremely tight). The engine's risk-share is a portfolio-wide decomposition, not a per-asset
sector weight, so even a trade that touches no sector weight at all can nudge it; the "AGG from GLD" example below
turns out to be exactly such a case (see its fixture comment and the tests that use it).

Rules pinned here:
  * a binding check is one the trade makes violate (ok on the current side -> violation on the proposed side);
  * a breach already present and left unchanged by the trade is never binding and never makes `none_fit` true;
  * # SPEC-AMBIGUITY (below): a pre-existing breach that the trade makes worse is binding (stricter reading);
  * `pipeline.b7_resize` runs the resizer only when the trade adds (or worsens) a violation.

Everything is offline: starter data, seed 7 (engine), mock LLM, no clock reads.
"""
import copy
import json
import os

import pytest

os.environ.setdefault("COHERENCE_ANALYZER_LLM", "mock")

from coherence_analyzer import config, data, engine, intent as intent_mod, pipeline, resizer  # noqa: E402
from coherence_analyzer.coherence import hard  # noqa: E402
from coherence_analyzer.rules import derive, limits as limits_mod  # noqa: E402

META_NAMES = ("asset_meta", "etf_sector_weights", "activity_tags")
ROBERT_HORIZON_M = 48                 # SPEC §3.5 fixture horizon (tests/trade/test_resizer.py)
AMIRA_HORIZON_M = 240                 # pipeline._horizon_months(amira profile): horizon_years 20
ROBERT_ZST = {"kind": "specific", "ticker": "ZST.TO"}
FLOOR_370K = 370_000.0                # above Robert's baseline p5_T 366 719, so H9 fails at w = 0
NO_CRYPTO = -0.01                     # max_crypto_weight < 0: Robert holds no crypto, so H2 is already "violated" (0 > -0.01)


# ---------------------------------------------------------------- builders

def _intent(ticker: str, target: float, funding: dict) -> dict:
    return {"intent_id": "in_0001", "event_id": "ev_0001", "action": "buy", "ticker": ticker,
            "target_weight_pct": target, "funding": funding, "status": "confirmed",
            "confirmed_at": "2026-11-14T10:05:00-05:00"}


def _profile(root, persona: str, value_cad: float, as_of: str) -> dict:
    raw = json.loads((root / "fixtures" / persona / "profile.json").read_text())
    p = derive.derive_all(raw, portfolio_value_cad=value_cad, as_of=as_of)
    lim, trace = limits_mod.build_limits(p, value_cad)
    p["limits"], p["limits_trace"] = lim, trace
    return p


def _portfolio(root, persona: str, horizon_m: int) -> dict:
    pf = data.load_portfolio(root / "fixtures" / persona / "portfolio.json", data.load_assets())
    pf["horizon_months"] = horizon_m      # SPEC-AMBIGUITY: §8 gives the resizer no horizon argument (see test_resizer.py)
    return pf


def _meta() -> dict:
    return {n: data.load_meta(n) for n in META_NAMES}


def _hard_checks(market, weights_pct: dict, value: float, horizon_m: int, profile: dict) -> list:
    assets = data.load_assets()
    w = {t: v for t, v in weights_pct.items() if v > 0}
    cheap = hard.run_cheap({t: v / 100 for t, v in w.items()}, profile, assets, _meta())
    m = engine.analyse(market, w, horizon_m, value, n=config.ENGINE_N)
    return cheap + hard.run_engine(m, profile)


def _b7_state(market, pf: dict, profile: dict, intent: dict) -> dict:
    """The keys `pipeline.b7_resize` can read: both sides' hard checks (SPEC §4 B6) + portfolio, intent, profile."""
    weights, value = intent_mod.apply(pf["weights_pct"], intent, pf["value_cad"])
    h = pf["horizon_months"]
    return {"portfolio": {k: v for k, v in pf.items() if k != "horizon_months"},
            "proposed": {"weights_pct": weights, "value_cad": value},
            "intent": intent, "profile": profile, "horizon_months": h,
            "checks_current": _hard_checks(market, pf["weights_pct"], pf["value_cad"], h, profile),
            "checks_proposed": _hard_checks(market, weights, value, h, profile)}


def _violating(checks: list) -> set:
    return {(c["id"], c.get("subject")) for c in checks if c["status"] == "violation"}


# ---------------------------------------------------------------- fixtures

@pytest.fixture(scope="module")
def robert_profile(root) -> dict:
    return _profile(root, "robert", 400_000, "2026-11-12")


@pytest.fixture(scope="module")
def robert_pf(root) -> dict:
    return _portfolio(root, "robert", ROBERT_HORIZON_M)


@pytest.fixture(scope="module")
def robert_breach(robert_profile) -> dict:
    """Robert's profile with a breach that exists before any trade and that an NVDA / ZST.TO trade cannot touch."""
    p = copy.deepcopy(robert_profile)
    p["limits"]["max_crypto_weight"] = NO_CRYPTO
    return p


@pytest.fixture(scope="module")
def amira_profile(root) -> dict:
    return _profile(root, "amira", 60_000, "2026-11-12")


@pytest.fixture(scope="module")
def amira_pf(root) -> dict:
    return _portfolio(root, "amira", AMIRA_HORIZON_M)


@pytest.fixture(scope="module")
def meta() -> dict:
    return _meta()


@pytest.fixture(scope="module")
def robert_nvda_golden(robert_pf, robert_profile, market, meta) -> dict:
    return resizer.resize(robert_pf, _intent("NVDA", 10, ROBERT_ZST), robert_profile, market, meta)


@pytest.fixture(scope="module")
def amira_msft_from_spy() -> dict:
    return _intent("MSFT", 2, {"kind": "specific", "ticker": "SPY"})


@pytest.fixture(scope="module")
def amira_agg_from_gld() -> dict:
    # AGG (bond, USD) funded from GLD (gold, USD): crypto, foreign-currency and tech SECTOR WEIGHT do not move, and
    # the engine floor p5_T only rises (measured at n=1000 and n=5000, every 0.5 pp step: 18 379 -> 18 608 at 12 %).
    # D19: GLD's low correlation was offsetting more portfolio risk than AGG does, so this trade (while inert on
    # every weight-based check) nudges the engine's risk-share decomposition and slightly worsens the already-tight
    # H10 (IT risk share 45.41 % -> 45.48 % at 12 %, vs a 45 % limit) at every step, however small.
    return _intent("AGG", 12, {"kind": "specific", "ticker": "GLD"})


# ================================================================ the premise: these breaches exist before the trade

def test_premise_amira_breaches_h2_h5_h6_before_the_msft_trade_and_it_leaves_h2_h5_unchanged(
        amira_pf, amira_profile, amira_msft_from_spy, market):
    # Measured premise of the audit gap (SPEC §7.3 H2/H5/H6): the three breaches are on the CURRENT side already, and
    # the trade (MSFT and SPY are both USD, neither is crypto) leaves H2 and H5 values exactly where they were.
    st = _b7_state(market, amira_pf, amira_profile, amira_msft_from_spy)
    cur, prop = st["checks_current"], st["checks_proposed"]
    assert {("H2", None), ("H5", None), ("H6", "Information Technology")} <= _violating(cur)
    assert {("H2", None), ("H5", None), ("H6", "Information Technology")} <= _violating(prop)
    for cid in ("H2", "H5"):
        before = [c["value"] for c in cur if c["id"] == cid]
        after = [c["value"] for c in prop if c["id"] == cid]
        assert after == pytest.approx(before, abs=1e-9), cid


# ================================================================ resizer: binding checks are the trade's own

def test_amira_msft_from_spy_never_names_a_breach_the_trade_does_not_touch(amira_pf, amira_profile,
                                                                           amira_msft_from_spy, market, meta):
    # SPEC §8 step 4: `binding_checks` = limits the smallest step "crosses". H2 and H5 are unchanged by the trade,
    # so they are not crossed by it (this was ['H2', 'H5', 'H6']).
    res = resizer.resize(amira_pf, amira_msft_from_spy, amira_profile, market, meta)
    ids = set(res["binding_checks"]) if res.get("none_fit") else {res["binding_check"]}
    assert "H2" not in ids and "H5" not in ids
    # SPEC-AMBIGUITY: §8 does not say whether a breach the trade makes WORSE counts; stricter reading: it does. H6 (employer
    # sector 16 % -> 18 % at 2 %) is the only unmet-and-worsened cheap check, so it is what binds from the first step.
    assert res.get("none_fit") is True
    assert "H6" in res["binding_checks"]
    # What the trade changes among violations at 2 %: H6 (worse) and the engine floor H9 (p5_T lower). Nothing else can bind.
    assert set(res["binding_checks"]) <= {"H6", "H9"}


def test_headroom_text_does_not_say_the_trade_crosses_a_limit_it_does_not_touch(amira_pf, amira_profile,
                                                                               amira_msft_from_spy, market, meta):
    # The template text states the binding checks as "this trade crosses your limit ..." (SPEC §8, D10): naming H2 or H5
    # here is a false statement about the user's trade.
    import re
    res = resizer.resize(amira_pf, amira_msft_from_spy, amira_profile, market, meta)
    for locale in ("en-CA", "fr-CA"):
        text = resizer.headroom_text(res, amira_msft_from_spy["funding"], amira_msft_from_spy["target_weight_pct"], locale)
        assert text and not re.search(r"\bH[25]\b", text), text


def test_a_breach_already_present_and_worsened_by_the_trade_still_makes_none_fit(
        amira_pf, amira_profile, amira_agg_from_gld, market, meta):
    # D19: Amira's preexisting H10 (IT risk share 45.41 % vs a 45 % limit) is so tight that AGG-from-GLD -- inert on
    # every weight-based check (H2/H5/H6 untouched, H9 only improves) -- still worsens it at every step through the
    # engine's portfolio-wide risk decomposition (see the fixture comment). Per the stricter SPEC-AMBIGUITY reading
    # pinned at the top of this file, a worsened preexisting breach binds: none_fit is True here, not False.
    res = resizer.resize(amira_pf, amira_agg_from_gld, amira_profile, market, meta)
    assert res.get("none_fit") is True
    assert res["binding_checks"] == ["H10"]


def test_unchanged_preexisting_breach_is_not_binding_for_robert_and_the_golden_numbers_do_not_move(
        robert_pf, robert_breach, robert_profile, robert_nvda_golden, market, meta):
    # D2: Robert + NVDA 10 % from ZST.TO = 1.5 %, binding H9. Adding a breach that exists before the trade and that the
    # trade cannot touch (H2: no crypto held, limit below 0) must not change a single number or the binding check.
    res = resizer.resize(robert_pf, _intent("NVDA", 10, ROBERT_ZST), robert_breach, market, meta)
    assert not res.get("none_fit")
    assert res["max_weight_pct"] == pytest.approx(1.5, abs=1e-9)
    assert res["binding_check"] == "H9"
    assert json.dumps(res, sort_keys=True, default=str) == json.dumps(robert_nvda_golden, sort_keys=True, default=str)


def test_a_trade_that_fits_is_capped_at_its_target_despite_a_preexisting_breach(robert_pf, robert_breach, market, meta):
    # tests/trade/test_resizer.py: NVDA 1 % from ZST.TO passes every check (p5_T >= 363 150) -> max = target. A breach
    # already present (H2) must not turn this into none_fit with binding H2 (the old behaviour).
    res = resizer.resize(robert_pf, _intent("NVDA", 1.0, ROBERT_ZST), robert_breach, market, meta)
    assert not res.get("none_fit")
    assert res["max_weight_pct"] == pytest.approx(1.0, abs=1e-9)
    assert res["binding_check"] is None


def test_none_fit_names_only_the_check_the_trade_adds_not_the_preexisting_one(robert_pf, robert_breach, market, meta):
    # Robert's baseline p5_T is 366 719: a 370 000 floor is breached at w = 0, so H9 is crossed by the very first step
    # (tests/trade/test_resizer.py: binding {H9}). H2 is breached too, before the trade, and must not be listed with it.
    p = copy.deepcopy(robert_breach)
    p["limits"]["floor_at_horizon"] = FLOOR_370K
    res = resizer.resize(robert_pf, _intent("NVDA", 10, ROBERT_ZST), p, market, meta)
    assert res["none_fit"] is True
    assert set(res) == {"none_fit", "binding_checks"}
    assert "H2" not in res["binding_checks"]
    assert set(res["binding_checks"]) == {"H9"}


def test_a_trade_that_worsens_a_preexisting_breach_counts_as_crossing_it(robert_pf, robert_breach, market, meta):
    # SPEC-AMBIGUITY: §8 "the first of the user's limits the trade crosses" does not say if pushing further past a limit
    # already breached counts. Stricter reading (and the one tests/trade/test_resizer.py's frozen none_fit cases need:
    # a 370 000 floor or a 40 % fixed-income minimum already breached at w = 0, still binding): a worsened breach binds.
    # Robert buys BTC-USD (crypto 0 % -> 0.5 %) with max_crypto_weight < 0: H2 was breached and the trade makes it worse.
    res = resizer.resize(robert_pf, _intent("BTC-USD", 2.0, ROBERT_ZST), robert_breach, market, meta)
    assert res["none_fit"] is True
    assert "H2" in res["binding_checks"]


def test_resize_with_a_preexisting_breach_is_deterministic_and_does_not_mutate_inputs(
        robert_pf, robert_breach, market, meta):
    pf, pr = copy.deepcopy(robert_pf), copy.deepcopy(robert_breach)
    it = _intent("NVDA", 10, ROBERT_ZST)
    it0 = copy.deepcopy(it)
    a = resizer.resize(robert_pf, it, robert_breach, market, meta)
    b = resizer.resize(robert_pf, it, robert_breach, market, meta)
    assert json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)
    assert robert_pf == pf and robert_breach == pr and it == it0


# ================================================================ pipeline.b7_resize: only when the trade adds a violation

@pytest.fixture
def resizer_spy(monkeypatch):
    calls: list[dict] = []
    real = resizer.resize

    def spy(portfolio, intent, profile, market, meta):
        calls.append(intent)
        return real(portfolio, intent, profile, market, meta)

    monkeypatch.setattr(pipeline.resizer, "resize", spy)
    return calls


def test_b7_skips_the_resizer_when_the_only_violations_were_there_before_the_trade(
        robert_pf, robert_breach, market, resizer_spy):
    # SPEC §4 B7 "if proposed has violations -> resizer", read with §8 (a measurement of THIS trade): NVDA 1 % from ZST.TO
    # adds nothing, Robert's H2 breach is on both sides unchanged -> headroom None, the resizer is not run.
    st = _b7_state(market, robert_pf, robert_breach, _intent("NVDA", 1.0, ROBERT_ZST))
    assert ("H2", None) in _violating(st["checks_current"]) and ("H2", None) in _violating(st["checks_proposed"])
    assert _violating(st["checks_proposed"]) == _violating(st["checks_current"])
    out = pipeline.b7_resize(st)
    assert out["headroom"] is None
    assert resizer_spy == []


def test_b7_runs_the_resizer_for_amira_agg_from_gld_that_worsens_her_tight_h10(
        amira_pf, amira_profile, amira_agg_from_gld, market, resizer_spy):
    # D19: the violating id sets are identical on both sides (same checks, same subjects, as before the trade),
    # but `resizer.violations_added` compares VALUES, not just ids -- AGG-from-GLD worsens the already-tight H10
    # (see the fixture comment), so b7_resize still runs the resizer instead of skipping it.
    st = _b7_state(market, amira_pf, amira_profile, amira_agg_from_gld)
    assert _violating(st["checks_proposed"]) == _violating(st["checks_current"]) != set()
    out = pipeline.b7_resize(st)
    assert len(resizer_spy) == 1
    h = out["headroom"]
    assert h["none_fit"] is True
    assert h["binding_checks"] == ["H10"]


def test_b7_runs_the_resizer_when_the_trade_adds_violations_and_returns_the_golden_headroom(
        robert_pf, robert_breach, robert_nvda_golden, market, resizer_spy):
    # NVDA 10 % from ZST.TO adds H3, H4, H7-H10 on top of the pre-existing H2: the resizer runs, and its result is the
    # D2 golden (1.5 %, H9) -- the same as without the H2 breach.
    st = _b7_state(market, robert_pf, robert_breach, _intent("NVDA", 10, ROBERT_ZST))
    assert _violating(st["checks_proposed"]) > _violating(st["checks_current"])
    out = pipeline.b7_resize(st)
    assert len(resizer_spy) == 1
    h = out["headroom"]
    assert h["max_weight_pct"] == pytest.approx(1.5, abs=1e-9)
    assert h["binding_check"] == "H9"
    assert h["funding"] == ROBERT_ZST
    assert json.loads(json.dumps(h, sort_keys=True)) == json.loads(
        json.dumps(robert_nvda_golden, sort_keys=True, default=str))


def test_b7_amira_msft_from_spy_headroom_does_not_blame_h2_or_h5(amira_pf, amira_profile, amira_msft_from_spy, market,
                                                                resizer_spy):
    # SPEC-AMBIGUITY: the trade worsens H6 (16 % -> 18 %), so under the stricter reading it still "adds" a violation and the
    # resizer runs; what it must not do is report H2 / H5 (unchanged) as crossed by the trade.
    st = _b7_state(market, amira_pf, amira_profile, amira_msft_from_spy)
    out = pipeline.b7_resize(st)
    assert len(resizer_spy) == 1
    h = out["headroom"]
    assert h["none_fit"] is True
    assert "H2" not in h["binding_checks"] and "H5" not in h["binding_checks"]
    assert "H6" in h["binding_checks"]
