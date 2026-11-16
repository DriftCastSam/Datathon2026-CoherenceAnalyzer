"""The Lambda-Function-URL HTTP Lambda (`coherence_analyzer/api_lambda_handler.py`): Mangum wrapping `api.app`
(mounted at `/api`) plus the `/site-api/*` display-layer routes (`deploy/site_api.py`) for the CloudFront-hosted
static site to call directly, instead of through `deploy/serve_local.py`'s uvicorn — same two route sets, same
paths, so the one `coherence-api-base` value works against either origin (see `api_lambda_handler.py`'s docstring).

No real AWS call anywhere: a synthetic Lambda Function URL event (the HTTP API v2 payload format a Function URL
actually sends, per Mangum's own `HTTPGateway.scope`: `version`, `requestContext.http.{method,path,sourceIp}`,
`rawQueryString`, `headers`, `body`, `isBase64Encoded`) is built by hand and passed straight to
`api_lambda_handler.handler(event, context)`. No boto3, no network, no running server. COHERENCE_ANALYZER_LLM=mock.

Routes exercised: `GET /api/health` (no path params, no body: proves the adapter's response shape — statusCode
200, JSON body matching `api.py`'s `/health` route exactly), `POST /api/profiles/{profile_id}/intake/skip` (a path
param baked into `rawPath`/`requestContext.http.path`, plus a JSON request body, to prove Mangum round-trips both a
path parameter and a JSON body through to the FastAPI route and back), a bare `/health` (no `/api` prefix) 404ing
to prove `api.app` is not reachable at the root, and `POST /site-api/profiles/{profile_id}/review` (the route
app.html's live mode actually calls) returning the real review plus the `site` display block.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

from coherence_analyzer import api_lambda_handler  # noqa: E402

ROBERT_ID = "persona-robert"


@pytest.fixture(autouse=True)
def store_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "store"
    d.mkdir()
    monkeypatch.setenv("COHERENCE_ANALYZER_STORE_DIR", str(d))
    monkeypatch.setenv("COHERENCE_ANALYZER_LLM", "mock")
    return d


def _event(method: str, path: str, body: dict | None = None, extra_headers: dict | None = None) -> dict:
    """A Lambda Function URL event (API Gateway HTTP API v2 payload format, per Mangum's `HTTPGateway.scope`:
    `requestContext.http.{method,path,sourceIp}` is what Mangum actually reads for routing, not `rawPath`)."""
    headers = {"host": "example.lambda-url.us-east-1.on.aws", **(extra_headers or {})}
    raw_body = None
    if body is not None:
        raw_body = json.dumps(body)
        headers["content-type"] = "application/json"
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": "",
        "headers": headers,
        "requestContext": {
            "http": {"method": method, "path": path, "sourceIp": "203.0.113.1"},
        },
        "body": raw_body,
        "isBase64Encoded": False,
    }


def test_handler_exports_a_plain_callable() -> None:
    assert callable(api_lambda_handler.handler)


def test_health_route_round_trips_through_mangum() -> None:
    response = api_lambda_handler.handler(_event("GET", "/api/health"), {})
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"ok": True}


def test_bare_root_health_is_not_mounted() -> None:
    """`api.app` is mounted at `/api`, not the root — matching `serve_local.py`'s layout so the same
    `coherence-api-base` value works against either origin (data_source.js's apiDir() always appends "api/")."""
    response = api_lambda_handler.handler(_event("GET", "/health"), {})
    assert response["statusCode"] == 404


def test_intake_skip_round_trips_path_param_and_json_body() -> None:
    """POST /api/profiles/{profile_id}/intake/skip: profile_id comes from the URL path, {"path": ...} from the
    JSON body. Both must reach the FastAPI route for the skip to land on the right field."""
    path = "identity.age"
    response = api_lambda_handler.handler(
        _event("POST", f"/api/profiles/{ROBERT_ID}/intake/skip", {"path": path}), {}
    )
    assert response["statusCode"] == 200, response["body"]
    profile = json.loads(response["body"])
    node = profile
    for part in path.split("."):
        node = node[part]
    assert node == {"value": None, "by": "user:skipped"}


def test_site_api_preflight_gets_a_cors_response_not_405() -> None:
    """A browser preflights any cross-origin POST with a JSON body (Content-Type: application/json is not a
    "simple" header) before sending it. `/site-api/*` routes live directly on `root_app`, which has no
    middleware of its own; without a CORSMiddleware scoped to them, Starlette's default OPTIONS handling
    returns a plain 405 and the browser never sends the real request (curl does not enforce CORS, so this
    bug was invisible to curl-based testing)."""
    response = api_lambda_handler.handler(
        _event(
            "OPTIONS", f"/site-api/profiles/{ROBERT_ID}/review",
            extra_headers={
                "origin": "https://example.com",
                "access-control-request-method": "POST",
                "access-control-request-headers": "content-type",
            },
        ),
        {},
    )
    assert response["statusCode"] < 400, response["body"]
    headers = response["headers"]
    assert headers.get("access-control-allow-origin") == "*"
    assert "POST" in headers.get("access-control-allow-methods", "")


def test_site_api_review_route_attaches_site_block() -> None:
    """POST /site-api/profiles/{profile_id}/review: the display-layer route app.html's live mode actually calls
    (not the bare /api/profiles/{id}/review) must be mounted on this same Lambda, with the `site` block attached."""
    response = api_lambda_handler.handler(
        _event("POST", f"/site-api/profiles/{ROBERT_ID}/review"), {}
    )
    assert response["statusCode"] == 200, response["body"]
    report = json.loads(response["body"])
    assert "site" in report
    assert "profile_card" in report["site"]
