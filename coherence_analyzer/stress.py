"""Rate-scenario stress (SPEC §17 step 16, §3.1 macro): what a yield shock does to the bond ETFs, through duration.

Facts and assumptions, no forecast and no advice. A bond ETF's price change ~= -effective duration x Δy (first order;
Δy in decimal, 100 bp = 0.01), plus 1/2 x convexity x Δy² only when a sourced convexity is given. The portfolio
impact in CAD = value_cad x Σ weight x price change. Equities, crypto and gold get no modelled impact: no rate beta
is estimated, and the assumptions say so instead of inventing one.

Durations: the starter pack has no duration column. A duration is `sourced` only when `data/meta/asset_meta.csv`
carries `effective_duration` with a `duration_source_url`; otherwise the ASSUMED table below is used and the block
says `sourced: false` (README -> Assumptions). Context comes from `macro_rates.csv` up to the reference date (D1).

The block is `info` only (invariants 2 and 3): it never produces a check, never enters the verdict and never feeds
`limits`. Numbers a narrator may copy are pre-formatted in `facts` (invariant 6, numcheck). Pure functions.
"""
from __future__ import annotations

from typing import Mapping

import pandas as pd

from . import data
from .factors import fmt

SHOCKS_BP: tuple[int, ...] = (-100, 100, 200)
ASSUMED = "ASSUMED"

# ASSUMED (team assumption, not sourced): effective durations in years, approximate values for the index each ETF
# tracks. TODO(human): source each issuer's published effective duration (URL + as-of date) into
# data/meta/asset_meta.csv columns `effective_duration, duration_source_url, duration_as_of`.
ASSUMED_DURATIONS: dict[str, float] = {
    "XBB.TO": 7.3,    # FTSE Canada Universe Bond Index
    "ZST.TO": 0.3,    # ultra short-term
    "AGG": 6.0,       # Bloomberg U.S. Aggregate
    "TLT": 16.5,      # ICE U.S. Treasury 20+ Year
}

# Benchmark yield in macro_rates.csv used as the context proxy for each ETF's yield (a proxy, not the ETF's yield).
RATE_SERIES: dict[str, str] = {
    "XBB.TO": "BD.CDN.10YR.DQ.YLD",
    "ZST.TO": "BD.CDN.2YR.DQ.YLD",
    "AGG": "DGS10",
    "TLT": "DGS10",
}

_BOND_CLASS = "etf_bond"
_EPS = 1e-12


def bond_impact(duration: float, shock_bp: float, convexity: float | None = None) -> float:
    """Price change (fraction) of a bond position for a yield change of `shock_bp` basis points."""
    dy = float(shock_bp) / 10_000.0
    out = -float(duration) * dy
    if convexity is not None:
        out += 0.5 * float(convexity) * dy * dy
    return out + 0.0          # no -0.0


def _durations(tickers: list[str], asset_meta: pd.DataFrame | None,
               override: Mapping[str, float] | None) -> tuple[dict[str, tuple[float, str, str | None]], list[str]]:
    """{ticker: (duration, source, as_of)} and the bond tickers with no duration at all (never guessed)."""
    sourced: dict[str, tuple[float, str, str | None]] = {}
    if asset_meta is not None and {"ticker", "effective_duration", "duration_source_url"} <= set(asset_meta.columns):
        for _, r in asset_meta.iterrows():
            raw, url = str(r["effective_duration"]).strip(), str(r["duration_source_url"]).strip()
            if raw and url and raw.lower() != "nan" and url.lower() != "nan":
                as_of = str(r.get("duration_as_of", "") or "").strip() or None
                sourced[str(r["ticker"]).strip()] = (float(raw), url, as_of)
    table = dict(ASSUMED_DURATIONS) if override is None else {**ASSUMED_DURATIONS, **override}
    out: dict[str, tuple[float, str, str | None]] = {}
    missing: list[str] = []
    for t in tickers:
        if t in sourced:
            out[t] = sourced[t]
        elif t in table:
            out[t] = (float(table[t]), ASSUMED, None)
        else:
            missing.append(t)
    return out, missing


def rate_context(macro: pd.DataFrame, series_id: str, as_of: str | None) -> dict | None:
    """Level of `series_id` at the reference date, its range in the window, and its largest 12-month rise and fall
    (month-end values, last observation of each month, in bp). None if the series has no data up to `as_of`."""
    try:
        s = data.macro_series(macro, series_id, upto=as_of)
    except KeyError:
        return None
    if s.empty:
        return None
    label_rows = macro.loc[macro["series_id"] == series_id, "label"]
    me = s.resample("ME").last().dropna()
    d = (me.diff(12) * 100.0).dropna()
    rise = fall = None
    rise_end = fall_end = None
    if not d.empty:
        rise, rise_end = round(float(d.max()), 1) + 0.0, d.idxmax().strftime("%Y-%m-%d")
        fall, fall_end = round(float(d.min()), 1) + 0.0, d.idxmin().strftime("%Y-%m-%d")
    return {"series_id": series_id, "label": str(label_rows.iloc[0]) if len(label_rows) else series_id,
            "unit": "percent", "current": float(s.iloc[-1]), "current_date": s.index[-1].strftime("%Y-%m-%d"),
            "window_start": s.index[0].strftime("%Y-%m-%d"), "min": float(s.min()), "max": float(s.max()),
            "max_12m_rise_bp": rise, "max_12m_rise_end": rise_end,
            "max_12m_fall_bp": fall, "max_12m_fall_end": fall_end}


def _scenario(sid: str, kind: str, shock_bp: float | None, shocks: Mapping[str, float], bonds: list[dict],
              value_cad: float) -> dict:
    holdings = []
    for b in bonds:
        if b["ticker"] not in shocks:
            continue
        sh = shocks[b["ticker"]]
        p = bond_impact(b["duration"], sh)
        holdings.append({"ticker": b["ticker"], "shock_bp": sh, "price_impact": p,
                         "impact_cad": round(value_cad * b["weight"] * p, 2) + 0.0})
    impact = sum(b["weight"] * h["price_impact"] for b in bonds for h in holdings if h["ticker"] == b["ticker"])
    return {"id": sid, "kind": kind, "shock_bp": shock_bp, "impact": impact + 0.0,
            "impact_cad": round(value_cad * impact, 2) + 0.0, "holdings": holdings}


def _shock_text(bp: float, locale: str) -> str:
    unit = "pb" if locale == "fr-CA" else "bp"
    sign = "+" if bp > 0 else ""
    return f"{sign}{fmt.number(bp, locale, 1)}{fmt.NBSP if locale == 'fr-CA' else ' '}{unit}"


def _facts(scenarios: list[dict], contexts: list[dict], locale: str) -> dict:
    sc = {}
    for s in scenarios:
        if s["kind"] == "parallel":
            shock = _shock_text(s["shock_bp"], locale)
        else:
            shock = ("plus forte hausse sur 12 mois de la fenêtre" if locale == "fr-CA"
                     else "largest 12-month rise in the window")
        sc[s["id"]] = {"shock": shock, "impact_pct": fmt.percent(s["impact"], locale, 2),
                       "impact_cad": fmt.amount(s["impact_cad"], locale)}
    ctx = {}
    for c in contexts:
        ctx[c["series_id"]] = {
            "label": c["label"], "date": c["current_date"],
            "current": fmt.percent_points(c["current"], locale, 2),
            "min": fmt.percent_points(c["min"], locale, 2), "max": fmt.percent_points(c["max"], locale, 2),
            "max_12m_rise": None if c["max_12m_rise_bp"] is None else _shock_text(c["max_12m_rise_bp"], locale),
            "max_12m_rise_end": c["max_12m_rise_end"]}
    return {"locale": locale, "scenarios": sc, "rate_context": ctx}


def stress_block(weights: Mapping[str, float], value_cad: float, assets: pd.DataFrame,
                 macro: pd.DataFrame | None = None, *, as_of: str | None = None,
                 shocks_bp: tuple[int, ...] = SHOCKS_BP, asset_meta: pd.DataFrame | None = None,
                 durations: Mapping[str, float] | None = None, locale: str = "fr-CA") -> dict:
    """The report's `stress` block. `weights` are fractions (0-1); `durations` overrides the ASSUMED table (still
    labelled ASSUMED, e.g. for tests); a sourced asset_meta duration wins over both."""
    fmt.check_locale(locale)
    held = {t: float(w) for t, w in weights.items() if float(w) > _EPS}
    cls = {t: str(assets.loc[t, "asset_class"]) if t in assets.index else "" for t in held}
    bond_tickers = sorted(t for t in held if cls[t] == _BOND_CLASS)
    other = sorted(t for t in held if cls[t] != _BOND_CLASS)
    dur, missing = _durations(bond_tickers, asset_meta, durations)

    bonds = [{"ticker": t, "weight": held[t], "duration": dur[t][0], "duration_source": dur[t][1],
              "duration_as_of": dur[t][2], "series_id": RATE_SERIES.get(t)} for t in bond_tickers if t in dur]
    scenarios = [_scenario(f"parallel_{bp:+d}bp", "parallel", bp, {b["ticker"]: bp for b in bonds}, bonds, value_cad)
                 for bp in shocks_bp]

    contexts: list[dict] = []
    if macro is not None:
        seen: list[str] = []
        for t in bond_tickers:
            sid = RATE_SERIES.get(t)
            if sid and sid not in seen:
                seen.append(sid)
        contexts = [c for c in (rate_context(macro, sid, as_of) for sid in seen) if c is not None]
        rise = {c["series_id"]: c["max_12m_rise_bp"] for c in contexts if c["max_12m_rise_bp"] is not None}
        hist = {b["ticker"]: rise[b["series_id"]] for b in bonds if b["series_id"] in rise}
        scenarios.append(_scenario("historical_max_12m_rise", "historical", None, hist, bonds, value_cad))

    other_w = sum(held[t] for t in other)
    assumed = [b for b in bonds if b["duration_source"] == ASSUMED]
    sourced = not assumed and not missing
    assumptions = [
        "Rate stress: an illustration, not a forecast. Bond ETF price change ~= -effective duration x yield change "
        "(first order, no convexity term: no sourced convexity).",
        "Parallel shift: every bond ETF's yield moves by the same number of basis points at once; curve shape, credit "
        "spreads and timing are not modelled.",
        f"No modelled impact for equities, equity ETFs, crypto and gold ({fmt.percent(other_w, 'en-CA')} of the "
        "portfolio): no rate beta is estimated, and their real response to a rate change is not zero.",
        "Shown for information only: not a check, not part of the verdict, not an input of the limits.",
    ]
    if assumed:
        assumptions.insert(1, "Effective durations are ASSUMED (team assumption, not sourced from issuer pages): "
                           + ", ".join(f"{b['ticker']} {fmt.number(b['duration'], 'en-CA')}" for b in assumed)
                           + ". TODO: a human sources issuer effective durations with a URL and date.")
    if any(str(assets.loc[b["ticker"], "currency"]) != "CAD" for b in bonds if b["ticker"] in assets.index):
        assumptions.append("USD bond ETFs: the price change is applied to their CAD value with USD/CAD held "
                           "constant.")
    if missing:
        assumptions.append(f"No duration for {', '.join(missing)}: its impact is not computed (listed, not guessed).")
    if macro is not None:
        assumptions.append(
            f"Rate context from macro_rates.csv up to {as_of or 'the end of the file'}: each bond ETF's yield is "
            "proxied by a benchmark government yield (" + ", ".join(f"{b['ticker']} -> {b['series_id']}" for b in bonds)
            + "). The historical scenario applies each series' largest 12-month rise on month-end values in the window.")

    return {
        "status": "info", "kind": "rate_scenario", "sourced": sourced, "feeds_limits": False, "as_of": as_of,
        "value_cad": float(value_cad),
        "method": "price_change = -effective_duration x delta_y (parallel shift, bp/10000)",
        "bond_holdings": bonds, "missing_duration": missing,
        "not_modelled": {"weight": other_w, "tickers": other},
        "scenarios": scenarios, "rate_context": contexts, "assumptions": assumptions,
        "facts": _facts(scenarios, contexts, locale),
    }
