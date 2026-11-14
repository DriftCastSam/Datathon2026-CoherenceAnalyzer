"""Profiles, events, portfolios, reports, audit (SPEC §10). Backend local (default) or s3 (P1).

Local layout under the store root (`COHERENCE_ANALYZER_STORE_DIR`, default: the current working directory, read at call
time so a test or a demo can chdir):

    profiles/<profile_id>/profile.v<N>.json
    portfolios/<portfolio_id>.json
    reports/<run_id>.json
    audit/<run_id>.json

`reports/` and `audit/` are git-ignored. Writes are deterministic (insertion-ordered keys, no clock): the only
run-specific parts of a report are its `run_id` (ULID) and whatever timestamps the caller put in it.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from . import config

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def root() -> Path:
    if config.COHERENCE_ANALYZER_STORE != "local":
        raise NotImplementedError("COHERENCE_ANALYZER_STORE=s3 is P1; only the local store exists")
    return Path(os.getenv("COHERENCE_ANALYZER_STORE_DIR") or Path.cwd())


def new_run_id() -> str:
    """A ULID (SPEC §4): 48-bit millisecond timestamp + 80 random bits, Crockford base32, 26 characters.
    Run identity only: it never feeds a computed value."""
    n = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    return "".join(_CROCKFORD[(n >> shift) & 31] for shift in range(125, -1, -5))


def _dump(doc: Any) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=2, default=_plain) + "\n"


def _plain(x: Any) -> Any:
    """JSON default: numpy scalars / arrays and tuples-like objects to plain Python."""
    if hasattr(x, "item") and getattr(x, "shape", None) == ():
        return x.item()
    if hasattr(x, "tolist"):
        return x.tolist()
    if isinstance(x, (set, frozenset)):
        return sorted(x)
    raise TypeError(f"not JSON serialisable: {type(x).__name__}")


def plain(doc: Any) -> Any:
    """`doc` as plain JSON types (numpy scalars -> Python numbers), e.g. the engine output."""
    return json.loads(json.dumps(doc, default=_plain))


def _write(path: Path, doc: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump(doc), encoding="utf-8")
    return path


def read_events(path: Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_event(path: Path, event: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def save_profile(profile: dict) -> Path:
    meta = profile["meta"]
    return _write(root() / "profiles" / meta["profile_id"] / f"profile.v{meta['version']}.json", profile)


def save_portfolio(portfolio: dict) -> Path:
    """The portfolio as stored: weights in percent only (the fraction view is a loader convenience)."""
    doc = {k: v for k, v in portfolio.items() if k != "weights"}
    return _write(root() / "portfolios" / f"{doc['portfolio_id']}.json", doc)


def save_report(report: dict) -> Path:
    return _write(root() / "reports" / f"{report['run_id']}.json", report)


def save_audit(audit: dict) -> Path:
    return _write(root() / "audit" / f"{audit['run_id']}.json", audit)


def load_report(run_id: str) -> dict:
    return json.loads((root() / "reports" / f"{run_id}.json").read_text(encoding="utf-8"))


def load_audit(run_id: str) -> dict:
    p = root() / "audit" / f"{run_id}.json"
    if not p.exists():
        raise FileNotFoundError(f"no audit record for run {run_id!r} under {p.parent}")
    return json.loads(p.read_text(encoding="utf-8"))
