"""Profiles, events, portfolios, reports, audit (SPEC §10). Backend local (default) or s3 (P1.3).

Local layout under the store root (COHERENCE_ANALYZER_STORE_DIR, default: the current working directory, read at call
time so a test or a demo can chdir):

    profiles/<profile_id>/profile.v<N>.json
    profiles/<profile_id>/events.jsonl
    portfolios/<portfolio_id>.json
    reports/<run_id>.json
    audit/<run_id>.json

The s3 backend (COHERENCE_ANALYZER_STORE=s3, COHERENCE_ANALYZER_S3_BUCKET, optional COHERENCE_ANALYZER_S3_PREFIX) uses
the same keys under the prefix, the same JSON bytes, ContentType application/json. The backend is chosen per call;
boto3 is imported lazily (never for the local backend) and the client is built at call time.

reports/ and audit/ are git-ignored. Writes are deterministic (insertion-ordered keys, no clock): the only
run-specific parts of a report are its run_id (ULID) and whatever timestamps the caller put in it.
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
BACKENDS = ("local", "s3")
JSON_TYPE = "application/json"


def backend() -> str:
    """The configured backend, read at call time. Raises a clear error for an unknown one."""
    name = config.COHERENCE_ANALYZER_STORE
    if name not in BACKENDS:
        raise ValueError(f"COHERENCE_ANALYZER_STORE must be one of {BACKENDS}, got {name!r}")
    return name


def _s3_target() -> tuple[str, str]:
    """(bucket, key prefix: empty or ending with a slash). Raises if the bucket is not configured."""
    bucket = config.COHERENCE_ANALYZER_S3_BUCKET
    if not bucket:
        raise ValueError("COHERENCE_ANALYZER_STORE=s3 needs COHERENCE_ANALYZER_S3_BUCKET to be set")
    prefix = (config.COHERENCE_ANALYZER_S3_PREFIX or "").strip("/")
    return bucket, (prefix + "/" if prefix else "")


def _s3_client() -> Any:
    import boto3   # lazy: the local backend never needs it

    return boto3.client("s3", region_name=config.AWS_REGION)


def _is_missing(exc: Exception) -> bool:
    code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
    return code in ("NoSuchKey", "404", "NotFound")


def _s3_get(key: str) -> bytes | None:
    """The object bytes, or None when the key does not exist. Other errors (AccessDenied...) propagate."""
    from botocore.exceptions import ClientError

    bucket, prefix = _s3_target()
    try:
        resp = _s3_client().get_object(Bucket=bucket, Key=prefix + key)
    except ClientError as exc:
        if _is_missing(exc):
            return None
        raise
    return resp["Body"].read()


def _s3_put(key: str, body: bytes) -> str:
    bucket, prefix = _s3_target()
    _s3_client().put_object(Bucket=bucket, Key=prefix + key, Body=body, ContentType=JSON_TYPE)
    return prefix + key


def _s3_list_keys(prefix: str) -> list[str]:
    """Every object key (relative to the configured bucket prefix) under `prefix`, paginated."""
    bucket, base = _s3_target()
    client = _s3_client()
    keys: list[str] = []
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": base + prefix}
        if token:
            kwargs["ContinuationToken"] = token
        resp = client.list_objects_v2(**kwargs)
        keys.extend(obj["Key"][len(base):] for obj in resp.get("Contents", []))
        if not resp.get("IsTruncated"):
            return keys
        token = resp.get("NextContinuationToken")


def _s3_list_profile_ids() -> list[str]:
    """Immediate `profiles/<id>/` names that hold a profile.json object (CommonPrefixes, one list call)."""
    bucket, base = _s3_target()
    client = _s3_client()
    ids: list[str] = []
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": base + "profiles/", "Delimiter": "/"}
        if token:
            kwargs["ContinuationToken"] = token
        resp = client.list_objects_v2(**kwargs)
        for entry in resp.get("CommonPrefixes", []):
            name = entry["Prefix"][len(base) + len("profiles/"):].rstrip("/")
            if name:
                ids.append(name)
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    return [pid for pid in ids if _s3_get(f"profiles/{pid}/profile.json") is not None]


def list_profile_ids() -> list[str]:
    """Every profile id in the store (local: directory scan; s3: a listing call), unvalidated beyond
    "has a profile.json". The caller still checks the other required files and `meta.profile_id` once the
    files are local (sync_profile_down for s3)."""
    if backend() == "s3":
        return _s3_list_profile_ids()
    root_dir = Path(os.getenv("COHERENCE_ANALYZER_STORE_DIR") or Path.cwd()) / "profiles"
    if not root_dir.is_dir():
        return []
    return [p.name for p in root_dir.iterdir() if p.is_dir() and (p / "profile.json").is_file()]


def sync_profile_down(local_dir: Path, profile_id: str) -> None:
    """s3 only (local backend: no-op, `local_dir` already is the store). Downloads every object under
    `profiles/<profile_id>/` into `local_dir`, so the rest of the codebase can keep working with it as a plain
    local directory (api.py's working-copy layer predates the s3 backend and never became s3-aware itself)."""
    if backend() != "s3":
        return
    prefix = f"profiles/{profile_id}/"
    for key in _s3_list_keys(prefix):
        rel = key[len(prefix):]
        if not rel:
            continue
        data = _s3_get(key)
        if data is None:
            continue
        dest = local_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)


def sync_profile_up(local_dir: Path, profile_id: str) -> None:
    """s3 only (local backend: no-op). Uploads every file currently in `local_dir` to `profiles/<profile_id>/...`
    so a write made through the local working copy (api.py) is visible to the next request, however it lands on
    this or another Lambda instance. Call after every mutation, not after every individual write: one directory
    sync per request is enough and matches how api.py already snapshots/restores whole files on failure."""
    if backend() != "s3":
        return
    if not local_dir.is_dir():
        return
    for path in local_dir.rglob("*"):
        if path.is_file():
            _s3_put(f"profiles/{profile_id}/{path.relative_to(local_dir).as_posix()}", path.read_bytes())


def root() -> Path:
    """Local store root. For s3 a display-only s3:/<bucket>/<prefix> path (never used for file access)."""
    if backend() == "s3":
        bucket, prefix = _s3_target()
        return Path("s3:") / bucket / prefix.strip("/")
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
    """doc as plain JSON types (numpy scalars -> Python numbers), e.g. the engine output."""
    return json.loads(json.dumps(doc, default=_plain))


def _write(key: str, doc: Any) -> Path:
    """Write the document under the layout key (local file or s3 object). Returns the local path / the s3 key."""
    if backend() == "s3":
        return Path(_s3_put(key, _dump(doc).encode("utf-8")))
    path = root() / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump(doc), encoding="utf-8")
    return path


def _read(key: str) -> str | None:
    """Text of the layout key, or None when absent."""
    if backend() == "s3":
        data = _s3_get(key)
        return None if data is None else data.decode("utf-8")
    p = root() / key
    return p.read_text(encoding="utf-8") if p.exists() else None


def _s3_events_key(path: Path) -> str | None:
    """On s3 a relative profiles/... path is a layout key (relative to the prefix); any other path (a fixtures
    directory, an absolute path) stays a local file, so fixture events still load under the s3 backend."""
    p = Path(path)
    return p.as_posix() if not p.is_absolute() and p.parts[:1] == ("profiles",) else None


def read_events(path: Path) -> list[dict]:
    p = Path(path)
    key = _s3_events_key(p) if backend() == "s3" else None
    if key is not None:
        text = _read(key)
        if text is None:
            return []
    elif not p.exists():
        return []
    else:
        text = p.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def append_event(path: Path, event: dict) -> None:
    """Local: append one line. s3: get + put of the whole object (non-atomic: a concurrent append can be lost)."""
    key = _s3_events_key(path) if backend() == "s3" else None
    line = json.dumps(event, ensure_ascii=False) + "\n"
    if key is None:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
        return
    old = _s3_get(key) or b""
    _s3_put(key, old + line.encode("utf-8"))


def save_profile(profile: dict) -> Path:
    meta = profile["meta"]
    return _write(f"profiles/{meta['profile_id']}/profile.v{meta['version']}.json", profile)


def save_portfolio(portfolio: dict) -> Path:
    """The portfolio as stored: weights in percent only (the fraction view is a loader convenience)."""
    doc = {k: v for k, v in portfolio.items() if k != "weights"}
    return _write(f"portfolios/{doc['portfolio_id']}.json", doc)


def save_report(report: dict) -> Path:
    return _write(f"reports/{report['run_id']}.json", report)


def save_audit(audit: dict) -> Path:
    return _write(f"audit/{audit['run_id']}.json", audit)


def load_report(run_id: str) -> dict:
    text = _read(f"reports/{run_id}.json")
    if text is None:
        raise FileNotFoundError(f"no report for run {run_id!r} under {root() / 'reports'}")
    return json.loads(text)


def load_audit(run_id: str) -> dict:
    text = _read(f"audit/{run_id}.json")
    if text is None:
        raise FileNotFoundError(f"no audit record for run {run_id!r} under {root() / 'audit'}")
    return json.loads(text)
