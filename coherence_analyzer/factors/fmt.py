"""Locale number formatting for section facts (pre-formatted strings the writer copies, docs/spec-factors.md §20.4).

fr-CA: decimal comma, NBSP as thousands separator, `12,0 %` and `360 000 $` (NBSP before the unit).
en-CA: decimal point, comma thousands, `12.0%` and `$360,000`. Pure functions, no wall clock, no locale module.
"""
from __future__ import annotations

NBSP = "\u00a0"
DASH = "\u2014"          # shown when a check has no value / limit
LOCALES = ("fr-CA", "en-CA")


def check_locale(locale: str) -> str:
    if locale not in LOCALES:
        raise ValueError(f"unsupported locale {locale!r}; expected one of {LOCALES}")
    return locale


def _digits(x: float, decimals: int) -> str:
    s = f"{abs(float(x)):.{decimals}f}"
    neg = float(s) != 0.0 and float(x) < 0
    return ("-" if neg else "") + s


def _decimal(s: str, locale: str) -> str:
    return s.replace(".", ",") if locale == "fr-CA" else s


def number(x: float, locale: str, max_decimals: int = 2) -> str:
    """Plain number: integers without decimals, otherwise up to `max_decimals`, trailing zeros trimmed."""
    s = _digits(x, max_decimals)
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return _decimal(s or "0", locale)


def percent_points(x: float, locale: str, decimals: int | None = None) -> str:
    """`x` is already in percent points (e.g. -12 -> '-12 %'). `decimals=None` trims like `number`."""
    s = number(x, locale) if decimals is None else _decimal(_digits(x, decimals), locale)
    return f"{s}{NBSP}%" if locale == "fr-CA" else f"{s}%"


def percent(fraction: float, locale: str, decimals: int = 1) -> str:
    """`fraction` in 0-1 (0.134 -> '13,4 %')."""
    return percent_points(float(fraction) * 100.0, locale, decimals)


def amount(x: float, locale: str) -> str:
    """CAD amount rounded to the dollar: '360 000 $' (fr-CA) / '$360,000' (en-CA)."""
    v = round(float(x))
    body = f"{abs(v):,}"
    sign = "-" if v < 0 else ""
    if locale == "fr-CA":
        return f"{sign}{body.replace(',', NBSP)}{NBSP}$"
    return f"{sign}${body}"


_FRACTION_UNITS = {"weight", "annual_vol", "drawdown", "risk_share"}
PERCENT_METRICS = {"vol", "mdd", "loss_prob"}


def check_value(x, unit: str | None, locale: str) -> str:
    """Format a check `value` / `limit` by its `unit` (hard.py units: weight, annual_vol, drawdown, risk_share, cad, ...)."""
    if x is None:
        return DASH
    if unit in _FRACTION_UNITS:
        return percent(x, locale)
    if unit == "cad":
        return amount(x, locale)
    return number(x, locale)


def metric(key: str, x, locale: str) -> str:
    """Engine metric by key: vol / mdd / loss_prob are fractions, the rest are CAD amounts."""
    return percent(x, locale) if key in PERCENT_METRICS else amount(x, locale)


def profile_value(value, hint: str, locale: str):
    """Pre-format a profile value for `declared` / `derived`. Strings and booleans stay as they are (enums are copied
    verbatim); numbers become strings by the field's hint (`*_pct` percent points, amounts, plain numbers)."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int, float)):
        last = hint.rsplit(".", 1)[-1]
        if last.endswith("_pct"):
            return percent_points(value, locale)
        if last in {"amount", "emergency_fund", "monthly_expenses"}:
            return amount(value, locale)
        return number(value, locale)
    if isinstance(value, (list, tuple)):
        return [profile_value(v, hint, locale) for v in value]
    if isinstance(value, dict):
        return {str(k): profile_value(v, str(k), locale) for k, v in value.items()}
    return profile_value(float(value), hint, locale)     # numpy scalar
