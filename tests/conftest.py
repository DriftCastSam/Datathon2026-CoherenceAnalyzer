import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("COHERENCE_ANALYZER_LLM", "mock")


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """Each test starts in its own tmp dir, so the local store (default root: cwd) never writes into the repo.
    Tests that chdir or set COHERENCE_ANALYZER_STORE_DIR themselves still win."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture(scope="session")
def root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def market():
    from coherence_analyzer.engine import load_market
    return load_market(str(ROOT / "data" / "starter"))


LOOKTHROUGH_FILES = ("etf_sector_weights.csv", "etf_country_weights.csv", "etf_top_holdings.csv")


@pytest.fixture(scope="module")
def without_lookthrough(tmp_path_factory):
    """D19: data/meta without the three ETF look-through files (asset_meta / activity_tags kept as they are), as
    config.META_DIR for the whole module. For the oracles of the no-look-through path (ETF part `unknown`); the
    look-through path itself is pinned by the engine look-through and exposure tests."""
    import shutil

    from coherence_analyzer import config
    d = tmp_path_factory.mktemp("meta_without_lookthrough")
    for f in config.META_DIR.glob("*.csv"):
        if f.name not in LOOKTHROUGH_FILES:
            shutil.copy(f, d / f.name)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config, "META_DIR", d)
        yield d
