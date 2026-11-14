"""LIMITS_TABLE + build_limits() + trace (SPEC §7.2). P0 step 2.

Inputs: rule: fields and user-declared values ONLY (invariant 2).
Table values are team calibrations; justify them in README -> Assumptions.
"""
from __future__ import annotations

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


def build_limits(profile: dict, portfolio_value_cad: float) -> tuple[dict, dict[str, list[str]]]:
    """Return (limits, limits_trace). Only approved parsed rules are promoted."""
    raise NotImplementedError
