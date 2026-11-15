"""The Lambda-Function-URL HTTP Lambda (`coherence_analyzer/api_lambda_handler.py`): Mangum wrapping the real FastAPI
app (`coherence_analyzer.api.app`) for the CloudFront-hosted static site to call directly, instead of through
`deploy/serve_local.py`'s uvicorn.

No real AWS call anywhere: a synthetic Lambda Function URL event (the HTTP API v2 payload format a Function URL
actually sends, per Mangum's own `HTTPGateway.scope`: `version`, `requestContext.http.{method,path,sourceIp}`,
`rawQueryString`, `headers`, `body`, `isBase64Encoded`) is built by hand and passed straight to
`api_lambda_handler.handler(event, context)`. No boto3, no network, no running server. COHERENCE_ANALYZER_LLM=mock.

Two routes are exercised: `GET /health` (no path params, no body: proves the adapter's response shape — statusCode
200, JSON body matching `api.py`'s `/health` route exactly) and `POST /profiles/{profile_id}/intake/skip` (a path
param baked into `rawPath`/`requestContext.http.path`, plus a JSON request body) to prove Mangum round-trips both a
path parameter and a JSON body through to the FastAPI route and back.
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


def _event(method: str, path: str, body: dict | None = None) -> dict:
    """A Lambda Function URL event (API Gateway HTTP API v2 payload format, per Mangum's `HTTPGateway.scope`:
    `requestContext.http.{method,path,sourceIp}` is what Mangum actually reads for routing, not `rawPath`)."""
    headers = {"host": "example.lambda-url.us-east-1.on.aws"}
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
    response = api_lambda_handler.handler(_event("GET", "/health"), {})
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"ok": True}


def test_intake_skip_round_trips_path_param_and_json_body() -> None:
    """POST /profiles/{profile_id}/intake/skip: profile_id comes from the URL path, {"path": ...} from the JSON
    body. Both must reach the FastAPI route for the skip to land on the right field."""
    path = "identity.age"
    response = api_lambda_handler.handler(
        _event("POST", f"/profiles/{ROBERT_ID}/intake/skip", {"path": path}), {}
    )
    assert response["statusCode"] == 200, response["body"]
    profile = json.loads(response["body"])
    node = profile
    for part in path.split("."):
        node = node[part]
    assert node == {"value": None, "by": "user:skipped"}
