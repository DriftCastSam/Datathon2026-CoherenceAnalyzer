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


def build_limits(profile: dict, portfolio_value_cad: float) -> tuple[dict, dict[str, list[str]]]:
    """Return (limits, limits_trace). Only approved parsed rules are promoted.

    Reads `risk.effective` (rule:) and user-declared values only (invariant 2). Weights are fractions;
    drawdown and MER stay in percent, like the profile fields they come from.
    """
    level = _rule_value(profile, "risk", "effective")
    row = LIMITS_TABLE[level]
    risk, prefs, values = profile["risk"], profile.get("preferences", {}), profile.get("values", {})

    max_dd = row["max_dd_pct"]
    user_dd = risk.get("max_acceptable_drawdown_pct")
    if user_dd is not None:
        max_dd = max(max_dd, user_dd)   # the less negative one

    excluded_types = _dedupe(list(prefs.get("asset_types_refused", [])))
    if row["max_crypto"] == 0 and "crypto" not in excluded_types:
        excluded_types.append("crypto")

    activities: list[str] = []
    for pillar in PILLARS:
        activities.extend(values.get(pillar, {}).get("exclusions", []))
    activities.extend(r["params"]["tag"] for r in _approved_parsed_rules(profile, "exclude_activity")
                      if isinstance(r.get("params", {}).get("tag"), str))

    themes: dict[str, float] = {}
    for r in _approved_parsed_rules(profile, "max_theme_weight"):
        pct = r.get("params", {}).get("max_pct")
        if isinstance(pct, bool) or not isinstance(pct, (int, float)):
            continue   # approved but the number is still unanswered (or skipped): nothing to promote yet
        theme, frac = r["params"]["theme"], pct / 100   # percent -> fraction, once
        themes[theme] = min(frac, themes.get(theme, frac))                # stricter wins

    limits: dict[str, Any] = {
        "_note": LIMITS_NOTE,
        "by": "rule:limits_table[risk.effective] + goals + preferences",
        "vol_band_annual": list(row["vol_band"]),
        "floor_at_horizon": round(portfolio_value_cad * row["floor_ratio"], 2),
        "max_drawdown_pct": max_dd,
        "min_fixed_income_weight": row["min_fixed_income"],
        "max_single_stock_weight": row["max_single_stock"],
        "max_sector_risk_share": row["max_sector_risk_share"],
        "max_crypto_weight": row["max_crypto"],
        "max_foreign_currency_weight": row["max_foreign_ccy"],
        "max_employer_sector_weight": MAX_EMPLOYER_SECTOR_WEIGHT,
        "excluded_asset_types": excluded_types,
        "excluded_activities": _dedupe(activities),
    }
    if "max_fee_mer_pct" in prefs:
        limits["max_fee_mer_pct"] = prefs["max_fee_mer_pct"]
    if themes:
        limits["max_theme_weight"] = {k: themes[k] for k in sorted(themes)}
    return limits, build_trace()
