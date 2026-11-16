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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# config.py reads the LLM mode at import time, so set it before importing the API.
os.environ.setdefault("COHERENCE_ANALYZER_LLM", "mock")

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from coherence_analyzer.api import app as api_app  # noqa: E402
from deploy import build_site, site_api  # noqa: E402

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_SITE_DIR = ROOT / ".cache" / "site"
API_MOUNT_PATH = "/api"
STORE_DIR_ENV = build_site.STORE_DIR_ENV
STORE_DIR_PREFIX = "coherence-serve-"


def create_app(site_dir: Path) -> FastAPI:
    """Build the one-origin app.

    Args:
        site_dir: directory holding the built static site (`app.html`, `design.html`, ...).

    Returns:
        A FastAPI app with the API mounted at `/api`, the `/site-api/*` display-layer routes
        (`deploy.site_api`) and the static site at `/`. GET `/` redirects to `/app.html`.
    """
    root_app = FastAPI(title="Compass local")

    @root_app.get("/")
    def redirect_root():
        """Redirect root to the English app page."""
        return RedirectResponse(url="/app.html", status_code=307)

    site_api.mount(root_app)
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
