"""`_known_intent_ids` scans every profile to find a globally-unique next intent id, and (s3 backend) each scan
re-syncs every profile's dir from S3 via `_store_dirs`. That is fine on its own, but `_draft_intent` calls it
*mid-mutation* for the very profile `post_event` already appended a new (not-yet-synced) event to: the re-sync
silently overwrote that profile's `events.jsonl` with the older S3 snapshot, so the event id `_draft_intent`
had just computed and appended disappeared before `pipeline.b1_parse_intent` could read it back, surfacing as
`ValueError: unknown event_id ...` on every live order_intent call (found in production, not from a unit test).

`_store_dirs`/`_profile_ids`/`_known_intent_ids` now take `skip_sync`, the profile_id already fresh in this
request, so the cross-profile scan does not re-sync (and clobber) it.

Offline only: a hand-written fake S3 client (list_objects_v2, get_object, put_object) seeded with one profile's
real fixture content, no botocore Stubber (call-order-strict, awkward for this read-then-write-then-read flow).
"""
from __future__ import annotations

from botocore.exceptions import ClientError

from coherence_analyzer import api, config, store

PROFILE_ID = "persona-robert"
PENDING_EVENT = {
    "event_id": "ev_robert_9999", "profile_id": PROFILE_ID, "type": "order_intent",
    "text": "test", "ts": "2026-01-01T00:00:00Z", "payload": None, "source": "ui",
}


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def list_objects_v2(self, **kw):
        prefix = kw["Prefix"]
        keys = sorted(k for k in self.objects if k.startswith(prefix))
        if kw.get("Delimiter") == "/":
            rest = {k[len(prefix):] for k in keys}
            prefixes = sorted({prefix + r.split("/", 1)[0] + "/" for r in rest if "/" in r})
            return {"CommonPrefixes": [{"Prefix": p} for p in prefixes], "IsTruncated": False}
        return {"Contents": [{"Key": k} for k in keys], "IsTruncated": False}

    def get_object(self, **kw):
        key = kw["Key"]
        if key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "absent"}}, "GetObject")
        return {"Body": _Body(self.objects[key])}

    def put_object(self, **kw):
        body = kw["Body"]
        self.objects[kw["Key"]] = body if isinstance(body, bytes) else body.encode("utf-8")
        return {}


def _seeded_fake(monkeypatch) -> _FakeS3:
    fake = _FakeS3()
    monkeypatch.setenv("COHERENCE_ANALYZER_STORE", "s3")
    monkeypatch.setenv("COHERENCE_ANALYZER_S3_BUCKET", "ca-test-bucket")
    monkeypatch.setenv("COHERENCE_ANALYZER_S3_PREFIX", "ca")
    monkeypatch.setattr(config, "COHERENCE_ANALYZER_STORE", "s3")
    monkeypatch.setattr(config, "COHERENCE_ANALYZER_S3_BUCKET", "ca-test-bucket")
    monkeypatch.setattr(config, "COHERENCE_ANALYZER_S3_PREFIX", "ca")
    monkeypatch.setattr(store, "_s3_client", lambda: fake)
    fixture_dir = api._fixture_dirs()[PROFILE_ID]
    for name in ("profile.json", "events.jsonl", "portfolio.json"):
        fake.objects[f"ca/profiles/{PROFILE_ID}/{name}"] = (fixture_dir / name).read_bytes()
    return fake


def test_known_intent_ids_skip_sync_keeps_the_callers_own_pending_event(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "_store_profiles_root", lambda: tmp_path / "work")
    _seeded_fake(monkeypatch)

    work, _ = api._workdir(PROFILE_ID)   # syncs the seeded fixture content down into tmp_path/work/persona-robert
    api._append_event(work, PENDING_EVENT)   # what post_event already did before calling _draft_intent
    assert "ev_robert_9999" in (work / "events.jsonl").read_text(encoding="utf-8")

    api._known_intent_ids(skip_sync=PROFILE_ID)

    assert "ev_robert_9999" in (work / "events.jsonl").read_text(encoding="utf-8"), (
        "skip_sync must stop the cross-profile scan from re-syncing (and overwriting) this profile's own "
        "working copy, which already holds a locally-appended event this same request has not synced up yet"
    )


def test_known_intent_ids_without_skip_sync_does_clobber_the_pending_event(monkeypatch, tmp_path):
    """Documents the bug `skip_sync` fixes: without it, the exact same scan overwrites the profile's own
    not-yet-synced local change. If this assertion ever fails, `sync_profile_down`'s overwrite behavior
    changed and the `skip_sync` guard in `_draft_intent` may no longer be needed."""
    monkeypatch.setattr(api, "_store_profiles_root", lambda: tmp_path / "work")
    _seeded_fake(monkeypatch)

    work, _ = api._workdir(PROFILE_ID)
    api._append_event(work, PENDING_EVENT)
    assert "ev_robert_9999" in (work / "events.jsonl").read_text(encoding="utf-8")

    api._known_intent_ids()   # no skip_sync: re-syncs every profile, including this one, from the stale S3 copy

    assert "ev_robert_9999" not in (work / "events.jsonl").read_text(encoding="utf-8")
