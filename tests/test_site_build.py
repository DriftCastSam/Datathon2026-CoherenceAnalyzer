"""Static site build: persona discovery, utf-8 JSON writes, app.html layout."""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))

import build_site  # noqa: E402


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


def test_build_reports_produces_english_headlines(tmp_path, monkeypatch):
    out = tmp_path / "reports"
    with tempfile.TemporaryDirectory() as store:
        monkeypatch.setenv(build_site.STORE_DIR_ENV, store)
        index = build_site.build_reports(out)

    rep_file = out / "robert-review.json"
    rep = json.loads(rep_file.read_text(encoding="utf-8"))
    headline = rep["explanation"]["headline"]
    assert "portfolio" in headline.lower()
    assert "portefeuille" not in headline.lower()
    assert rep["explanation"]["disclaimer"] == "Educational simulation with fictional profiles. This is not personalised financial advice. No transaction is executed."


def test_build_emits_app_html_and_index_html(tmp_path, monkeypatch):
    with tempfile.TemporaryDirectory() as store:
        monkeypatch.setenv(build_site.STORE_DIR_ENV, store)
        build_site.build(tmp_path)

    assert (tmp_path / "app.html").is_file(), "app.html (English app) should exist"
    assert (tmp_path / "index.html").is_file(), "index.html (French dashboard) should exist"
    assert not (tmp_path / "legacy.html").is_file(), "legacy.html should not exist"

    app_html = (tmp_path / "app.html").read_text(encoding="utf-8")
    assert 'lang="en-CA"' in app_html
    assert "Profile Coherence" in app_html

    index_html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'lang="fr"' in index_html
