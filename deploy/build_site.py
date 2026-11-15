#!/usr/bin/env python3
"""Build the static dashboard for CloudFront (deploy/cloudfront.yaml) from the REAL pipeline.

Output (<out>, default .cache/site/):
  index.html, design.html   deploy/site/ (the dashboard and the static design board)
  reports/index.json        the reports the dashboard can open (Robert current, Robert + NVDA, Amira, Mateo)
  reports/robert-nvda.json  the Flow B demo report (deploy/preview_report.py, Robert NVDA 10 %): its own file, not in the index
  reports/universe.json     starter universe + base metrics (Search and ask) of the first review's profile (legacy file)
  reports/whatif.json       precomputed What-if scenarios (deploy/whatif.py) of the first review's profile (legacy file)
  reports/universe-<dir>.json, reports/whatif-<dir>.json   the same for each profile directory REPORTS names; every built review
                            names its own pair in `site.whatif`
  reports/<name>.json       `pipeline.review(...)` reports (SPEC §13 + §20.4 sections + §20.9 profile_coherence, plus the
                            P2 blocks `stress`, `goals`, `metrics_current.lookthrough`) and `site`: display extras
                            computed by the same rules code (profile card, limits)

Runs offline in mock mode (recorded LLM outputs, fixtures/llm_cache/). The store is a temp dir, so building
the site never writes to reports/ or profiles/. `build(out)` is importable (tests/dashboard/test_site.py).
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
FR_LOCALE = "fr-CA"
EN_LOCALE = "en-CA"
SITE_LOCALE = EN_LOCALE  # backward compatibility for serve_local.py
INTAKE_FILE = "intake.json"
STORE_DIR_ENV = "COHERENCE_ANALYZER_STORE_DIR"

# French labels for dashboard (index.html) - must match origin/loop/p0 for frozen tests
REPORTS_FR = [  # (file name, persona fixture dir, portfolio file, picker label); the index keeps this order
    ("robert-review-nvda", "robert", "portfolio_review.json", "Robert · portefeuille avec NVDA à {NVDA} %"),
    ("robert-review", "robert", "portfolio.json", "Robert · portefeuille actuel"),
    ("amira-review", "amira", "portfolio.json", "Amira · portefeuille actuel"),
    ("mateo-review", "mateo", "portfolio.json", "Mateo · portefeuille actuel"),
]

# English labels for app (app.html)
LABEL_SEPARATOR = " · "
CURRENT_LABEL = "current portfolio"
EXTRA_PORTFOLIOS = {  # dir name -> [(report stem suffix, portfolio file, label)]
    "robert": [("-nvda", "portfolio_review.json", "portfolio with NVDA at 10%")],
}

# The Flow B demo report (deploy/preview_report.py output): its own file, and the profile directory it was built from.
FLOW_B_NAME, FLOW_B_PROFILE = "robert-nvda", "robert"

# The profiles the what-if is precomputed for are the ones REPORTS_FR names (deploy/whatif.py takes the profile directory).


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
            "name": name, "age": p["identity"]["age"], "occupation": p["identity"]["occupation"],
            "life_stage": _val(p["identity"]["life_stage"]), "risk_declared": p["risk"]["declared"],
            "risk_capacity": _val(p["risk"]["capacity"]), "risk_effective": _val(p["risk"]["effective"]),
            "horizon_years": p["objectives"]["horizon_years"],
            "effective_horizon_years": _val(p["objectives"]["effective_horizon_years"]),
            "first_withdrawal": first, "emergency_fund_months": _val(p["situation"]["emergency_fund_months"]),
            "goals": goals, "notes": p["values"].get("notes", ""),
        },
    }


def _label(template: str, extras: dict) -> str:
    """Picker label; any figure in it is read from the portfolio file (`{NVDA}` = that weight in percent)."""
    return template.format(**{t.replace(".", "_"): f"{w:g}" for t, w in extras["weights_pct"].items()})


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


def build_whatif_fr(out: Path) -> dict[str, dict]:
    """Precompute the what-if (deploy/whatif.py: universe + scenarios, real engine, no LLM) for each profile directory REPORTS_FR
    names, once per directory. Files: reports/universe-<dir>.json and reports/whatif-<dir>.json; the legacy reports/universe.json
    and reports/whatif.json are those of the first review. Returns {dir name: {"universe": file, "scenarios": file}}."""
    import whatif  # deploy/whatif.py
    out.mkdir(parents=True, exist_ok=True)
    files: dict[str, dict] = {}
    for _, persona, _, _ in REPORTS_FR:
        if persona in files:
            continue
        uni, scen = f"universe-{persona}.json", f"whatif-{persona}.json"
        with tempfile.TemporaryDirectory() as tmp:
            whatif.build(Path(tmp), FIXTURES / persona)
            shutil.copyfile(Path(tmp) / "universe.json", out / uni)
            shutil.copyfile(Path(tmp) / "whatif.json", out / scen)
        files[persona] = {"universe": uni, "scenarios": scen}
    first = files[REPORTS_FR[0][1]]
    shutil.copyfile(out / first["universe"], out / "universe.json")
    shutil.copyfile(out / first["scenarios"], out / "whatif.json")
    return files


def build_whatif_en(out: Path, personas: list[Persona]) -> None:
    """Precompute the what-if for each persona. Files: reports/en-CA/universe-<profile_id>.json and whatif-<profile_id>.json."""
    import whatif  # deploy/whatif.py
    out.mkdir(parents=True, exist_ok=True)
    for persona in personas:
        uni, scen = f"universe-{persona.profile_id}.json", f"whatif-{persona.profile_id}.json"
        with tempfile.TemporaryDirectory() as tmp:
            whatif.build(Path(tmp), FIXTURES / persona.dir_name)
            shutil.copyfile(Path(tmp) / "universe.json", out / uni)
            shutil.copyfile(Path(tmp) / "whatif.json", out / scen)
    # Also create legacy unsuffixed files for the first persona (Robert)
    first_id = personas[0].profile_id
    shutil.copyfile(out / f"universe-{first_id}.json", out / "universe.json")
    shutil.copyfile(out / f"whatif-{first_id}.json", out / "whatif.json")


def build_reports_fr(out: Path, whatif_files: dict[str, dict]) -> list[dict]:
    """Build French reports for dashboard (index.html). Entry shape: {file, label, verdict, profile} - must match origin/loop/p0."""
    from coherence_analyzer import pipeline
    out.mkdir(parents=True, exist_ok=True)
    index = []
    for name, persona, pf_file, template in REPORTS_FR:
        rep = pipeline.review(FIXTURES / persona, pf_file, locale=FR_LOCALE)
        pers = load_persona(persona)
        extras = site_extras(FIXTURES / persona, pers.name, pf_file, whatif_files.get(persona))
        label = _label(template, extras)
        rep["site"] = {**extras, "label": label, "intake": missing_by_factor(persona)}
        (out / f"{name}.json").write_text(json.dumps(rep, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        index.append({"file": f"{name}.json", "label": label, "verdict": rep["verdict"],
                      "profile": rep.get("profile_coherence", {}).get("status")})
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    return index


def build_reports_en(out: Path, personas: list[Persona]) -> list[dict]:
    """Build English reports for app (app.html). Entry shape: {persona_id, name, file, label, portfolio, verdict, profile}."""
    from coherence_analyzer import pipeline
    out.mkdir(parents=True, exist_ok=True)
    index = []
    for persona in personas:
        for stem, pf_file, label_suffix in report_specs_en(persona):
            rep = pipeline.review(FIXTURES / persona.dir_name, pf_file, locale=EN_LOCALE)
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
    (out / INTAKE_FILE).write_text(json.dumps(intake_payload(EN_LOCALE), ensure_ascii=False), encoding="utf-8")
    return index


def build_flow_b(out: Path) -> Path:
    """Flow B demo report (Robert, NVDA 10 %, funding specific ZST.TO): `deploy/preview_report.py` `build()` into a temp dir,
    then ONLY `robert-nvda.json` is published, as its own file. It is not listed in reports/index.json (that index stays the
    four flow C reviews, tests/dashboard/test_ui_flow_b.py); the page loads it with loadFlowB()."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("preview_report_site", ROOT / "deploy" / "preview_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        paths = mod.build(Path(tmp))
        src = next(p for p in paths if p.stem == FLOW_B_NAME)
        dest = out / f"{FLOW_B_NAME}.json"
        doc = json.loads(src.read_text(encoding="utf-8"))
        doc["intake"] = missing_by_factor(FLOW_B_PROFILE)  # static demo: the missing list, read-only (same profile as the report)
        dest.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dest


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
            # French reports for dashboard (index.html) - must match origin/loop/p0 for frozen tests
            fr_files = build_whatif_fr(out / "reports")
            build_reports_fr(out / "reports", fr_files)
            build_flow_b(out / "reports")
            # English reports for app (app.html) - full shape with persona_id, name, etc.
            # Preserve order and deduplicate persona directories from REPORTS_FR
            seen = set()
            persona_dirs = []
            for _, dir_name, _, _ in REPORTS_FR:
                if dir_name not in seen:
                    seen.add(dir_name)
                    persona_dirs.append(dir_name)
            personas = [load_persona(dir_name) for dir_name in persona_dirs]
            build_whatif_en(out / "reports" / "en-CA", personas)
            build_reports_en(out / "reports" / "en-CA", personas)
    finally:
        if saved is None:
            os.environ.pop(STORE_DIR_ENV, None)
        else:
            os.environ[STORE_DIR_ENV] = saved
    return out


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))   # `import whatif` when run as a script from anywhere
    out = build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".cache" / "site")
    for p in sorted(out.rglob("*")):
        if p.is_file():
            print(p.relative_to(out), p.stat().st_size)
