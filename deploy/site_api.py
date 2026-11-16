#!/usr/bin/env python3
"""The three display-layer routes app.html's live mode actually calls (`siteApiDir()` in data_source.js):
POST .../review, POST .../whatif, POST .../whatif/amount. Each wraps a real `coherence_analyzer.api` call with the
`site` display-only block (`build_site.site_extras`: profile card, limits, "points to clarify") or the what-if grid
(`deploy/whatif.py`), computed from the profile's *working copy* (`api._workdir`/`api.workdir`) so edited intake
answers change what's shown — same engine, same store, local fixture dir or S3-backed user alike.

Shared by `deploy/serve_local.py` (mounted alongside the built static site) and
`coherence_analyzer/api_lambda_handler.py` (mounted alongside `coherence_analyzer.api:app`, no static site: CloudFront
serves that separately). Keeping this one copy avoids the two call sites drifting apart.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from coherence_analyzer import api, data  # noqa: E402
from deploy import build_site, whatif  # noqa: E402

SITE_API_PREFIX = "/site-api"
PORTFOLIO_FILE = "portfolio.json"
LIVE_LABEL_SUFFIX = " · live analysis"
MAX_WHATIF_TICKERS = 10
MAX_WHATIF_AMOUNT_CAD = 1e9


class WhatifRequest(BaseModel):
    tickers: list[str] = Field(..., min_length=1, max_length=MAX_WHATIF_TICKERS)


class WhatifAmountRequest(BaseModel):
    ticker: str
    amount_cad: float = Field(..., gt=0, le=MAX_WHATIF_AMOUNT_CAD)
    funding: str


def live_review(profile_id: str) -> dict[str, Any]:
    """Run the API review, then attach the display-only `site` block.

    The block is computed by `build_site.site_extras` from the persona's
    working copy, so edited intake answers change limits and the profile card.

    Raises:
        HTTPException: 404 for an unknown profile, 422 when the review rejects it.
    """
    with api._LOCK:
        report = api.post_review(profile_id, locale=build_site.SITE_LOCALE)
        work, fixture_or_namespace = api._workdir(profile_id)

        # For fixture profiles, use load_persona for the name (has proper fallback)
        # For store-only profiles, read name from work dir's profile
        if fixture_or_namespace.is_dir() and (fixture_or_namespace / "profile.json").exists():
            pers = build_site.load_persona(fixture_or_namespace.name)
            name = pers.name
        else:
            profile = json.loads((work / "profile.json").read_text(encoding="utf-8"))
            name = (profile.get("identity") or {}).get("name") or profile["meta"].get("name") or profile_id

        report["site"] = {
            **build_site.site_extras(work, str(name), PORTFOLIO_FILE),
            "label": f"{name}{LIVE_LABEL_SUFFIX}",
        }
    return report


def live_whatif(profile_id: str, body: WhatifRequest) -> dict[str, Any]:
    """Compute what-if scenarios from the working copy profile.

    Computes scenarios with the user's edited intake answers (working copy) rather
    than the original fixture, so changes to risk level, goals, or constraints
    affect the checks and verdict in each scenario.

    Args:
        profile_id: persona id (e.g. "persona-robert")
        body: tickers to compute scenarios for (1 to MAX_WHATIF_TICKERS)

    Returns:
        dict with "universe" ({assets, base}) and "whatif" ({ticker: {asset, scenarios}})

    Raises:
        HTTPException: 404 for unknown profile, 422 for invalid tickers or count
    """
    assets = data.load_assets()
    valid_tickers = set(assets.index)
    for ticker in body.tickers:
        if ticker not in valid_tickers:
            raise HTTPException(status_code=422, detail=f"ticker {ticker!r} is not in the universe")

    with api._LOCK:
        work = api.workdir(profile_id)
        try:
            ctx = whatif.context(work, build_site.SITE_LOCALE)
            univ = whatif.universe(ctx)
            scen = whatif.scenarios(ctx, body.tickers)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {"universe": univ, "whatif": scen}


def live_whatif_amount(profile_id: str, body: WhatifAmountRequest) -> dict[str, Any]:
    """Compute one what-if scenario for a user-typed CAD amount, from the working copy profile.

    Args:
        profile_id: persona id (e.g. "persona-robert")
        body: ticker, amount_cad (> 0) and funding id ("pro_rata_all", "specific:<T>" or "cash")

    Returns:
        {"scenario": {...}}, the same shape as one entry of whatif.json's scenarios plus value_cad

    Raises:
        HTTPException: 404 for unknown profile, 422 for an unknown ticker or funding, or an amount that the
        chosen funding cannot cover
    """
    if body.ticker not in set(data.load_assets().index):
        raise HTTPException(status_code=422, detail=f"ticker {body.ticker!r} is not in the universe")
    with api._LOCK:
        work = api.workdir(profile_id)
        try:
            ctx = whatif.context(work, build_site.SITE_LOCALE)
            scen = whatif.scenario_for_amount(ctx, body.ticker, body.amount_cad, body.funding)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"scenario": scen}


def mount(root_app: FastAPI) -> None:
    """Add the three `SITE_API_PREFIX` routes to `root_app`. Caller still mounts `coherence_analyzer.api:app`
    (at `/api` for `serve_local.py`'s one-origin app; directly for the Lambda, whose Function URL IS the API
    origin) and, for `serve_local.py` only, the built static site."""
    root_app.add_api_route(
        f"{SITE_API_PREFIX}/profiles/{{profile_id}}/review", live_review, methods=["POST"]
    )
    root_app.add_api_route(
        f"{SITE_API_PREFIX}/profiles/{{profile_id}}/whatif", live_whatif, methods=["POST"]
    )
    root_app.add_api_route(
        f"{SITE_API_PREFIX}/profiles/{{profile_id}}/whatif/amount", live_whatif_amount, methods=["POST"]
    )
