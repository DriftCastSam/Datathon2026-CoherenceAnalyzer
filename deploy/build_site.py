#!/usr/bin/env python3
"""Build the static dashboard for CloudFront (deploy/cloudfront.yaml) from the REAL pipeline.

Output (<out>, default .cache/site/):
  app.html, design.html     deploy/site/ (the app and the static design board)
  reports/en-CA/index.json  the reports the app can open (Robert current, Amira, Mateo + Robert's NVDA scenario)
  reports/en-CA/robert-nvda.json   trade.js's offline worked example (Robert, NVDA 10 %): its own file, not in the index
  reports/en-CA/universe-<dir>.json, reports/en-CA/whatif-<dir>.json   per-profile what-if (deploy/whatif.py); every
                            built review names its own pair in `site.whatif`
  reports/en-CA/intake.json the intake question manifest (deploy/site/js/screens/conversation.js's static demo path)
  reports/en-CA/<name>.json `pipeline.review(...)` reports (SPEC §13 + §20.4 sections + §20.9 profile_coherence, plus the
                            P2 blocks `stress`, `goals`, `metrics_current.lookthrough`) and `site`: display extras
                            computed by the same rules code (profile card, limits)

Runs offline in mock mode (recorded LLM outputs, fixtures/llm_cache/). The store is a temp dir, so building
the site never writes to reports/ or profiles/. `build(out)` is importable (tests/dashboard/test_site.py).

`deploy/site/index.html` (the earlier French single-page dashboard) is retired: every feature it had is now in
app.html, and the four-plus frozen oracles that used to pin it as text were retargeted to app.html's real modules
(see git history / README.md's P8.UNIFY entry). This module's French report pipeline was dropped with it.
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
EN_LOCALE = "en-CA"
SITE_LOCALE = EN_LOCALE
INTAKE_FILE = "intake.json"
STORE_DIR_ENV = "COHERENCE_ANALYZER_STORE_DIR"

# The persona fixture directories the site builds for, in picker order.
PERSONA_DIRS = ["robert", "amira", "mateo"]

# English labels for app (app.html)
LABEL_SEPARATOR = " · "
CURRENT_LABEL = "current portfolio"
EXTRA_PORTFOLIOS = {  # dir name -> [(report stem suffix, portfolio file, label)]
    "robert": [("-nvda", "portfolio_review.json", "portfolio with NVDA at 10%")],
}

# The profiles the what-if is precomputed for are the ones PERSONA_DIRS names (deploy/whatif.py takes the profile directory).


def _val(x):
    return x.get("value") if isinstance(x, dict) and "value" in x else x


class Persona:
    """A persona fixture: directory name, profile id, and display name."""
    def __init__(self, dir_name: str, profile_id: str, name: str):
        self.dir_name = dir_name
        self.profile_id = profile_id
        self.name = name


def load_persona(dir_name: str) -> Persona:
    """Read `fixtures/<dir_name>/profile.json` and return the persona (id from `meta.profile_id`, name from identity)."""
    profile = json.loads((FIXTURES / dir_name / "profile.json").read_text(encoding="utf-8"))
    name = (profile.get("identity") or {}).get("name") or dir_name.capitalize()
    return Persona(dir_name, profile["meta"]["profile_id"], name)


def report_specs_en(persona: Persona) -> list[tuple[str, str, str]]:
    """English report specs for one persona: (stem, portfolio_file, label).
    Returns current portfolio first, then any extra scenario portfolios."""
    specs = [(f"{persona.dir_name}-review", "portfolio.json", CURRENT_LABEL)]
    for suffix, pf_file, label in EXTRA_PORTFOLIOS.get(persona.dir_name, []):
        specs.append((f"{persona.dir_name}-review{suffix}", pf_file, label))
    return specs


def site_extras(profile_dir: Path, name: str, portfolio_file: str, whatif_files: dict | None = None) -> dict:
    """Display-only extras from the real rules code (derive + limits); nothing here is a check or a status.

    Args:
        profile_dir: directory holding `profile.json` and the portfolio file (fixture or API working copy).
        name: display name for the profile card.
        portfolio_file: name of the portfolio JSON file within profile_dir.
        whatif_files: {"universe": file, "scenarios": file} (names inside reports/) of this profile's precomputed what-if.
    """
    from coherence_analyzer.rules import derive, limits as limits_mod, registry
    profile = json.loads((profile_dir / "profile.json").read_text(encoding="utf-8"))
    pf = json.loads((profile_dir / portfolio_file).read_text(encoding="utf-8"))
    value, as_of = float(pf["value_cad"]), pf["as_of"]
    p = derive.derive_all(copy.deepcopy(profile), portfolio_value_cad=value, as_of=as_of)
    lim, trace = limits_mod.build_limits(p, value)
    withdrawals = p["situation"].get("planned_withdrawals")
    if registry.is_skipped(withdrawals):
        first = None
    elif isinstance(withdrawals, list) and len(withdrawals) > 0:
        first = withdrawals[0]
    else:
        first = {}
    goals = p["objectives"].get("goals", [])
    if registry.is_skipped(goals):
        goals = []
    return {
        "as_of": as_of, "value_cad": value, "portfolio_file": portfolio_file, "whatif": dict(whatif_files) if whatif_files else False,
        "weights_pct": {k: float(v) for k, v in pf["weights_pct"].items() if float(v) > 0},
        "limits": {k: v for k, v in lim.items() if not k.startswith("_") and k != "by"},
        "limits_trace": trace,
        "profile_card": {
            "name": name, "age": _val(p["identity"]["age"]), "occupation": _val(p["identity"]["occupation"]),
            "life_stage": _val(p["identity"]["life_stage"]), "risk_declared": _val(p["risk"]["declared"]),
            "risk_capacity": _val(p["risk"]["capacity"]), "risk_effective": _val(p["risk"]["effective"]),
            "horizon_years": _val(p["objectives"]["horizon_years"]),
            "effective_horizon_years": _val(p["objectives"]["effective_horizon_years"]),
            "first_withdrawal": first, "emergency_fund_months": _val(p["situation"]["emergency_fund_months"]),
            "goals": goals, "notes": _val(p["values"].get("notes", "")),
        },
    }


def missing_by_factor(persona: str) -> dict:
    """{factor: [missing intake path, ...]} = `intake.missing` on the persona's profile (paths only: no AI output, no limit).
    The static demo has no server, so the « Points à clarifier » card reads this list, read-only."""
    from coherence_analyzer import intake
    profile = json.loads((FIXTURES / persona / "profile.json").read_text(encoding="utf-8"))
    return {f: intake.missing(profile, f) for f in intake.FACTORS}


def intake_payload(locale: str) -> dict:
    """Intake checklist for the site: required questions with texts in the given locale."""
    from coherence_analyzer import intake
    questions = [{"factor": factor, "path": path, "text": intake.QUESTIONS[path][locale]}
                 for factor, paths in intake.REQUIRED.items() for path in paths]
    return {"skipped_by": intake.SKIPPED_BY, "questions": questions}


def _build_whatif(out: Path, personas: list[tuple[str, str]], locale: str, legacy_first: bool = True) -> dict[str, dict]:
    """Precompute what-if for personas. Each tuple is (dir_name, key_for_filename): key is profile_id.
    Returns {dir_name: {"universe": file, "scenarios": file}}."""
    import whatif  # deploy/whatif.py
    out.mkdir(parents=True, exist_ok=True)
    files: dict[str, dict] = {}
    for dir_name, file_key in personas:
        uni, scen = f"universe-{file_key}.json", f"whatif-{file_key}.json"
        with tempfile.TemporaryDirectory() as tmp:
            whatif.build(Path(tmp), FIXTURES / dir_name, locale=locale)
            shutil.copyfile(Path(tmp) / "universe.json", out / uni)
            shutil.copyfile(Path(tmp) / "whatif.json", out / scen)
        files[dir_name] = {"universe": uni, "scenarios": scen}
    if legacy_first and personas:
        first_key = personas[0][1]
        shutil.copyfile(out / f"universe-{first_key}.json", out / "universe.json")
        shutil.copyfile(out / f"whatif-{first_key}.json", out / "whatif.json")
    return files


def build_whatif_en(out: Path, personas: list[Persona]) -> None:
    """Precompute the what-if for each persona. Files: reports/en-CA/universe-<profile_id>.json and whatif-<profile_id>.json."""
    persona_tuples = [(p.dir_name, p.profile_id) for p in personas]
    _build_whatif(out, persona_tuples, EN_LOCALE)


def _build_reports(out: Path, locale: str, report_specs: list[tuple],
                   include_intake: bool = False) -> list[dict]:
    """Build reports for given locale and specs: (Persona, stem, pf_file, label_suffix)."""
    from coherence_analyzer import pipeline
    out.mkdir(parents=True, exist_ok=True)
    index = []

    for persona, stem, pf_file, label_suffix in report_specs:
        rep = pipeline.review(FIXTURES / persona.dir_name, pf_file, locale=locale)
        extras = site_extras(FIXTURES / persona.dir_name, persona.name, pf_file, whatif_files=None)
        label = f"{persona.name}{LABEL_SEPARATOR}{label_suffix}"
        rep["site"] = {**extras, "label": label, "intake": missing_by_factor(persona.dir_name)}
        (out / f"{stem}.json").write_text(json.dumps(rep, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        index.append({
            "persona_id": persona.profile_id,
            "name": persona.name,
            "file": f"{stem}.json",
            "label": label,
            "portfolio": pf_file,
            "verdict": rep["verdict"],
            "profile": rep.get("profile_coherence", {}).get("status")
        })

    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    if include_intake:
        (out / INTAKE_FILE).write_text(json.dumps(intake_payload(locale), ensure_ascii=False), encoding="utf-8")
    return index


def build_reports_en(out: Path, personas: list[Persona]) -> list[dict]:
    """Build English reports for app (app.html). Entry shape: {persona_id, name, file, label, portfolio, verdict, profile}."""
    specs = [(p, stem, pf_file, label_suffix)
             for p in personas
             for stem, pf_file, label_suffix in report_specs_en(p)]
    return _build_reports(out, EN_LOCALE, specs, include_intake=True)


def build(out: Path) -> Path:
    """Write the whole site into `out` (replaced if it exists) and return it. The store is a temp dir that is
    removed afterwards and the previous COHERENCE_ANALYZER_STORE_DIR is restored."""
    out = Path(out)
    if out.exists():
        for item in out.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
    shutil.copytree(ROOT / "deploy" / "site", out, dirs_exist_ok=True)
    saved = os.environ.get("COHERENCE_ANALYZER_STORE_DIR")
    try:
        with tempfile.TemporaryDirectory() as store:
            os.environ[STORE_DIR_ENV] = store
            # English reports for app (app.html) - full shape with persona_id, name, etc.
            personas = [load_persona(dir_name) for dir_name in PERSONA_DIRS]
            build_whatif_en(out / "reports" / "en-CA", personas)
            build_reports_en(out / "reports" / "en-CA", personas)
            # app.html's trade.js reads its own offline/static worked example directly at
            # reports/en-CA/robert-nvda.json (STATIC_DEMO_PATH), never through index.json. Built by
            # deploy/preview_report.py's `build_en_ca` (additive to its own `build()`/`build_flow_b`), which
            # fills in `headroom.line` with the real SPEC §8 EN template (tests/dashboard/test_ui_flow_b.py).
            import preview_report  # deploy/preview_report.py (deploy/ is on sys.path below, like `import whatif`)
            preview_report.build_en_ca(out / "reports" / "en-CA")
    finally:
        if saved is None:
            os.environ.pop(STORE_DIR_ENV, None)
        else:
            os.environ[STORE_DIR_ENV] = saved
    return out


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))   # `import whatif` when run as a script from anywhere
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg.startswith("-"):
            print("usage: python -m deploy.build_site [OUT_DIR]", file=sys.stderr)
            sys.exit(2)
        out_path = Path(arg).resolve()
        root_path = ROOT.resolve()
        if out_path == root_path or out_path in root_path.parents:
            print(f"error: refusing to build into {out_path} (ROOT or ancestor of repo)", file=sys.stderr)
            sys.exit(2)
        out = build(out_path)
    else:
        out = build(ROOT / ".cache" / "site")
    for p in sorted(out.rglob("*")):
        if p.is_file():
            print(p.relative_to(out), p.stat().st_size)
