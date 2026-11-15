"""AWS Lambda entry point for the HTTP-facing Flow A/B app (SPEC §14 AWS footprint; CloudFront + Lambda Function URL).

This is a SECOND Lambda, separate from `lambda_handler.py` (the Step Functions version of Flow B, invoked per-step by
`deploy/aws/flow_b.asl.json`). Here the whole FastAPI app (`coherence_analyzer.api.app`: profiles, reviews,
corrections, intake) is exposed over HTTP through a Lambda Function URL, so the CloudFront-hosted static site
(`deploy/site/`) can call it directly instead of going through `deploy/serve_local.py`'s uvicorn process.

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

The CloudFormation for this Lambda (a parallel lane) points its `Handler` at this exact module path and attribute:
`coherence_analyzer.api_lambda_handler.handler`. Don't rename the module or the attribute without updating that
template.
"""
from __future__ import annotations

from mangum import Mangum

from .api import app

handler = Mangum(app, lifespan="off")
