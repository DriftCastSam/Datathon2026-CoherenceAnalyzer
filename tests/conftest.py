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
