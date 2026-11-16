"""Static site build: persona discovery, utf-8 JSON writes, app.html layout."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))

import build_site  # noqa: E402
import whatif  # noqa: E402


def test_load_persona_returns_name_and_ids():
    p = build_site.load_persona("robert")
    assert p.dir_name == "robert"
    assert p.profile_id == "persona-robert"
    assert p.name == "Robert"


def test_site_extras_unwraps_skipped_profile_fields(tmp_path):
    fixture = build_site.FIXTURES / "robert"
    for name in ("profile.json", "portfolio.json"):
        shutil.copyfile(fixture / name, tmp_path / name)
    profile_path = tmp_path / "profile.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    skipped = {"value": None, "by": "user:skipped"}
    profile["situation"]["planned_withdrawals"] = skipped
    profile["objectives"]["goals"] = skipped
    profile_path.write_text(json.dumps(profile), encoding="utf-8")

    card = build_site.site_extras(tmp_path, "Robert", "portfolio.json")["profile_card"]

    assert card["first_withdrawal"] is None
    assert card["goals"] == []


def test_build_reports_en_produces_full_index_shape(tmp_path, monkeypatch):
    """English reports have persona_id, name, file, label, portfolio, verdict, profile in index entries."""
    personas = [build_site.load_persona("robert"), build_site.load_persona("amira")]
    out = tmp_path / "reports" / "en-CA"
    with tempfile.TemporaryDirectory() as store:
        monkeypatch.setenv(build_site.STORE_DIR_ENV, store)
        index = build_site.build_reports_en(out, personas)

    assert len(index) > 0
    entry = index[0]
    assert "persona_id" in entry
    assert "name" in entry
    assert "file" in entry
    assert "label" in entry
    assert "portfolio" in entry
    assert "verdict" in entry
    assert "profile" in entry
    assert entry["persona_id"] == "persona-robert"
    assert entry["name"] == "Robert"
    assert build_site.LABEL_SEPARATOR in entry["label"]
    assert "current portfolio" in entry["label"]

    rep_file = out / "robert-review.json"
    rep = json.loads(rep_file.read_text(encoding="utf-8"))
    headline = rep["explanation"]["headline"]
    assert "portfolio" in headline.lower()
    assert "portefeuille" not in headline.lower()
    assert rep["explanation"]["disclaimer"] == "Educational simulation with fictional profiles. This is not personalised financial advice. No transaction is executed."




def test_build_emits_app_html(tmp_path, monkeypatch):
    with tempfile.TemporaryDirectory() as store:
        monkeypatch.setenv(build_site.STORE_DIR_ENV, store)
        build_site.build(tmp_path)

    assert (tmp_path / "app.html").is_file(), "app.html (English app) should exist"
    assert not (tmp_path / "index.html").is_file(), "index.html (the retired French dashboard) should not exist"
    assert not (tmp_path / "legacy.html").is_file(), "legacy.html should not exist"

    # Check report directories
    assert (tmp_path / "reports" / "en-CA" / "index.json").is_file(), "English reports index"

    # Verify English reports (full shape with persona_id, name, etc.)
    en_index = json.loads((tmp_path / "reports" / "en-CA" / "index.json").read_text(encoding="utf-8"))
    assert len(en_index) > 0
    en_entry = en_index[0]
    assert "persona_id" in en_entry, "English index must have persona_id"
    assert "name" in en_entry, "English index must have name"
    assert "file" in en_entry
    assert "label" in en_entry
    assert "portfolio" in en_entry, "English index must have portfolio"
    assert "verdict" in en_entry
    assert en_entry["persona_id"] == "persona-robert"
    assert "current portfolio" in en_entry["label"]

    en_rep = json.loads((tmp_path / "reports" / "en-CA" / "robert-review.json").read_text(encoding="utf-8"))
    assert "portfolio" in en_rep["explanation"]["headline"].lower()
    assert en_rep["explanation"]["disclaimer"].startswith("Educational simulation")

    # Verify universe/whatif files exist for English app with persona_id filenames
    assert (tmp_path / "reports" / "en-CA" / "universe-persona-robert.json").is_file()
    assert (tmp_path / "reports" / "en-CA" / "whatif-persona-robert.json").is_file()
    assert (tmp_path / "reports" / "en-CA" / "universe-persona-amira.json").is_file()
    assert (tmp_path / "reports" / "en-CA" / "whatif-persona-amira.json").is_file()
    assert (tmp_path / "reports" / "en-CA" / "universe-persona-mateo.json").is_file()
    assert (tmp_path / "reports" / "en-CA" / "whatif-persona-mateo.json").is_file()

    # Verify intake.json exists for English
    assert (tmp_path / "reports" / "en-CA" / "intake.json").is_file()

    app_html = (tmp_path / "app.html").read_text(encoding="utf-8")
    assert 'lang="en-CA"' in app_html
    assert "Compass" in app_html


def test_build_default_ships_empty_api_base(tmp_path, monkeypatch):
    """Without --api-base, the built app.html keeps the empty meta tag (today's behavior, pinned elsewhere
    by tests/dashboard/test_ui_flow_b.py et al. against the SOURCE file) — and the source file is untouched."""
    source_before = (build_site.ROOT / "deploy" / "site" / "app.html").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as store:
        monkeypatch.setenv(build_site.STORE_DIR_ENV, store)
        build_site.build(tmp_path)
    app_html = (tmp_path / "app.html").read_text(encoding="utf-8")
    assert '<meta name="coherence-api-base" content="">' in app_html
    assert (build_site.ROOT / "deploy" / "site" / "app.html").read_text(encoding="utf-8") == source_before


def test_build_with_api_base_sets_built_meta_tag_only(tmp_path, monkeypatch):
    """--api-base rewrites the BUILT copy's meta tag content (normalized to end with one slash) and never
    touches deploy/site/app.html, the source file other tests pin empty."""
    source_before = (build_site.ROOT / "deploy" / "site" / "app.html").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as store:
        monkeypatch.setenv(build_site.STORE_DIR_ENV, store)
        build_site.build(tmp_path, api_base="https://example.lambda-url.us-east-1.on.aws")
    app_html = (tmp_path / "app.html").read_text(encoding="utf-8")
    assert '<meta name="coherence-api-base" content="https://example.lambda-url.us-east-1.on.aws/">' in app_html
    assert (build_site.ROOT / "deploy" / "site" / "app.html").read_text(encoding="utf-8") == source_before


def test_whatif_respects_locale_parameter(tmp_path):
    """Whatif build with en-CA locale produces English labels."""
    # Build English what-if for Robert
    whatif.build(tmp_path, build_site.FIXTURES / "robert", locale="en-CA")

    uni = json.loads((tmp_path / "universe.json").read_text(encoding="utf-8"))
    base = uni["base"]

    # Check that funding labels are English
    assert any("Pro rata from all holdings" in f["label"] for f in base["fundings"])

    # Build French what-if for comparison
    tmp_fr = tmp_path / "fr"
    tmp_fr.mkdir()
    whatif.build(tmp_fr, build_site.FIXTURES / "robert", locale="fr-CA")

    uni_fr = json.loads((tmp_fr / "universe.json").read_text(encoding="utf-8"))
    base_fr = uni_fr["base"]

    # Check that funding labels are French
    assert any("Au prorata de toutes les positions" in f["label"] for f in base_fr["fundings"])


def test_build_site_main_rejects_dash_prefix(tmp_path, monkeypatch, capsys):
    """__main__ refuses arguments starting with dash."""
    result = subprocess.run(
        [sys.executable, "-m", "deploy.build_site", "--help"],
        cwd=str(build_site.ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "usage:" in result.stderr.lower()


def test_build_site_main_rejects_root_as_out_dir(tmp_path, monkeypatch):
    """__main__ refuses ROOT or ancestor of ROOT as out directory."""
    # Try to build into ROOT itself
    result = subprocess.run(
        [sys.executable, "-m", "deploy.build_site", str(build_site.ROOT)],
        cwd=str(build_site.ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "ROOT or ancestor" in result.stderr

    # Try to build into parent of ROOT
    result = subprocess.run(
        [sys.executable, "-m", "deploy.build_site", str(build_site.ROOT.parent)],
        cwd=str(build_site.ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "ROOT or ancestor" in result.stderr
