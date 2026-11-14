#!/usr/bin/env python3
"""Build the static dashboard for CloudFront (deploy/cloudfront.yaml) from the REAL pipeline.

Output (<out>, default .cache/site/):
  index.html, design.html   deploy/site/ (the dashboard and the static design board)
  reports/index.json        the reports the dashboard can open (Robert current, Robert + NVDA, Amira, Mateo)
  reports/universe.json     starter universe + base metrics (Search and ask)
  reports/whatif.json       precomputed What-if scenarios (deploy/whatif.py)
  reports/<name>.json       `pipeline.review(...)` reports (SPEC §13 + §20.4 sections + §20.9 profile_coherence, plus the
                            P2 blocks `stress`, `goals`, `metrics_current.lookthrough`) and `site`: display extras
                            computed by the same rules code (profile card, limits)

Runs offline in mock mode (recorded LLM outputs, fixtures/llm_cache/). The store is a temp dir, so building
the site never writes to reports/ or profiles/. `build(out)` is importable (tests/test_site.py).
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

FIXTURES = ROOT / "fixtures"
REPORTS = [  # (file name, persona fixture dir, portfolio file, picker label); the index keeps this order
    ("robert-review-nvda", "robert", "portfolio_review.json", "Robert · portefeuille avec NVDA à {NVDA} %"),
    ("robert-review", "robert", "portfolio.json", "Robert · portefeuille actuel"),
    ("amira-review", "amira", "portfolio.json", "Amira · portefeuille actuel"),
    ("mateo-review", "mateo", "portfolio.json", "Mateo · portefeuille actuel"),
]
WHATIF_PERSONA = "robert"   # deploy/whatif.py precomputes the What-if scenarios for Robert's profile only


def _val(x):
    return x.get("value") if isinstance(x, dict) and "value" in x else x


def site_extras(persona: str, portfolio_file: str) -> dict:
    """Display-only extras from the real rules code (derive + limits); nothing here is a check or a status."""
    from coherence_analyzer.rules import derive, limits as limits_mod
    fixture = FIXTURES / persona
    profile = json.loads((fixture / "profile.json").read_text())
    pf = json.loads((fixture / portfolio_file).read_text())
    value, as_of = float(pf["value_cad"]), pf["as_of"]
    p = derive.derive_all(copy.deepcopy(profile), portfolio_value_cad=value, as_of=as_of)
    lim, trace = limits_mod.build_limits(p, value)
    first = (p["situation"].get("planned_withdrawals") or [{}])[0]
    return {
        "as_of": as_of, "value_cad": value, "portfolio_file": portfolio_file, "whatif": persona == WHATIF_PERSONA,
        "weights_pct": {k: float(v) for k, v in pf["weights_pct"].items() if float(v) > 0},
        "limits": {k: v for k, v in lim.items() if not k.startswith("_") and k != "by"},
        "limits_trace": trace,
        "profile_card": {
            "name": persona.capitalize(), "age": p["identity"]["age"], "occupation": p["identity"]["occupation"],
            "life_stage": _val(p["identity"]["life_stage"]), "risk_declared": p["risk"]["declared"],
            "risk_capacity": _val(p["risk"]["capacity"]), "risk_effective": _val(p["risk"]["effective"]),
            "horizon_years": p["objectives"]["horizon_years"],
            "effective_horizon_years": _val(p["objectives"]["effective_horizon_years"]),
            "first_withdrawal": first, "emergency_fund_months": _val(p["situation"]["emergency_fund_months"]),
            "goals": p["objectives"].get("goals", []), "notes": p["values"].get("notes", ""),
        },
    }


def _label(template: str, extras: dict) -> str:
    """Picker label; any figure in it is read from the portfolio file (`{NVDA}` = that weight in percent)."""
    return template.format(**{t.replace(".", "_"): f"{w:g}" for t, w in extras["weights_pct"].items()})


def build_reports(out: Path) -> list[dict]:
    from coherence_analyzer import pipeline
    out.mkdir(parents=True, exist_ok=True)
    index = []
    for name, persona, pf_file, template in REPORTS:
        rep = pipeline.review(FIXTURES / persona, pf_file)
        extras = site_extras(persona, pf_file)
        label = _label(template, extras)
        rep["site"] = {**extras, "label": label}
        (out / f"{name}.json").write_text(json.dumps(rep, ensure_ascii=False, separators=(",", ":")))
        index.append({"file": f"{name}.json", "label": label, "verdict": rep["verdict"],
                      "profile": rep.get("profile_coherence", {}).get("status")})
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False))
    return index


def build(out: Path) -> Path:
    """Write the whole site into `out` (replaced if it exists) and return it. The store is a temp dir that is
    removed afterwards and the previous COHERENCE_ANALYZER_STORE_DIR is restored."""
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "deploy" / "site", out)
    saved = os.environ.get("COHERENCE_ANALYZER_STORE_DIR")
    try:
        with tempfile.TemporaryDirectory() as store:
            os.environ["COHERENCE_ANALYZER_STORE_DIR"] = store
            build_reports(out / "reports")
            import whatif  # deploy/whatif.py: Search-and-ask universe + What-if scenarios (real engine, no LLM)
            whatif.build(out / "reports")
    finally:
        if saved is None:
            os.environ.pop("COHERENCE_ANALYZER_STORE_DIR", None)
        else:
            os.environ["COHERENCE_ANALYZER_STORE_DIR"] = saved
    return out


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))   # `import whatif` when run as a script from anywhere
    out = build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".cache" / "site")
    for p in sorted(out.rglob("*")):
        if p.is_file():
            print(p.relative_to(out), p.stat().st_size)
