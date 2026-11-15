"""P2.16 (SPEC §17 step 16): rate-scenario stress from macro_rates.csv, duration-based, labelled as an assumption.

Sources: SPEC §3.1 (macro "P2: rate-scenario stress"; etf_bond = fixed income), §1 invariants 2, 3, 6, 9, §9.1, §13,
§17 step 16, §19. The stress block is `info` only: it never produces a violation or warning, never enters the verdict
and never feeds `limits`. Price impact of a bond ETF ~= -duration x Δy (Δy in decimal, 100 bp = 0.01); portfolio impact
in CAD = value_cad x Σ weight x price impact. Equities, crypto and gold get no modelled impact (said in `assumptions`).
There is no duration column in the starter data, so the default durations are a labelled team assumption
(`sourced: false`) unless asset_meta.csv carries a sourced `effective_duration`.

Hand-computable cases: 100 % TLT at duration 16 under +100 bp -> -16 % and -16 000 $ on 100 000 $; 0 % bonds -> 0.
"""
import json
import os
from pathlib import Path

import pandas as pd
import pytest
from jsonschema import Draft202012Validator

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

from coherence_analyzer import data, numfacts, pipeline  # noqa: E402

try:                                                # collected (and red) before the module exists
    from coherence_analyzer import stress
except ImportError:  # pragma: no cover
    stress = None

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "fixtures" / "robert"
AS_OF = "2026-11-12"
BONDS = {"XBB.TO", "ZST.TO", "AGG", "TLT"}           # etf_bond rows of the starter universe


@pytest.fixture(scope="module")
def assets():
    return data.load_assets()


@pytest.fixture(scope="module")
def macro():
    return data.load_macro()


@pytest.fixture(scope="module")
def schema():
    return json.loads((ROOT / "coherence_analyzer" / "schemas" / "report.json").read_text(encoding="utf-8"))


def _scenario(block: dict, sid: str) -> dict:
    hits = [s for s in block["scenarios"] if s["id"] == sid]
    assert len(hits) == 1, [s["id"] for s in block["scenarios"]]
    return hits[0]


# ---------------------------------------------------------------- formula

def test_bond_impact_is_minus_duration_times_delta_y():
    assert stress.bond_impact(16.0, 100) == pytest.approx(-0.16, abs=1e-12)
    assert stress.bond_impact(7.0, -100) == pytest.approx(0.07, abs=1e-12)      # rates down -> bond prices up
    assert stress.bond_impact(7.0, 0) == 0.0
    assert stress.bond_impact(0.3, 200) == pytest.approx(-0.006, abs=1e-12)


def test_bond_impact_convexity_term_only_when_given():
    # -10 x 0.01 + 0.5 x 100 x 0.01^2 = -0.10 + 0.005
    assert stress.bond_impact(10.0, 100, convexity=100.0) == pytest.approx(-0.095, abs=1e-12)
    assert stress.bond_impact(10.0, 100, convexity=None) == pytest.approx(-0.10, abs=1e-12)


# ---------------------------------------------------------------- the universe's bond ETFs

def test_every_starter_bond_etf_has_a_duration_and_a_rate_series(assets):
    etf_bond = {t for t, c in assets["asset_class"].items() if c == "etf_bond"}
    assert etf_bond == BONDS
    assert set(stress.ASSUMED_DURATIONS) >= etf_bond
    assert set(stress.RATE_SERIES) >= etf_bond
    assert all(d > 0 for d in stress.ASSUMED_DURATIONS.values())
    # CAD bonds on Government of Canada yields, USD bonds on Treasury yields (macro_rates.csv series)
    assert stress.RATE_SERIES["XBB.TO"].startswith("BD.CDN.")
    assert stress.RATE_SERIES["ZST.TO"].startswith("BD.CDN.")
    assert stress.RATE_SERIES["AGG"].startswith("DGS") and stress.RATE_SERIES["TLT"].startswith("DGS")
    # ordering a human can check against issuer pages: ultra short < aggregate < 20+ year
    d = stress.ASSUMED_DURATIONS
    assert d["ZST.TO"] < 1 < d["AGG"] < d["TLT"] and d["XBB.TO"] < d["TLT"]


# ---------------------------------------------------------------- hand-computable scenarios

def test_single_bond_etf_known_duration(assets):
    block = stress.stress_block({"TLT": 1.0}, 100_000, assets, durations={"TLT": 16.0})
    assert [s["shock_bp"] for s in block["scenarios"] if s["kind"] == "parallel"] == [-100, 100, 200]
    up = _scenario(block, "parallel_+100bp")
    assert up["impact"] == pytest.approx(-0.16, abs=1e-9)
    assert up["impact_cad"] == pytest.approx(-16_000, abs=0.01)
    (h,) = up["holdings"]
    assert h["ticker"] == "TLT" and h["shock_bp"] == 100
    assert h["price_impact"] == pytest.approx(-0.16, abs=1e-9)
    assert h["impact_cad"] == pytest.approx(-16_000, abs=0.01)
    down = _scenario(block, "parallel_-100bp")
    assert down["impact"] == pytest.approx(0.16, abs=1e-9) and down["impact_cad"] == pytest.approx(16_000, abs=0.01)
    assert _scenario(block, "parallel_+200bp")["impact_cad"] == pytest.approx(-32_000, abs=0.01)


def test_zero_bond_weight_gives_zero_impact(assets):
    block = stress.stress_block({"SPY": 0.6, "BTC-USD": 0.1, "GLD": 0.1, "RY.TO": 0.2}, 50_000, assets)
    for s in block["scenarios"]:
        assert s["impact"] == 0.0 and s["impact_cad"] == 0.0 and s["holdings"] == []
    assert block["bond_holdings"] == []
    assert block["not_modelled"]["weight"] == pytest.approx(1.0, abs=1e-9)
    assert set(block["not_modelled"]["tickers"]) == {"SPY", "BTC-USD", "GLD", "RY.TO"}
    assert block["status"] == "info"


def test_mixed_portfolio_only_bonds_move(assets):
    block = stress.stress_block({"XBB.TO": 0.5, "SPY": 0.5}, 200_000, assets, durations={"XBB.TO": 7.0})
    s = _scenario(block, "parallel_+200bp")
    assert s["impact"] == pytest.approx(-0.07, abs=1e-9)                 # 0.5 x -7 x 0.02
    assert s["impact_cad"] == pytest.approx(-14_000, abs=0.01)
    assert [h["ticker"] for h in s["holdings"]] == ["XBB.TO"]
    assert block["not_modelled"]["weight"] == pytest.approx(0.5, abs=1e-9)


def test_robert_current_portfolio_uses_the_assumed_table(assets):
    w = {"XIU.TO": .18, "RY.TO": .07, "ENB.TO": .06, "FTS.TO": .07, "JNJ": .05, "PG": .05, "SPY": .12,
         "XBB.TO": .22, "ZST.TO": .13, "GLD": .05}
    block = stress.stress_block(w, 400_000, assets)
    d = stress.ASSUMED_DURATIONS
    expected = -(0.22 * d["XBB.TO"] + 0.13 * d["ZST.TO"]) * 0.01
    s = _scenario(block, "parallel_+100bp")
    assert s["impact"] == pytest.approx(expected, abs=1e-9)
    assert s["impact_cad"] == pytest.approx(expected * 400_000, abs=0.01)
    assert {b["ticker"] for b in block["bond_holdings"]} == {"XBB.TO", "ZST.TO"}
    assert all(b["duration_source"] == "ASSUMED" for b in block["bond_holdings"])


def test_bond_without_a_duration_is_listed_not_guessed():
    fake = pd.DataFrame({"ticker": ["NEWBOND", "SPY"], "asset_class": ["etf_bond", "etf_equity"],
                         "currency": ["CAD", "USD"]}).set_index("ticker")
    block = stress.stress_block({"NEWBOND": 0.4, "SPY": 0.6}, 10_000, fake)
    assert block["missing_duration"] == ["NEWBOND"]
    s = _scenario(block, "parallel_+100bp")
    assert s["impact"] == 0.0 and s["holdings"] == []
    assert any("NEWBOND" in a for a in block["assumptions"])
    assert block["status"] == "info"


# ---------------------------------------------------------------- labelling (invariant 9) and info-only (inv. 2, 3)

def test_default_durations_are_labelled_assumed_and_unsourced(assets):
    block = stress.stress_block({"XBB.TO": 1.0}, 1_000, assets)
    assert block["sourced"] is False
    text = " ".join(block["assumptions"])
    assert "ASSUMED" in text
    assert "duration" in text.lower()
    assert any("equit" in a.lower() and "crypto" in a.lower() and "gold" in a.lower() for a in block["assumptions"])
    assert any("parallel" in a.lower() for a in block["assumptions"])
    assert all(isinstance(a, str) and a for a in block["assumptions"])


def test_sourced_durations_from_asset_meta(assets):
    meta = pd.DataFrame({"ticker": ["XBB.TO", "ZST.TO"], "effective_duration": ["7.1", "0.2"],
                         "duration_source_url": ["https://example.org/xbb", ""],
                         "duration_as_of": ["2026-10-31", ""]})
    block = stress.stress_block({"XBB.TO": 1.0}, 1_000, assets, asset_meta=meta)
    (b,) = block["bond_holdings"]
    assert b["duration"] == pytest.approx(7.1) and b["duration_source"] == "https://example.org/xbb"
    assert block["sourced"] is True
    # ZST.TO has a value but no source URL: not used, falls back to the labelled assumption
    block2 = stress.stress_block({"ZST.TO": 1.0}, 1_000, assets, asset_meta=meta)
    (b2,) = block2["bond_holdings"]
    assert b2["duration"] == stress.ASSUMED_DURATIONS["ZST.TO"] and b2["duration_source"] == "ASSUMED"
    assert block2["sourced"] is False


def test_status_is_info_and_never_feeds_limits(assets, macro):
    block = stress.stress_block({"TLT": 1.0}, 1_000_000, assets, macro, as_of=AS_OF)   # big loss, still info
    assert block["status"] == "info"
    assert block["feeds_limits"] is False
    assert "violation" not in json.dumps(block) and "warning" not in json.dumps(block)
    for mod in ("limits.py", "derive.py", "registry.py"):
        assert "stress" not in (ROOT / "coherence_analyzer" / "rules" / mod).read_text(encoding="utf-8")
    assert "stress" not in (ROOT / "coherence_analyzer" / "coherence" / "hard.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------- macro context

def test_load_macro_series(macro):
    s = data.load_macro_series("BD.CDN.10YR.DQ.YLD")
    assert s.index.is_monotonic_increasing and s.dtype.kind == "f"
    assert s.index[0] == pd.Timestamp("2021-11-12") and s.iloc[0] == pytest.approx(1.67)
    upto = data.load_macro_series("BD.CDN.10YR.DQ.YLD", upto="2021-11-15")
    assert list(upto.values) == pytest.approx([1.67, 1.72])
    with pytest.raises(KeyError):
        data.load_macro_series("NOPE")


def test_rate_context_for_held_bond_series(assets, macro):
    block = stress.stress_block({"XBB.TO": 0.5, "TLT": 0.5}, 1_000, assets, macro, as_of=AS_OF)
    ctx = {c["series_id"]: c for c in block["rate_context"]}
    assert set(ctx) == {"BD.CDN.10YR.DQ.YLD", "DGS10"}
    raw = macro[macro["series_id"] == "BD.CDN.10YR.DQ.YLD"].set_index("date")["value"]
    c = ctx["BD.CDN.10YR.DQ.YLD"]
    assert c["current"] == pytest.approx(float(raw.sort_index().iloc[-1]))
    assert c["current_date"] == "2026-11-12"
    assert c["min"] == pytest.approx(float(raw.min())) and c["max"] == pytest.approx(float(raw.max()))
    # largest 12-month rise on month-end values: 2021-12 -> 2022-12
    assert c["max_12m_rise_bp"] == pytest.approx(188, abs=0.5)
    assert c["max_12m_rise_end"] == "2022-12-31"
    assert c["max_12m_fall_bp"] <= 0
    assert c["label"]


def test_rate_context_respects_as_of(assets, macro):
    block = stress.stress_block({"XBB.TO": 1.0}, 1_000, assets, macro, as_of="2022-01-31")
    (c,) = block["rate_context"]
    raw = macro[(macro["series_id"] == "BD.CDN.10YR.DQ.YLD") & (macro["date"] <= "2022-01-31")]
    assert c["current_date"] == raw["date"].max().strftime("%Y-%m-%d")
    assert c["current_date"] <= "2022-01-31"


def test_historical_scenario_uses_each_series_largest_12m_rise(assets, macro):
    block = stress.stress_block({"XBB.TO": 0.5, "TLT": 0.5}, 100_000, assets, macro, as_of=AS_OF)
    hist = _scenario(block, "historical_max_12m_rise")
    assert hist["kind"] == "historical" and hist["shock_bp"] is None
    rise = {c["series_id"]: c["max_12m_rise_bp"] for c in block["rate_context"]}
    total = 0.0
    for h in hist["holdings"]:
        sid = stress.RATE_SERIES[h["ticker"]]
        assert h["shock_bp"] == pytest.approx(rise[sid])
        p = stress.bond_impact(stress.ASSUMED_DURATIONS[h["ticker"]], rise[sid])
        assert h["price_impact"] == pytest.approx(p, abs=1e-12)
        total += 0.5 * p
    assert hist["impact"] == pytest.approx(total, abs=1e-12)
    assert hist["impact_cad"] == pytest.approx(total * 100_000, abs=0.01)


def test_without_macro_no_context_no_historical(assets):
    block = stress.stress_block({"XBB.TO": 1.0}, 1_000, assets)
    assert block["rate_context"] == []
    assert [s["kind"] for s in block["scenarios"]] == ["parallel"] * 3


# ---------------------------------------------------------------- numcheck (invariant 6)

@pytest.mark.parametrize("locale", ["fr-CA", "en-CA"])
def test_facts_are_preformatted_and_pass_numcheck(assets, macro, locale):
    block = stress.stress_block({"XBB.TO": 0.22, "ZST.TO": 0.13, "SPY": 0.65}, 400_000, assets, macro,
                                as_of=AS_OF, locale=locale)
    f = block["facts"]
    assert f["locale"] == locale
    up = f["scenarios"]["parallel_+100bp"]
    text = f"{up['shock']}: {up['impact_pct']} ({up['impact_cad']})."
    ctx = f["rate_context"]["BD.CDN.10YR.DQ.YLD"]
    text += f" {ctx['current']} ; {ctx['min']} - {ctx['max']} ; {ctx['max_12m_rise']}."
    assert numfacts.numcheck(text, block) == []
    assert numfacts.numcheck(text + " 47,3 %", block) == ["47,3 %"]


# ---------------------------------------------------------------- report (SPEC §13) and pipeline hook

def test_schema_accepts_the_stress_block(assets, macro, schema):
    block = stress.stress_block({"XBB.TO": 0.5, "TLT": 0.2, "SPY": 0.3}, 10_000, assets, macro, as_of=AS_OF)
    sub = {"$defs": schema["$defs"], **schema["properties"]["stress"]}
    assert list(Draft202012Validator(sub).iter_errors(block)) == []
    bad = dict(block, status="violation")
    assert list(Draft202012Validator(sub).iter_errors(bad))
    bad = dict(block, feeds_limits=True)
    assert list(Draft202012Validator(sub).iter_errors(bad))


def test_review_attaches_stress_block(tmp_path, monkeypatch, schema):
    monkeypatch.setenv("COHERENCE_ANALYZER_STORE_DIR", str(tmp_path))
    rep = pipeline.review(FIX)
    assert list(Draft202012Validator(schema).iter_errors(rep)) == []
    st = rep["stress"]
    assert st["status"] == "info" and st["sourced"] is False and st["feeds_limits"] is False
    assert {b["ticker"] for b in st["bond_holdings"]} == {"XBB.TO", "ZST.TO"}
    assert st["as_of"] == AS_OF
    assert "historical_max_12m_rise" in {s["id"] for s in st["scenarios"]}
    # info only: no check comes from the stress block and the verdict is unchanged (Robert current = coherent, D7)
    assert all(not c["id"].startswith("ST") for c in rep["checks_current"])
    assert rep["verdict"] == "coherent"
    saved = json.loads(next((tmp_path / "reports").glob("*.json")).read_text(encoding="utf-8"))
    assert saved["stress"] == st
