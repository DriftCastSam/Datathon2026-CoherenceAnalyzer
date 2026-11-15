#!/usr/bin/env python3
"""Serve the built site and the API on one origin for local use.

`/` serves the static site (default `.cache/site`), `/api/*` serves
`coherence_analyzer.api` (the same routes as `uvicorn coherence_analyzer.api:app`).
The LLM is the offline mock and the store is a fresh temp dir per run, so
nothing is written to `profiles/` or `reports/`.

Usage: python deploy/serve_local.py [--no-build] [--port 8000]
       [--host 127.0.0.1] [--site-dir .cache/site]
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# config.py reads the LLM mode at import time, so set it before importing the API.
os.environ.setdefault("COHERENCE_ANALYZER_LLM", "mock")

import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from coherence_analyzer import api, data  # noqa: E402
from coherence_analyzer.api import app as api_app  # noqa: E402
from deploy import build_site, whatif  # noqa: E402

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_SITE_DIR = ROOT / ".cache" / "site"
API_MOUNT_PATH = "/api"
SITE_API_PREFIX = "/site-api"
PORTFOLIO_FILE = "portfolio.json"
LIVE_LABEL_SUFFIX = " · live analysis"
STORE_DIR_ENV = build_site.STORE_DIR_ENV
STORE_DIR_PREFIX = "coherence-serve-"
MAX_WHATIF_TICKERS = 10


class WhatifRequest(BaseModel):
    tickers: list[str] = Field(..., min_length=1, max_length=MAX_WHATIF_TICKERS)


def live_review(profile_id: str) -> dict[str, Any]:
    """Run the API review, then attach the display-only `site` block.

    The block is computed by `build_site.site_extras` from the persona's
    working copy, so edited intake answers change limits and the profile card.

    Raises:
        HTTPException: 404 for an unknown profile, 422 when the review rejects it.
    """
    report = api.post_review(profile_id, locale=build_site.SITE_LOCALE)
    work, fixture = api.workdir(profile_id)
    pers = build_site.load_persona(fixture.name)
    report["site"] = {
        **build_site.site_extras(work, pers.name, PORTFOLIO_FILE),
        "label": f"{pers.name}{LIVE_LABEL_SUFFIX}",
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
    work, _ = api.workdir(profile_id)
    assets = data.load_assets()
    valid_tickers = set(assets.index)
    for ticker in body.tickers:
        if ticker not in valid_tickers:
            raise HTTPException(status_code=422, detail=f"ticker {ticker!r} is not in the universe")

    with api._LOCK:
        try:
            ctx = whatif.context(work, build_site.SITE_LOCALE)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    univ = whatif.universe(ctx)

    return {"universe": univ, "whatif": whatif.scenarios(ctx, body.tickers)}


def create_app(site_dir: Path) -> FastAPI:
    """Build the one-origin app.

    Args:
        site_dir: directory holding the built static site (`index.html`, `app.html`, ...).

    Returns:
        A FastAPI app with the API mounted at `/api` and the static site at `/`.
        GET `/` redirects to `/app.html` (the English app page).
    """
    root_app = FastAPI(title="CoherenceAnalyzer local")

    @root_app.get("/")
    def redirect_root():
        """Redirect root to the English app page."""
        return RedirectResponse(url="/app.html", status_code=307)

    root_app.add_api_route(
        f"{SITE_API_PREFIX}/profiles/{{profile_id}}/review", live_review, methods=["POST"]
    )
    root_app.add_api_route(
        f"{SITE_API_PREFIX}/profiles/{{profile_id}}/whatif", live_whatif, methods=["POST"]
    )
    root_app.mount(API_MOUNT_PATH, api_app)
    root_app.mount("/", StaticFiles(directory=site_dir, html=True), name="site")
    return root_app


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-build", action="store_true", help="reuse the existing site dir")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--site-dir", type=Path, default=DEFAULT_SITE_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Optionally build the site, then serve it with uvicorn.

    Side effects: builds into `--site-dir` (replacing its contents) unless
    `--no-build`; sets a temp store dir in the environment when none is set.
    """
    args = parse_args(argv)
    if not args.no_build:
        build_site.build(args.site_dir)
    os.environ.setdefault(STORE_DIR_ENV, tempfile.mkdtemp(prefix=STORE_DIR_PREFIX))
    uvicorn.run(create_app(args.site_dir), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
