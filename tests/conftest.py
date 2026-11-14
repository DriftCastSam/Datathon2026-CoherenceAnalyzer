import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("COHERENCE_ANALYZER_LLM", "mock")


@pytest.fixture(scope="session")
def root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def market():
    from coherence_analyzer.engine import load_market
    return load_market(str(ROOT / "data" / "starter"))
