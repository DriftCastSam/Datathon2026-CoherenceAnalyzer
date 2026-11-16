"""ETF sector look-through (SPEC §3.2 etf_sector_weights, §5 sector_lookthrough, §7.3 H6/H10/H11, §13, §19).

Oracle for P2.13. Every `etf_sector_weights.csv` used here is a tiny file written by the test into `tmp_path`;
the real `data/meta/*.csv` are never read or written (the real numbers must come from issuer holdings pages,
entered by a human with a source URL; `activity_tags.csv` is human-only, CLAUDE.md). The test CSV values are
made-up round numbers, only meant to make the expected splits easy to compute.

Pinned behaviour:
- no file, or a header-only file -> engine output unchanged and `lookthrough` False (H6 / H10 keep their `unknown`);
- file present and every held equity ETF covered -> `lookthrough` True, `groups.sector` splits an ETF's weight and
  risk contribution across its sectors by weight, nothing else in the engine output moves;
- H6 adds Σ ETF weight x sector share to the direct equity weight; H10 stops flagging `Broad market` unknown;
- H11 never turns into a `pass` or a `violation` from sector weights (only a sourced tag row can violate).

How the engine finds the meta dir is not fixed by the spec: `_analyse` adapts to a `meta_dir=` / `meta=` keyword on
`engine.analyse` if one exists, else redirects `data.load_meta` / `config.META_DIR` to `tmp_path`.
"""
from __future__ import annotations

import copy
import csv
import inspect
import json

import pandas as pd
import pytest

from coherence_analyzer import config, data, engine
from coherence_analyzer.coherence import hard
from coherence_analyzer.rules import derive, limits as limits_mod

AS_OF = "2026-11-12"
ROBERT_VALUE = 400_000
META_NAMES = ("asset_meta", "etf_sector_weights", "activity_tags")
SECTOR_COLS = ["etf", "sector", "weight", "source_url", "as_of"]
URL = "https://example.test/holdings"
BROAD = "Broad market"
IT = "Information Technology"
TOL = 1e-9

# Made-up sector weights (fractions, each ETF sums to 1). ESGV is deliberately absent.
SHARES: dict[str, dict[str, float]] = {
    "SPY": {IT: 0.30, "Financials": 0.20, "Health Care": 0.20, "Energy": 0.10, "Consumer Discretionary": 0.20},
    "XIU.TO": {"Financials": 0.40, "Energy": 0.20, "Industrials": 0.20, "Materials": 0.20},
    "QQQ": {IT: 0.60, "Communication Services": 0.20, "Consumer Discretionary": 0.20},
}
# Percent weights (sum 100). All held equity ETFs (SPY, XIU.TO, QQQ) are covered by SHARES.
FULL = {"SPY": 25, "XIU.TO": 15, "QQQ": 10, "RY.TO": 8, "NVDA": 7, "JNJ": 5, "XBB.TO": 25, "GLD": 5}
# ESGV is held but has no rows.
PARTIAL = {"SPY": 20, "ESGV": 15, "XIU.TO": 10, "RY.TO": 10, "XBB.TO": 40, "GLD": 5}


# ---------------------------------------------------------------- helpers

def _rows(shares: dict[str, dict[str, float]] = SHARES) -> list[dict]:
    return [{"etf": e, "sector": s, "weight": repr(w), "source_url": URL, "as_of": "2026-11-01"}
            for e, d in shares.items() for s, w in d.items()]


def _write(meta_dir, rows: list[dict], name: str = "etf_sector_weights") -> None:
    with open(meta_dir / f"{name}.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SECTOR_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in SECTOR_COLS})


def _meta(meta_dir) -> dict:
    return {n: data.load_meta(n, meta_dir) for n in META_NAMES}


def _analyse(mp, mk, wpct: dict, meta_dir, horizon: int = 24, start: float = 100_000, n: int = 300) -> dict:
    """engine.analyse with `meta_dir` as the meta directory (SPEC-AMBIGUITY: the spec does not name the hook)."""
    params = inspect.signature(engine.analyse).parameters
    kw: dict = {"n": n}
    if "meta_dir" in params:
        kw["meta_dir"] = meta_dir
    elif "meta" in params:
        kw["meta"] = _meta(meta_dir)
    else:
        orig, real_dir = data.load_meta, config.META_DIR

        def redirected(name, *a, **k):
            if not a and not k:
                return orig(name, meta_dir)
            if (a and a[0] == real_dir) or k.get("meta_dir") == real_dir:
                return orig(name, meta_dir)
            return orig(name, *a, **k)

        mp.setattr(data, "load_meta", redirected)
        mp.setattr(config, "META_DIR", meta_dir)
        mp.setattr(data, "META_DIR", meta_dir, raising=False)
        if hasattr(engine, "load_meta"):
            mp.setattr(engine, "load_meta", redirected)
    return engine.analyse(mk, wpct, horizon, start, **kw)


def _expected_sector(mk, wpct: dict, shares: dict[str, dict[str, float]]) -> dict[str, tuple[float, float]]:
    """{sector: (weight, risk share)} computed from the engine's own risk decomposition (SPEC §5)."""
    w = pd.Series(wpct, dtype=float)
    w /= w.sum()
    cov = mk.r_cad[w.index].cov() * 12
    var = float(w @ cov @ w)
    rc = w * (cov @ w) / var
    out: dict[str, list[float]] = {}
    for t in w.index:
        for s, sh in (shares[t] if t in shares else {engine.sector(mk, t): 1.0}).items():
            acc = out.setdefault(s, [0.0, 0.0])
            acc[0] += float(w[t]) * sh
            acc[1] += float(rc[t]) * sh
    return {s: (a, b) for s, (a, b) in out.items()}


def _sector_groups(out: dict) -> dict[str, dict]:
    return {g["name"]: g for g in out["groups"]["sector"]}


def _dump(out: dict) -> str:
    return json.dumps(out, sort_keys=True, default=str)


@pytest.fixture
def mp():
    with pytest.MonkeyPatch.context() as m:
        yield m


@pytest.fixture
def full_dir(tmp_path):
    d = tmp_path / "full"
    d.mkdir()
    _write(d, _rows())
    return d


@pytest.fixture
def empty_dir(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    return d


@pytest.fixture
def header_dir(tmp_path):
    d = tmp_path / "header"
    d.mkdir()
    _write(d, [])
    return d


@pytest.fixture(scope="module")
def robert_profile(root) -> dict:
    raw = json.loads((root / "fixtures" / "robert" / "profile.json").read_text())
    p = derive.derive_all(raw, portfolio_value_cad=ROBERT_VALUE, as_of=AS_OF)
    lim, trace = limits_mod.build_limits(p, ROBERT_VALUE)
    p["limits"], p["limits_trace"] = lim, trace
    return p


@pytest.fixture(scope="module")
def assets():
    return data.load_assets()


def _profile(base: dict, employer_sector: str | None = None, **limits) -> dict:
    p = copy.deepcopy(base)
    if employer_sector is not None:
        p["identity"]["employer_sector"] = employer_sector
    p["limits"].update(limits)
    return p


def _fr(wpct: dict) -> dict[str, float]:
    return {t: v / 100 for t, v in wpct.items()}


def _of(checks, cid: str) -> list[dict]:
    return [c for c in checks if c["id"] == cid]


# ---------------------------------------------------------------- loader (SPEC §3.2)

@pytest.mark.parametrize("blank", ["source_url", "as_of", "sector", "etf"])
def test_loader_rejects_rows_without_source_url_as_of_sector_or_etf(tmp_path, blank):
    """§3.2 / CLAUDE.md: a row without its source (url, date) or key is rejected at load, never guessed."""
    rows = _rows()
    rows[1][blank] = ""
    _write(tmp_path, rows)
    with pytest.raises(ValueError):
        data.load_meta("etf_sector_weights", tmp_path)


def test_loader_rejects_a_weight_outside_zero_one(tmp_path):
    """SOURCES.md: `weight` is a fraction (0-1); a percent typed by mistake (40) is rejected."""
    rows = _rows()
    rows[0]["weight"] = "40"
    _write(tmp_path, rows)
    with pytest.raises(ValueError):
        data.load_meta("etf_sector_weights", tmp_path)


def test_loader_reads_a_sourced_file_with_float_weights(full_dir):
    """§3.2: columns etf, sector, weight, source_url, as_of; weight typed as a float fraction."""
    df = data.load_meta("etf_sector_weights", full_dir)
    assert list(df.columns)[:5] == SECTOR_COLS
    assert set(df["etf"]) == set(SHARES)
    spy = df[df["etf"] == "SPY"].set_index("sector")["weight"]
    assert spy[IT] == pytest.approx(0.30, abs=TOL)
    assert float(spy.sum()) == pytest.approx(1.0, abs=TOL)


def test_loader_header_only_file_is_empty_and_missing_file_is_none(empty_dir, header_dir):
    """§3.2: header-only loads as zero rows; a file that is not built yet loads as None."""
    assert data.load_meta("etf_sector_weights", empty_dir) is None
    df = data.load_meta("etf_sector_weights", header_dir)
    assert df is not None and len(df) == 0


def test_the_real_meta_file_if_present_loads_and_is_fully_sourced():
    """The real file is human-entered from issuer pages; whatever is committed must pass the loader."""
    df = data.load_meta("etf_sector_weights")
    if df is None:      # not built yet: a human task, never code
        return
    assert (df["source_url"] != "").all() and (df["as_of"] != "").all()


# ---------------------------------------------------------------- engine: no file -> unchanged (SPEC §5)

def test_engine_without_a_file_or_with_a_header_only_file_is_unchanged_and_lookthrough_false(
        mp, market, empty_dir, header_dir):
    """§5: no file (or no rows) -> `Broad market` kept, lookthrough False, output byte-identical in both cases."""
    none = _analyse(mp, market, FULL, empty_dir)
    header = _analyse(mp, market, FULL, header_dir)
    assert none["lookthrough"] is False
    assert header["lookthrough"] is False
    assert "Broad market (diversified)" in _sector_groups(none)
    assert _dump(header) == _dump(none)


def test_engine_without_a_file_matches_the_pre_lookthrough_sector_split(mp, market, empty_dir):
    """§5: with no look-through the sector groups are exactly the per-ticker `engine.sector` buckets."""
    out = _analyse(mp, market, FULL, empty_dir)
    exp = _expected_sector(market, FULL, {})
    got = _sector_groups(out)
    assert set(got) == set(exp)
    for s, (wt, rk) in exp.items():
        assert got[s]["weight"] == pytest.approx(round(wt, 4), abs=TOL)
        assert got[s]["risk"] == pytest.approx(round(rk, 4), abs=TOL)


def test_engine_exposes_a_sector_lookthrough_function():
    """§5 names `sector_lookthrough`; its signature is not specified, so only existence is pinned."""
    assert callable(getattr(engine, "sector_lookthrough", None))


# ---------------------------------------------------------------- engine: look-through on (SPEC §5)

def test_engine_lookthrough_flag_is_true_when_every_held_equity_etf_is_covered(mp, market, full_dir):
    """§5: file exists and covers the held equity ETFs -> lookthrough True (a real bool, JSON-serialisable)."""
    out = _analyse(mp, market, FULL, full_dir)
    assert out["lookthrough"] is True
    json.dumps(out["lookthrough"])


def test_engine_lookthrough_splits_etf_weight_and_risk_by_sector_weight(mp, market, full_dir):
    """§5 sector_lookthrough: ETF weight and risk contribution are split across sectors by weight."""
    out = _analyse(mp, market, FULL, full_dir)
    exp = _expected_sector(market, FULL, SHARES)
    got = _sector_groups(out)
    assert set(got) == set(exp)
    for s, (wt, rk) in exp.items():
        assert got[s]["weight"] == pytest.approx(round(wt, 4), abs=TOL), s
        assert got[s]["risk"] == pytest.approx(round(rk, 4), abs=TOL), s


def test_engine_lookthrough_removes_broad_market_when_all_etfs_are_covered(mp, market, full_dir):
    """§5/§7.3: the `Broad market` buckets disappear; bonds and gold keep their own (non-equity) buckets."""
    got = _sector_groups(_analyse(mp, market, FULL, full_dir))
    assert not [n for n in got if n.startswith(BROAD)]
    assert "Bonds" in got and "Gold" in got
    assert IT in got and "Financials" in got


def test_engine_lookthrough_keeps_weight_and_risk_totals_and_sort_order(mp, market, full_dir):
    """§5: the split moves weight and risk between sectors, it never creates or loses any (sums stay 1)."""
    g = _analyse(mp, market, FULL, full_dir)["groups"]["sector"]
    assert sum(x["weight"] for x in g) == pytest.approx(1.0, abs=1e-3)   # 4-dp rounding per group
    assert sum(x["risk"] for x in g) == pytest.approx(1.0, abs=1e-3)
    risks = [x["risk"] for x in g]
    assert risks == sorted(risks, reverse=True)                           # same ordering rule as before


def test_engine_lookthrough_changes_only_the_sector_group_and_the_flag(mp, market, empty_dir, full_dir):
    """§5 / CLAUDE.md: the math is untouched; only groups.sector (and `lookthrough`) differ from the no-file run."""
    base = _analyse(mp, market, FULL, empty_dir)
    look = _analyse(mp, market, FULL, full_dir)
    for out in (base, look):
        out["groups"] = {k: v for k, v in out["groups"].items() if k != "sector"}
        del out["lookthrough"]
        # SPEC-AMBIGUITY: `assumptions` may gain a look-through line, so it is not compared.
        del out["assumptions"]
    assert _dump(look) == _dump(base)


def test_engine_lookthrough_is_deterministic(mp, market, full_dir):
    """§5: same inputs, same seed -> identical output with the look-through on."""
    assert _dump(_analyse(mp, market, FULL, full_dir)) == _dump(_analyse(mp, market, FULL, full_dir))


def test_engine_lookthrough_leaves_an_uncovered_etf_as_broad_market(mp, market, full_dir):
    """§3.2/§5: ESGV has no rows -> it stays `Broad market`; the covered SPY and XIU.TO are still split."""
    out = _analyse(mp, market, PARTIAL, full_dir)
    exp = _expected_sector(market, PARTIAL, SHARES)
    got = _sector_groups(out)
    broad = [n for n in got if n.startswith(BROAD)]
    assert broad == ["Broad market (diversified)"]
    assert got[broad[0]]["weight"] == pytest.approx(round(exp["Broad market (diversified)"][0], 4), abs=TOL)
    assert got[broad[0]]["risk"] == pytest.approx(round(exp["Broad market (diversified)"][1], 4), abs=TOL)
    assert got[IT]["weight"] == pytest.approx(round(exp[IT][0], 4), abs=TOL)
    assert got[IT]["risk"] == pytest.approx(round(exp[IT][1], 4), abs=TOL)
    assert isinstance(out["lookthrough"], bool)


def test_engine_lookthrough_ignores_rows_of_etfs_not_held(mp, market, tmp_path):
    """§5: rows for an ETF that is not in the portfolio change nothing (FULL without QQQ, file has QQQ rows)."""
    d = tmp_path / "with_qqq"
    d.mkdir()
    _write(d, _rows())
    d2 = tmp_path / "without_qqq"
    d2.mkdir()
    _write(d2, _rows({k: v for k, v in SHARES.items() if k != "QQQ"}))
    wpct = {k: v for k, v in FULL.items() if k != "QQQ"}
    wpct["XBB.TO"] += FULL["QQQ"]
    assert _dump(_analyse(mp, market, wpct, d)) == _dump(_analyse(mp, market, wpct, d2))


# ---------------------------------------------------------------- H6 employer sector (SPEC §7.3)

# NVDA 0.05 direct IT; ETF IT part = SPY 0.30 x 0.30 + QQQ 0.10 x 0.60 + XIU.TO 0.10 x 0 = 0.15 -> 0.20 in total.
H6_PF = {"NVDA": 0.05, "SPY": 0.30, "QQQ": 0.10, "XIU.TO": 0.10, "XBB.TO": 0.45}


def _h6_main(checks) -> list[dict]:
    return [c for c in _of(checks, "H6") if c["status"] in ("pass", "violation")]


def test_h6_adds_the_etf_lookthrough_part_and_flags_a_violation_the_direct_part_hides(
        robert_profile, assets, full_dir):
    """§7.3 H6: Σ equity weight in employer_sector (+ ETF look-through): 0.05 direct + 0.15 via ETFs = 0.20 > 0.15."""
    p = _profile(robert_profile, IT, max_employer_sector_weight=0.15)
    checks = hard.run_cheap(H6_PF, p, assets, _meta(full_dir))
    main = _h6_main(checks)
    assert len(main) == 1
    assert main[0]["status"] == "violation"
    assert main[0]["subject"] == IT
    assert main[0]["value"] == pytest.approx(0.20, abs=TOL)
    assert main[0]["limit"] == pytest.approx(0.15, abs=TOL)
    # every equity ETF is covered: nothing is left unknown for the ETF part
    assert [c for c in _of(checks, "H6") if c["status"] == "unknown"] == []


def test_h6_passes_with_the_same_total_under_a_higher_limit_and_no_unknown(robert_profile, assets, full_dir):
    """§7.3 H6: total 0.20 <= 0.25 -> pass with value 0.20 (not the direct 0.05); the `unknown` ETF part is gone."""
    p = _profile(robert_profile, IT, max_employer_sector_weight=0.25)
    checks = hard.run_cheap(H6_PF, p, assets, _meta(full_dir))
    main = _h6_main(checks)
    assert [c["status"] for c in main] == ["pass"]
    assert main[0]["value"] == pytest.approx(0.20, abs=TOL)
    assert [c for c in _of(checks, "H6") if c["status"] == "unknown"] == []


def test_h6_other_employer_sector_uses_that_sectors_shares(robert_profile, assets, full_dir):
    """§7.3 H6 (Health Care): JNJ 0.05 + SPY 0.30 x 0.20 = 0.11; QQQ and XIU.TO have no Health Care rows (0)."""
    pf = dict(H6_PF, JNJ=0.05, **{"XBB.TO": 0.40})
    for lim, status in ((0.15, "pass"), (0.10, "violation")):
        p = _profile(robert_profile, "Health Care", max_employer_sector_weight=lim)
        main = _h6_main(hard.run_cheap(pf, p, assets, _meta(full_dir)))
        assert [c["status"] for c in main] == [status]
        assert main[0]["value"] == pytest.approx(0.11, abs=TOL)


def test_h6_bond_and_gold_etfs_do_not_count_for_the_employer_sector(robert_profile, assets, full_dir):
    """§7.3 H6: only equity ETFs are looked through; bond/gold ETFs add nothing and never raise an `unknown`."""
    p = _profile(robert_profile, IT, max_employer_sector_weight=0.25)
    pf = {"NVDA": 0.05, "XBB.TO": 0.30, "GLD": 0.05, "AGG": 0.10}
    checks = hard.run_cheap(pf, p, assets, _meta(full_dir))
    main = _h6_main(checks)
    assert [c["status"] for c in main] == ["pass"]
    assert main[0]["value"] == pytest.approx(0.05, abs=TOL)
    assert [c for c in _of(checks, "H6") if c["status"] == "unknown"] == []


def test_h6_uncovered_etf_still_emits_unknown_for_its_part_only(robert_profile, assets, full_dir):
    """§7.3 H6: "ETF part missing -> also emit unknown". ESGV has no rows: covered part counted, ESGV part unknown."""
    p = _profile(robert_profile, IT, max_employer_sector_weight=0.25)
    pf = dict(H6_PF, ESGV=0.10, **{"XBB.TO": 0.35})
    checks = hard.run_cheap(pf, p, assets, _meta(full_dir))
    main = _h6_main(checks)
    assert [c["status"] for c in main] == ["pass"]
    assert main[0]["value"] == pytest.approx(0.20, abs=TOL)
    unk = [c for c in _of(checks, "H6") if c["status"] == "unknown"]
    assert len(unk) == 1
    assert unk[0]["subject"] == IT
    # SPEC-AMBIGUITY: the unknown's value is the weight that could not be looked through (ESGV only), the stricter reading.
    assert unk[0]["value"] == pytest.approx(0.10, abs=TOL)


@pytest.mark.parametrize("which", ["no_file", "header_only"])
def test_h6_without_lookthrough_rows_is_the_p0_behaviour(robert_profile, assets, empty_dir, header_dir, which):
    """§7.3 H6: no file or no rows -> direct part only (0.05) plus one `unknown` for the ETF weight (0.50)."""
    p = _profile(robert_profile, IT, max_employer_sector_weight=0.15)
    meta = _meta(empty_dir if which == "no_file" else header_dir)
    checks = hard.run_cheap(H6_PF, p, assets, meta)
    main = _h6_main(checks)
    assert [(c["status"], c["value"]) for c in main] == [("pass", pytest.approx(0.05, abs=TOL))]
    unk = [c for c in _of(checks, "H6") if c["status"] == "unknown"]
    assert len(unk) == 1
    assert unk[0]["value"] == pytest.approx(0.50, abs=TOL)


def test_h6_checks_keep_the_section_7_3_shape_and_are_hard(robert_profile, assets, full_dir):
    """§7.3: each H6 result carries the documented keys, kind hard, confidence 1.0, and derives from the limit."""
    p = _profile(robert_profile, IT, max_employer_sector_weight=0.15)
    for c in _of(hard.run_cheap(H6_PF, p, assets, _meta(full_dir)), "H6"):
        assert {"id", "kind", "status", "subject", "value", "limit", "unit", "derived_from", "source",
                "confidence", "message_key"} <= set(c)
        assert c["kind"] == "hard" and c["confidence"] == 1.0
        assert "limits.max_employer_sector_weight" in c["derived_from"]


# ---------------------------------------------------------------- H10 sector risk concentration (SPEC §7.3)

def _synthetic_metrics(base: dict, groups: list[dict], lookthrough: bool) -> dict:
    m = copy.deepcopy(base)
    m["groups"]["sector"] = groups
    m["lookthrough"] = lookthrough
    return m


@pytest.fixture
def base_metrics(mp, market, empty_dir):
    return _analyse(mp, market, FULL, empty_dir)


LOOK_GROUPS = [{"name": IT, "weight": 0.30, "risk": 0.45}, {"name": "Financials", "weight": 0.20, "risk": 0.30},
               {"name": "Bonds", "weight": 0.30, "risk": 0.10}, {"name": "Health Care", "weight": 0.20, "risk": 0.15}]


@pytest.mark.parametrize("lim,status", [(0.40, "violation"), (0.50, "pass")])
def test_h10_with_lookthrough_uses_the_max_sector_share_and_emits_no_unknown(
        robert_profile, base_metrics, lim, status):
    """§7.3 H10: max sector `risk` share vs max; with look-through on there is nothing left to flag `unknown`."""
    p = _profile(robert_profile, max_sector_risk_share=lim)
    m = _synthetic_metrics(base_metrics, LOOK_GROUPS, True)
    h10 = _of(hard.run_engine(m, p), "H10")
    assert [c["status"] for c in h10] == [status]
    assert h10[0]["subject"] == IT
    assert h10[0]["value"] == pytest.approx(0.45, abs=TOL)
    assert h10[0]["limit"] == pytest.approx(lim, abs=TOL)


def test_h10_without_lookthrough_excludes_broad_market_and_flags_it_unknown(robert_profile, base_metrics):
    """§7.3 H10 (unchanged): look-through off -> Broad market is out of the max and reported `unknown`."""
    groups = [{"name": "Broad market (diversified)", "weight": 0.5, "risk": 0.60},
              {"name": "Financials", "weight": 0.2, "risk": 0.25}, {"name": "Bonds", "weight": 0.3, "risk": 0.15}]
    p = _profile(robert_profile, max_sector_risk_share=0.30)
    h10 = _of(hard.run_engine(_synthetic_metrics(base_metrics, groups, False), p), "H10")
    assert sorted(c["status"] for c in h10) == ["pass", "unknown"]
    assert [c["subject"] for c in h10 if c["status"] == "pass"] == ["Financials"]
    assert [c["subject"] for c in h10 if c["status"] == "unknown"] == ["Broad market"]


def test_h10_end_to_end_with_lookthrough_has_no_unknown_and_tops_the_real_sector_split(
        mp, market, robert_profile, full_dir):
    """§5 + §7.3: engine look-through output feeds H10 directly: no `unknown`, subject = the riskiest sector."""
    out = _analyse(mp, market, FULL, full_dir)
    top = max(out["groups"]["sector"], key=lambda g: g["risk"])
    p = _profile(robert_profile, max_sector_risk_share=top["risk"] - 0.01)
    h10 = _of(hard.run_engine(out, p), "H10")
    assert [c["status"] for c in h10] == ["violation"]
    assert h10[0]["subject"] == top["name"]
    assert h10[0]["value"] == pytest.approx(top["risk"], abs=TOL)
    p_ok = _profile(robert_profile, max_sector_risk_share=top["risk"] + 0.01)
    assert [c["status"] for c in _of(hard.run_engine(out, p_ok), "H10")] == ["pass"]


def test_h10_without_a_file_end_to_end_keeps_the_unknown(mp, market, robert_profile, empty_dir):
    """§7.3 H10: no etf_sector_weights file -> the Broad market `unknown` stays (nothing is guessed)."""
    out = _analyse(mp, market, FULL, empty_dir)
    h10 = _of(hard.run_engine(out, robert_profile), "H10")
    assert "unknown" in {c["status"] for c in h10}


# ---------------------------------------------------------------- H11 ETF part (SPEC §7.3, §19)

def _h11_etfs(checks, etfs=("SPY", "QQQ", "XIU.TO")) -> dict[str, dict]:
    return {c["subject"]: c for c in _of(checks, "H11") if c["subject"] in etfs}


def test_h11_sector_weights_alone_never_pass_or_violate_an_etf(robert_profile, assets, full_dir):
    """§7.3 H11: a violation needs a sourced tag row; sector weights are not tags, so the ETF is not cleared."""
    p = _profile(robert_profile, excluded_activities=["fossil_fuel_producer"])
    pf = {"SPY": 0.30, "QQQ": 0.10, "XIU.TO": 0.10, "XBB.TO": 0.50}
    etfs = _h11_etfs(hard.run_cheap(pf, p, assets, _meta(full_dir)))
    assert set(etfs) == {"SPY", "QQQ", "XIU.TO"}
    # SPEC-AMBIGUITY: §3.2/§19 say look-through removes "most unknowns on H11"; sector weights carry no activity
    # evidence, so the stricter reading is kept: an ETF with no sourced tag stays `unknown`, never `pass`/`violation`.
    assert {c["status"] for c in etfs.values()} == {"unknown"}


def test_h11_lookthrough_file_does_not_change_a_held_stocks_h11_result(robert_profile, assets, full_dir, empty_dir):
    """§7.3 H11: a held equity with no tag row is `unknown` with or without the sector file."""
    p = _profile(robert_profile, excluded_activities=["fossil_fuel_producer"])
    pf = {"XOM": 0.05, "SPY": 0.30, "XBB.TO": 0.65}

    def stock(d):
        return [(c["status"], c["value"], c["message_key"])
                for c in _of(hard.run_cheap(pf, p, assets, _meta(d)), "H11") if c["subject"] == "XOM"]

    assert stock(full_dir) == stock(empty_dir)
    assert [s for s, _, _ in stock(full_dir)] == ["unknown"]


def test_h11_still_violates_on_a_sourced_tag_row_with_lookthrough_on(robert_profile, assets, full_dir):
    """§3.2/§7.3 H11: a human-sourced tag row on a held stock gives a violation; ETFs stay `unknown`."""
    with open(full_dir / "activity_tags.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "tag", "evidence_summary", "source_url", "as_of", "added_by"])
        w.writerow(["XOM", "fossil_fuel_producer", "oil and gas producer", URL, "2026-11-01", "test-human"])
    p = _profile(robert_profile, excluded_activities=["fossil_fuel_producer"])
    pf = {"XOM": 0.05, "SPY": 0.30, "XBB.TO": 0.65}
    h11 = _of(hard.run_cheap(pf, p, assets, _meta(full_dir)), "H11")
    assert [(c["subject"], c["status"]) for c in h11 if c["status"] == "violation"] == [("XOM", "violation")]
    assert [c["status"] for c in h11 if c["subject"] == "SPY"] == ["unknown"]


# ---------------------------------------------------------------- docs + report schema (SPEC §13, §19)

def test_metrics_with_lookthrough_carry_a_boolean_flag_and_stay_report_schema_valid(
        mp, market, full_dir, root):
    """§13: the engine metrics (with look-through) still satisfy the report `metrics` definition."""
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((root / "coherence_analyzer" / "schemas" / "report.json").read_text())
    out = json.loads(json.dumps(_analyse(mp, market, FULL, full_dir), default=str))
    assert out["lookthrough"] is True
    jsonschema.validate(out, {"$schema": schema.get("$schema"), "$defs": schema["$defs"],
                              "$ref": "#/$defs/metrics"})


def test_sources_md_and_readme_document_the_file_and_the_four_etfs(root):
    """Task P2.13: format + the four ETFs (XIU.TO, SPY, QQQ, ESGV) are documented in SOURCES.md and docs/assumptions.md."""
    sources = (root / "data" / "meta" / "SOURCES.md").read_text()
    readme = (root / "docs" / "assumptions.md").read_text()
    for text in (sources, readme):
        assert "etf_sector_weights" in text
        assert "look-through" in text.lower() or "lookthrough" in text.lower()
    for etf in ("XIU.TO", "SPY", "QQQ", "ESGV"):
        assert etf in sources
        assert etf in readme
    assert "source_url" in sources and "as_of" in sources

