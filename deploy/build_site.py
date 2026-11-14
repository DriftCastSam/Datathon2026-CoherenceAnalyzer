#!/usr/bin/env python3
"""Build the static prototype site for CloudFront (deploy/cloudfront.yaml).

docs/architecture.html is an HTML fragment (no doctype/head). Wrap it in a full document so it
renders in standards mode, and write the result to <out>/index.html (default .cache/site/).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEAD = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n')


def build(out: Path) -> Path:
    body = (ROOT / "docs" / "architecture.html").read_text(encoding="utf-8")
    out.mkdir(parents=True, exist_ok=True)
    page = out / "index.html"
    page.write_text(HEAD + body + "\n</html>\n", encoding="utf-8")
    return page


if __name__ == "__main__":
    print(build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".cache" / "site"))
