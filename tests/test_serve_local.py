"""One-origin local server: static site at `/`, API at `/api` (offline, mock LLM)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from deploy.serve_local import create_app

INDEX_HTML = "<!doctype html><title>site</title><p>hello</p>"
SITE_REVIEW_URL = "/site-api/profiles"
EDITED_RISK = "high"
SKIPPABLE_PATH = "risk.reaction_to_20pct_drop"
PERSONA_IDS = {"persona-robert", "persona-amira", "persona-mateo"}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    monkeypatch.setenv("COHERENCE_ANALYZER_STORE_DIR", str(tmp_path / "store"))
    return TestClient(create_app(site_dir))


def test_api_health_is_mounted_under_api(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_root_serves_index_html(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.text == INDEX_HTML


def test_api_lists_three_fixture_personas(client: TestClient) -> None:
    response = client.get("/api/profiles")

    assert response.status_code == 200
    assert {item["profile_id"] for item in response.json()} == PERSONA_IDS


def test_unknown_static_path_returns_404(client: TestClient) -> None:
    assert client.get("/missing.html").status_code == 404


def test_site_review_attaches_limits_and_profile_card(client: TestClient) -> None:
    response = client.post(f"{SITE_REVIEW_URL}/persona-robert/review")

    assert response.status_code == 200
    site = response.json()["site"]
    assert site["limits"]
    assert site["profile_card"]["name"] == "Robert"
    assert site["label"] == "Robert · live analysis"


def test_site_review_reflects_edited_risk_answer(client: TestClient) -> None:
    answer = client.post(
        "/api/profiles/persona-robert/intake/answer",
        json={"path": "risk.declared", "value": EDITED_RISK},
    )
    assert answer.status_code == 200

    response = client.post(f"{SITE_REVIEW_URL}/persona-robert/review")

    card = response.json()["site"]["profile_card"]
    assert card["risk_declared"] == EDITED_RISK


def test_site_review_unknown_profile_returns_404(client: TestClient) -> None:
    assert client.post(f"{SITE_REVIEW_URL}/persona-nobody/review").status_code == 404


def test_site_review_survives_skipped_answer(client: TestClient) -> None:
    skip = client.post("/api/profiles/persona-robert/intake/skip", json={"path": SKIPPABLE_PATH})
    assert skip.status_code == 200

    response = client.post(f"{SITE_REVIEW_URL}/persona-robert/review")

    assert response.status_code == 200
    card = response.json()["site"]["profile_card"]
    assert not any(isinstance(value, dict) and "value" in value for value in card.values())


def test_intake_skip_allows_skippable_required_paths(client: TestClient) -> None:
    response = client.post(
        "/api/profiles/persona-robert/intake/skip", json={"path": "situation.planned_withdrawals"}
    )

    assert response.status_code == 200


def test_intake_skip_rejects_horizon(client: TestClient) -> None:
    response = client.post(
        "/api/profiles/persona-robert/intake/skip", json={"path": "objectives.horizon_years"}
    )

    assert response.status_code == 422
    assert "needed" in response.text.lower() and "analysis" in response.text.lower()


def test_intake_answer_rejects_non_intake_path(client: TestClient) -> None:
    response = client.post(
        "/api/profiles/persona-robert/intake/answer", json={"path": "identity.name", "value": "x"}
    )

    assert response.status_code == 422
