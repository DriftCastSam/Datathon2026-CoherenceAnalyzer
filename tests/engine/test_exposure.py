"""D19 exposure block: ETF look-through per sector, country, class, currency and single company.

Every meta CSV here is a tiny made-up file in tmp_path (round numbers so the splits are easy to check); the real
data/meta files are never read.
"""
from __future__ import annotations

import csv

import pytest

from coherence_analyzer import data, engine, exposure

PF = {"QQQ": 40, "NVDA": 10, "XIU.TO": 20, "AGG": 20, "GLD": 10}
URL, AS_OF = "https://issuer.example/holdings", "2026-10-31"


def _write(path, header, rows):
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


@pytest.fixture
def meta(tmp_path):
    _write(tmp_path / "etf_sector_weights.csv", ["etf", "sector", "weight", "source_url", "as_of"],
           [["QQQ", "Information Technology", 0.5, URL, AS_OF], ["QQQ", "Communication Services", 0.5, URL, AS_OF],
            ["XIU.TO", "Financials", 0.4, URL, AS_OF], ["XIU.TO", "Energy", 0.6, URL, AS_OF]])
    _write(tmp_path / "etf_country_weights.csv", ["etf", "country", "weight", "source_url", "as_of"],
           [["QQQ", "United States", 0.9, URL, AS_OF], ["QQQ", "Netherlands", 0.1, URL, AS_OF],
            ["XIU.TO", "Canada", 1.0, URL, AS_OF], ["AGG", "United States", 1.0, URL, AS_OF]])
    _write(tmp_path / "etf_top_holdings.csv",
           ["etf", "holding_ticker", "holding_name", "weight", "source_url", "as_of"],
           [["QQQ", "NVDA", "NVIDIA Corp", 0.1, URL, AS_OF], ["QQQ", "META", "Meta Platforms", 0.05, URL, AS_OF],
            ["XIU.TO", "RY.TO", "Royal Bank", 0.08, URL, AS_OF]])
    return tmp_path


@pytest.fixture
def exp(market, meta):
    return engine.portfolio_exposure(market, PF, 100_000, meta_dir=meta)


def _row(dim, name):
    return next(r for r in dim["rows"] if r["name"] == name)


def test_sector_split_and_parts(exp):
    sec = exp["dimensions"]["sector"]
    assert sec["covered"] and sec["uncovered"] == []
    it = _row(sec, "Information Technology")
    assert it["weight"] == pytest.approx(0.30)               # NVDA 10 direct + QQQ 40 x 0.5
    assert it["value_cad"] == 30_000
    assert [(p["ticker"], p["via"], p["weight"]) for p in it["parts"]] == [("QQQ", "etf", 0.2), ("NVDA", "direct", 0.1)]
    assert [(p["ticker"], p["via"]) for p in _row(sec, "Bonds")["parts"]] == [("AGG", "etf")]  # kept whole, still a fund
    assert sum(r["weight"] for r in sec["rows"]) == pytest.approx(1.0)


def test_country_split(exp):
    c = exp["dimensions"]["country"]
    assert c["covered"]
    assert _row(c, "United States")["weight"] == pytest.approx(0.10 + 0.36 + 0.20)
    assert _row(c, "Netherlands")["weight"] == pytest.approx(0.04)
    assert _row(c, "Global (gold)")["weight"] == pytest.approx(0.10)


def test_class_counts_equity_etfs_as_stocks_and_currency(exp):
    cls = exp["dimensions"]["class"]
    assert _row(cls, "Stocks")["weight"] == pytest.approx(0.70)
    assert {r["name"] for r in cls["rows"]} == {"Stocks", "Bonds", "Gold"}
    assert _row(exp["dimensions"]["currency"], "USD")["weight"] == pytest.approx(0.80)


def test_single_names_overlap_is_a_lower_bound(exp):
    sn = exp["single_names"]
    assert sn["partial"] is True
    nvda = next(r for r in sn["rows"] if r["ticker"] == "NVDA")
    assert nvda["direct"] == pytest.approx(0.10) and nvda["weight"] == pytest.approx(0.14)
    assert nvda["via"] == [{"etf": "QQQ", "name": "Invesco QQQ Trust (Nasdaq-100)", "weight": 0.04}]
    ry = next(r for r in sn["rows"] if r["ticker"] == "RY.TO")
    assert ry["name"] == "Royal Bank of Canada" and ry["direct"] == 0     # universe name wins
    assert sn["etf_listed"] == {"QQQ": 0.15, "XIU.TO": 0.08}
    assert sn["uncovered"] == []                                         # AGG / GLD hold no companies


def test_sources_only_for_held_etfs(exp):
    assert {s["etf"] for s in exp["sources"]} == {"QQQ", "XIU.TO", "AGG"}
    assert all(s["as_of"] == AS_OF and s["source_url"] == URL for s in exp["sources"])


def test_no_files_keeps_own_buckets_and_lists_uncovered(market, tmp_path):
    e = engine.portfolio_exposure(market, PF, 100_000, meta_dir=tmp_path)
    sec, c = e["dimensions"]["sector"], e["dimensions"]["country"]
    assert not sec["covered"] and sec["uncovered"] == ["QQQ", "XIU.TO"]
    assert _row(sec, "Broad market (diversified)")["weight"] == pytest.approx(0.60)
    assert c["uncovered"] == ["AGG", "QQQ", "XIU.TO"]
    assert e["single_names"]["uncovered"] == ["QQQ", "XIU.TO"] and e["sources"] == []


def test_analyse_output_has_no_exposure(market, meta):
    """D19: exposure sits next to the risk math, not inside it (engine.analyse is unchanged)."""
    assert "exposure" not in engine.analyse(market, PF, 12, 100_000, n=50, meta_dir=meta)


def test_review_report_carries_exposure(root):
    from coherence_analyzer import pipeline
    rep = pipeline.review(root / "fixtures" / "amira")
    e = rep["metrics_current"]["exposure"]
    assert set(e["dimensions"]) == set(exposure.DIMENSIONS)
    assert sum(r["weight"] for r in e["dimensions"]["sector"]["rows"]) == pytest.approx(1.0, abs=1e-3)
    assert e["value_cad"] == 60_000


def test_loader_rejects_unsourced_rows(tmp_path):
    _write(tmp_path / "etf_country_weights.csv", ["etf", "country", "weight", "source_url", "as_of"],
           [["SPY", "United States", 1.0, "", AS_OF]])
    with pytest.raises(ValueError, match="source_url"):
        data.load_meta("etf_country_weights", tmp_path)
    _write(tmp_path / "etf_top_holdings.csv", ["etf", "holding_ticker", "holding_name", "weight", "source_url", "as_of"],
           [["SPY", "AAPL", "Apple", 7, URL, AS_OF]])
    with pytest.raises(ValueError, match="out of range"):
        data.load_meta("etf_top_holdings", tmp_path)


def test_build_is_pure_and_descriptive():
    labels = {"X": {"name": "X", "asset_class": "equity", "is_etf": False, "sector": "Energy", "country": "Canada",
                    "class": "Stocks", "currency": "CAD"}}
    out = exposure.build({"X": 1.0}, labels, 1000)
    assert out["dimensions"]["sector"]["rows"][0]["value_cad"] == 1000
    assert "status" not in str(out) and "limit" not in str(out)
