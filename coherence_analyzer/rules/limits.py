"""LIMITS_TABLE + build_limits() + trace (SPEC §7.2).

Inputs: rule: fields and user-declared values ONLY (invariant 2).
Table values are team calibrations; see README -> Assumptions.
"""
from __future__ import annotations

from typing import Any

from . import registry

# level: (vol_lo, vol_hi, max_dd_pct, min_fixed_inc, max_single_stock, max_sector_risk_share,
#         max_crypto, max_foreign_ccy, floor_ratio)
LIMITS_TABLE: dict[str, dict] = {
    "low":           dict(vol_band=(0.02, 0.06), max_dd_pct=-8,  min_fixed_income=0.50, max_single_stock=0.05,
                          max_sector_risk_share=0.25, max_crypto=0.00, max_foreign_ccy=0.30, floor_ratio=0.95),
    "low_moderate":  dict(vol_band=(0.05, 0.09), max_dd_pct=-12, min_fixed_income=0.30, max_single_stock=0.08,
                          max_sector_risk_share=0.30, max_crypto=0.00, max_foreign_ccy=0.40, floor_ratio=0.90),
    "moderate":      dict(vol_band=(0.08, 0.12), max_dd_pct=-20, min_fixed_income=0.20, max_single_stock=0.10,
                          max_sector_risk_share=0.35, max_crypto=0.02, max_foreign_ccy=0.50, floor_ratio=0.85),
    "moderate_high": dict(vol_band=(0.11, 0.16), max_dd_pct=-28, min_fixed_income=0.10, max_single_stock=0.12,
                          max_sector_risk_share=0.40, max_crypto=0.05, max_foreign_ccy=0.60, floor_ratio=0.75),
    "high":          dict(vol_band=(0.14, 0.22), max_dd_pct=-40, min_fixed_income=0.00, max_single_stock=0.15,
                          max_sector_risk_share=0.45, max_crypto=0.10, max_foreign_ccy=0.70, floor_ratio=0.60),
}

MAX_EMPLOYER_SECTOR_WEIGHT = 0.15
NO_PORTFOLIO_VALUE = "portfolio.value_cad (not set)"
NO_PORTFOLIO_INPUTS = "portfolio.value_cad, portfolio.as_of (not set)"
LIMITS_NOTE = "Derived from the profile only. This is what the portfolio struct gets compared against."

# Trace path for user-approved parsed rules. The llm: field `values.parsed_notes` itself is never a
# limits input: only items the user explicitly approved (a user declaration) are promoted (§6.2 N3, §7.2).
APPROVED_PARSED_RULES = "values.parsed_notes[approved_by_user]"
PILLARS = ("environment", "social", "governance")
_TABLE = ("risk.effective",)

# Direct profile inputs of each limit. limits_trace expands these through the rule registry.
LIMIT_INPUTS: dict[str, tuple[str, ...]] = {
    "vol_band_annual": _TABLE,
    "floor_at_horizon": _TABLE,
    "max_drawdown_pct": _TABLE + ("risk.max_acceptable_drawdown_pct",),
    "min_fixed_income_weight": _TABLE,
    "max_single_stock_weight": _TABLE,
    "max_sector_risk_share": _TABLE,
    "max_crypto_weight": _TABLE,
    "max_foreign_currency_weight": _TABLE,
    "max_employer_sector_weight": (),
    "excluded_asset_types": _TABLE + ("preferences.asset_types_refused",),
    "excluded_activities": tuple(f"values.{p}.exclusions" for p in PILLARS) + (APPROVED_PARSED_RULES,),
    "max_fee_mer_pct": ("preferences.max_fee_mer_pct",),
    "max_theme_weight": (APPROVED_PARSED_RULES,),
}


def build_trace() -> dict[str, list[str]]:
    """{limit: [profile paths it depends on, fully expanded through the registry]} (SPEC §3.3 item 5)."""
    trace: dict[str, list[str]] = {}
    for name, inputs in LIMIT_INPUTS.items():
        paths: list[str] = []
        for p in inputs:
            for q in [p, *registry.expand(p)]:
                if q not in paths:
                    paths.append(q)
        trace[name] = paths
    return trace


def _val(node: Any) -> Any:
    return node["value"] if isinstance(node, dict) and "value" in node else node


def _rule_value(profile: dict, section: str, key: str) -> Any:
    node = profile[section][key]
    if isinstance(node, dict) and not str(node.get("by", "")).startswith("rule:"):
        raise ValueError(f"limits input {section}.{key} must be a rule: field, got by={node.get('by')!r}")
    return _val(node)


def _approved_parsed_rules(profile: dict, kind: str) -> list[dict]:
    node = profile.get("values", {}).get("parsed_notes")
    items = _val(node) if node is not None else []
    return [r for r in (items or [])
            if isinstance(r, dict) and r.get("approved_by_user") is True and r.get("rule_kind") == kind]


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    for x in items:
        if x not in out:
            out.append(x)
    return out


def _skip_trace(profile: dict, trace: dict[str, list[str]]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Add `<path> (skipped)` to the trace of every limit that reads a skipped answer (directly or through a rule:
    field). Returns (trace, {limit: skipped paths}); a limit without a skipped input keeps its trace unchanged."""
    out: dict[str, list[str]] = {}
    skipped: dict[str, list[str]] = {}
    for name, paths in trace.items():
        hit = registry.skipped_paths(profile, paths)
        skipped[name] = hit
        out[name] = list(paths) + [f"{p} (skipped)" for p in hit]
    return out, skipped


def build_limits(profile: dict, portfolio_value_cad: float | None) -> tuple[dict, dict[str, list[str]]]:
    """Return (limits, limits_trace). Only approved parsed rules are promoted.

    Reads `risk.effective` (rule:) and user-declared values only (invariant 2). Weights are fractions;
    drawdown and MER stay in percent, like the profile fields they come from.

    A limit whose input is skipped or null (`{value: null, by: user:skipped}`, docs/spec-factors.md §20.6) is
    None, never a default; `limits_trace` names the skipped input ("<path> (skipped)") and the checks that need
    the limit report `unknown`.

    No portfolio value yet (`portfolio_value_cad=None`, P5.NOPF): `floor_at_horizon` is None and its trace says
    "portfolio.value_cad (not set)"; when `risk.effective` is null too (it needs the portfolio value and date) every
    table limit is None with a trace entry of the same kind. Nothing is defaulted.
    """
    level = _rule_value(profile, "risk", "effective")
    row = LIMITS_TABLE[level] if level is not None else None
    risk, prefs, values = profile["risk"], profile.get("preferences", {}), profile.get("values", {})

    def table(key: str) -> Any:
        return row[key] if row is not None else None

    max_dd = table("max_dd_pct")
    user_dd_node = risk.get("max_acceptable_drawdown_pct")
    user_dd = _val(user_dd_node)
    if registry.is_skipped(user_dd_node):
        max_dd = None            # the user's own drawdown is unknown: the limit is not computable
    elif user_dd is not None and max_dd is not None:
        max_dd = max(max_dd, user_dd)   # the less negative one

    refused_node = prefs.get("asset_types_refused", [])
    excluded_types: list[str] | None
    if registry.is_skipped(refused_node) or row is None:
        excluded_types = None
    else:
        excluded_types = _dedupe(list(_val(refused_node) or []))
        if row["max_crypto"] == 0 and "crypto" not in excluded_types:
            excluded_types.append("crypto")

    activities: list[str] = []
    for pillar in PILLARS:
        activities.extend(values.get(pillar, {}).get("exclusions", []))
    activities.extend(r["params"]["tag"] for r in _approved_parsed_rules(profile, "exclude_activity")
                      if isinstance(r.get("params", {}).get("tag"), str))

    themes: dict[str, float] = {}
    themeless = 0
    for r in _approved_parsed_rules(profile, "max_theme_weight"):
        params = r.get("params")
        params = params if isinstance(params, dict) else {}
        theme = params.get("theme")
        if not isinstance(theme, str) or not theme:
            themeless += 1   # approved but names no theme: nothing to limit, skipped and traced (never a KeyError)
            continue
        pct = params.get("max_pct")
        if isinstance(pct, bool) or not isinstance(pct, (int, float)):
            continue   # approved but the number is still unanswered (or skipped): nothing to promote yet
        frac = pct / 100   # percent -> fraction, once
        themes[theme] = min(frac, themes.get(theme, frac))                # stricter wins

    limits: dict[str, Any] = {
        "_note": LIMITS_NOTE,
        "by": "rule:limits_table[risk.effective] + goals + preferences",
        "vol_band_annual": list(row["vol_band"]) if row is not None else None,
        "floor_at_horizon": (round(portfolio_value_cad * row["floor_ratio"], 2)
                             if row is not None and portfolio_value_cad is not None else None),
        "max_drawdown_pct": max_dd,
        "min_fixed_income_weight": table("min_fixed_income"),
        "max_single_stock_weight": table("max_single_stock"),
        "max_sector_risk_share": table("max_sector_risk_share"),
        "max_crypto_weight": table("max_crypto"),
        "max_foreign_currency_weight": table("max_foreign_ccy"),
        "max_employer_sector_weight": MAX_EMPLOYER_SECTOR_WEIGHT,
        "excluded_asset_types": excluded_types,
        "excluded_activities": _dedupe(activities),
    }
    if "max_fee_mer_pct" in prefs:
        limits["max_fee_mer_pct"] = prefs["max_fee_mer_pct"]
    if themes:
        limits["max_theme_weight"] = {k: themes[k] for k in sorted(themes)}
    trace, _ = _skip_trace(profile, build_trace())
    if portfolio_value_cad is None:
        trace["floor_at_horizon"] = [*trace["floor_at_horizon"], NO_PORTFOLIO_VALUE]
        if row is None:       # risk.effective needs the portfolio value and date: the table limits wait for them
            for name, inputs in LIMIT_INPUTS.items():
                if name != "floor_at_horizon" and _TABLE[0] in inputs:
                    trace[name] = [*trace[name], NO_PORTFOLIO_INPUTS]
    if themeless:
        trace["max_theme_weight"] = [*trace.get("max_theme_weight", []),
                                     f"values.parsed_notes (skipped: {themeless} approved max_theme_weight rule(s) "
                                     "without a theme)"]
    return limits, trace
