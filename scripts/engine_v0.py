"""Risk engine prototype: real starter data -> cone, risk decomposition, drivers.
Outputs JSON consumed by the design (and later by the Bedrock explainer)."""
import json, sys
import numpy as np, pandas as pd

D = sys.argv[1] if len(sys.argv) > 1 else "data/starter"
px = pd.read_csv(f"{D}/prices_daily.csv", parse_dates=["date"])
assets = pd.read_csv(f"{D}/assets_universe.csv").set_index("ticker")
fx = pd.read_csv(f"{D}/fx_usdcad_daily.csv", parse_dates=["date"]).set_index("date")["usdcad"]

# month-end adj_close per ticker, converted to CAD with month-end USDCAD (last available rate)
wide = px.pivot(index="date", columns="ticker", values="adj_close").sort_index()
m = wide.resample("ME").last()
fxm = fx.resample("ME").last().reindex(m.index).ffill()
usd = [t for t in m.columns if assets.loc[t, "currency"] == "USD"]
m_cad = m.copy(); m_cad[usd] = m[usd].mul(fxm, axis=0)
# drop partial first month (starts 2021-11-12) and partial last month (ends 2026-11-12)
r_cad = m_cad.pct_change().iloc[2:-1]
r_loc = m.pct_change().iloc[2:-1]
fxr = fxm.pct_change().iloc[2:-1]
assert r_cad.notna().all().all(), r_cad.columns[r_cad.isna().any()].tolist()

def country(t):
    a = assets.loc[t]
    if a.asset_class == "crypto": return "Global (crypto)"
    if t == "GLD": return "Global (gold)"
    return "Canada" if a.currency == "CAD" else "United States"

def klass(t):
    return {"equity": "Stocks", "etf_equity": "Equity ETFs", "etf_bond": "Bonds",
            "etf_commodity": "Gold", "crypto": "Crypto"}[assets.loc[t, "asset_class"]]

def sector(t):
    s = assets.loc[t, "sector"]
    if s.startswith("Broad market"): return "Broad market (diversified)"
    if s.startswith("Bonds"): return "Bonds"
    return s

ASSUMED = {"Stocks": .06, "Equity ETFs": .06, "Bonds": .03, "Gold": .03, "Crypto": .06}  # team assumption, annual, CAD

def analyse(w, horizon_m, start=100_000, n=5000, block=12, seed=7, drift="assumed"):
    w = pd.Series(w, dtype=float); w /= w.sum()
    R = r_cad[w.index]
    cov = R.cov() * 12
    var = float(w @ cov @ w); vol = var ** 0.5
    rc = w * (cov @ w) / var  # share of variance, sums to 1
    port = (R * w).sum(axis=1)
    if drift == "assumed":
        mu = pd.Series({t: (1 + ASSUMED[klass(t)]) ** (1 / 12) - 1 for t in w.index})
        Rb = R - R.mean() + mu
    else:
        Rb = R
    port_b = (Rb * w).sum(axis=1)
    # local-currency vol (no FX) to isolate the currency effect
    port_loc = (r_loc[w.index] * w).sum(axis=1)
    vol_loc = float(port_loc.std() * 12 ** 0.5)
    # historical max drawdown of monthly-rebalanced portfolio
    nav = (1 + port).cumprod(); dd = nav / nav.cummax() - 1
    mdd = float(dd.min()); mdd_date = dd.idxmin().strftime("%Y-%m")
    # circular block bootstrap on the joint monthly vector (keeps cross-asset correlation)
    rng = np.random.default_rng(seed); T = len(port); pv = port_b.values
    paths = np.empty((n, horizon_m))
    for i in range(n):
        idx = []
        while len(idx) < horizon_m:
            s = rng.integers(T); idx += [(s + k) % T for k in range(block)]
        paths[i] = np.cumprod(1 + pv[idx[:horizon_m]])
    q = np.percentile(paths, [5, 25, 50, 75, 95], axis=0) * start
    groups = {}
    for name, fn in [("sector", sector), ("country", country), ("class", klass)]:
        g = {}
        for t in w.index:
            k = fn(t); g.setdefault(k, [0, 0]); g[k][0] += w[t]; g[k][1] += rc[t]
        groups[name] = sorted([{"name": k, "weight": round(v[0], 4), "risk": round(v[1], 4)} for k, v in g.items()],
                              key=lambda x: -x["risk"])
    width = q[4, -1] - q[0, -1]
    drivers = sorted([{"ticker": t, "name": assets.loc[t, "name"], "weight": round(w[t], 4),
                       "risk": round(rc[t], 4), "vol": round(float(R[t].std() * 12 ** 0.5), 4)} for t in w.index],
                     key=lambda x: -x["risk"])
    return {"vol": round(vol, 4), "vol_local": round(vol_loc, 4), "mdd": round(mdd, 4), "mdd_date": mdd_date,
            "cone": {p: [round(float(x), 0) for x in q[i]] for i, p in enumerate(["p5", "p25", "p50", "p75", "p95"])},
            "width": round(float(width), 0), "loss_prob": round(float((paths[:, -1] < 1).mean()), 4),
            "groups": groups, "drivers": drivers,
            "corr_eq_bond": round(float(r_cad[["SPY", "XBB.TO"]].corr().iloc[0, 1]), 3)}

ROBERT = {"XIU.TO": 18, "RY.TO": 7, "ENB.TO": 6, "FTS.TO": 7, "JNJ": 5, "PG": 5, "SPY": 12,
          "XBB.TO": 22, "ZST.TO": 13, "GLD": 5}
ROBERT_NVDA = dict(ROBERT); ROBERT_NVDA["NVDA"] = 10; ROBERT_NVDA["ZST.TO"] = 3  # funded from short-term bonds
AMIRA = {"QQQ": 22, "NVDA": 8, "SHOP.TO": 8, "BTC-USD": 10, "ETH-USD": 5, "SPY": 15, "XIU.TO": 10,
         "CNR.TO": 6, "GLD": 6, "AGG": 10}

out = {"window": [str(r_cad.index[0].date()), str(r_cad.index[-1].date())], "months": len(r_cad),
       "robert": analyse(ROBERT, 48, 400_000), "robert_nvda": analyse(ROBERT_NVDA, 48, 400_000),
       "amira": analyse(AMIRA, 180, 60_000),
       "robert_hist": analyse(ROBERT, 48, 400_000, drift="historical"),
       "portfolios": {"robert": ROBERT, "robert_nvda": ROBERT_NVDA, "amira": AMIRA}}
json.dump(out, open("engine_out.json", "w"), indent=1)
for k in ["robert", "robert_nvda", "amira"]:
    o = out[k]; print(k, "vol", o["vol"], "vol_local", o["vol_local"], "mdd", o["mdd"], o["mdd_date"],
                      "P5/P50/P95", o["cone"]["p5"][-1], o["cone"]["p50"][-1], o["cone"]["p95"][-1], "width", o["width"], "loss", o["loss_prob"])
    print("  sector", o["groups"]["sector"][:5]); print("  country", o["groups"]["country"]); print("  class", o["groups"]["class"])
    print("  drivers", [(d["ticker"], d["weight"], d["risk"], d["vol"]) for d in o["drivers"][:6]])
print(out["window"], out["months"], "corr SPY/XBB", out["robert"]["corr_eq_bond"])
