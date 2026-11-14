"""Apply a confirmed intent to a portfolio (SPEC §3.6). P0 step 5.

Funding kinds: specific | pro_rata_all | pro_rata_class | cash_contribution.

Pure, deterministic, never mutates its inputs. Weights are percent in and out (the edge of the code, SPEC
conventions); the result always sums to 100 and is never negative. Cases the spec leaves open raise
``ValueError`` instead of producing an invalid portfolio (see README "Assumptions", intent.apply).
"""
from __future__ import annotations

import math
from functools import lru_cache

from . import data

FUNDING_KINDS = ("specific", "pro_rata_all", "pro_rata_class", "cash_contribution")

_TOL = 1e-9        # pp: below this, a weight difference is float noise
_ZERO = 1e-12      # pp: weights this small are dropped


@lru_cache(maxsize=1)
def _assets():
    return data.load_assets()


def _asset_class(ticker: str) -> str:
    try:
        return str(_assets().loc[ticker, "asset_class"])
    except KeyError as e:
        raise ValueError(f"ticker not in the asset universe: {ticker}") from e


def _clean(weights: dict[str, float]) -> dict[str, float]:
    """Drop float-noise zeros and clamp tiny negatives (insertion order is kept)."""
    return {t: (0.0 if abs(v) < _ZERO else v) for t, v in weights.items() if abs(v) >= _ZERO}


def _scale_pool(new: dict[str, float], pool: list[str], delta: float) -> None:
    """Shrink `pool` tickers pro rata so that they release exactly `delta` pp."""
    total = math.fsum(new[t] for t in pool)
    if not pool or total <= 0:
        raise ValueError("nothing to fund from: the funding pool is empty")
    if delta > total + _TOL:
        raise ValueError(f"funding pool holds {total:.4f}% but {delta:.4f}% is needed")
    factor = max(total - delta, 0.0) / total
    for t in pool:
        new[t] = new[t] * factor


def apply(weights_pct: dict[str, float], intent: dict, value_cad: float) -> tuple[dict[str, float], float]:
    """Return (new weights_pct summing to 100, new value_cad).

    Only ``cash_contribution`` changes the portfolio value. The target ticker lands exactly on
    ``target_weight_pct`` except for an under-funded cash contribution (it then gets all the cash).
    Raises ValueError if the intent cannot be applied without negative weights.
    """
    if intent.get("action", "buy") != "buy":
        raise ValueError(f"unsupported action: {intent.get('action')!r}")
    ticker = intent["ticker"]
    target = float(intent["target_weight_pct"])
    funding = intent["funding"]
    kind = funding.get("kind")
    if kind not in FUNDING_KINDS:
        raise ValueError(f"unknown funding kind: {kind!r}")
    if not 0 <= target <= 100:
        raise ValueError(f"target weight out of range: {target}")

    new = {t: float(v) for t, v in weights_pct.items()}
    cur = new.get(ticker, 0.0)
    delta = target - cur
    if delta < -_TOL:
        raise ValueError(f"target {target}% is below the current weight {cur}% of {ticker}: not a buy")
    if abs(delta) <= _TOL and kind != "cash_contribution":
        return _clean(new), value_cad
    delta = max(delta, 0.0)

    if kind == "specific":
        src = funding["ticker"]
        if src == ticker:
            raise ValueError("a ticker cannot fund itself")
        have = new.get(src, 0.0)
        if delta > have + _TOL:
            raise ValueError(f"{src} holds {have}% but {delta}% is needed")
        new[src] = max(have - delta, 0.0)
        new[ticker] = target
        return _clean(new), value_cad

    if kind == "pro_rata_all":
        if cur >= 100 - _TOL:
            raise ValueError("nothing to fund from")
        factor = (100 - target) / (100 - cur)
        out = {t: (v if t == ticker else v * factor) for t, v in new.items()}
        out[ticker] = target
        return _clean(out), value_cad

    if kind == "pro_rata_class":
        cls = funding["asset_class"]
        pool = [t for t in new if t != ticker and new[t] > 0 and _asset_class(t) == cls]
        _scale_pool(new, pool, delta)
        new[ticker] = target
        return _clean(new), value_cad

    # cash_contribution: nothing is sold; existing holdings keep their CAD value.
    amount = float(funding["amount_cad"])
    if amount < 0:
        raise ValueError("cash contribution must be >= 0")
    new_value = value_cad + amount
    cad = {t: v * value_cad / 100 for t, v in new.items()}
    need = max(target / 100 * new_value - cad.get(ticker, 0.0), 0.0)
    buy = min(amount, need)
    excess = amount - buy                    # cash beyond the target: spread pro rata over the other holdings
    others = [t for t in cad if t != ticker and cad[t] > 0]
    base = math.fsum(cad[t] for t in others)
    if excess > 1e-9 and base <= 0:
        raise ValueError("cash exceeds the target and there is no other holding to place it in")
    for t in others:
        cad[t] += excess * cad[t] / base if excess > 1e-9 else 0.0
    cad[ticker] = cad.get(ticker, 0.0) + buy
    out = {t: v / new_value * 100 for t, v in cad.items()}
    return _clean(out), new_value
