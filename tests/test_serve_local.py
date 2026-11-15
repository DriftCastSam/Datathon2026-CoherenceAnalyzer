"""One-origin local server: static site at `/`, API at `/api` (offline, mock LLM)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from deploy.serve_local import create_app

INDEX_HTML = "<!doctype html><title>site</title><p>hello</p>"
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
