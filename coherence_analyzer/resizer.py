"""What fits within the user's own limits (SPEC §8). Facts about the profile, not advice. P0 step 5.

Runs SPEC §8 as written (docs/spec-decisions.md D2: trust the engine): for each funding mode a cheap scan
(H1-H6, H11-H13) in `config.RESIZER_STEP_PP` steps, then the engine checks (H7-H10) with n=1000 and a
binary search downward, then confirmation with the full n=5000. The result is a grid value (multiple of the
step). Pure and deterministic: the engine is seeded, nothing reads the clock.
"""
from __future__ import annotations

import json
from . import config, engine, intent as intent_mod
from .coherence import hard

MAX_OPTIONS = 3
FUNDING_BINDING = "FUNDING"        # the funding source cannot cover the weight (no hard check involved)
_EPS = 1e-9
_METRIC_KEYS = ("vol", "mdd", "p5_T", "p50_T", "p95_T", "width", "loss_prob")


def _key(funding: dict) -> str:
    return json.dumps(funding, sort_keys=True)


def _modes(user_funding: dict) -> list[dict]:
    """[user's funding, pro_rata_all, pro_rata_class=etf_equity, pro_rata_class=equity], de-duplicated."""
    cands = [dict(user_funding), {"kind": "pro_rata_all"},
             {"kind": "pro_rata_class", "asset_class": "etf_equity"},
             {"kind": "pro_rata_class", "asset_class": "equity"}]
    seen: set[str] = set()
    out = []
    for f in cands:
        if _key(f) not in seen:
            seen.add(_key(f))
            out.append(f)
    return out


def _grid(w0: float, target: float, step: float) -> list[float]:
    """Current weight, then the multiples of `step` above it, then the target itself."""
    pts = [w0]
    k = int(w0 / step + _EPS) + 1
    while k * step < target - _EPS:
        pts.append(k * step)
        k += 1
    if target > w0 + _EPS:
        pts.append(target)
    return pts


def _violations(checks: list, ids: tuple[str, ...] | None = None) -> list[str]:
    out: list[str] = []
    for c in checks:
        if c["status"] == "violation" and (ids is None or c["id"] in ids) and c["id"] not in out:
            out.append(c["id"])
    return out


class _Evaluator:
    """Memoised cheap / engine evaluation of one (funding mode, weight) point."""

    def __init__(self, portfolio: dict, intent: dict, profile: dict, market, meta: dict, funding: dict):
        self.pf, self.intent, self.profile, self.market, self.meta = portfolio, intent, profile, market, meta
        self.funding = funding
        self._cheap: dict[float, tuple[list[str], dict | None, float]] = {}
        self._eng: dict[tuple[float, int], tuple[list[str], dict]] = {}

    def weights(self, w: float) -> tuple[dict[str, float], float]:
        it = dict(self.intent, target_weight_pct=w, funding=self.funding)
        return intent_mod.apply(self.pf["weights_pct"], it, self.pf["value_cad"])

    def cheap(self, w: float) -> list[str]:
        if w not in self._cheap:
            try:
                weights, value = self.weights(w)
            except ValueError:
                self._cheap[w] = ([FUNDING_BINDING], None, 0.0)
            else:
                frac = {t: v / 100 for t, v in weights.items() if v > 0}
                ids = _violations(hard.run_cheap(frac, self.profile, self.market.assets, self.meta))
                self._cheap[w] = (ids, weights, value)
        return self._cheap[w][0]

    def engine(self, w: float, n: int) -> tuple[list[str], dict]:
        if (w, n) not in self._eng:
            self.cheap(w)
            _, weights, value = self._cheap[w]
            m = engine.analyse(self.market, {t: v for t, v in weights.items() if v > 0},
                               int(self.pf["horizon_months"]), value, n=n)
            self._eng[(w, n)] = (_violations(hard.run_engine(m, self.profile), hard.ENGINE), m)
        return self._eng[(w, n)]


def _solve_mode(ev: _Evaluator, grid: list[float]) -> dict:
    """Largest grid weight that passes every check; returns {idx, binding, metrics}."""
    # 1. cheap scan: stop at the first failing step
    cheap_idx, stop_ids = -1, []
    for i, w in enumerate(grid):
        ids = ev.cheap(w)
        if ids:
            stop_ids = ids
            break
        cheap_idx = i
    if cheap_idx < 0:
        return {"idx": 0, "binding": stop_ids, "metrics": None, "fits": False}

    # 2. engine checks at the last cheap-passing weight with n=1000, binary search downward if they fail
    def ok_cheap_n(i: int) -> bool:
        return not ev.engine(grid[i], config.ENGINE_N_CHEAP)[0]

    idx = cheap_idx
    if not ok_cheap_n(idx):
        lo, hi = -1, cheap_idx - 1          # largest passing index in [lo, hi]; -1 = none passes
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if ok_cheap_n(mid):
                lo = mid
            else:
                hi = mid - 1
        idx = max(lo, 0)

    # 3. confirm with the full n: step down while it fails, step up while the next grid point still passes
    while True:
        ids, m = ev.engine(grid[idx], config.ENGINE_N)
        if ids:
            if idx == 0:
                return {"idx": 0, "binding": ids, "metrics": None, "fits": False}
            idx -= 1
            continue
        if idx == len(grid) - 1:
            binding: list[str] = []                 # the whole requested weight fits
        elif idx == cheap_idx:
            binding = stop_ids
        else:
            nids, _ = ev.engine(grid[idx + 1], config.ENGINE_N)
            if not nids:
                idx += 1
                continue
            binding = nids
        return {"idx": idx, "binding": binding, "metrics": {k: m[k] for k in _METRIC_KEYS}, "fits": True}


def resize(portfolio: dict, intent: dict, profile: dict, market, meta: dict) -> dict:
    """Return {"options": [{funding, max_weight_pct, binding_check, metrics}, ...<=3]}
    or {"none_fit": True, "binding_checks": [...]}.

    `portfolio` needs `weights_pct`, `value_cad` and `horizon_months` (SPEC §3.5 fixture horizon; passed
    explicitly because §8 gives the resizer no horizon argument). `binding_check` is None when the whole
    requested weight fits. Inputs are never mutated.
    """
    if "horizon_months" not in portfolio:
        raise ValueError("portfolio['horizon_months'] is required (SPEC §3.5)")
    ticker, target = intent["ticker"], float(intent["target_weight_pct"])
    w0 = float(portfolio["weights_pct"].get(ticker, 0.0))
    grid = _grid(w0, target, config.RESIZER_STEP_PP)

    modes = _modes(intent["funding"])
    solved: list[tuple[int, dict, dict]] = []
    for order, funding in enumerate(modes):
        ev = _Evaluator(portfolio, intent, profile, market, meta, funding)
        solved.append((order, funding, _solve_mode(ev, grid)))

    fitting = [(o, f, s) for o, f, s in solved if s["idx"] > 0]
    if not fitting:
        ids = sorted({b for _, _, s in solved for b in s["binding"]})
        return {"none_fit": True, "binding_checks": ids}

    fitting.sort(key=lambda x: (-grid[x[2]["idx"]], x[0]))
    chosen = fitting[:MAX_OPTIONS]
    if all(o != 0 for o, _, _ in chosen) and any(o == 0 for o, _, _ in fitting):
        chosen = chosen[:MAX_OPTIONS - 1] + [next(x for x in fitting if x[0] == 0)]   # keep the user's own funding
    return {"options": [{"funding": dict(f), "max_weight_pct": float(grid[s["idx"]]),
                         "binding_check": s["binding"][0] if s["binding"] else None,
                         "binding_checks": list(s["binding"]), "metrics": dict(s["metrics"])}
                        for _, f, s in chosen]}
