"""Static site build: persona discovery, index entries, utf-8 JSON writes."""
import json
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
