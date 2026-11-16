"""One-origin local server: static site at `/`, API at `/api` (offline, mock LLM)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from deploy.serve_local import create_app

INDEX_HTML = "<!doctype html><title>dashboard</title><p>dashboard</p>"
APP_HTML = "<!doctype html><title>app</title><p>app</p>"
SITE_REVIEW_URL = "/site-api/profiles"
EDITED_RISK = "high"
SKIPPABLE_PATH = "preferences.prefers_dividends"
PERSONA_IDS = {"persona-robert", "persona-amira", "persona-mateo"}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (site_dir / "app.html").write_text(APP_HTML, encoding="utf-8")
    monkeypatch.setenv("COHERENCE_ANALYZER_STORE_DIR", str(tmp_path / "store"))
    return TestClient(create_app(site_dir))


def test_api_health_is_mounted_under_api(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_root_redirects_to_app_html(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/app.html"


def test_root_redirect_resolves_to_app_html(client: TestClient) -> None:
    response = client.get("/", follow_redirects=True)

    assert response.status_code == 200
    assert response.text == APP_HTML


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


@pytest.mark.parametrize("path", [
    "preferences.prefers_dividends",
    "objectives.horizon_years",
    "risk.declared",
    "identity.age",
])
def test_site_review_survives_skipped_answer(client: TestClient, path: str) -> None:
    skip = client.post("/api/profiles/persona-robert/intake/skip", json={"path": path})
    assert skip.status_code == 200

    response = client.post(f"{SITE_REVIEW_URL}/persona-robert/review")

    assert response.status_code == 200
    card = response.json()["site"]["profile_card"]
    assert not any(isinstance(value, dict) and "value" in value for value in card.values())


def test_intake_skip_allows_any_required_path(client: TestClient) -> None:
    # loop/p0: all required paths including horizon are now skippable
    for path in ("preferences.prefers_dividends", "objectives.horizon_years", "risk.declared"):
        response = client.post("/api/profiles/persona-robert/intake/skip", json={"path": path})
        assert response.status_code == 200, f"Skip {path} should succeed"


def test_intake_answer_rejects_non_intake_path(client: TestClient) -> None:
    response = client.post(
        "/api/profiles/persona-robert/intake/answer", json={"path": "identity.name", "value": "x"}
    )

    assert response.status_code == 422


def test_api_review_with_locale_query_param_returns_english(client: TestClient) -> None:
    response = client.post("/api/profiles/persona-robert/review?locale=en-CA")

    assert response.status_code == 200
    headline = response.json()["explanation"]["headline"]
    assert "portfolio" in headline.lower()
    assert "portefeuille" not in headline.lower()


def test_site_review_produces_english_headline(client: TestClient) -> None:
    response = client.post(f"{SITE_REVIEW_URL}/persona-robert/review")

    assert response.status_code == 200
    headline = response.json()["explanation"]["headline"]
    assert "portfolio" in headline.lower()
    assert "portefeuille" not in headline.lower()


def test_whatif_route_returns_universe_and_scenarios(client: TestClient) -> None:
    response = client.post(
        f"{SITE_REVIEW_URL}/persona-robert/whatif", json={"tickers": ["AAPL"]}
    )

    assert response.status_code == 200
    data = response.json()
    assert "universe" in data
    assert "whatif" in data
    assert "assets" in data["universe"]
    assert "base" in data["universe"]
    assert "AAPL" in data["whatif"]


def test_whatif_route_rejects_empty_ticker_list(client: TestClient) -> None:
    response = client.post(
        f"{SITE_REVIEW_URL}/persona-robert/whatif", json={"tickers": []}
    )

    assert response.status_code == 422


def test_whatif_route_rejects_too_many_tickers(client: TestClient) -> None:
    from deploy.site_api import MAX_WHATIF_TICKERS

    tickers = [f"TICK{i}" for i in range(MAX_WHATIF_TICKERS + 1)]
    response = client.post(
        f"{SITE_REVIEW_URL}/persona-robert/whatif", json={"tickers": tickers}
    )

    assert response.status_code == 422


def test_whatif_route_rejects_unknown_ticker(client: TestClient) -> None:
    response = client.post(
        f"{SITE_REVIEW_URL}/persona-robert/whatif", json={"tickers": ["ZZZZZ"]}
    )

    assert response.status_code == 422
    assert "not in the universe" in response.text.lower()


def test_whatif_route_unknown_profile_returns_404(client: TestClient) -> None:
    response = client.post(
        f"{SITE_REVIEW_URL}/persona-nobody/whatif", json={"tickers": ["AAPL"]}
    )

    assert response.status_code == 404


def test_whatif_route_reflects_edited_risk_answer(client: TestClient) -> None:
    # Get the original what-if with Robert's fixture profile
    response_original = client.post(
        f"{SITE_REVIEW_URL}/persona-robert/whatif", json={"tickers": ["AAPL"]}
    )
    assert response_original.status_code == 200
    original_whatif = response_original.json()
    original_base = original_whatif["universe"]["base"]

    # Change risk level in the working copy to "low" (Robert's fixture is "moderate_high")
    answer = client.post(
        "/api/profiles/persona-robert/intake/answer",
        json={"path": "risk.declared", "value": "low"},
    )
    assert answer.status_code == 200

    # Get the edited what-if
    response_edited = client.post(
        f"{SITE_REVIEW_URL}/persona-robert/whatif", json={"tickers": ["AAPL"]}
    )
    assert response_edited.status_code == 200
    edited_whatif = response_edited.json()
    edited_base = edited_whatif["universe"]["base"]

    # The checks dictionary should differ (risk.declared affects limits and checks)
    # At minimum, the risk-related checks should be different
    assert edited_base["checks"] != original_base["checks"]


def test_whatif_route_rejects_invalid_ticker_without_building_context(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from deploy import whatif

    def fail_if_called(*args, **kwargs):
        raise AssertionError("whatif.context should not be called for invalid ticker")

    monkeypatch.setattr(whatif, "context", fail_if_called)

    response = client.post(
        f"{SITE_REVIEW_URL}/persona-robert/whatif", json={"tickers": ["INVALID_TICKER"]}
    )

    assert response.status_code == 422
    assert "not in the universe" in response.text.lower()


def test_whatif_route_returns_422_for_invalid_profile(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coherence_analyzer import pipeline

    original_load = pipeline._load_profile

    def raise_on_load(fixture_dir):
        result = original_load(fixture_dir)
        raise ValueError("profile validation failed: invalid field")

    monkeypatch.setattr(pipeline, "_load_profile", raise_on_load)

    response = client.post(
        f"{SITE_REVIEW_URL}/persona-robert/whatif", json={"tickers": ["AAPL"]}
    )

    assert response.status_code == 422
    assert "profile validation failed" in response.text


def test_whatif_route_survives_skipped_horizon(client: TestClient) -> None:
    skip = client.post("/api/profiles/persona-robert/intake/skip", json={"path": "objectives.horizon_years"})
    assert skip.status_code == 200

    response = client.post(
        f"{SITE_REVIEW_URL}/persona-robert/whatif", json={"tickers": ["AAPL"]}
    )

    assert response.status_code == 200
    data = response.json()
    assert "universe" in data
    assert "whatif" in data


AMOUNT_URL = f"{SITE_REVIEW_URL}/persona-robert/whatif/amount"


def test_whatif_amount_route_returns_one_scenario(client: TestClient) -> None:
    response = client.post(AMOUNT_URL, json={"ticker": "AAPL", "amount_cad": 2500, "funding": "cash"})
    assert response.status_code == 200, response.text
    scenario = response.json()["scenario"]
    assert {"verdict", "metrics", "weights_pct", "value_cad", "sections"} <= set(scenario)


@pytest.mark.parametrize("body", [
    {"ticker": "AAPL", "amount_cad": 0, "funding": "cash"},
    {"ticker": "AAPL", "amount_cad": 100, "funding": "specific:NOPE"},
    {"ticker": "NOT_A_TICKER", "amount_cad": 100, "funding": "cash"},
    {"ticker": "AAPL", "amount_cad": 1e8, "funding": "pro_rata_all"},
])
def test_whatif_amount_route_rejects_bad_input(client: TestClient, body: dict) -> None:
    assert client.post(AMOUNT_URL, json=body).status_code == 422


def test_whatif_amount_route_unknown_profile_returns_404(client: TestClient) -> None:
    response = client.post(f"{SITE_REVIEW_URL}/persona-nobody/whatif/amount",
                           json={"ticker": "AAPL", "amount_cad": 100, "funding": "cash"})
    assert response.status_code == 404
