"""Hard checks H1-H14 + G1 (SPEC §7.3). Only these may return `violation`.

P0 step 4 implements H1-H10 and H14. H11-H13 read the hand-built meta files (SPEC §3.2, P1.2): a missing file or
row gives `unknown`, never a guess. G1 (P2.14, `g1`) is separate from `run_all`: it is always `info`, has no limit,
and the pipeline reports it in its own `goals` block (docs/spec-factors.md §20.8, tests/engine/test_goals.py).

Pure functions. Weights are fractions (0-1). Comparisons tolerate float noise from percent/100
conversion (EPS), so a weight exactly at a limit never flips the status.
"""
from __future__ import annotations

from typing import Literal, TypedDict

from ..data import ASSET_CLASS_MAP, etf_sector_shares

Status = Literal["pass", "violation", "info", "unknown"]


class Check(TypedDict, total=False):
    id: str
    kind: Literal["hard", "soft"]
    status: str
    subject: str | None
    value: float | None
    limit: float | None
    unit: str
    derived_from: list[str]
    source: str
    confidence: float
    message_key: str
    date: str          # H14: month of the historical drawdown trough; G1: the goal's date
    amount: float      # G1 only: the goal amount (CAD)
    month: int         # G1 only: months from portfolio.as_of
    assumptions: list[str]   # G1 only: assumption labels (invariant 9)


CHEAP = ("H1", "H2", "H3", "H4", "H5", "H6", "H11", "H12", "H13")  # no engine needed (resizer)
ENGINE = ("H7", "H8", "H9", "H10")

EPS = 1e-9
BROAD = "Broad market"
SELL_REACTIONS = ("sell_some", "sell_all")
H14_DRAWDOWN = -0.20
_ROUND = 10   # strip float noise from summed fractions in reported values


def _gt(a: float, b: float) -> bool:
    return a > b + EPS


def _lt(a: float, b: float) -> bool:
    return a < b - EPS


def _r(x: float) -> float:
    return round(float(x), _ROUND)


def _derived(profile: dict, limit_name: str, *extra: str) -> list[str]:
    out = [f"limits.{limit_name}"]
    for p in [*profile.get("limits_trace", {}).get(limit_name, []), *extra]:
        if p not in out:
            out.append(p)
    return out


def _check(cid: str, status: str, subject: str | None, value: float | None, limit: float | None,
           unit: str, derived_from: list[str], source: str, message_key: str) -> Check:
    return Check(id=cid, kind="hard", status=status, subject=subject, value=value, limit=limit, unit=unit,
                 derived_from=derived_from, source=source, confidence=1.0, message_key=message_key)


def _val(node):
    return node["value"] if isinstance(node, dict) and "value" in node else node


def _held(weights: dict[str, float]) -> list[str]:
    return sorted(t for t, w in weights.items() if w > 0)


# ---------------------------------------------------------------- cheap checks

def _h1(weights, profile, assets) -> list[Check]:
    lim = profile["limits"]
    excluded = set(lim["excluded_asset_types"])
    der = _derived(profile, "excluded_asset_types")
    out: list[Check] = []
    for t in _held(weights):
        types = ASSET_CLASS_MAP[assets.loc[t, "asset_class"]][0]
        if excluded.intersection(types):
            out.append(_check("H1", "violation", t, _r(weights[t]), None, "weight", der,
                              "rule:excluded_asset_types", "excluded_asset_type_held"))
    if not out:
        out.append(_check("H1", "pass", None, 0.0, None, "weight", der,
                          "rule:excluded_asset_types", "excluded_asset_types_ok"))
    return out


def _sum_class(weights, assets, cls: str) -> float:
    return sum(weights[t] for t in _held(weights) if assets.loc[t, "asset_class"] == cls)


def _h2(weights, profile, assets) -> list[Check]:
    lim = profile["limits"]["max_crypto_weight"]
    v = _sum_class(weights, assets, "crypto")
    status = "violation" if _gt(v, lim) else "pass"
    return [_check("H2", status, None, _r(v), lim, "weight", _derived(profile, "max_crypto_weight"),
                   "rule:max_crypto", "crypto_over" if status == "violation" else "crypto_ok")]


def _h3(weights, profile, assets) -> list[Check]:
    lim = profile["limits"]["max_single_stock_weight"]
    der = _derived(profile, "max_single_stock_weight")
    out: list[Check] = []
    for t in _held(weights):
        if assets.loc[t, "asset_class"] != "equity":
            continue
        v = weights[t]
        status = "violation" if _gt(v, lim) else "pass"
        out.append(_check("H3", status, t, _r(v), lim, "weight", der, "rule:max_single_stock",
                          "single_stock_over" if status == "violation" else "single_stock_ok"))
    if not out:
        out.append(_check("H3", "pass", None, 0.0, lim, "weight", der, "rule:max_single_stock", "single_stock_ok"))
    return out


def _h4(weights, profile, assets) -> list[Check]:
    lim = profile["limits"]["min_fixed_income_weight"]
    v = sum(weights[t] for t in _held(weights) if ASSET_CLASS_MAP[assets.loc[t, "asset_class"]][1])
    status = "violation" if _lt(v, lim) else "pass"
    return [_check("H4", status, None, _r(v), lim, "weight", _derived(profile, "min_fixed_income_weight"),
                   "rule:min_fixed_income", "fixed_income_under" if status == "violation" else "fixed_income_ok")]


def _h5(weights, profile, assets) -> list[Check]:
    lim = profile["limits"]["max_foreign_currency_weight"]
    v = sum(weights[t] for t in _held(weights) if assets.loc[t, "currency"] != "CAD")
    status = "violation" if _gt(v, lim) else "pass"
    return [_check("H5", status, None, _r(v), lim, "weight", _derived(profile, "max_foreign_currency_weight"),
                   "rule:max_foreign_currency",
                   "foreign_currency_over" if status == "violation" else "foreign_currency_ok")]


def _h6(weights, profile, assets, meta) -> list[Check]:
    lim = profile["limits"]["max_employer_sector_weight"]
    der = _derived(profile, "max_employer_sector_weight", "identity.employer_sector")
    sector = _val(profile.get("identity", {}).get("employer_sector"))
    if not sector:
        return [_check("H6", "unknown", None, None, lim, "weight", der,
                       "rule:max_employer_sector", "employer_sector_not_declared")]
    held = _held(weights)
    direct = sum(weights[t] for t in held
                 if assets.loc[t, "asset_class"] == "equity" and assets.loc[t, "sector"] == sector)
    # ETF part (SPEC §7.3): Σ ETF weight x its share of the employer sector, for the equity ETFs covered by
    # etf_sector_weights; the weight of an equity ETF with no rows cannot be looked through -> unknown.
    shares = etf_sector_shares((meta or {}).get("etf_sector_weights"))
    etfs = [t for t in held if assets.loc[t, "asset_class"] == "etf_equity"]
    via_etf = sum(weights[t] * shares[t].get(sector, 0.0) for t in etfs if t in shares)
    uncovered = [t for t in etfs if t not in shares]
    total = direct + via_etf
    status = "violation" if _gt(total, lim) else "pass"
    out = [_check("H6", status, sector, _r(total), lim, "weight", der, "rule:max_employer_sector",
                  "employer_sector_over" if status == "violation" else "employer_sector_ok")]
    if uncovered:
        out.append(_check("H6", "unknown", sector, _r(sum(weights[t] for t in uncovered)), lim, "weight", der,
                          "rule:max_employer_sector", "employer_sector_etf_part_unknown"))
    return out


ETF_CLASSES = ("etf_equity", "etf_bond", "etf_commodity")


def _asset_rows(meta: dict) -> dict[str, dict] | None:
    """asset_meta as {ticker: row}; None when the file is not built (SPEC §3.2)."""
    df = (meta or {}).get("asset_meta")
    if df is None:
        return None
    return {str(r["ticker"]): r for r in df.to_dict("records")}


def _themes(cell) -> list[str]:
    if isinstance(cell, str):
        return [x.strip() for x in cell.split(";") if x.strip()]
    return [str(x) for x in (cell if cell is not None else [])]


def _h11(weights, profile, assets, meta) -> list[Check]:
    """Excluded activities. Violation only from a sourced, human-written tag row; otherwise `unknown`."""
    excluded = sorted(set(profile["limits"].get("excluded_activities") or []))
    der = _derived(profile, "excluded_activities")
    src = "rule:excluded_activities"
    if not excluded:
        return [_check("H11", "pass", None, 0.0, None, "weight", der, src, "excluded_activities_ok")]
    tags = (meta or {}).get("activity_tags")
    rows: dict[tuple[str, str], list[str]] = {}          # (ticker, tag) -> source urls, in file order
    if tags is not None:
        for r in tags.to_dict("records"):
            url = str(r.get("source_url") or "").strip()
            if r["tag"] in excluded and url:
                rows.setdefault((str(r["ticker"]), str(r["tag"])), []).append(url)
    assets_meta = _asset_rows(meta)
    out: list[Check] = []
    for t in _held(weights):
        cls = assets.loc[t, "asset_class"]
        if cls == "equity":
            hits = [tag for tag in excluded if (t, tag) in rows]
            for tag in hits:
                c = _check("H11", "violation", t, tag, None, "weight", der, src, "excluded_activity_held")
                c["weight"] = _r(weights[t])
                c["evidence"] = list(rows[(t, tag)])           # source_url(s) of the human-written tag row(s)
                out.append(c)
            if not hits:
                out.append(_check("H11", "unknown", t, _r(weights[t]), None, "weight", der, src,
                                  "excluded_activity_no_tag_rows"))
        elif cls in ETF_CLASSES:
            # No constituent tag data exists in v1 (§3.2), so an ETF is never verified, with or without look-through.
            row = (assets_meta or {}).get(t)
            look = bool(row["has_lookthrough"]) if row is not None else False
            out.append(_check("H11", "unknown", t, _r(weights[t]), None, "weight", der, src,
                              "excluded_activity_etf_constituents_unknown" if look
                              else "excluded_activity_etf_no_lookthrough"))
    return out


def _h12(weights, profile, assets, meta) -> list[Check]:
    """Fees: each held ETF's MER (percent points) against limits.max_fee_mer_pct. Missing MER -> unknown."""
    lim = profile["limits"].get("max_fee_mer_pct")
    if lim is None:
        return []
    der = _derived(profile, "max_fee_mer_pct")
    rows = _asset_rows(meta)
    out: list[Check] = []
    for t in _held(weights):
        if assets.loc[t, "asset_class"] not in ETF_CLASSES:
            continue
        mer = (rows or {}).get(t, {}).get("mer_pct")
        mer = None if mer is None or mer != mer or mer == "" else float(mer)
        if mer is None:
            out.append(_check("H12", "unknown", t, None, lim, "pct", der, "rule:max_fee", "fee_unknown"))
            continue
        status = "violation" if _gt(mer, lim) else "pass"
        out.append(_check("H12", status, t, _r(mer), lim, "pct", der, "rule:max_fee",
                          "fee_over" if status == "violation" else "fee_ok"))
    return out


def _h13(weights, profile, assets, meta) -> list[Check]:
    """Theme weight: Σ weight of held assets whose asset_meta themes contain the theme (an ETF counts fully)."""
    limits = profile["limits"].get("max_theme_weight") or {}
    if not limits:
        return []
    der = _derived(profile, "max_theme_weight")
    rows = _asset_rows(meta)
    out: list[Check] = []
    for theme in sorted(limits):
        lim = limits[theme]
        if rows is None:
            out.append(_check("H13", "unknown", theme, None, lim, "weight", der, "rule:max_theme_weight",
                              "theme_weight_unknown"))
            continue
        v = sum(weights[t] for t in _held(weights) if theme in _themes(rows.get(t, {}).get("themes")))
        status = "violation" if _gt(v, lim) else "pass"
        out.append(_check("H13", status, theme, _r(v), lim, "weight", der, "rule:max_theme_weight",
                          "theme_weight_over" if status == "violation" else "theme_weight_ok"))
    return out


def run_cheap(weights: dict[str, float], profile: dict, assets, meta: dict) -> list[Check]:
    """H1-H6 and H11-H13 (CHEAP order) on fraction weights. H11-H13 read the meta files; a missing file -> unknown."""
    return (_h1(weights, profile, assets) + _h2(weights, profile, assets) + _h3(weights, profile, assets)
            + _h4(weights, profile, assets) + _h5(weights, profile, assets) + _h6(weights, profile, assets, meta)
            + _h11(weights, profile, assets, meta) + _h12(weights, profile, assets, meta)
            + _h13(weights, profile, assets, meta))


# ---------------------------------------------------------------- engine checks

def _h7(m, profile) -> list[Check]:
    lo, hi = profile["limits"]["vol_band_annual"]
    v = m["vol"]
    if _gt(v, hi):
        status, limit, key = "violation", hi, "vol_above_band"
    elif _lt(v, lo):
        status, limit, key = "info", lo, "vol_below_band"
    else:
        status, limit, key = "pass", hi, "vol_in_band"
    return [_check("H7", status, None, v, limit, "annual_vol", _derived(profile, "vol_band_annual"),
                   "rule:vol_band", key)]


def _h8(m, profile) -> list[Check]:
    lim = profile["limits"]["max_drawdown_pct"] / 100   # limits keep drawdown in percent (§7.2)
    v = m["mdd"]
    status = "violation" if _lt(v, lim) else "pass"
    return [_check("H8", status, None, v, _r(lim), "drawdown", _derived(profile, "max_drawdown_pct"),
                   "rule:max_drawdown", "drawdown_over" if status == "violation" else "drawdown_ok")]


def _h9(m, profile) -> list[Check]:
    lim = profile["limits"]["floor_at_horizon"]
    v = m["p5_T"]
    status = "violation" if v < lim else "pass"
    return [_check("H9", status, None, v, lim, "cad", _derived(profile, "floor_at_horizon"),
                   "rule:floor_at_horizon", "floor_breached" if status == "violation" else "floor_ok")]


def _h10(m, profile) -> list[Check]:
    lim = profile["limits"]["max_sector_risk_share"]
    der = _derived(profile, "max_sector_risk_share")
    groups = m["groups"]["sector"]
    broad = [g for g in groups if g["name"].startswith(BROAD)]
    rest = [g for g in groups if not g["name"].startswith(BROAD)] if not m.get("lookthrough") else list(groups)
    out: list[Check] = []
    if rest:
        top = max(rest, key=lambda g: (g["risk"], g["name"]))
        status = "violation" if _gt(top["risk"], lim) else "pass"
        out.append(_check("H10", status, top["name"], top["risk"], lim, "risk_share", der, "rule:max_sector_risk",
                          "sector_risk_over" if status == "violation" else "sector_risk_ok"))
    if broad and not m.get("lookthrough"):
        out.append(_check("H10", "unknown", BROAD, _r(sum(g["risk"] for g in broad)), lim, "risk_share", der,
                          "rule:max_sector_risk", "sector_risk_broad_market_unknown"))
    return out


def _h14(m, profile) -> list[Check]:
    reaction = _val(profile.get("risk", {}).get("reaction_to_20pct_drop"))
    triggered = reaction in SELL_REACTIONS and m["mdd"] < H14_DRAWDOWN
    c = _check("H14", "info" if triggered else "pass", None, m["mdd"], H14_DRAWDOWN, "drawdown",
               ["risk.reaction_to_20pct_drop"], "rule:stated_reaction_vs_history",
               "stated_reaction_vs_history" if triggered else "stated_reaction_consistent")
    if triggered:
        c["date"] = m["mdd_date"]
    return [c]


def run_engine(metrics: dict, profile: dict) -> list[Check]:
    """H7-H10, H14 on engine.analyse output (G1: P2, needs goal_probs)."""
    return (_h7(metrics, profile) + _h8(metrics, profile) + _h9(metrics, profile)
            + _h10(metrics, profile) + _h14(metrics, profile))


def run_all(weights: dict[str, float], metrics: dict, profile: dict, assets, meta: dict) -> list[Check]:
    return run_cheap(weights, profile, assets, meta) + run_engine(metrics, profile)


# ---------------------------------------------------------------- G1 (SPEC §7.3): goal probabilities

G1_LABELS = (
    "Probability = share of simulated bootstrap paths at or above the goal amount at the goal month "
    "(historical sample, not a forecast).",
    "The monthly contribution is the amount the user declared in situation.monthly_contribution, added at the end "
    "of each month; the simulation assumes it is paid every month.",
    "Planned withdrawals (situation.planned_withdrawals) are not deducted in the simulation.",
)


def g1(m: dict, profile: dict) -> list[Check]:
    """G1: one `info` check per declared goal (SPEC §7.3), in the profile's order. G1 compares to no limit, never
    reads `limits` and never changes the verdict. `m` = engine.analyse output computed with the user-declared
    monthly contribution and goals. A goal outside the simulated horizon has value None (no number is invented)."""
    goals = list((profile.get("objectives") or {}).get("goals") or [])
    probs = list(m.get("goal_probs") or [])
    labels = [*(m.get("assumptions") or []), *G1_LABELS]
    out: list[Check] = []
    for g in goals:
        hit = next((p for p in probs if (p["name"], p["date"], p["amount"]) == (g["name"], g["date"], g["amount"])),
                   None)
        if hit is not None:
            probs.remove(hit)
        c = _check("G1", "info", g["name"], hit["prob"] if hit else None, None, "probability",
                   ["objectives.goals", "situation.monthly_contribution"], "rule:goal_probability",
                   "goal_probability" if hit else "goal_outside_horizon")
        c["date"] = g["date"]
        c["amount"] = g["amount"]
        if hit:
            c["month"] = hit["month"]
        c["assumptions"] = list(labels)
        out.append(c)
    return out
