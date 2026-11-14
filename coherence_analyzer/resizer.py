"""Headroom of the user's own trade before it crosses one of their limits (SPEC §8, docs/spec-decisions.md D10).

A measurement, not a suggestion: only the funding the user gave is evaluated (D10 point 2). Nothing here picks a
funding source, ticker or size, compares variants, or phrases the result as something to do.

Runs SPEC §8 as written (docs/spec-decisions.md D2: trust the engine): a cheap scan
(H1-H6, H11-H13) in `config.RESIZER_STEP_PP` steps, then the engine checks (H7-H10) with n=1000 and a
binary search downward, then confirmation with the full n=5000. The result is a grid value (multiple of the
step). Pure and deterministic: the engine is seeded, nothing reads the clock.
"""
from __future__ import annotations

from . import config, engine, intent as intent_mod
from .coherence import hard

FUNDING_BINDING = "FUNDING"        # the funding source cannot cover the weight (no hard check involved)
_EPS = 1e-9
_METRIC_KEYS = ("vol", "mdd", "p5_T", "p50_T", "p95_T", "width", "loss_prob")


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
    """Memoised cheap / engine evaluation of one weight point, with the user's funding."""

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
    """Return the headroom of the user's own trade (SPEC §8 step 4), `{funding, max_weight_pct, binding_check,
    metrics}`, or `{"none_fit": True, "binding_checks": [...]}` when even the first step above the current
    weight crosses a limit.

    `funding` is the intent's funding, unchanged. `portfolio` needs `weights_pct`, `value_cad` and `horizon_months`
    (SPEC §3.5 fixture horizon; passed explicitly because §8 gives the resizer no horizon argument).
    `binding_check` is the first limit crossed just above `max_weight_pct` (None when the whole requested weight
    stays within the limits, FUNDING when the funding source cannot cover more). Inputs are never mutated.
    """
    if "horizon_months" not in portfolio:
        raise ValueError("portfolio['horizon_months'] is required (SPEC §3.5)")
    ticker, target = intent["ticker"], float(intent["target_weight_pct"])
    w0 = float(portfolio["weights_pct"].get(ticker, 0.0))
    grid = _grid(w0, target, config.RESIZER_STEP_PP)
    funding = dict(intent["funding"])
    s = _solve_mode(_Evaluator(portfolio, intent, profile, market, meta, funding), grid)
    if s["idx"] == 0:
        return {"none_fit": True, "binding_checks": sorted(set(s["binding"]))}
    return {"funding": funding, "max_weight_pct": float(grid[s["idx"]]),
            "binding_check": s["binding"][0] if s["binding"] else None, "metrics": dict(s["metrics"])}


# ---------------------------------------------------------------- template text (SPEC §8 presentation)

_CLASS = {"fr-CA": {"etf_equity": "vos FNB d'actions", "equity": "vos actions", "etf_bond": "vos FNB d'obligations"},
          "en-CA": {"etf_equity": "your equity ETFs", "equity": "your stocks", "etf_bond": "your bond ETFs"}}


def _funding_label(funding: dict, locale: str) -> str:
    fr = locale == "fr-CA"
    kind = funding.get("kind")
    if kind == "specific":
        return str(funding.get("ticker"))
    if kind == "pro_rata_all":
        return "au prorata de tout le portefeuille" if fr else "pro rata across the whole portfolio"
    if kind == "pro_rata_class":
        cls = funding.get("asset_class")
        name = _CLASS["fr-CA" if fr else "en-CA"].get(cls, cls)
        return f"au prorata de {name}" if fr else f"pro rata from {name}"
    if kind == "cash_contribution":
        return "nouvel apport en espèces" if fr else "new cash contribution"
    return str(kind)


def _pct(x: float, locale: str) -> str:
    s = f"{x:.1f}"
    return (s.replace(".", ",") + " %") if locale == "fr-CA" else (s + " %")


def headroom_text(headroom: dict | None, funding: dict, target_pct: float, locale: str = "fr-CA") -> str | None:
    """One factual sentence about the user's own trade (D10: describes, never prescribes). None when no headroom.

    `funding` is the user's funding (the `none_fit` record does not carry it); `target_pct` is the weight asked for.
    """
    if not headroom:
        return None
    fr = locale == "fr-CA"
    src = _funding_label(funding, locale)
    if headroom.get("none_fit"):
        ids = ", ".join(headroom["binding_checks"])
        return (f"Avec le financement indiqué ({src}), la transaction franchit votre limite {ids} "
                f"dès le premier palier." if fr else
                f"With the funding you gave ({src}), this trade crosses your limit {ids} from the first step.")
    w, b = _pct(headroom["max_weight_pct"], locale), headroom["binding_check"]
    if b is None:
        return (f"Avec le financement indiqué ({src}), la transaction reste dans vos limites jusqu'à "
                f"{_pct(target_pct, locale)}." if fr else
                f"With the funding you gave ({src}), this trade stays within your limits up to "
                f"{_pct(target_pct, locale)}.")
    if b == FUNDING_BINDING:
        return (f"Le financement indiqué ({src}) ne couvre pas la transaction au-delà de {w}." if fr else
                f"The funding you gave ({src}) does not cover this trade above {w}.")
    return (f"Avec le financement indiqué ({src}), la transaction franchit votre limite {b} "
            f"au-delà de {w}." if fr else
            f"With the funding you gave ({src}), this trade crosses your limit {b} above {w}.")
