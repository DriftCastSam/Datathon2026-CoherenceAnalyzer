"""Static site build: persona discovery, index entries, utf-8 JSON writes."""
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))

import build_site  # noqa: E402

EXPECTED_IDS = ["persona-robert", "persona-amira", "persona-mateo"]
EXPECTED_LABELS = [
    "Robert · current portfolio",
    "Robert · portfolio with NVDA at 10%",
    "Amira · current portfolio",
    "Mateo · current portfolio",
]
EXPECTED_STEMS = ["robert-review", "robert-review-nvda", "amira-review", "mateo-review"]


def _all_specs():
    return [s for p in build_site.discover_personas() for s in build_site.report_specs(p)]


def test_discovers_three_personas_with_profile_ids():
    personas = build_site.discover_personas()
    assert [p.persona_id for p in personas] == EXPECTED_IDS
    assert [p.name for p in personas] == ["Robert", "Amira", "Mateo"]


def test_report_specs_have_english_labels_and_stems():
    specs = _all_specs()
    assert [s.label for s in specs] == EXPECTED_LABELS
    assert [s.stem for s in specs] == EXPECTED_STEMS


def test_index_entry_keeps_legacy_fields():
    spec = _all_specs()[1]
    rep = {"verdict": "warning", "profile_coherence": {"status": "pass"}}
    entry = build_site.index_entry(spec, rep)
    assert entry == {
        "persona_id": "persona-robert", "name": "Robert",
        "file": "robert-review-nvda.json",
        "label": "Robert · portfolio with NVDA at 10%",
        "portfolio": "portfolio_review.json",
        "verdict": "warning", "profile": "pass",
    }


def test_write_json_round_trips_utf8(tmp_path):
    target = tmp_path / "label.json"
    build_site.write_json(target, {"label": "Robert · current"})
    assert "·".encode("utf-8") in target.read_bytes()
    assert json.loads(target.read_text(encoding="utf-8")) == {"label": "Robert · current"}


def test_intake_payload_covers_required_paths_in_order():
    from coherence_analyzer import intake

    payload = build_site.intake_payload()

    assert payload["skipped_by"] == intake.SKIPPED_BY
    expected = [(f, p) for f, paths in intake.REQUIRED.items() for p in paths]
    assert [(q["factor"], q["path"]) for q in payload["questions"]] == expected
    assert all(q["text"] for q in payload["questions"])


def test_intake_payload_marks_horizon_not_skippable():
    from coherence_analyzer import intake

    payload = build_site.intake_payload()
    questions_by_path = {q["path"]: q for q in payload["questions"]}

    assert not questions_by_path["objectives.horizon_years"]["skippable"]
    for path in questions_by_path:
        if path != "objectives.horizon_years":
            assert questions_by_path[path]["skippable"]


def test_store_dir_env_restores_previous_value(monkeypatch):
    monkeypatch.setenv(build_site.STORE_DIR_ENV, "before")
    with build_site.store_dir_env("temp"):
        assert os.environ[build_site.STORE_DIR_ENV] == "temp"
    assert os.environ[build_site.STORE_DIR_ENV] == "before"


def test_store_dir_env_removes_key_when_absent_even_on_error(monkeypatch):
    monkeypatch.delenv(build_site.STORE_DIR_ENV, raising=False)
    try:
        with build_site.store_dir_env("temp"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert build_site.STORE_DIR_ENV not in os.environ


def test_site_extras_unwraps_skipped_profile_fields(tmp_path):
    fixture = build_site.FIXTURES_DIR / "robert"
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
    import tempfile
    from coherence_analyzer import pipeline

    out = tmp_path / "reports"
    personas = build_site.discover_personas()
    with tempfile.TemporaryDirectory() as store:
        monkeypatch.setenv(build_site.STORE_DIR_ENV, store)
        index = build_site.build_reports(out, personas[:1])

    rep_file = out / f"{index[0]['file']}"
    rep = json.loads(rep_file.read_text(encoding="utf-8"))
    headline = rep["explanation"]["headline"]
    assert "portfolio" in headline.lower()
    assert "portefeuille" not in headline.lower()
    assert rep["explanation"]["disclaimer"] == "Educational simulation with fictional profiles. This is not personalised financial advice. No transaction is executed."
