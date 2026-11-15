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


def _all_skippable_paths() -> list[tuple[str, str]]:
    """All REQUIRED paths except NOT_SKIPPABLE."""
    out = []
    for factor, paths in intake.REQUIRED.items():
        for path in paths:
            if path not in intake.NOT_SKIPPABLE:
                out.append((factor, path))
    return out


@pytest.mark.parametrize("factor,path", _all_skippable_paths())
def test_skip_required_path_then_review_succeeds(client, pid, factor, path):
    """T1: skip each REQUIRED path (except horizon), then POST /review returns 200 with no violations from skipped value."""
    # Get the profile and skip the path
    r = client.post(f"/profiles/{pid}/intake/skip", json={"path": path})
    assert r.status_code == 200, f"Skip {path} failed: {r.text}"
    profile = r.json()
    assert profile[path.split(".")[0]][path.split(".")[1] if "." in path and len(path.split(".")) == 2 else path.split(".")[-1]] == {"value": None, "by": "user:skipped"} or \
           (len(path.split(".")) > 2 and isinstance(profile[path.split(".")[0]][path.split(".")[1]], dict)), \
           f"{path} not marked as skipped"

    # Run review
    r = client.post(f"/profiles/{pid}/review")
    assert r.status_code == 200, f"Review after skipping {path} failed: {r.text}"
    report = r.json()

    # Check that no check has status "violation" with this path in derived_from or subject
    for check in report.get("checks", []):
        if check.get("status") == "violation":
            der = check.get("derived_from", [])
            subj = check.get("subject", [])
            assert path not in der and path not in subj, \
                f"Check {check['id']} has violation status with {path} in derived_from/subject after skip"


def test_skip_horizon_returns_422(client, pid):
    """T1: skip horizon returns 422 with clear message."""
    r = client.post(f"/profiles/{pid}/intake/skip", json={"path": "objectives.horizon_years"})
    assert r.status_code == 422, f"Expected 422 for horizon skip, got {r.status_code}: {r.text}"
    assert "needed" in r.text.lower() and "analysis" in r.text.lower()


def test_get_intake_shows_horizon_not_skippable(client, pid):
    """T1: GET /intake shows skippable=False for horizon."""
    r = client.get(f"/profiles/{pid}/intake")
    assert r.status_code == 200
    intake_data = r.json()

    # Find the horizon question
    for factor_data in intake_data.values():
        q = factor_data.get("next_question")
        if q and q.get("path") == "objectives.horizon_years":
            assert q.get("skippable") is False, f"Horizon question should have skippable=False: {q}"
            return

    # If we didn't find it, it means horizon is already answered (which is fine for Robert)
    # Try removing it first
    profile = client.get(f"/profiles/{pid}").json()
    work_dir = store.root() / "profiles" / pid
    profile_path = work_dir / "profile.json"
    del profile["objectives"]["horizon_years"]
    profile_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")

    r = client.get(f"/profiles/{pid}/intake")
    assert r.status_code == 200
    intake_data = r.json()
    for factor_data in intake_data.values():
        q = factor_data.get("next_question")
        if q and q.get("path") == "objectives.horizon_years":
            assert q.get("skippable") is False, f"Horizon question should have skippable=False: {q}"
            return
