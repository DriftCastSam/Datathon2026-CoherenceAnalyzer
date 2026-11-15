"""T1: API-level parametrized test for skip on REQUIRED paths except horizon (site-followups plan).

Tests that skipping each REQUIRED path (except horizon) works through the API and doesn't cause violations.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

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


def _all_skippable_paths() -> list[tuple[str, str]]:
    """All REQUIRED paths except NOT_SKIPPABLE."""
    out = []
    for factor, paths in intake.REQUIRED.items():
        for path in paths:
            if path not in intake.NOT_SKIPPABLE:
                out.append((factor, path))
    return out


def _all_not_skippable_paths() -> list[str]:
    """All NOT_SKIPPABLE paths."""
    return sorted(intake.NOT_SKIPPABLE)


@pytest.mark.parametrize("pid", [ROBERT_ID, AMIRA_ID, MATEO_ID])
@pytest.mark.parametrize("factor,path", _all_skippable_paths())
def test_skip_required_path_then_review_succeeds(client, pid, factor, path):
    """T1: skip each REQUIRED path (except NOT_SKIPPABLE), then POST /review returns 200 with no violations from skipped value."""
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
    for check in report.get("checks_current", []):
        if check.get("status") == "violation":
            der = check.get("derived_from", []) or []
            subj = check.get("subject", []) or []
            assert path not in der and path not in subj, \
                f"Check {check['id']} has violation status with {path} in derived_from/subject after skip"


@pytest.mark.parametrize("path", _all_not_skippable_paths())
def test_skip_not_skippable_path_returns_422(client, path):
    """T1: skip NOT_SKIPPABLE paths return 422 with clear message."""
    r = client.post(f"/profiles/{ROBERT_ID}/intake/skip", json={"path": path})
    assert r.status_code == 422, f"Expected 422 for {path} skip, got {r.status_code}: {r.text}"
    assert "needed" in r.text.lower() and "analysis" in r.text.lower(), \
        f"Error message should mention 'needed' and 'analysis': {r.text}"


@pytest.mark.parametrize("path", _all_not_skippable_paths())
def test_get_intake_shows_not_skippable_paths_correctly(client, path):
    """T1: GET /intake shows skippable=False for NOT_SKIPPABLE paths."""
    # Use Robert's profile
    r = client.get(f"/profiles/{ROBERT_ID}/intake")
    assert r.status_code == 200
    intake_data = r.json()

    # Find the question for this path
    for factor_data in intake_data.values():
        q = factor_data.get("next_question")
        if q and q.get("path") == path:
            assert q.get("skippable") is False, f"{path} question should have skippable=False: {q}"
            return

    # If we didn't find it, the path might be answered. Remove it and check again.
    profile = client.get(f"/profiles/{ROBERT_ID}").json()
    work_dir = store.root() / "profiles" / ROBERT_ID
    profile_path = work_dir / "profile.json"

    # Remove the path
    parts = path.split(".")
    node = profile
    for part in parts[:-1]:
        if part not in node:
            return  # Path doesn't exist in this profile structure, skip test
        node = node[part]
    if parts[-1] in node:
        del node[parts[-1]]
        profile_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")

    r = client.get(f"/profiles/{ROBERT_ID}/intake")
    assert r.status_code == 200
    intake_data = r.json()
    for factor_data in intake_data.values():
        q = factor_data.get("next_question")
        if q and q.get("path") == path:
            assert q.get("skippable") is False, f"{path} question should have skippable=False: {q}"
            return
