#!/usr/bin/env python3
"""Build the static dashboard for CloudFront (deploy/cloudfront.yaml).

Output (<out>, default .cache/site/):
  index.html             deploy/site/index.html (the dashboard)
  reports/index.json     list of reports the dashboard can open
  reports/<name>.json    report JSON (SPEC §13 shape + dashboard extras)

Reports come from deploy/preview_report.py (real engine + rules + limits, preview checks) until the
pipeline (P0.8) writes real reports; then point this script at those instead.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import preview_report  # noqa: E402


def build(out: Path) -> Path:
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "deploy" / "site", out)
    preview_report.build(out / "reports")
    return out


if __name__ == "__main__":
    out = build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".cache" / "site")
    for p in sorted(out.rglob("*")):
        if p.is_file():
            print(p.relative_to(out), p.stat().st_size)
