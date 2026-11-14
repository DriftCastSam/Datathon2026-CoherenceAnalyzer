"""Loaders + adapter from starter filenames to engine names (SPEC §3.1).

Weights: percent in portfolio.json, fractions (0-1) inside the code. Convert here, once.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .config import DATA_DIR, META_DIR

# asset_class -> (profile asset type(s), is_fixed_income)
ASSET_CLASS_MAP: dict[str, tuple[tuple[str, ...], bool]] = {
    "equity": (("stocks",), False),
    "etf_equity": (("etf",), False),
    "etf_bond": (("bonds", "etf"), True),
    "etf_commodity": (("gold", "etf"), False),
    "crypto": (("crypto",), False),
}


def load_prices(data_dir: Path = DATA_DIR) -> pd.DataFrame:
    return pd.read_csv(Path(data_dir) / "prices_daily.csv", parse_dates=["date"])


def load_assets(data_dir: Path = DATA_DIR) -> pd.DataFrame:
    return pd.read_csv(Path(data_dir) / "assets_universe.csv").set_index("ticker")


def load_fx(data_dir: Path = DATA_DIR) -> pd.Series:
    return pd.read_csv(Path(data_dir) / "fx_usdcad_daily.csv", parse_dates=["date"]).set_index("date")["usdcad"]


def load_macro(data_dir: Path = DATA_DIR) -> pd.DataFrame:
    """Long format `date, series_id, value, unit, frequency, label, source`. Used by the rate stress (P2.16)."""
    return pd.read_csv(Path(data_dir) / "macro_rates.csv", parse_dates=["date"])


def macro_series(macro: pd.DataFrame, series_id: str, upto: str | None = None) -> pd.Series:
    """One series of a `load_macro` frame as a float Series indexed by date (sorted, NaN dropped), up to and including
    `upto` (D1: the reference date, never the wall clock). KeyError if the series is absent."""
    rows = macro[macro["series_id"] == series_id]
    if rows.empty:
        raise KeyError(f"macro series {series_id!r} not in macro_rates.csv")
    s = rows.set_index("date")["value"].astype(float).dropna().sort_index()
    if upto is not None:
        s = s[s.index <= pd.Timestamp(upto)]
    return s


def load_macro_series(series_id: str, data_dir: Path = DATA_DIR, upto: str | None = None) -> pd.Series:
    return macro_series(load_macro(data_dir), series_id, upto)


META_COLUMNS: dict[str, tuple[str, ...]] = {
    "asset_meta": ("ticker", "mer_pct", "mer_source_url", "mer_as_of", "has_lookthrough", "themes"),
    "etf_sector_weights": ("etf", "sector", "weight", "source_url", "as_of"),
    "activity_tags": ("ticker", "tag", "evidence_summary", "source_url", "as_of", "added_by"),
}
_TAG_REQUIRED = ("ticker", "tag", "source_url", "as_of", "added_by")   # evidence_summary may stay empty
_BOOLS = {"true": True, "1": True, "yes": True, "false": False, "0": False, "no": False, "": False}


def _etf_tickers(data_dir: Path = DATA_DIR) -> set[str]:
    """ETF tickers of the starter universe (asset_class etf_*); empty if the universe file is absent."""
    try:
        a = load_assets(data_dir)
    except FileNotFoundError:
        return set()
    return {t for t, c in a["asset_class"].items() if str(c).startswith("etf")}


def _number(raw: str, where: str, lo: float | None = None, hi: float | None = None) -> float:
    """Blank -> NaN. Non-numeric or outside [lo, hi] -> ValueError."""
    if raw == "":
        return float("nan")
    try:
        x = float(raw)
    except ValueError:
        raise ValueError(f"{where}: {raw!r} is not a number") from None
    if x != x or (lo is not None and x < lo) or (hi is not None and x > hi):
        raise ValueError(f"{where}: {raw!r} out of range")
    return x


def _blank(df: pd.DataFrame, cols: tuple[str, ...]) -> pd.Series:
    return (df[list(cols)] == "").any(axis=1)


def _validate_meta(name: str, df: pd.DataFrame, path: Path) -> pd.DataFrame:
    """SPEC §3.2 validation. `df` is all-string (blank = ''). Returns the typed frame."""
    if name == "activity_tags":
        bad = df[_blank(df, _TAG_REQUIRED)]
        if len(bad):
            raise ValueError(f"{path}: activity_tags rows need ticker, tag, source_url, as_of and added_by "
                             f"(human, sourced); incomplete row(s) at line(s) {[int(i) + 2 for i in bad.index]}")
        return df
    if name == "etf_sector_weights":
        bad = df[_blank(df, ("etf", "sector", "source_url", "as_of"))]
        if len(bad):
            raise ValueError(f"{path}: etf_sector_weights rows need etf, sector, source_url and as_of; "
                             f"incomplete row(s) at line(s) {[int(i) + 2 for i in bad.index]}")
        df["weight"] = [_number(w, f"{path}: weight", 0.0, 1.0) for w in df["weight"]]
        if df["weight"].isna().any():
            raise ValueError(f"{path}: etf_sector_weights rows need a weight")
        return df
    # asset_meta
    if (df["ticker"] == "").any():
        raise ValueError(f"{path}: asset_meta rows need a ticker")
    dup = sorted(df["ticker"][df["ticker"].duplicated()].unique())
    if dup:
        raise ValueError(f"{path}: duplicate asset_meta ticker(s) {dup}")
    etfs = _etf_tickers()
    df["mer_pct"] = [_number(m, f"{path}: mer_pct of {t}", 0.0) for t, m in zip(df["ticker"], df["mer_pct"])]
    for t, mer, url in zip(df["ticker"], df["mer_pct"], df["mer_source_url"]):
        # "Don't guess": a fee that is not a stock/crypto zero needs the issuer page it came from.
        if mer == mer and url == "" and (mer > 0 or t in etfs):
            raise ValueError(f"{path}: {t} has mer_pct {mer} but no mer_source_url")
    try:
        df["has_lookthrough"] = [_BOOLS[str(v).strip().lower()] for v in df["has_lookthrough"]]
    except KeyError as e:
        raise ValueError(f"{path}: has_lookthrough must be true/false, got {e.args[0]!r}") from None
    df["themes"] = [[x.strip() for x in str(v).split(";") if x.strip()] for v in df["themes"]]
    return df


def load_meta(name: str, meta_dir: Path = META_DIR) -> pd.DataFrame | None:
    """asset_meta | etf_sector_weights | activity_tags (SPEC §3.2). None if the file isn't built yet.

    Validates the documented columns and the sourcing rules (ValueError otherwise). Typed on load:
    asset_meta mer_pct float (NaN = not filled), has_lookthrough bool, themes list[str];
    etf_sector_weights weight float (fraction). Blank cells elsewhere stay ''.
    """
    if name not in META_COLUMNS:
        raise ValueError(f"unknown meta file {name!r}; expected one of {sorted(META_COLUMNS)}")
    p = Path(meta_dir) / f"{name}.csv"
    if not p.exists():
        return None
    try:
        df = pd.read_csv(p, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        raise ValueError(f"{p}: empty file, expected the header {','.join(META_COLUMNS[name])}") from None
    df.columns = [str(c).strip() for c in df.columns]
    missing = [c for c in META_COLUMNS[name] if c not in df.columns]
    if missing:
        raise ValueError(f"{p}: missing column(s) {missing}")
    for c in df.columns:
        df[c] = df[c].str.strip()
    return _validate_meta(name, df, p)


def load_portfolio(path: Path, assets: pd.DataFrame | None = None) -> dict:
    """Load portfolio.json, validate weights (sum 100 +- 0.01, known tickers), add `weights` as fractions."""
    pf = json.loads(Path(path).read_text())
    w = pf["weights_pct"]
    total = sum(w.values())
    if abs(total - 100) > 0.01:
        raise ValueError(f"{path}: weights sum to {total}, expected 100")
    if assets is not None:
        unknown = set(w) - set(assets.index)
        if unknown:
            raise ValueError(f"{path}: tickers not in universe: {sorted(unknown)}")
    pf["weights"] = {t: v / 100 for t, v in w.items()}
    return pf
