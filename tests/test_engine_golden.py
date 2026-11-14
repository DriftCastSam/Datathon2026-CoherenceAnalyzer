"""Refactored engine == scripts/engine_v0.py on starter data (SPEC §5, §15)."""
import json
import subprocess
import sys

import numpy as np
import pytest

from portee import engine

CASES = {"robert": (engine.ROBERT, 48, 400_000), "robert_nvda": (engine.ROBERT_NVDA, 48, 400_000),
         "amira": (engine.AMIRA, 180, 60_000)}
KEYS = ("vol", "vol_local", "mdd", "mdd_date", "width", "loss_prob", "groups", "drivers", "corr_eq_bond")


@pytest.fixture(scope="module")
def golden(root, tmp_path_factory):
    out = tmp_path_factory.mktemp("golden")
    subprocess.run([sys.executable, str(root / "scripts" / "engine_v0.py"), str(root / "data" / "starter")],
                   cwd=out, check=True, capture_output=True)
    return json.loads((out / "engine_out.json").read_text())


@pytest.mark.parametrize("name", CASES)
def test_matches_v0(golden, market, name):
    w, h, start = CASES[name]
    new, old = engine.analyse(market, w, h, start), golden[name]
    for k in KEYS:
        assert new[k] == old[k], k
    for p, series in old["cone"].items():
        np.testing.assert_allclose(new["cone"][p], series, atol=1e-9)
    assert new["p5_T"] == old["cone"]["p5"][-1]


def test_window(market):
    assert market.window == ["2022-01-31", "2026-10-31"]
    assert len(market.r_cad) == 58
