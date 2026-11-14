#!/usr/bin/env python3
"""Build the static dashboard for CloudFront (deploy/cloudfront.yaml) from the REAL pipeline.

Output (<out>, default .cache/site/):
  index.html, design.html   deploy/site/ (the dashboard and the static design board)
  reports/index.json        the reports the dashboard can open
  reports/<name>.json       `pipeline.review(...)` reports (SPEC §13 + §20.4 sections + §20.9 profile_coherence)
                            plus `site`: display extras computed by the same rules code (profile card, limits)

Runs offline in mock mode (recorded LLM outputs, fixtures/llm_cache/). The store is a temp dir, so building
the site never writes to reports/ or profiles/.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("COHERENCE_ANALYZER_LLM", "mock")

FIXTURE = ROOT / "fixtures" / "robert"
REPORTS = [  # (file name, portfolio file, picker label)
    ("robert-review-nvda", "portfolio_review.json", "Robert · portefeuille avec NVDA à 10 %"),
    ("robert-review", "portfolio.json", "Robert · portefeuille actuel"),
]


def _val(x):
    return x.get("value") if isinstance(x, dict) and "value" in x else x


def site_extras(portfolio_file: str) -> dict:
    """Display-only extras from the real rules code (derive + limits); nothing here is a check or a status."""
    from coherence_analyzer.rules import derive, limits as limits_mod
    profile = json.loads((FIXTURE / "profile.json").read_text())
    pf = json.loads((FIXTURE / portfolio_file).read_text())
    value, as_of = float(pf["value_cad"]), pf["as_of"]
    p = derive.derive_all(copy.deepcopy(profile), portfolio_value_cad=value, as_of=as_of)
    lim, trace = limits_mod.build_limits(p, value)
    first = (p["situation"].get("planned_withdrawals") or [{}])[0]
    return {
        "as_of": as_of, "value_cad": value, "portfolio_file": portfolio_file,
        "weights_pct": {k: float(v) for k, v in pf["weights_pct"].items() if float(v) > 0},
        "limits": {k: v for k, v in lim.items() if not k.startswith("_") and k != "by"},
        "limits_trace": trace,
        "profile_card": {
            "name": "Robert", "age": p["identity"]["age"], "occupation": p["identity"]["occupation"],
            "life_stage": _val(p["identity"]["life_stage"]), "risk_declared": p["risk"]["declared"],
            "risk_capacity": _val(p["risk"]["capacity"]), "risk_effective": _val(p["risk"]["effective"]),
            "horizon_years": p["objectives"]["horizon_years"],
            "effective_horizon_years": _val(p["objectives"]["effective_horizon_years"]),
            "first_withdrawal": first, "emergency_fund_months": _val(p["situation"]["emergency_fund_months"]),
            "goals": p["objectives"].get("goals", []), "notes": p["values"].get("notes", ""),
        },
    }


def build_reports(out: Path) -> list[dict]:
    from coherence_analyzer import pipeline
    out.mkdir(parents=True, exist_ok=True)
    index = []
    with tempfile.TemporaryDirectory() as store:
        os.environ["COHERENCE_ANALYZER_STORE_DIR"] = store
        for name, pf_file, label in REPORTS:
            rep = pipeline.review(FIXTURE, pf_file)
            rep["site"] = {**site_extras(pf_file), "label": label}
            (out / f"{name}.json").write_text(json.dumps(rep, ensure_ascii=False, separators=(",", ":")))
            index.append({"file": f"{name}.json", "label": label, "verdict": rep["verdict"],
                          "profile": rep.get("profile_coherence", {}).get("status")})
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False))
    return index


def build(out: Path) -> Path:
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "deploy" / "site", out)
    build_reports(out / "reports")
    return out


if __name__ == "__main__":
    out = build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".cache" / "site")
    for p in sorted(out.rglob("*")):
        if p.is_file():
            print(p.relative_to(out), p.stat().st_size)
