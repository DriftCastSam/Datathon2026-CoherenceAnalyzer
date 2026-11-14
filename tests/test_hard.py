"""Hard checks H1-H10, H14 + verdict on the Robert fixture (SPEC §7.3, §7.5, §13, §15 test_hard).

P0 covers Robert only. Amira (H2 at `high`) and Mateo (H11) cases are P1 and live elsewhere later.
Engine-backed checks (H7-H10, H14) are pinned as properties of `engine.analyse` output against the
limits, except where docs/spec-decisions.md D2 records the measured status (Robert + NVDA: H8, H10).
"""
import json

import pytest

from coherence_analyzer import data, engine
from coherence_analyzer.coherence import hard, report
from coherence_analyzer.rules import derive, limits as limits_mod

AS_OF = "2026-11-12"          # portfolio.as_of (docs/spec-decisions.md D1); no clock reads
ROBERT_VALUE = 400_000        # fixtures/robert/portfolio.json value_cad
ROBERT_HORIZON_M = 48         # SPEC §3.5 fixture horizon
META_NAMES = ("asset_meta", "etf_sector_weights", "activity_tags")

CHECK_KEYS = {"id", "kind", "status", "subject", "value", "limit", "unit",
              "derived_from", "source", "confidence", "message_key"}
HARD_STATUSES = {"pass", "violation", "info", "unknown"}
VERDICTS = {"coherent", "coherent_with_warnings", "incoherent"}
BROAD = "Broad market"

# check id -> the limits key it compares against (SPEC §7.2/§7.3)
LIMIT_OF = {
    "H1": "excluded_asset_types", "H2": "max_crypto_weight", "H3": "max_single_stock_weight",
    "H4": "min_fixed_income_weight", "H5": "max_foreign_currency_weight", "H6": "max_employer_sector_weight",
    "H7": "vol_band_annual", "H8": "max_drawdown_pct", "H9": "floor_at_horizon", "H10": "max_sector_risk_share",
}
LLM_PATHS = ("risk.behavioural", "knowledge.question_quality", "knowledge.score")


# ---------------------------------------------------------------- fixtures

@pytest.fixture(scope="module")
def profile(root) -> dict:
    raw = json.loads((root / "fixtures" / "robert" / "profile.json").read_text())
    p = derive.derive_all(raw, portfolio_value_cad=ROBERT_VALUE, as_of=AS_OF)
    lim, trace = limits_mod.build_limits(p, ROBERT_VALUE)
    p["limits"], p["limits_trace"] = lim, trace
    return p


@pytest.fixture(scope="module")
def assets():
    return data.load_assets()


@pytest.fixture(scope="module")
def meta() -> dict:
    return {n: data.load_meta(n) for n in META_NAMES}


@pytest.fixture(scope="module")
def robert_pf(root, assets) -> dict:
    return data.load_portfolio(root / "fixtures" / "robert" / "portfolio.json", assets)


def _fractions(weights_pct: dict[str, float]) -> dict[str, float]:
    return {t: v / 100 for t, v in weights_pct.items()}


def _variant(base_pct: dict[str, float], **delta_pct: float) -> dict[str, float]:
    """Robert with some tickers changed (percent). Keys use '_' for '.' (e.g. ZST_TO)."""
    out = dict(base_pct)
    for k, v in delta_pct.items():
        t = k.replace("_TO", ".TO")
        out[t] = out.get(t, 0) + v
        if out[t] == 0:
            del out[t]
    assert sum(out.values()) == pytest.approx(100, abs=1e-9)
    return out


@pytest.fixture(scope="module")
def m_base(market) -> dict:
    return engine.analyse(market, engine.ROBERT, ROBERT_HORIZON_M, ROBERT_VALUE)


@pytest.fixture(scope="module")
def m_nvda(market) -> dict:
    return engine.analyse(market, engine.ROBERT_NVDA, ROBERT_HORIZON_M, ROBERT_VALUE)


@pytest.fixture(scope="module")
def checks_base(profile, assets, meta, m_base) -> list:
    return hard.run_all(_fractions(engine.ROBERT), m_base, profile, assets, meta)


@pytest.fixture(scope="module")
def checks_nvda(profile, assets, meta, m_nvda) -> list:
    return hard.run_all(_fractions(engine.ROBERT_NVDA), m_nvda, profile, assets, meta)


def _by_id(checks: list, cid: str) -> list:
    return [c for c in checks if c["id"] == cid]


def _one(checks: list, cid: str, status: str | None = None) -> dict:
    found = [c for c in _by_id(checks, cid) if status is None or c["status"] == status]
    assert len(found) == 1, (cid, status, found)
    return found[0]


def _statuses(checks: list, cid: str) -> set[str]:
    return {c["status"] for c in _by_id(checks, cid)}


# ---------------------------------------------------------------- fixture sanity

def test_robert_inputs_match_spec(robert_pf, profile):
    """Fixture portfolio == engine.ROBERT; intent NVDA 10% from ZST == engine.ROBERT_NVDA; limits as §7.2/§15."""
    assert robert_pf["weights_pct"] == engine.ROBERT
    assert engine.ROBERT_NVDA == _variant(engine.ROBERT, NVDA=10, ZST_TO=-10)
    lim = profile["limits"]
    assert lim["max_single_stock_weight"] == 0.08
    assert lim["min_fixed_income_weight"] == 0.30
    assert lim["max_foreign_currency_weight"] == 0.40
    assert lim["max_crypto_weight"] == 0.0
    assert lim["max_employer_sector_weight"] == 0.15
    assert "crypto" in lim["excluded_asset_types"]


# ---------------------------------------------------------------- §7.3 result shape + invariant 3

@pytest.mark.parametrize("case", ["base", "nvda"])
def test_every_check_has_spec_keys_and_hard_semantics(case, checks_base, checks_nvda, profile):
    checks = checks_base if case == "base" else checks_nvda
    ids = {c["id"] for c in checks}
    assert {f"H{i}" for i in range(1, 11)} | {"H14"} <= ids
    for c in checks:
        assert CHECK_KEYS <= set(c), (c.get("id"), CHECK_KEYS - set(c))
        assert c["status"] in HARD_STATUSES, c
        assert c["kind"] == "hard", c
        assert isinstance(c["source"], str) and c["source"].startswith("rule:"), c
        # SPEC-AMBIGUITY: §7.3 example shows confidence 1.0; hard checks are deterministic so we require exactly 1.0.
        assert c["confidence"] == 1.0, c
        assert isinstance(c["message_key"], str) and c["message_key"], c
        assert isinstance(c["unit"], str) and c["unit"], c
        assert isinstance(c["derived_from"], list) and c["derived_from"], c
        assert all(isinstance(p, str) for p in c["derived_from"]), c


@pytest.mark.parametrize("case", ["base", "nvda"])
def test_derived_from_traces_back_to_limit_and_profile(case, checks_base, checks_nvda, profile):
    """§7.3: derived_from = ["limits.<name>", ...limits_trace[name]]; never an llm: path (invariant 2)."""
    checks = checks_base if case == "base" else checks_nvda
    for c in checks:
        lim = LIMIT_OF.get(c["id"])
        if lim is None:
            continue
        assert f"limits.{lim}" in c["derived_from"], c
        assert set(profile["limits_trace"][lim]) <= set(c["derived_from"]), c
        for p in c["derived_from"]:
            assert not p.startswith(LLM_PATHS), (c["id"], p)


def test_hard_checks_never_emit_warning(checks_base, checks_nvda):
    """Invariant 3 / §7.3: hard statuses are pass|violation|info|unknown; violations only from hard rule: checks."""
    for c in checks_base + checks_nvda:
        assert c["status"] != "warning", c
        if c["status"] == "violation":
            assert c["kind"] == "hard" and c["source"].startswith("rule:"), c


# ---------------------------------------------------------------- Robert baseline (§15)

def test_baseline_h1_h2_pass(checks_base):
    assert _statuses(checks_base, "H1") == {"pass"}
    h2 = _one(checks_base, "H2")
    assert h2["status"] == "pass"
    assert h2["value"] == pytest.approx(0.0, abs=1e-9)
    assert h2["limit"] == 0.0


def test_baseline_h3_single_stock_pass_max_ry(checks_base):
    h3 = _by_id(checks_base, "H3")
    assert h3 and {c["status"] for c in h3} == {"pass"}
    assert max(c["value"] for c in h3) == pytest.approx(0.07, abs=1e-9)
    assert all(c["limit"] == 0.08 for c in h3)
    # SPEC-AMBIGUITY: RY.TO and FTS.TO tie at 7%; §15 names RY as the max, so RY.TO must be reported at 0.07.
    assert any(c["subject"] == "RY.TO" and c["value"] == pytest.approx(0.07, abs=1e-9) for c in h3)


def test_baseline_h4_fixed_income_pass(checks_base):
    h4 = _one(checks_base, "H4")
    assert h4["status"] == "pass"
    assert h4["value"] == pytest.approx(0.35, abs=1e-9)
    assert h4["limit"] == 0.30


def test_baseline_h5_foreign_currency_pass(checks_base):
    h5 = _one(checks_base, "H5")
    assert h5["status"] == "pass"
    assert h5["value"] == pytest.approx(0.27, abs=1e-9)   # JNJ 5 + PG 5 + SPY 12 + GLD 5
    assert h5["limit"] == 0.40


def test_baseline_h6_employer_sector_pass_plus_etf_unknown(checks_base, meta):
    """Health Care: JNJ 5% direct; XIU/SPY have no look-through file -> also an `unknown` for the ETF part."""
    h6 = _by_id(checks_base, "H6")
    assert "violation" not in {c["status"] for c in h6}
    direct = _one(checks_base, "H6", "pass")
    assert direct["value"] == pytest.approx(0.05, abs=1e-9)
    assert direct["limit"] == 0.15
    assert meta["etf_sector_weights"] is None   # look-through not built in P0
    _one(checks_base, "H6", "unknown")


# ---------------------------------------------------------------- engine checks as properties (§7.3)

def _expected_engine_statuses(m: dict, lim: dict) -> dict[str, str]:
    lo, hi = lim["vol_band_annual"]
    h7 = "violation" if m["vol"] > hi else "info" if m["vol"] < lo else "pass"
    h8 = "violation" if m["mdd"] < lim["max_drawdown_pct"] / 100 else "pass"   # limit in percent (§7.2)
    h9 = "violation" if m["p5_T"] < lim["floor_at_horizon"] else "pass"
    # SPEC-AMBIGUITY: every non-broad-market group in groups.sector counts (incl. Bonds/Gold), the stricter reading.
    shares = [g["risk"] for g in m["groups"]["sector"] if not g["name"].startswith(BROAD)]
    h10 = "violation" if max(shares) > lim["max_sector_risk_share"] else "pass"
    return {"H7": h7, "H8": h8, "H9": h9, "H10": h10}


@pytest.mark.parametrize("case", ["base", "nvda"])
def test_h7_h10_follow_status_logic(case, checks_base, checks_nvda, m_base, m_nvda, profile):
    checks, m = (checks_base, m_base) if case == "base" else (checks_nvda, m_nvda)
    expected = _expected_engine_statuses(m, profile["limits"])
    for cid in ("H7", "H8", "H9"):
        c = _one(checks, cid)
        assert c["status"] == expected[cid], (cid, c)
    assert _one(checks, "H7")["value"] == pytest.approx(m["vol"], abs=1e-9)
    assert _one(checks, "H8")["value"] == pytest.approx(m["mdd"], abs=1e-9)
    assert _one(checks, "H9")["value"] == pytest.approx(m["p5_T"], abs=1e-9)
    assert _one(checks, "H9")["limit"] == pytest.approx(360_000, abs=1e-9)
    h10 = {c["status"] for c in _by_id(checks, "H10")} - {"unknown"}
    assert h10 == {expected["H10"]}


@pytest.mark.parametrize("case", ["base", "nvda"])
def test_h10_broad_market_flagged_unknown_without_lookthrough(case, checks_base, checks_nvda, m_base, m_nvda):
    """§7.3 H10: broad-market ETFs (XIU, SPY) are excluded from the max when look-through is off, and flagged unknown."""
    checks, m = (checks_base, m_base) if case == "base" else (checks_nvda, m_nvda)
    assert m["lookthrough"] is False
    assert any(g["name"].startswith(BROAD) for g in m["groups"]["sector"])
    _one(checks, "H10", "unknown")
    for c in _by_id(checks, "H10"):
        if c["status"] != "unknown":
            assert not str(c["subject"]).startswith(BROAD), c


def test_h14_robert_real_history(checks_base, checks_nvda, m_base, m_nvda, profile):
    """Robert says sell_some: H14 is info iff engine.mdd < -0.20; never a violation."""
    assert profile["risk"]["reaction_to_20pct_drop"] == "sell_some"
    for checks, m in ((checks_base, m_base), (checks_nvda, m_nvda)):
        h14 = _one(checks, "H14")
        # SPEC-AMBIGUITY: §7.3 only defines the triggered case (info); the untriggered status is taken as `pass`.
        assert h14["status"] == ("info" if m["mdd"] < -0.20 else "pass")


@pytest.mark.parametrize("reaction,mdd,expected", [
    ("sell_some", -0.25, "info"),
    ("sell_all", -0.25, "info"),
    ("sell_some", -0.20, "pass"),     # strict inequality: mdd < -0.20
    ("hold", -0.25, "pass"),
    ("buy_more", -0.25, "pass"),
])
def test_h14_status_logic(profile, m_base, reaction, mdd, expected):
    p = json.loads(json.dumps(profile))
    p["risk"]["reaction_to_20pct_drop"] = reaction
    m = dict(m_base, mdd=mdd, mdd_date="2022-09")
    h14 = _one(hard.run_engine(m, p), "H14")
    assert h14["status"] == expected
    assert h14["kind"] == "hard" and h14["source"].startswith("rule:")
    assert "risk.reaction_to_20pct_drop" in h14["derived_from"]
    if expected == "info":
        assert h14["value"] == pytest.approx(mdd, abs=1e-9)
        # §7.3: info carries "the drawdown and its date"
        assert "2022-09" in json.dumps(h14, default=str)


# ---------------------------------------------------------------- Robert + NVDA 10% from ZST (§15)

def test_nvda_h3_violation(checks_nvda):
    h3 = _by_id(checks_nvda, "H3")
    over = [c for c in h3 if c["status"] == "violation"]
    assert len(over) == 1                          # one result per ticker over; RY at 0.07 is not
    c = over[0]
    assert c["subject"] == "NVDA"
    assert c["value"] == pytest.approx(0.10, abs=1e-9)
    assert c["limit"] == 0.08
    assert c["unit"] == "weight"
    assert c["source"] == "rule:max_single_stock"
    assert c["message_key"] == "single_stock_over"
    assert c["derived_from"][0] == "limits.max_single_stock_weight"


def test_nvda_h4_violation(checks_nvda):
    h4 = _one(checks_nvda, "H4")
    assert h4["status"] == "violation"
    assert h4["value"] == pytest.approx(0.25, abs=1e-9)
    assert h4["limit"] == 0.30


def test_nvda_h5_pass(checks_nvda):
    h5 = _one(checks_nvda, "H5")
    assert h5["status"] == "pass"
    assert h5["value"] == pytest.approx(0.37, abs=1e-9)
    assert h5["limit"] == 0.40


def test_nvda_h1_h2_h6_unchanged(checks_nvda):
    assert _statuses(checks_nvda, "H1") == {"pass"}
    assert _one(checks_nvda, "H2")["status"] == "pass"
    assert "violation" not in _statuses(checks_nvda, "H6")    # NVDA is IT, employer sector is Health Care
    assert _one(checks_nvda, "H6", "pass")["value"] == pytest.approx(0.05, abs=1e-9)


def test_nvda_h8_h10_violation_measured(checks_nvda):
    """docs/spec-decisions.md D2: Robert + NVDA 10% fails H8 (mdd -13.4% vs -12%) and H10 (IT 34.7% vs 30%)."""
    assert _one(checks_nvda, "H8")["status"] == "violation"
    h10 = _one(checks_nvda, "H10", "violation")
    assert h10["subject"] == "Information Technology"
    assert h10["limit"] == 0.30


def test_nvda_verdict_incoherent(checks_nvda):
    assert report.verdict(checks_nvda) == "incoherent"


# ---------------------------------------------------------------- cheap-check status logic on Robert variants

def _cheap(profile, assets, meta, weights_pct):
    return hard.run_cheap(_fractions(weights_pct), profile, assets, meta)


def test_crypto_triggers_h1_and_h2(profile, assets, meta):
    """Robert refuses crypto and max_crypto = 0 at low_moderate: 1% BTC is H1 and H2 violation."""
    checks = _cheap(profile, assets, meta, _variant(engine.ROBERT, **{"BTC-USD": 1}, ZST_TO=-1))
    h1 = _one(checks, "H1", "violation")
    assert h1["subject"] == "BTC-USD"
    h2 = _one(checks, "H2")
    assert h2["status"] == "violation"
    assert h2["value"] == pytest.approx(0.01, abs=1e-9)
    assert h2["limit"] == 0.0


def test_boundaries_are_inclusive(profile, assets, meta):
    """NVDA 8% from ZST: single stock 0.08 is not > 0.08; fixed income 0.30 is not < 0.30."""
    checks = _cheap(profile, assets, meta, _variant(engine.ROBERT, NVDA=8, ZST_TO=-8))
    # SPEC-AMBIGUITY: weights come from percent/100; float noise at exactly the limit must not flip the status.
    assert "violation" not in _statuses(checks, "H3")
    assert _one(checks, "H4")["status"] == "pass"
    assert _one(checks, "H4")["value"] == pytest.approx(0.30, abs=1e-9)


def test_h5_foreign_currency_over(profile, assets, meta):
    """SPY +14 from ZST: foreign = 0.27 + 0.14 = 0.41 > 0.40."""
    checks = _cheap(profile, assets, meta, _variant(engine.ROBERT, SPY=14, ZST_TO=-13, XBB_TO=-1))
    h5 = _one(checks, "H5")
    assert h5["status"] == "violation"
    assert h5["value"] == pytest.approx(0.41, abs=1e-9)


def test_h6_employer_sector_over(profile, assets, meta):
    """JNJ +11 from ZST: Health Care direct equity = 0.16 > 0.15 -> H6 violation (and H3 for JNJ)."""
    checks = _cheap(profile, assets, meta, _variant(engine.ROBERT, JNJ=11, ZST_TO=-11))
    h6 = _one(checks, "H6", "violation")
    assert h6["value"] == pytest.approx(0.16, abs=1e-9)
    assert h6["limit"] == 0.15
    assert {c["subject"] for c in _by_id(checks, "H3") if c["status"] == "violation"} == {"JNJ"}


def test_run_all_is_cheap_plus_engine(profile, assets, meta, m_base, checks_base):
    cheap = hard.run_cheap(_fractions(engine.ROBERT), profile, assets, meta)
    eng = hard.run_engine(m_base, profile)
    assert checks_base == cheap + eng
    assert {c["id"] for c in cheap} <= set(hard.CHEAP)
    assert {"H7", "H8", "H9", "H10", "H14"} <= {c["id"] for c in eng}


def test_deterministic(profile, assets, meta, m_base, checks_base):
    again = hard.run_all(_fractions(engine.ROBERT), m_base, profile, assets, meta)
    assert again == checks_base


# ---------------------------------------------------------------- verdict + unknowns (§7.5, D3)

def test_baseline_verdict_follows_rule(checks_base):
    v = report.verdict(checks_base)
    assert v in VERDICTS
    assert v == ("incoherent" if any(c["status"] == "violation" for c in checks_base) else "coherent")


@pytest.mark.parametrize("case", ["base", "nvda"])
def test_unknowns_listed_separately(case, checks_base, checks_nvda):
    """§7.5: unknown items are always listed under "what we can't verify", whatever the verdict."""
    checks = checks_base if case == "base" else checks_nvda
    u = report.unknowns(checks)
    assert u == [c for c in checks if c["status"] == "unknown"]
    assert {"H6", "H10"} <= {c["id"] for c in u}


def _c(cid: str, status: str, kind: str = "hard") -> dict:
    return {"id": cid, "kind": kind, "status": status}


@pytest.mark.parametrize("statuses,expected", [
    (["pass", "pass"], "coherent"),
    (["pass", "info"], "coherent"),
    (["pass", "unknown"], "coherent"),                       # D3: unknowns never change the verdict
    (["pass", "warning"], "coherent_with_warnings"),
    (["warning", "unknown"], "coherent_with_warnings"),
    (["violation", "warning", "unknown"], "incoherent"),
    (["violation", "pass"], "incoherent"),
])
def test_verdict_rule(statuses, expected):
    checks = [_c(f"X{i}", s, "soft" if s == "warning" else "hard") for i, s in enumerate(statuses)]
    assert report.verdict(checks) == expected
