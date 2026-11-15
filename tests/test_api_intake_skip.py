"""T1: API-level parametrized test for skip on REQUIRED paths (loop/p0: all skippable).

Tests that skipping any REQUIRED path works through the API and doesn't cause violations.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

from coherence_analyzer import api, intake, store  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ROBERT_ID = "persona-robert"
AMIRA_ID = "persona-amira"
MATEO_ID = "persona-mateo"


@pytest.fixture(autouse=True)
def store_dir(tmp_path, monkeypatch):
    d = tmp_path / "store"
    d.mkdir()
    monkeypatch.setenv("COHERENCE_ANALYZER_STORE_DIR", str(d))
    monkeypatch.setenv("COHERENCE_ANALYZER_LLM", "mock")
    return d


@pytest.fixture()
def client():
    return TestClient(api.app)


@pytest.fixture()
def pid():
    return ROBERT_ID


def _get_path(profile: dict, path: str) -> Any:
    """Navigate a dotted path in the profile, returning None if not found."""
    node = profile
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _all_required_paths() -> list[tuple[str, str]]:
    """All REQUIRED paths (loop/p0: all are now skippable)."""
    out = []
    for factor, paths in intake.REQUIRED.items():
        for path in paths:
            out.append((factor, path))
    return out


@pytest.mark.parametrize("pid", [ROBERT_ID, AMIRA_ID, MATEO_ID])
@pytest.mark.parametrize("factor,path", _all_required_paths())
def test_skip_any_required_path_then_review_succeeds(client, pid, factor, path):
    """loop/p0: skip any REQUIRED path, then POST /review returns 200 with unknown checks for skipped value."""
    # Get the profile and skip the path
    r = client.post(f"/profiles/{pid}/intake/skip", json={"path": path})
    assert r.status_code == 200, f"Skip {path} failed: {r.text}"
    profile = r.json()
    node = _get_path(profile, path)
    assert node == {"value": None, "by": "user:skipped"}, f"{path} not marked as skipped: {node}"

    # Run review
    r = client.post(f"/profiles/{pid}/review")
    assert r.status_code == 200, f"Review after skipping {path} failed: {r.text}"
    report = r.json()

    # Check that no check has status "violation" with this path in derived_from or subject
    # Skipped paths should result in "unknown" status, not "violation"
    for check in report.get("checks_current", []):
        if check.get("status") == "violation":
            der = check.get("derived_from", []) or []
            subj = check.get("subject", []) or []
            assert path not in der and path not in subj, \
                f"Check {check['id']} has violation status with {path} in derived_from/subject after skip"


def test_get_intake_shows_no_skippable_field(client):
    """loop/p0: GET /intake no longer returns skippable field in questions."""
    r = client.get(f"/profiles/{ROBERT_ID}/intake")
    assert r.status_code == 200
    intake_data = r.json()

    for factor_data in intake_data.values():
        q = factor_data.get("next_question")
        if q:
            assert "skippable" not in q, f"Question should not have skippable field: {q}"
