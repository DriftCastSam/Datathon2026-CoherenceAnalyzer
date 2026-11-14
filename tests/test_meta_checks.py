"""Meta file loaders (SPEC §3.2) + hard checks H11 excluded activities, H12 fees, H13 theme weight (SPEC §7.3).

Oracle for P1.2. Every meta file is written by the test itself into `tmp_path` and loaded with
`data.load_meta(name, meta_dir)`: the real `data/meta/*.csv` files are never read or written here
(`activity_tags.csv` is human-only, CLAUDE.md). Mateo's committed portfolio (fixtures/mateo/portfolio.json) is
checked against a minimal copy of the Robert profile whose `limits.excluded_activities` is edited (SPEC §15
test_hard Mateo bullet). Unknowns never change the verdict (docs/spec-decisions.md D3).
"""
from __future__ import annotations

import copy
import csv
import json

import pytest

from coherence_analyzer import data
from coherence_analyzer.coherence import hard, report
from coherence_analyzer.factors import mapping

META_NAMES = ("asset_meta", "etf_sector_weights", "activity_tags")
ASSET_META_COLS = ["ticker", "mer_pct", "mer_source_url", "mer_as_of", "has_lookthrough", "themes"]
SECTOR_COLS = ["etf", "sector", "weight", "source_url", "as_of"]
TAG_COLS = ["ticker", "tag", "evidence_summary", "source_url", "as_of", "added_by"]
COLS = {"asset_meta": ASSET_META_COLS, "etf_sector_weights": SECTOR_COLS, "activity_tags": TAG_COLS}

CHECK_KEYS = {"id", "kind", "status", "subject", "value", "limit", "unit",
              "derived_from", "source", "confidence", "message_key"}
ENV = "significant_environmental_controversy"
SOC = "significant_social_controversy"
URL = "https://example.test/xom-controversy-source"
TOL = 1e-9

# Mateo's committed portfolio (fixtures/mateo/portfolio.json): equities and ETFs held.
MATEO_EQUITIES = ["CNR.TO", "ENB.TO", "FTS.TO", "MSFT", "RY.TO", "XOM"]
MATEO_ETFS = ["AGG", "ESGV", "SPY", "XBB.TO", "XIU.TO"]


# ---------------------------------------------------------------- helpers

def _write(tmp_path, name: str, rows: list[dict], cols: list[str] | None = None) -> None:
    cols = cols or COLS[name]
    with open(tmp_path / f"{name}.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def _am(ticker, mer="", url="", as_of="", look="false", themes="") -> dict:
    return {"ticker": ticker, "mer_pct": mer, "mer_source_url": url, "mer_as_of": as_of,
            "has_lookthrough": look, "themes": themes}


def _etf(ticker, mer) -> dict:
    """An asset_meta row for an ETF with a sourced MER (percent)."""
    return _am(ticker, mer=mer, url=f"https://example.test/{ticker}", as_of="2026-11-01")


def _tag(ticker, tag, url=URL) -> dict:
    return {"ticker": ticker, "tag": tag, "evidence_summary": "test evidence", "source_url": url,
            "as_of": "2026-11-01", "added_by": "tester"}


def _meta(tmp_path, asset_meta=None, activity_tags=None) -> dict:
    """Write the given rows (None -> file absent) and load the three meta files from tmp_path."""
    if asset_meta is not None:
        _write(tmp_path, "asset_meta", asset_meta)
    if activity_tags is not None:
        _write(tmp_path, "activity_tags", activity_tags)
    return {n: data.load_meta(n, tmp_path) for n in META_NAMES}


@pytest.fixture(scope="module")
def assets():
    return data.load_assets()


@pytest.fixture(scope="module")
def mateo_weights(root, assets) -> dict[str, float]:
    return data.load_portfolio(root / "fixtures" / "mateo" / "portfolio.json", assets)["weights"]


@pytest.fixture(scope="module")
def robert_raw(root) -> dict:
    return json.loads((root / "fixtures" / "robert" / "profile.json").read_text())


@pytest.fixture
def make_profile(robert_raw):
    """Minimal edited copy of the Robert profile (limits + limits_trace kept as committed)."""
    def make(excluded=(ENV, SOC), fee="keep", themes=None) -> dict:
        p = copy.deepcopy(robert_raw)
        lim = p["limits"]
        lim["excluded_activities"] = list(excluded)
        if fee is None:
            lim.pop("max_fee_mer_pct", None)
        elif fee != "keep":
            lim["max_fee_mer_pct"] = fee
        if themes is None:
            lim.pop("max_theme_weight", None)
        else:
            lim["max_theme_weight"] = dict(themes)
        return p
    return make


def _by(checks, cid, **where) -> list[dict]:
    return [c for c in checks if c["id"] == cid and all(c.get(k) == v for k, v in where.items())]


def _one(checks, cid, **where) -> dict:
    got = _by(checks, cid, **where)
    assert len(got) == 1, f"expected exactly one {cid} {where}, got {got}"
    return got[0]


def _assert_shape(checks, cid, unit) -> None:
    for c in _by(checks, cid):
        assert CHECK_KEYS <= set(c)
        assert c["kind"] == "hard"
        assert c["confidence"] == 1.0
        assert c["status"] in {"pass", "violation", "info", "unknown"}
        assert c["unit"] == unit
        assert isinstance(c["message_key"], str) and c["message_key"]
        assert isinstance(c["source"], str) and c["source"].startswith("rule:")


# ================================================================ data.load_meta (SPEC §3.2)

@pytest.mark.parametrize("name", META_NAMES)
def test_load_meta_missing_file_is_none(tmp_path, name):
    """§3.2: a meta file that is not built yet loads as None (unchanged behaviour)."""
    assert data.load_meta(name, tmp_path) is None


def test_load_meta_valid_files_keep_the_spec_columns(tmp_path):
    """§3.2: each file loads with exactly its documented columns and one row per CSV row."""
    _write(tmp_path, "asset_meta", [_etf("XIU.TO", 0.18), _am("MSFT", mer=0, themes="ai")])
    _write(tmp_path, "etf_sector_weights", [{"etf": "XIU.TO", "sector": "Financials", "weight": 0.3,
                                             "source_url": "https://example.test/xiu", "as_of": "2026-11-01"}])
    _write(tmp_path, "activity_tags", [_tag("XOM", ENV)])
    for name, n_rows in (("asset_meta", 2), ("etf_sector_weights", 1), ("activity_tags", 1)):
        df = data.load_meta(name, tmp_path)
        assert df is not None and len(df) == n_rows
        assert set(COLS[name]) <= set(df.columns) | {df.index.name}


@pytest.mark.parametrize("name,missing", [
    ("asset_meta", "themes"), ("asset_meta", "mer_source_url"), ("asset_meta", "has_lookthrough"),
    ("etf_sector_weights", "source_url"), ("etf_sector_weights", "weight"),
    ("activity_tags", "source_url"), ("activity_tags", "added_by"), ("activity_tags", "as_of"),
])
def test_load_meta_rejects_a_missing_column(tmp_path, name, missing):
    """§3.2: a file without one of its documented columns is rejected with ValueError."""
    cols = [c for c in COLS[name] if c != missing]
    _write(tmp_path, name, [], cols=cols)
    with pytest.raises(ValueError):
        data.load_meta(name, tmp_path)


@pytest.mark.parametrize("blank", ["source_url", "as_of", "added_by"])
def test_load_meta_rejects_unsourced_activity_tag_row(tmp_path, blank):
    """§3.2/§1: an activity_tags row needs a source_url, an as_of date and a human added_by."""
    row = _tag("XOM", ENV)
    row[blank] = ""
    _write(tmp_path, "activity_tags", [_tag("ENB.TO", SOC), row])
    with pytest.raises(ValueError):
        data.load_meta("activity_tags", tmp_path)


def test_load_meta_rejects_etf_mer_without_source_url(tmp_path):
    """§3.2: 'Don't guess': an ETF row with mer_pct but no mer_source_url is rejected."""
    _write(tmp_path, "asset_meta", [_am("XIU.TO", mer=0.18, url="", as_of="2026-11-01")])
    with pytest.raises(ValueError):
        data.load_meta("asset_meta", tmp_path)


def test_load_meta_accepts_unfilled_etf_mer_and_unsourced_stock_zero(tmp_path):
    """§3.2: mer_pct is 0 for single stocks/crypto (no source needed); an unfilled ETF MER is allowed (H12 -> unknown)."""
    _write(tmp_path, "asset_meta", [_am("MSFT", mer=0), _am("BTC-USD", mer=0), _am("XBB.TO", mer="")])
    df = data.load_meta("asset_meta", tmp_path)
    assert df is not None and len(df) == 3


def test_load_meta_asset_meta_themes_are_a_semicolon_list(tmp_path):
    """§3.2: `themes` is a semicolon list (NVDA -> ai;semis); one theme stays a one-item list; blank is empty."""
    _write(tmp_path, "asset_meta", [_am("NVDA", mer=0, themes="ai;semis"), _am("QQQ", mer=0.2, url="https://example.test/q",
                                                                                    as_of="2026-11-01", themes="tech_heavy"),
                                    _am("RY.TO", mer=0, themes="")])
    df = data.load_meta("asset_meta", tmp_path)
    by = {r["ticker"]: r for r in (df.reset_index() if "ticker" not in df.columns else df).to_dict("records")}
    # SPEC-AMBIGUITY: §3.2 says themes is a semicolon list and the task says the loader parses it; the stricter
    # reading is that the loaded cell is already a list of strings (a blank cell gives an empty list).
    assert list(by["NVDA"]["themes"]) == ["ai", "semis"]
    assert list(by["QQQ"]["themes"]) == ["tech_heavy"]
    assert list(by["RY.TO"]["themes"]) == []


# ================================================================ H11 excluded activities (SPEC §7.3, §15)

def _h11(mateo_weights, make_profile, assets, tmp_path, tags=None, asset_meta=None, excluded=(ENV, SOC)):
    meta = _meta(tmp_path, asset_meta=asset_meta, activity_tags=tags)
    p = make_profile(excluded=excluded)
    return p, [c for c in hard.run_cheap(mateo_weights, p, assets, meta) if c["id"] == "H11"]


def test_h11_no_tags_file_every_held_equity_and_etf_is_unknown_never_violation(
        mateo_weights, make_profile, assets, tmp_path):
    """§7.3/§15 Mateo: with no activity_tags file XOM and ENB.TO (and every held equity) are `unknown`, one per ticker.
    Held ETFs with no asset_meta have no look-through -> `unknown` too. Nothing is `pass` or `violation`."""
    p, h11 = _h11(mateo_weights, make_profile, assets, tmp_path)
    assert {c["status"] for c in h11} == {"unknown"}
    subjects = [c["subject"] for c in h11]
    for t in MATEO_EQUITIES:
        assert subjects.count(t) == 1, t          # one per ticker, not one per excluded tag
    for t in MATEO_ETFS:
        # SPEC-AMBIGUITY: "held ETF without look-through" is read as every held ETF class (bond ETFs included).
        assert subjects.count(t) == 1, t
    assert sorted(subjects) == sorted(MATEO_EQUITIES + MATEO_ETFS)
    _assert_shape(h11, "H11", "weight")
    assert all(c["derived_from"] == hard._derived(p, "excluded_activities") for c in h11)
    assert h11[0]["derived_from"][0] == "limits.excluded_activities"
    assert "values.environment.exclusions" in h11[0]["derived_from"]


def test_h11_sourced_tag_row_is_a_violation_on_that_ticker(mateo_weights, make_profile, assets, tmp_path):
    """§15 Mateo: a sourced XOM significant_environmental_controversy row -> H11 violation on XOM, value the tag,
    evidence = the row's source_url; the other equities stay `unknown`."""
    _, h11 = _h11(mateo_weights, make_profile, assets, tmp_path, tags=[_tag("XOM", ENV)])
    viol = [c for c in h11 if c["status"] == "violation"]
    assert len(viol) == 1
    v = viol[0]
    assert v["subject"] == "XOM"
    assert v["value"] == ENV
    assert URL in json.dumps(v, default=str)                  # evidence travels with the check
    assert v["unit"] == "weight" and v["kind"] == "hard" and v["confidence"] == 1.0
    assert v["derived_from"][0] == "limits.excluded_activities"
    others = {c["subject"] for c in h11 if c["status"] == "unknown"}
    assert {"ENB.TO", "RY.TO", "CNR.TO", "FTS.TO", "MSFT"} <= others
    assert "XOM" not in {c["subject"] for c in h11 if c["status"] == "pass"}
    unk = [c["subject"] for c in h11 if c["status"] == "unknown"]
    assert len(unk) == len(set(unk))                          # never one unknown per tag


def test_h11_one_violation_per_excluded_tag_and_one_unknown_per_ticker(mateo_weights, make_profile, assets, tmp_path):
    """§7.3: two sourced tags on one equity -> a violation for each tag; unknowns stay one per ticker."""
    tags = [_tag("XOM", ENV, "https://example.test/a"), _tag("XOM", SOC, "https://example.test/b")]
    _, h11 = _h11(mateo_weights, make_profile, assets, tmp_path, tags=tags)
    viol = [c for c in h11 if c["status"] == "violation"]
    assert sorted(c["value"] for c in viol) == sorted([ENV, SOC])
    assert {c["subject"] for c in viol} == {"XOM"}
    blob = json.dumps(viol, default=str)
    assert "https://example.test/a" in blob and "https://example.test/b" in blob
    unk = [c["subject"] for c in h11 if c["status"] == "unknown"]
    assert len(unk) == len(set(unk))


def test_h11_tag_outside_the_excluded_categories_never_fires(mateo_weights, make_profile, assets, tmp_path):
    """§7.3: tag rows for non-excluded tags never fire: XOM tagged only fossil_fuel_producer (not excluded here)
    gets no violation and no pass; it is `unknown` (no tag row in an excluded category)."""
    tags = [_tag("XOM", "fossil_fuel_producer"), _tag("ENB.TO", "tobacco")]
    _, h11 = _h11(mateo_weights, make_profile, assets, tmp_path, tags=tags)
    assert not [c for c in h11 if c["status"] in ("violation", "pass")]
    assert "fossil_fuel_producer" not in {c["value"] for c in h11}
    xom = [c for c in h11 if c["subject"] == "XOM"]
    assert len(xom) == 1 and xom[0]["status"] == "unknown"


def test_h11_tag_on_a_ticker_that_is_not_held_never_fires(mateo_weights, make_profile, assets, tmp_path):
    """§7.3: only held tickers are checked; a sourced controversy row for NVDA (not held) adds nothing."""
    _, h11 = _h11(mateo_weights, make_profile, assets, tmp_path, tags=[_tag("NVDA", ENV)])
    assert "NVDA" not in {c["subject"] for c in h11}
    assert not [c for c in h11 if c["status"] == "violation"]


def test_h11_etf_without_lookthrough_is_unknown_even_with_asset_meta(mateo_weights, make_profile, assets, tmp_path):
    """§7.3: asset_meta rows with has_lookthrough false -> each held ETF is `unknown`, exactly once."""
    am = [_am(t, look="false") for t in MATEO_ETFS]
    _, h11 = _h11(mateo_weights, make_profile, assets, tmp_path, tags=[_tag("XOM", ENV)], asset_meta=am)
    for t in MATEO_ETFS:
        got = [c for c in h11 if c["subject"] == t]
        assert len(got) == 1 and got[0]["status"] == "unknown", t


def test_h11_no_excluded_activities_follows_the_h1_pass_convention(mateo_weights, make_profile, assets, tmp_path):
    """§7.3 + H1 convention: nothing excluded -> one pass check (subject None, value 0.0, limit None), no unknowns,
    even with no meta files at all."""
    p, h11 = _h11(mateo_weights, make_profile, assets, tmp_path, excluded=())
    assert len(h11) == 1
    c = h11[0]
    assert c["status"] == "pass" and c["subject"] is None
    assert c["value"] == pytest.approx(0.0, abs=TOL) and c["limit"] is None
    assert c["unit"] == "weight"
    assert c["derived_from"] == hard._derived(p, "excluded_activities")
    _assert_shape(h11, "H11", "weight")
    # same shape as H1's pass result
    h1 = _one(hard.run_cheap(mateo_weights, p, assets, {n: None for n in META_NAMES}), "H1")
    assert (h1["status"], h1["subject"], h1["value"], h1["limit"], h1["unit"]) == \
           (c["status"], c["subject"], c["value"], c["limit"], c["unit"])


# ================================================================ H12 fees (SPEC §3.2, §7.3)

FEE_ROWS = [_etf("XIU.TO", 0.18), _etf("ESGV", 0.09), _etf("SPY", 0.75), _am("XBB.TO", mer=""),
            _am("MSFT", mer=0), _am("RY.TO", mer=0)]       # SPY over 0.5; XBB.TO blank; AGG has no row at all


def _h12(weights, profile, assets, tmp_path, rows):
    meta = _meta(tmp_path, asset_meta=rows)
    return [c for c in hard.run_cheap(weights, profile, assets, meta) if c["id"] == "H12"]


def test_h12_per_etf_pass_violation_unknown(mateo_weights, make_profile, assets, tmp_path):
    """§7.3 H12: each held ETF: mer_pct > max_fee_mer_pct (0.5) -> violation; blank MER or missing row -> unknown;
    else pass. Value = the MER in percent, limit = the limit, unit pct, one result per ETF."""
    p = make_profile()
    h12 = _h12(mateo_weights, p, assets, tmp_path, FEE_ROWS)
    assert sorted(c["subject"] for c in h12) == MATEO_ETFS           # ETFs only: no stock, one per ETF
    want = {"XIU.TO": "pass", "ESGV": "pass", "SPY": "violation", "XBB.TO": "unknown", "AGG": "unknown"}
    assert {c["subject"]: c["status"] for c in h12} == want
    for t, mer in (("XIU.TO", 0.18), ("ESGV", 0.09), ("SPY", 0.75)):
        c = _one(h12, "H12", subject=t)
        assert c["value"] == pytest.approx(mer, abs=TOL)
    for c in h12:
        assert c["limit"] == pytest.approx(0.5, abs=TOL)
        assert c["derived_from"] == hard._derived(p, "max_fee_mer_pct")
    assert h12[0]["derived_from"][0] == "limits.max_fee_mer_pct"
    assert "preferences.max_fee_mer_pct" in h12[0]["derived_from"]
    _assert_shape(h12, "H12", "pct")


def test_h12_no_asset_meta_file_every_held_etf_is_unknown(mateo_weights, make_profile, assets, tmp_path):
    """§3.2/§7.3: asset_meta not built -> MER unknown for every held ETF (never pass)."""
    h12 = _h12(mateo_weights, make_profile(), assets, tmp_path, None)
    assert {c["subject"]: c["status"] for c in h12} == {t: "unknown" for t in MATEO_ETFS}
    assert all(c["limit"] == pytest.approx(0.5, abs=TOL) for c in h12)


def test_h12_equality_with_the_limit_is_a_pass(assets, make_profile, tmp_path):
    """§7.3: `>` is a violation, so MER == limit passes; float noise below EPS (_gt) also passes; above EPS fails."""
    rows = [_etf("XIU.TO", 0.5), _etf("SPY", 0.5 + 1e-10), _etf("ESGV", 0.5 + 1e-6)]
    w = {"XIU.TO": 0.1, "SPY": 0.1, "ESGV": 0.1}
    h12 = _h12(w, make_profile(), assets, tmp_path, rows)
    assert {c["subject"]: c["status"] for c in h12} == {"XIU.TO": "pass", "SPY": "pass", "ESGV": "violation"}


def test_h12_checks_commodity_and_bond_etfs_but_not_stocks_or_crypto(assets, make_profile, tmp_path):
    """§7.3: every held ETF class (etf_equity, etf_bond, etf_commodity) is checked; equity and crypto are not."""
    rows = [_etf("GLD", 0.40), _etf("TLT", 0.15), _am("MSFT", mer=0), _am("BTC-USD", mer=0)]
    w = {"GLD": 0.1, "TLT": 0.1, "MSFT": 0.1, "BTC-USD": 0.05}
    h12 = _h12(w, make_profile(fee=0.25), assets, tmp_path, rows)
    assert {c["subject"]: c["status"] for c in h12} == {"GLD": "violation", "TLT": "pass"}
    assert _one(h12, "H12", subject="GLD")["limit"] == pytest.approx(0.25, abs=TOL)


def test_h12_unheld_etf_is_not_checked(assets, make_profile, tmp_path):
    """§7.3: only held ETFs: a high-MER row for an ETF with weight 0 or absent produces nothing."""
    rows = [_etf("XIU.TO", 0.18), _etf("QQQ", 0.9), _etf("SPY", 0.9)]
    h12 = _h12({"XIU.TO": 0.2, "SPY": 0.0}, make_profile(), assets, tmp_path, rows)
    assert [c["subject"] for c in h12] == ["XIU.TO"]


def test_h12_without_max_fee_limit_emits_no_results(mateo_weights, make_profile, assets, tmp_path):
    """§7.3: no max_fee_mer_pct in limits -> no H12 results at all (nothing to compare against)."""
    assert _h12(mateo_weights, make_profile(fee=None), assets, tmp_path, FEE_ROWS) == []


# ================================================================ H13 theme weight (SPEC §3.2, §7.3)

THEME_ROWS = [_am("QQQ", mer=0.2, url="https://example.test/qqq", as_of="2026-11-01", themes="tech_heavy"),
              _am("NVDA", mer=0, themes="ai;semis"), _am("MSFT", mer=0, themes="ai;tech_heavy"),
              _am("RY.TO", mer=0, themes="")]
THEME_W = {"QQQ": 0.20, "NVDA": 0.08, "MSFT": 0.07, "RY.TO": 0.10, "XBB.TO": 0.55}


def _h13(weights, profile, assets, tmp_path, rows):
    meta = _meta(tmp_path, asset_meta=rows)
    return [c for c in hard.run_cheap(weights, profile, assets, meta) if c["id"] == "H13"]


def test_h13_sums_weights_per_theme_and_compares_with_the_max(make_profile, assets, tmp_path):
    """§7.3 H13: Σ weight of holdings carrying the theme; an ETF counts fully. tech_heavy = QQQ .20 + MSFT .07 = .27
    > .25 violation; semis = NVDA .08 <= .10 pass; one result per theme in limits.max_theme_weight."""
    p = make_profile(themes={"tech_heavy": 0.25, "semis": 0.10})
    h13 = _h13(THEME_W, p, assets, tmp_path, THEME_ROWS)
    # SPEC-AMBIGUITY: §7.3 gives no subject for H13; the theme name is the subject (as sector is for H6/H10).
    tech = _one(h13, "H13", subject="tech_heavy")
    assert tech["status"] == "violation"
    assert tech["value"] == pytest.approx(0.27, abs=TOL) and tech["limit"] == pytest.approx(0.25, abs=TOL)
    semis = _one(h13, "H13", subject="semis")
    assert semis["status"] == "pass"
    assert semis["value"] == pytest.approx(0.08, abs=TOL) and semis["limit"] == pytest.approx(0.10, abs=TOL)
    assert len(h13) == 2
    assert all(c["derived_from"] == hard._derived(p, "max_theme_weight") for c in h13)
    assert h13[0]["derived_from"][0] == "limits.max_theme_weight"
    _assert_shape(h13, "H13", "weight")


def test_h13_equality_is_a_pass_and_float_noise_does_not_flip_it(make_profile, assets, tmp_path):
    """§7.3 + `_gt` tolerance: ai = NVDA .08 + MSFT .07 = .15 against max .15 passes (0.08 + 0.07 is not exactly 0.15)."""
    h13 = _h13(THEME_W, make_profile(themes={"ai": 0.15}), assets, tmp_path, THEME_ROWS)
    c = _one(h13, "H13", subject="ai")
    assert c["status"] == "pass"
    assert c["value"] == pytest.approx(0.15, abs=TOL)
    over = _h13(THEME_W, make_profile(themes={"ai": 0.1499}), assets, tmp_path, THEME_ROWS)
    assert _one(over, "H13", subject="ai")["status"] == "violation"


def test_h13_theme_nobody_holds_passes_with_zero(make_profile, assets, tmp_path):
    """§7.3: a limited theme no holding carries sums to 0 -> pass."""
    c = _one(_h13(THEME_W, make_profile(themes={"ev": 0.05}), assets, tmp_path, THEME_ROWS), "H13", subject="ev")
    assert c["status"] == "pass" and c["value"] == pytest.approx(0.0, abs=TOL)


def test_h13_no_asset_meta_is_unknown_per_theme(make_profile, assets, tmp_path):
    """§7.3/§3.2: no asset_meta file -> themes cannot be read -> `unknown` for each limited theme, never pass."""
    p = make_profile(themes={"tech_heavy": 0.25, "ai": 0.15})
    h13 = _h13(THEME_W, p, assets, tmp_path, None)
    assert {c["subject"]: c["status"] for c in h13} == {"tech_heavy": "unknown", "ai": "unknown"}
    assert sorted(c["limit"] for c in h13) == pytest.approx([0.15, 0.25], abs=TOL)


def test_h13_without_max_theme_weight_emits_no_results(make_profile, assets, tmp_path):
    """§7.3: no max_theme_weight in limits -> no H13 results."""
    assert _h13(THEME_W, make_profile(themes=None), assets, tmp_path, THEME_ROWS) == []


# ================================================================ run_cheap integration (SPEC §8, §20.2, D3)

def test_run_cheap_emits_h11_h12_h13_in_cheap_order(mateo_weights, make_profile, assets, tmp_path):
    """§8: H1-H6 then H11, H12, H13 in `hard.CHEAP` order; every one of them is in run_cheap's output."""
    meta = _meta(tmp_path, asset_meta=THEME_ROWS + [r for r in FEE_ROWS if r["ticker"] in MATEO_ETFS])
    p = make_profile(themes={"tech_heavy": 0.25})
    ids = [c["id"] for c in hard.run_cheap(mateo_weights, p, assets, meta)]
    assert {"H11", "H12", "H13"} <= set(ids)
    assert list(hard.CHEAP) == ["H1", "H2", "H3", "H4", "H5", "H6", "H11", "H12", "H13"]
    order = [hard.CHEAP.index(i) for i in ids]
    assert order == sorted(order)                                   # grouped, in CHEAP order


@pytest.mark.parametrize("cid", ["H11", "H12", "H13"])
def test_h11_h12_h13_belong_to_contraintes(cid):
    """§20.2: the three meta checks are owned by the `contraintes` factor."""
    assert mapping.factor_of(cid) == "contraintes"


def test_meta_unknowns_never_change_the_verdict_but_a_violation_does(mateo_weights, make_profile, assets, tmp_path):
    """D3/§7.5: Mateo with only H11-H13 unknowns is `coherent` and lists them; one sourced tag makes it `incoherent`."""
    p = make_profile(themes={"tech_heavy": 0.25})
    only = [c for c in hard.run_cheap(mateo_weights, p, assets, _meta(tmp_path))
            if c["id"] in ("H11", "H12", "H13")]
    assert only and {c["status"] for c in only} == {"unknown"}
    assert report.verdict(only) == "coherent"
    assert report.unknowns(only) == only
    tags = _meta(tmp_path, activity_tags=[_tag("XOM", ENV)])
    with_tag = [c for c in hard.run_cheap(mateo_weights, p, assets, tags) if c["id"] in ("H11", "H12", "H13")]
    assert report.verdict(with_tag) == "incoherent"
    assert {c["id"] for c in with_tag if c["status"] == "violation"} == {"H11"}
