"""Risk engine: starter data -> vol, drawdown, bootstrap cone, risk decomposition, drivers (SPEC §5).

Importable refactor of scripts/engine_v0.py. The math is unchanged; tests/test_engine_golden.py
compares against the original script. Don't change the math without updating the golden test.
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from . import config, data

# Team assumption: annual drift in CAD per class (labelled in `assumptions`, not a forecast).
ASSUMED = {"Stocks": .06, "Equity ETFs": .06, "Bonds": .03, "Gold": .03, "Crypto": .06}

CLASS_LABEL = {"equity": "Stocks", "etf_equity": "Equity ETFs", "etf_bond": "Bonds",
               "etf_commodity": "Gold", "crypto": "Crypto"}


@dataclass(frozen=True)
class Market:
    r_cad: pd.DataFrame   # monthly returns in CAD
    r_loc: pd.DataFrame   # monthly returns in listing currency
    fxr: pd.Series        # monthly USDCAD returns
    assets: pd.DataFrame  # indexed by ticker

    @property
    def window(self) -> list[str]:
        return [str(self.r_cad.index[0].date()), str(self.r_cad.index[-1].date())]


@lru_cache(maxsize=4)
def load_market(data_dir: str | Path = config.DATA_DIR) -> Market:
    px = data.load_prices(data_dir)
    assets = data.load_assets(data_dir)
    fx = data.load_fx(data_dir)
    # month-end adj_close per ticker, converted to CAD with month-end USDCAD (last available rate)
    wide = px.pivot(index="date", columns="ticker", values="adj_close").sort_index()
    m = wide.resample("ME").last()
    fxm = fx.resample("ME").last().reindex(m.index).ffill()
    usd = [t for t in m.columns if assets.loc[t, "currency"] == "USD"]
    m_cad = m.copy()
    m_cad[usd] = m[usd].mul(fxm, axis=0)
    # iloc[2:-1]: drops partial first month, Dec 2021 (kept for golden parity, SPEC §3.1) and partial last month
    r_cad = m_cad.pct_change().iloc[2:-1]
    r_loc = m.pct_change().iloc[2:-1]
    fxr = fxm.pct_change().iloc[2:-1]
    assert r_cad.notna().all().all(), r_cad.columns[r_cad.isna().any()].tolist()
    return Market(r_cad=r_cad, r_loc=r_loc, fxr=fxr, assets=assets)


def country(mk: Market, t: str) -> str:
    a = mk.assets.loc[t]
    if a.asset_class == "crypto":
        return "Global (crypto)"
    if t == "GLD":
        return "Global (gold)"
    return "Canada" if a.currency == "CAD" else "United States"


def klass(mk: Market, t: str) -> str:
    return CLASS_LABEL[mk.assets.loc[t, "asset_class"]]


def sector(mk: Market, t: str) -> str:
    s = mk.assets.loc[t, "sector"]
    if s.startswith("Broad market"):
        return "Broad market (diversified)"
    if s.startswith("Bonds"):
        return "Bonds"
    return s


def sector_lookthrough(mk: Market, w: pd.Series, rc: pd.Series,
                       shares: dict[str, dict[str, float]] | None = None) -> list[dict]:
    """Sector groups (SPEC §5): each equity ETF in `shares` has its weight and risk contribution split across its
    sectors by weight; every other ticker keeps its own sector() bucket. With no shares this is the plain
    per-ticker split (Broad market kept). Sorted by risk, descending."""
    shares = shares or {}
    g: dict[str, list[float]] = {}
    for t in w.index:
        split = shares.get(t) if mk.assets.loc[t, "asset_class"] == "etf_equity" else None
        for k, sh in (split.items() if split else [(sector(mk, t), None)]):
            acc = g.setdefault(k, [0, 0])
            acc[0] += w[t] if sh is None else w[t] * sh
            acc[1] += rc[t] if sh is None else rc[t] * sh
    return sorted([{"name": k, "weight": round(v[0], 4), "risk": round(v[1], 4)} for k, v in g.items()],
                  key=lambda x: -x["risk"])


def _lookthrough_shares(mk: Market, tickers: list[str],
                        meta_dir: str | Path | None) -> tuple[dict[str, dict[str, float]], bool]:
    """(shares of the held equity ETFs that are covered, lookthrough flag). The flag is True only when the file has
    rows and every held equity ETF is covered; no file or no rows -> ({}, False)."""
    df = data.load_meta("etf_sector_weights", config.META_DIR if meta_dir is None else Path(meta_dir))
    avail = data.etf_sector_shares(df)
    held = [t for t in tickers if mk.assets.loc[t, "asset_class"] == "etf_equity"]
    shares = {t: avail[t] for t in held if t in avail}
    return shares, bool(avail) and len(shares) == len(held)


def assumptions(mk: Market, drift: str, block: int) -> list[str]:
    return [
        f"Drift ({drift}): {ASSUMED if drift == 'assumed' else 'historical sample mean'}"
        + (" (team assumption, annual, CAD; not a forecast)" if drift == "assumed" else ""),
        f"Data window: monthly returns {mk.window[0]} to {mk.window[1]} ({len(mk.r_cad)} months)",
        f"Circular block bootstrap, block = {block} months, on the joint monthly return vector",
        "Survivorship bias: the asset universe was selected in 2026",
        "The sample includes 2022, when stocks and bonds fell together",
    ]


_YM = re.compile(r"^(\d{4})-(\d{2})(?:-\d{2})?$")


def _ym(date: str, what: str) -> int:
    """Month index (year * 12 + month - 1) of a YYYY-MM or YYYY-MM-DD string."""
    m = _YM.match(str(date))
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise ValueError(f"{what}: expected YYYY-MM or YYYY-MM-DD, got {date!r}")
    return int(m.group(1)) * 12 + int(m.group(2)) - 1


def goal_probabilities(paths_cad: np.ndarray, goals: list[dict], as_of: str) -> list[dict]:
    """SPEC §5: for each goal inside the horizon, the share of paths at or above `amount` at the goal's month.
    Month m counts from the reference date `as_of` (D1): m = 1 is the first simulated step. Goals dated at or before
    `as_of`, or after the horizon, are left out. Input order is kept."""
    horizon = paths_cad.shape[1]
    ref = _ym(as_of, "as_of")
    out: list[dict] = []
    for g in goals:
        month = _ym(g["date"], f"goal {g.get('name')!r} date") - ref
        if 1 <= month <= horizon:
            prob = round(float((paths_cad[:, month - 1] >= g["amount"]).mean()), 4)
            out.append({"name": g["name"], "date": g["date"], "amount": g["amount"], "month": month, "prob": prob})
    return out


def analyse(market: Market, weights_pct: dict[str, float], horizon_m: int, start_cad: float = 100_000, *,
            n: int = config.ENGINE_N, block: int = config.ENGINE_BLOCK, seed: int = config.ENGINE_SEED,
            drift: str = "assumed", monthly_contribution: float = 0.0, goals: list[dict] | None = None,
            as_of: str | None = None, return_paths: bool = False,
            meta_dir: str | Path | None = None) -> dict:
    """monthly_contribution (CAD, >= 0) is added to the NAV at the end of each simulated month. `goals` need the
    reference date `as_of` (D1: portfolio.as_of); the engine never reads the wall clock."""
    c = float(monthly_contribution or 0.0)
    if not c >= 0.0:
        raise ValueError(f"monthly_contribution must be >= 0, got {monthly_contribution!r}")
    if goals and as_of is None:
        raise ValueError("goals need `as_of` (the reference date, portfolio.as_of; SPEC D1)")

    mk = market
    w = pd.Series(weights_pct, dtype=float)
    w /= w.sum()
    R = mk.r_cad[w.index]
    cov = R.cov() * 12
    var = float(w @ cov @ w)
    vol = var ** 0.5
    rc = w * (cov @ w) / var  # share of variance, sums to 1
    port = (R * w).sum(axis=1)
    if drift == "assumed":
        mu = pd.Series({t: (1 + ASSUMED[klass(mk, t)]) ** (1 / 12) - 1 for t in w.index})
        Rb = R - R.mean() + mu
    else:
        Rb = R
    port_b = (Rb * w).sum(axis=1)
    # local-currency vol (no FX) to isolate the currency effect
    port_loc = (mk.r_loc[w.index] * w).sum(axis=1)
    vol_loc = float(port_loc.std() * 12 ** 0.5)
    # historical max drawdown of monthly-rebalanced portfolio
    nav = (1 + port).cumprod()
    dd = nav / nav.cummax() - 1
    mdd = float(dd.min())
    mdd_date = dd.idxmin().strftime("%Y-%m")
    # circular block bootstrap on the joint monthly vector (keeps cross-asset correlation)
    rng = np.random.default_rng(seed)
    T = len(port)
    pv = port_b.values
    paths = np.empty((n, horizon_m))
    rets = np.empty((n, horizon_m))
    for i in range(n):
        idx: list[int] = []
        while len(idx) < horizon_m:
            s = rng.integers(T)
            idx += [(s + k) % T for k in range(block)]
        rets[i] = pv[idx[:horizon_m]]
        paths[i] = np.cumprod(1 + rets[i])
    if c:
        # contributions: NAV_t = NAV_{t-1} (1 + r_t) + c, in CAD (not normalised); the draws do not depend on c
        nav_cad = np.empty_like(paths)
        nav = np.full(n, float(start_cad))
        for t in range(horizon_m):
            nav = nav * (1 + rets[:, t]) + c
            nav_cad[:, t] = nav
        q = np.percentile(nav_cad, [5, 25, 50, 75, 95], axis=0)
        loss = float((nav_cad[:, -1] < start_cad + c * horizon_m).mean())   # a loss = below the money put in
    else:
        nav_cad = paths * start_cad
        q = np.percentile(paths, [5, 25, 50, 75, 95], axis=0) * start_cad
        loss = float((paths[:, -1] < 1).mean())
    shares, lookthrough = _lookthrough_shares(mk, list(w.index), meta_dir)
    groups = {"sector": sector_lookthrough(mk, w, rc, shares)}
    for name, fn in [("country", country), ("class", klass)]:
        g: dict[str, list[float]] = {}
        for t in w.index:
            k = fn(mk, t)
            g.setdefault(k, [0, 0])
            g[k][0] += w[t]
            g[k][1] += rc[t]
        groups[name] = sorted([{"name": k, "weight": round(v[0], 4), "risk": round(v[1], 4)} for k, v in g.items()],
                              key=lambda x: -x["risk"])
    width = q[4, -1] - q[0, -1]
    drivers = sorted([{"ticker": t, "name": mk.assets.loc[t, "name"], "weight": round(w[t], 4),
                       "risk": round(rc[t], 4), "vol": round(float(R[t].std() * 12 ** 0.5), 4)} for t in w.index],
                     key=lambda x: -x["risk"])
    out = {"vol": round(vol, 4), "vol_local": round(vol_loc, 4), "mdd": round(mdd, 4), "mdd_date": mdd_date,
           "cone": {p: [round(float(x), 0) for x in q[i]] for i, p in enumerate(["p5", "p25", "p50", "p75", "p95"])},
           "width": round(float(width), 0), "loss_prob": round(loss, 4),
           "groups": groups, "drivers": drivers,
           "corr_eq_bond": round(float(mk.r_cad[["SPY", "XBB.TO"]].corr().iloc[0, 1]), 3),
           # additions (SPEC §5)
           "p5_T": round(float(q[0, -1]), 0), "p50_T": round(float(q[2, -1]), 0), "p95_T": round(float(q[4, -1]), 0),
           "lookthrough": lookthrough,
           "goal_probs": goal_probabilities(nav_cad, list(goals), as_of) if goals else [],
           "assumptions": assumptions(mk, drift, block) + (
               ["ETF sector look-through: each covered equity ETF's weight and risk are split across its sectors by "
                "the issuer sector weights of data/meta/etf_sector_weights.csv (as_of per row)"] if shares else [])}
    if c:
        amt = f"{c:,.2f}".rstrip("0").rstrip(".")
        out["assumptions"].append(
            f"Monthly contribution: {amt} CAD, as declared by the user, added to the portfolio at the end of each "
            "month (not a forecast); loss_prob compares the end value with the start value plus the contributions")
    if return_paths:
        out["paths"] = nav_cad
    return out


# Fixture portfolios (percent), kept here for the CLI printout and golden test.
ROBERT = {"XIU.TO": 18, "RY.TO": 7, "ENB.TO": 6, "FTS.TO": 7, "JNJ": 5, "PG": 5, "SPY": 12,
          "XBB.TO": 22, "ZST.TO": 13, "GLD": 5}
ROBERT_NVDA = dict(ROBERT, NVDA=10, **{"ZST.TO": 3})  # funded from short-term bonds
AMIRA = {"QQQ": 22, "NVDA": 8, "SHOP.TO": 8, "BTC-USD": 10, "ETH-USD": 5, "SPY": 15, "XIU.TO": 10,
         "CNR.TO": 6, "GLD": 6, "AGG": 10}


def _main() -> None:
    ap = argparse.ArgumentParser(description="Print the engine summary for the fixture portfolios.")
    ap.add_argument("--data", default=str(config.DATA_DIR))
    mk = load_market(ap.parse_args().data)
    runs = {"robert": analyse(mk, ROBERT, 48, 400_000), "robert_nvda": analyse(mk, ROBERT_NVDA, 48, 400_000),
            "amira": analyse(mk, AMIRA, 180, 60_000)}
    for k, o in runs.items():
        print(k, "vol", o["vol"], "vol_local", o["vol_local"], "mdd", o["mdd"], o["mdd_date"],
              "P5/P50/P95", o["p5_T"], o["p50_T"], o["p95_T"], "width", o["width"], "loss", o["loss_prob"])
        print("  sector", o["groups"]["sector"][:5])
        print("  drivers", [(d["ticker"], d["weight"], d["risk"], d["vol"]) for d in o["drivers"][:6]])
    print(mk.window, len(mk.r_cad), "corr SPY/XBB", runs["robert"]["corr_eq_bond"])


if __name__ == "__main__":
    _main()
