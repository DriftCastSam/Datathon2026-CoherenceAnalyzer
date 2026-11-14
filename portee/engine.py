"""Risk engine: starter data -> vol, drawdown, bootstrap cone, risk decomposition, drivers (SPEC §5).

Importable refactor of scripts/engine_v0.py. The math is unchanged; tests/test_engine_golden.py
compares against the original script. Don't change the math without updating the golden test.
"""
from __future__ import annotations

import argparse
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


def assumptions(mk: Market, drift: str, block: int) -> list[str]:
    return [
        f"Drift ({drift}): {ASSUMED if drift == 'assumed' else 'historical sample mean'}"
        + (" (team assumption, annual, CAD; not a forecast)" if drift == "assumed" else ""),
        f"Data window: monthly returns {mk.window[0]} to {mk.window[1]} ({len(mk.r_cad)} months)",
        f"Circular block bootstrap, block = {block} months, on the joint monthly return vector",
        "Survivorship bias: the asset universe was selected in 2026",
        "The sample includes 2022, when stocks and bonds fell together",
    ]


def analyse(market: Market, weights_pct: dict[str, float], horizon_m: int, start_cad: float = 100_000, *,
            n: int = config.ENGINE_N, block: int = config.ENGINE_BLOCK, seed: int = config.ENGINE_SEED,
            drift: str = "assumed", monthly_contribution: float = 0.0, goals: list[dict] | None = None,
            return_paths: bool = False) -> dict:
    if monthly_contribution:
        raise NotImplementedError("monthly_contribution: P2 (SPEC §17 step 14)")
    if goals:
        raise NotImplementedError("goal_probs: P2 (SPEC §17 step 14)")

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
    for i in range(n):
        idx: list[int] = []
        while len(idx) < horizon_m:
            s = rng.integers(T)
            idx += [(s + k) % T for k in range(block)]
        paths[i] = np.cumprod(1 + pv[idx[:horizon_m]])
    q = np.percentile(paths, [5, 25, 50, 75, 95], axis=0) * start_cad
    groups = {}
    for name, fn in [("sector", sector), ("country", country), ("class", klass)]:
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
           "width": round(float(width), 0), "loss_prob": round(float((paths[:, -1] < 1).mean()), 4),
           "groups": groups, "drivers": drivers,
           "corr_eq_bond": round(float(mk.r_cad[["SPY", "XBB.TO"]].corr().iloc[0, 1]), 3),
           # additions (SPEC §5)
           "p5_T": round(float(q[0, -1]), 0), "p50_T": round(float(q[2, -1]), 0), "p95_T": round(float(q[4, -1]), 0),
           "lookthrough": False,  # TODO P2 step 13: etf_sector_weights.csv -> sector_lookthrough
           "goal_probs": [],
           "assumptions": assumptions(mk, drift, block)}
    if return_paths:
        out["paths"] = paths * start_cad
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
