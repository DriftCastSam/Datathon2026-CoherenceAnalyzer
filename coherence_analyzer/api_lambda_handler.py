"""AWS Lambda entry point for the HTTP-facing Flow A/B app (SPEC §14 AWS footprint; CloudFront + Lambda Function URL).

This is a SECOND Lambda, separate from `lambda_handler.py` (the Step Functions version of Flow B, invoked per-step by
`deploy/aws/flow_b.asl.json`). Here the whole FastAPI app (`coherence_analyzer.api.app`: profiles, reviews,
corrections, intake) plus the `/site-api/*` display-layer routes app.html's live mode actually calls
(`deploy/site_api.py`: review/whatif wrapped with the `site` block — profile card, limits, "points to clarify") are
exposed over one Lambda Function URL, so the CloudFront-hosted static site (`deploy/site/`) can call it directly.
`api.app` is mounted at `/api` here, exactly like `deploy/serve_local.py`'s `create_app()` does for local one-origin
serving (`API_MOUNT_PATH = "/api"`) — not at the root — because `data_source.js`'s `apiDir()`/`siteApiDir()` always
append `"api/"`/`"site-api/"` to whatever `<meta name="coherence-api-base">` is set to; mounting `api.app` at the
root here would make every `apiDir()` call 404. So the one difference from `serve_local.py`'s layout is the static
file mount, which CloudFront owns instead of this Lambda — everything else (the two route sets, their paths) is
identical, so the same `coherence-api-base` value works against either origin.

A Lambda Function URL invokes its function with an API-Gateway-v2-compatible payload: `{"version": "2.0", "rawPath",
"rawQueryString", "headers", "requestContext": {"http": {"method", "path", "sourceIp", ...}}, "body",
"isBase64Encoded", ...}`, and expects back `{"statusCode", "headers", "body", "isBase64Encoded", ...}`. Mangum
(https://github.com/kludex/mangum) is the adapter that does that translation both ways: it turns one Lambda event into
one ASGI `scope`/`receive`/`send` cycle against `app`, then turns the single ASGI response back into the dict shape
above. `handler` below is exactly that adapter, called once per invocation.

`Mangum(app, lifespan="off")`: this app has no startup/shutdown ASGI lifespan events that matter here (no connection
pool to open, no background task to start) and Lambda runs one request per invocation anyway, so there is nothing to
gain from `"auto"` (the default) probing the app for lifespan support, and `"off"` avoids the extra lifespan handshake
on every cold start.

This module sets no environment variables itself, the same pattern as `api.py` and `lambda_handler.py`: everything
is read from `coherence_analyzer.config` / `os.environ` at call time. For this Lambda to work against real data and a
real model, its function configuration must set `COHERENCE_ANALYZER_LLM=live`, `COHERENCE_ANALYZER_STORE=s3`, and the
matching `COHERENCE_ANALYZER_S3_BUCKET`, `COHERENCE_ANALYZER_S3_PREFIX`, `BEDROCK_MODEL_ID`, `BEDROCK_GUARDRAIL_ID`,
`BEDROCK_GUARDRAIL_VERSION` (and `AWS_REGION` if not the Lambda's own region) that `coherence_analyzer/config.py`
already reads — this file does not read or set any of them directly, it only imports the already-configured `app`.

CORS: `api.app` already carries its own `CORSMiddleware` (`allow_origins=["*"]`), relied on by the documented
`uvicorn coherence_analyzer.api:app` standalone usage (README), so it is not touched here. `/site-api/*` lives
directly on `root_app`, which has no middleware of its own, so without a second `CORSMiddleware` scoped to those
routes, a browser's preflight `OPTIONS` for any `/site-api/*` POST gets Starlette's plain 405 instead of a CORS
response, and the real request never fires (curl doesn't enforce CORS, so this was invisible to curl-based
testing). Giving `/site-api/*` its own sub-app with its own matching `CORSMiddleware` — rather than putting one on
`root_app` directly, which would double up with `api_app`'s for every `/api/*` request, the same bug this file
used to have on `ApiFunctionUrl`'s now-removed `Cors` property (two `Access-Control-Allow-Origin` values fails the
Fetch spec's check) — keeps exactly one CORS layer per route.

`deploy/site_api.py` imports `deploy.build_site` and `deploy.whatif` (for `site_extras`/`context`/`universe`/
`scenarios`/`scenario_for_amount`), so the Lambda deployment package must include `deploy/site_api.py`,
`deploy/build_site.py` and `deploy/whatif.py` alongside `coherence_analyzer/`, `data/starter`, `data/meta` and
`fixtures/` (see `deploy/aws/deploy.sh`'s zip step) — not just the `coherence_analyzer/` package.

The CloudFormation for this Lambda (a parallel lane) points its `Handler` at this exact module path and attribute:
`coherence_analyzer.api_lambda_handler.handler`. Don't rename the module or the attribute without updating that
template.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

from deploy import site_api

from .api import app as api_app

API_MOUNT_PATH = "/api"

site_api_app = FastAPI()
site_api.mount(site_api_app)
site_api_app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST", "PUT"], allow_headers=["*"],
)

root_app = FastAPI(title="Compass API")
root_app.mount(API_MOUNT_PATH, api_app)
root_app.mount("/", site_api_app)

handler = Mangum(root_app, lifespan="off")
