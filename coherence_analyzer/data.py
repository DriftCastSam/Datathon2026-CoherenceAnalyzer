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
    """Not used in v1 (P2: rate-scenario stress)."""
    return pd.read_csv(Path(data_dir) / "macro_rates.csv", parse_dates=["date"])


def load_meta(name: str, meta_dir: Path = META_DIR) -> pd.DataFrame | None:
    """asset_meta | etf_sector_weights | activity_tags. None if the file isn't built yet."""
    p = Path(meta_dir) / f"{name}.csv"
    return pd.read_csv(p) if p.exists() else None


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
