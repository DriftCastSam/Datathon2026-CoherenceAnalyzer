"""Portfolio exposure with ETF look-through (D19). Pure functions, no I/O.

What the user really holds, per dimension (sector, country, asset class, listing currency) and per single company,
with each figure split into the holdings it comes from: a stock held directly, or a share of an ETF taken from the
issuer weights in data/meta/etf_{sector,country}_weights.csv and etf_top_holdings.csv (loaded by engine.py).

Rules (nothing is guessed):
- An ETF covered by a weights file is split by those weights; one that is not keeps its own bucket
  (e.g. `Broad market (diversified)`) and is listed in `uncovered`.
- Single companies come from the ETFs' published top holdings only, so an ETF's smaller positions are not seen:
  every single-company figure is a lower bound (`partial: true`), and `etf_listed` says how much of each ETF the
  listed holdings cover.
- Descriptive only (D10): no limit, no status, no ranking of choices. Checks keep reading engine `groups`.
"""
from __future__ import annotations

DIRECT = "direct"
ETF = "etf"
DIMENSIONS = ("sector", "country", "class", "currency")


def _r(x: float) -> float:
    return round(float(x), 4)


def _dimension(weights: dict[str, float], labels: dict[str, dict], key: str,
               shares: dict[str, dict[str, float]], value_cad: float) -> dict:
    """{rows: [{name, weight, value_cad, parts: [{ticker, name, via, weight}]}], uncovered: [etf tickers]}.
    `via` is `etf` for any ETF (split or kept whole: a bond ETF in `Bonds` is still held through a fund).
    rows heaviest first; parts heaviest first. Weights are fractions of the whole portfolio."""
    acc: dict[str, dict[str, list]] = {}
    uncovered: list[str] = []
    for t, w in weights.items():
        lab = labels[t]
        split = shares.get(t) if lab["is_etf"] else None
        if lab["is_etf"] and split is None and key in ("sector", "country") and lab.get("lookthrough_" + key, True):
            uncovered.append(t)
        for bucket, sh in (split.items() if split else [(lab[key], 1.0)]):
            part = acc.setdefault(bucket, {}).setdefault(t, [0.0, ETF if lab["is_etf"] else DIRECT])
            part[0] += w * sh
    rows = []
    for bucket, parts in acc.items():
        tot = sum(p[0] for p in parts.values())
        rows.append({"name": bucket, "weight": _r(tot), "value_cad": round(float(tot * value_cad), 0),
                     "parts": sorted([{"ticker": t, "name": labels[t]["name"], "via": v, "weight": _r(x)}
                                      for t, (x, v) in parts.items()], key=lambda p: (-p["weight"], p["ticker"]))})
    rows.sort(key=lambda r: (-r["weight"], r["name"]))
    return {"rows": rows, "covered": not uncovered, "uncovered": sorted(uncovered)}


def _single_names(weights: dict[str, float], labels: dict[str, dict], top: dict[str, list[dict]],
                  value_cad: float) -> dict:
    """Each company held directly or listed among a held ETF's top holdings: total = direct + Σ ETF weight x the
    company's weight in that ETF. Lower bounds (top holdings only)."""
    acc: dict[str, dict] = {}

    def row(t: str, name: str) -> dict:
        return acc.setdefault(t, {"ticker": t, "name": name, "direct": 0.0, "via": {}})

    for t, w in weights.items():
        lab = labels[t]
        if lab["asset_class"] == "equity":
            row(t, lab["name"])["direct"] += w
        elif lab["is_etf"]:
            for h in top.get(t, []):
                r = row(h["ticker"], labels[h["ticker"]]["name"] if h["ticker"] in labels else h["name"])
                r["via"][t] = r["via"].get(t, 0.0) + w * h["weight"]
    rows = []
    for r in acc.values():
        via = sorted([{"etf": e, "name": labels[e]["name"], "weight": _r(x)} for e, x in r["via"].items()],
                     key=lambda v: (-v["weight"], v["etf"]))
        tot = r["direct"] + sum(r["via"].values())
        rows.append({"ticker": r["ticker"], "name": r["name"], "weight": _r(tot), "value_cad": round(float(tot * value_cad), 0),
                     "direct": _r(r["direct"]), "via": via})
    rows.sort(key=lambda r: (-r["weight"], r["ticker"]))
    held_etfs = [t for t in weights if labels[t]["is_etf"] and labels[t].get("lookthrough_names", True)]
    return {"rows": rows, "partial": True,
            "etf_listed": {e: _r(sum(h["weight"] for h in top[e])) for e in held_etfs if e in top},
            "uncovered": sorted(e for e in held_etfs if e not in top)}


def build(weights: dict[str, float], labels: dict[str, dict], value_cad: float, *,
          sector_shares: dict[str, dict[str, float]] | None = None,
          country_shares: dict[str, dict[str, float]] | None = None,
          top_holdings: dict[str, list[dict]] | None = None,
          class_shares: dict[str, dict[str, float]] | None = None,
          sources: list[dict] | None = None) -> dict:
    """`weights`: {ticker: fraction}, summing to 1. `labels[ticker]`: name, asset_class, is_etf, sector, country,
    class, currency (the per-ticker buckets used when there is nothing to look through), and optional
    lookthrough_<sector|country|names>: False for an ETF that has nothing to look through on that dimension
    (a bond ETF has no GICS sector, gold has no country), so it is not reported as uncovered. `class_shares`:
    {etf: {class: share}} for ETFs whose underlying class differs from the wrapper (an equity ETF = stocks)."""
    held = set(weights)
    pick = (lambda d: {e: v for e, v in (d or {}).items() if e in held})
    dims = {"sector": _dimension(weights, labels, "sector", pick(sector_shares), value_cad),
            "country": _dimension(weights, labels, "country", pick(country_shares), value_cad),
            "class": _dimension(weights, labels, "class", pick(class_shares), value_cad),
            "currency": _dimension(weights, labels, "currency", {}, value_cad)}
    used = {e for e in held if labels[e]["is_etf"]}
    return {"value_cad": round(float(value_cad), 0), "dimensions": dims,
            "single_names": _single_names(weights, labels, pick(top_holdings), value_cad),
            "sources": [s for s in (sources or []) if s["etf"] in used]}
