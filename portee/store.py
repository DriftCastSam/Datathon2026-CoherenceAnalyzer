"""Profiles, events, portfolios, reports, audit (SPEC §10). Backend local (default) or s3 (P1)."""
from __future__ import annotations

import json
from pathlib import Path


def read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def append_event(path: Path, event: dict) -> None:
    with open(path, "a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def save_report(report: dict) -> Path:
    raise NotImplementedError


def save_audit(audit: dict) -> Path:
    raise NotImplementedError


def load_audit(run_id: str) -> dict:
    raise NotImplementedError
