"""Pure amount helpers of deploy/site/js/screens/whatif/whatif_view.js, run under node (skipped without it)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VIEW = ROOT / "deploy" / "site" / "js" / "screens" / "whatif" / "whatif_view.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const fmt = (v, d = 0) => Number(v).toFixed(d);
const App = {fmtCad: fmt, fmtPct: (v, d = 0) => fmt(Math.abs(v) < 1 ? v * 100 : v, d) + "%",
             statusLabel: {}, riskLevels: {}};
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"), {App});
const v = App.whatifView;
const base = {value_cad: 200000, metrics: {p5_T: 150000, p50_T: 220000, p95_T: 300000, vol: 0.1, mdd: -20},
              shares: {}, sections: {}, verdict: "coherent"};
const scen = {metrics: {p5_T: 160000, p50_T: 240000, p95_T: 330000, vol: 0.11, mdd: -21}, shares: {}, sections: [],
              verdict: "coherent", value_cad: 210000};
const out = {
  pct: v.pctFromAmount(5000, 200000), cad: v.amountFromPct(2.5, 200000), oddPct: v.pctFromAmount(7333.33, 200000), fromOddPct: v.amountFromPct(3.67, 200000),
  presetUnheld: v.presetFor(10000, 0, 200000, [5, 10], "pro_rata_all"),
  presetHeld: v.presetFor(4000, 3, 200000, [5, 10], "pro_rata_all"),
  presetMiss: v.presetFor(4321, 0, 200000, [5, 10], "pro_rata_all"),
  presetCash: v.presetFor(10000, 0, 200000, [5, 10], "cash"),
  labelHeld: v.amountLabel(5, 200000, 3),
  capPro: v.capacityCad("pro_rata_all", 3, 200000, {}), capSpec: v.capacityCad("specific:XBB", 0, 200000, {XBB: 20}),
  capCash: v.capacityCad("cash", 0, 200000, {}),
  probZero: v.amountProblem(0, null) !== "", probOver: v.amountProblem(50000, 40000) !== "", probOk: v.amountProblem(100, 40000),
  view: v.scenarioView({base, scenario: scen, amountCad: 10000, funding: "cash", fundingLabel: "New cash contribution", years: 5}),
};
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def out() -> dict:
    r = subprocess.run([NODE, "-e", HARNESS, str(VIEW)], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def test_amount_and_percent_convert_both_ways(out):
    assert out["pct"] == 2.5 and out["cad"] == 5000
    # The typed field is authoritative; the mirrored one is shown at 2 dp (so a typed % moves in $20 steps here).
    assert out["oddPct"] == 3.67 and out["fromOddPct"] == 7340


def test_presets_match_only_the_cad_the_engine_actually_buys(out):
    assert out["presetUnheld"] == 5                  # nothing held: 5% target = $10,000
    assert out["presetHeld"] == 5                    # held 3% -> 5% target buys 2% = $4,000
    assert out["presetMiss"] is None and out["presetCash"] is None
    assert out["labelHeld"].startswith("$4000.00")


def test_capacity_and_validation(out):
    assert out["capPro"] == 194000 and out["capSpec"] == 40000 and out["capCash"] is None
    assert out["probZero"] and out["probOver"] and out["probOk"] == ""


def test_cash_finding_names_the_contribution_and_uses_the_grown_value_for_the_loss(out):
    finding = out["view"]["finding"]
    assert finding.startswith("With a new cash contribution of $10000.00 (5% of the current portfolio value)")
    loss_row = next(r for r in out["view"]["rows"] if r["label"] == "Bad-scenario loss")
    assert loss_row["today"] == "50000" and loss_row["withAsset"] == "50000"   # 200k-150k, 210k-160k
