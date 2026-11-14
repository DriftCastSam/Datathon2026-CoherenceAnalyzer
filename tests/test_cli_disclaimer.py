"""P2.CLI19 oracle: `cli review` prints the fixed disclaimer; README documents every SPEC §19 limitation.

SPEC §1 invariant 7 and §9.4 (every user-facing output carries the fixed disclaimer, text from `config.DISCLAIMER`,
never retyped, never LLM text), §12 CLI (`review` is a headline command), §19 (known limitations: document them),
docs/spec-decisions.md D10 (describe, never prescribe). Offline: LLM=mock, no network, nothing written to the repo
(each run happens in a temp cwd on a copy of the fixture).
"""
import contextlib
import inspect
import io
import os
import re
import shutil
from pathlib import Path

import pytest

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

from coherence_analyzer import cli, config  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
README = ROOT / "README.md"

# SPEC §9.4 verbatim; the tests compare against config.DISCLAIMER and against these literals.
FR = ("Simulation éducative avec des profils fictifs. Ceci n'est pas un conseil financier personnalisé. "
      "Aucune transaction n'est exécutée.")
EN = ("Educational simulation with fictional profiles. This is not personalised financial advice. "
      "No transaction is executed.")
ADVISORY = ("tu devrais", "vous devriez", "je recommande", "nous recommandons", "you should", "we recommend",
            "i recommend", "next step", "prochaine étape")


def _fake_report(disclaimer: str) -> dict:
    return {
        "verdict": "coherent", "explanation": {"mode": "template"}, "run_id": "20260101T000000Z-test",
        "sections": [{"factor": "goals", "status": "pass"}, {"factor": "risk", "status": "warning"}],
        "unknowns": [{"id": "H6"}, {"id": "H11"}, {"id": "H6"}],
        "profile_coherence": {"status": "pass", "findings": []},
        "disclaimer": disclaimer,
    }


def _capture(fn, *args) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        fn(*args)
    return out.getvalue()


def _last_line(text: str) -> str:
    return [ln for ln in text.splitlines() if ln.strip()][-1]


@pytest.fixture
def review_run(tmp_path, monkeypatch):
    """`cli.main(["review", <copy of fixtures/robert>])` in a temp cwd; returns stdout."""
    monkeypatch.setenv("COHERENCE_ANALYZER_LLM", "mock")
    monkeypatch.setenv("COHERENCE_ANALYZER_STORE", "local")
    monkeypatch.delenv("COHERENCE_ANALYZER_STORE_DIR", raising=False)
    fx = tmp_path / "fixtures" / "robert"
    shutil.copytree(FIXTURES / "robert", fx)
    cwd = tmp_path / "work"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    return _capture(cli.main, ["review", str(fx)])


# ------------------------------------------------------------------ the disclaimer on `review`

def test_config_disclaimer_is_the_spec_text():
    # SPEC §9.4: both locales verbatim (guards the source the CLI must use).
    assert config.DISCLAIMER["fr-CA"] == FR
    assert config.DISCLAIMER["en-CA"] == EN


def test_print_review_prints_the_fixed_disclaimer_once_and_last():
    # SPEC §1 inv. 7 / §9.4: every user-facing output carries the disclaimer; it closes the output, as in `check`.
    out = _capture(cli._print_review, "robert", _fake_report(config.DISCLAIMER["fr-CA"]))
    assert out.count(config.DISCLAIMER["fr-CA"]) == 1
    assert _last_line(out) == config.DISCLAIMER["fr-CA"]


def test_print_review_follows_the_report_locale():
    # SPEC-AMBIGUITY: §9.4 gives FR and EN; stricter reading: the CLI prints the text of the report's own locale
    # (the text the report carries from config.DISCLAIMER), never a hard-coded FR one.
    out = _capture(cli._print_review, "robert", _fake_report(config.DISCLAIMER["en-CA"]))
    assert _last_line(out) == config.DISCLAIMER["en-CA"]
    assert config.DISCLAIMER["fr-CA"] not in out


def test_print_review_keeps_its_existing_lines():
    # The disclaimer is added, nothing else is lost (label/verdict, sections, unknowns, run_id, profile line).
    rep = _fake_report(config.DISCLAIMER["fr-CA"])
    out = _capture(cli._print_review, "robert", rep)
    assert out.startswith("robert: coherent")
    assert "goals=pass, risk=warning" in out
    assert "cannot verify: H11, H6" in out
    assert f"run_id {rep['run_id']}" in out
    assert cli.profile_line(rep["profile_coherence"]) in out


def test_print_review_does_not_retype_the_disclaimer():
    # CLAUDE.md / §9.4: the text comes from config.py. No literal copy of either locale in the function source.
    src = inspect.getsource(cli._print_review)
    assert "Simulation" not in src and "Educational" not in src and "transaction" not in src
    assert "disclaimer" in src.lower() or "DISCLAIMER" in src


def test_cli_review_command_prints_the_disclaimer(review_run):
    # SPEC §12 CLI: `review fixtures/robert` end to end (mock LLM). Robert's profile locale is fr-CA.
    assert config.DISCLAIMER["fr-CA"] in review_run
    assert _last_line(review_run) == config.DISCLAIMER["fr-CA"]


def test_cli_review_output_describes_and_never_prescribes(review_run):
    # D10 / §1 inv. 7: nothing in the review output advises; the disclaimer says no advice and no transaction.
    low = review_run.lower()
    for phrase in ADVISORY:
        assert phrase not in low


def test_demo_still_prints_the_disclaimer(tmp_path, monkeypatch):
    # Regression: `demo` printed it before this task and must keep doing so.
    monkeypatch.setenv("COHERENCE_ANALYZER_LLM", "mock")
    monkeypatch.setenv("COHERENCE_ANALYZER_STORE", "local")
    monkeypatch.delenv("COHERENCE_ANALYZER_STORE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    fx = tmp_path / "fx"
    fx.mkdir()
    shutil.copytree(FIXTURES / "robert", fx / "robert")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.demo(fx)
    assert config.DISCLAIMER["fr-CA"] in out.getvalue()
    assert _last_line(out.getvalue()) == config.DISCLAIMER["fr-CA"]


# ------------------------------------------------------------------ README "Known limitations" (SPEC §19)

def _limitations_section() -> str:
    text = README.read_text(encoding="utf-8")
    m = re.search(r"^(#{2,3})\s+Known limitations\b.*$", text, flags=re.MULTILINE | re.IGNORECASE)
    assert m, "README.md needs a 'Known limitations' heading (SPEC §19: document them)"
    level = len(m.group(1))
    rest = text[m.end():]
    nxt = re.search(r"^#{1,%d}\s" % level, rest, flags=re.MULTILINE)
    return rest[:nxt.start()] if nxt else rest


def test_readme_has_a_known_limitations_section_with_a_body():
    assert len(_limitations_section().strip()) > 400


def test_readme_limitation_window_bootstrap_and_assumed_drift():
    # SPEC §19 bullet 1: 5-year window; block bootstrap reuses that history; drift is a team assumption (ASSUMED).
    s = _limitations_section()
    low = s.lower()
    assert re.search(r"5[- ]year", low)
    assert "block bootstrap" in low
    assert "drift" in low
    assert "ASSUMED" in s
    assert "forecast" in low


def test_readme_limitation_survivorship_bias():
    # SPEC §19 bullet 2.
    assert "survivorship" in _limitations_section().lower()


def test_readme_limitation_lookthrough_unknowns_and_what_it_covers_now():
    # SPEC §19 bullet 3 + README "ETF sector look-through (P2.13)": without it sector / employer / activity checks
    # on ETFs are unknown; the section must also say what look-through covers now (sector + employer H6 and H10 from
    # data/meta/etf_sector_weights.csv; H11 activity stays unknown without a sourced activity_tags row).
    s = _limitations_section()
    low = s.lower()
    assert "look-through" in low
    for word in ("sector", "employer", "activity", "unknown"):
        assert word in low
    assert "etf_sector_weights" in s
    assert "H6" in s and "H10" in s
    assert "H11" in s
    assert "activity_tags" in s


def test_readme_limitation_behaviour_classification():
    # SPEC §19 bullet 4: noisy, confidence capped at 0.8, needs >= 2 events, a warning and never a block.
    s = _limitations_section()
    low = s.lower()
    assert "noisy" in low
    assert "0.8" in s
    assert re.search(r"(≥|>=|at least)\s*2\s+events", low)
    assert "warning" in low
    assert re.search(r"never (a )?block", low)


def test_readme_limitation_amf_ciro_kyc_out_of_scope():
    # SPEC §19 bullet 5 (and the UI note in deploy/site/index.html): team calibrations, not regulation.
    s = _limitations_section()
    low = s.lower()
    assert "AMF" in s and "CIRO" in s and "KYC" in s
    assert "calibration" in low
    assert "out of scope" in low
    assert "regulation" in low


def test_readme_limitations_are_descriptive_not_advisory():
    # D10: the section describes limits of the tool; it gives no advice.
    low = _limitations_section().lower()
    for phrase in ADVISORY:
        assert phrase not in low


def test_readme_disclaimer_section_is_kept():
    # README keeps the fixed EN disclaimer (SPEC §9.4); the new section must not displace it.
    assert EN in README.read_text(encoding="utf-8")
