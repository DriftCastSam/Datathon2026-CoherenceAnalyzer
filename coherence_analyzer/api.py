"""Thin FastAPI over the Flow C pipeline (SPEC §12; docs/spec-decisions.md D7; P1.4).

Profiles, intake, events, parsed-rule decisions, review, reports, audit. Run: `uvicorn coherence_analyzer.api:app`.

Working copies: `fixtures/` is never written. On first access to a profile its fixture files (profile.json,
events.jsonl, portfolio*.json, intents.json) are copied to `store.root()/profiles/<profile_id>/` and every endpoint
works there (`pipeline.review(<that dir>)`). The URL id is `meta.profile_id`.

Invariants kept here (SPEC §1):
- inv. 2: no request body carries `limits`, `limits_trace` or any `llm:` field. Bodies forbid unknown keys (422), and
  intake paths are restricted to the intake checklist. Limits are recomputed by `pipeline.review`; the refreshed
  profile (limits included) replaces the working profile.json after each review.
- inv. 7: no route path names a trade action beyond SPEC §12's `/intents/{id}/confirm`; nothing is executed.

Flow B (P2.APIFB, D7, D10): an `order_intent` event is appended, N4 drafts the intent (B1, through the gate), and the
response is `{event, intent}` with the draft (`status: draft`, only the fields the user stated, the rest null). The
draft is stored in the working copy (`intents.json`, `intent_drafts.json`). `POST /intents/{id}/confirm` takes the
user-edited intent (ticker, target_weight_pct, funding), appends an `intent_confirmed` event and runs B3-B9; the
response is the Flow B report. A second confirmation of the same intent is a 409.
"""
from __future__ import annotations

import copy
import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from . import config, data, intake, pipeline, store
from . import intent as intent_mod

EVENT_TYPES = ("question", "order_intent", "quiz_result", "profile_edit", "note", "intent_confirmed")
COPY_PATTERNS = ("profile.json", "events.jsonl", "portfolio*.json", "intents.json")
DEFAULT_LOCALE = "fr-CA"
DEFAULT_SOURCE = "ui_chat"

_INTENT_ID = re.compile(r"^in_(\d{4,})$")
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_EVENT_ID = re.compile(r"^ev_(.+)_(\d+)$")
_ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?$")
_THEME_NUMBER_PATH = re.compile(
    r"^" + re.escape(intake.PARSED_RULES_PATH) + r"\[(\d+)\]\.params\." + re.escape(intake.THEME_NUMBER_KEY) + r"$")

_LOCK = threading.RLock()     # one writer at a time: a local demo server, files are small

app = FastAPI(title="CoherenceAnalyzer")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])


# ---------------------------------------------------------------- request bodies (unknown keys -> 422)

class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EventIn(_Body):
    type: Literal["question", "order_intent", "quiz_result", "profile_edit", "note", "intent_confirmed"]
    text: str = Field(min_length=1)
    ts: str
    source: str = Field(default=DEFAULT_SOURCE, min_length=1)
    payload: dict[str, Any] | None = None
    parent_event_id: str | None = None

    @field_validator("text")
    @classmethod
    def _text_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text must not be blank")
        return v

    @field_validator("ts")
    @classmethod
    def _ts_iso(cls, v: str) -> str:
        if not _ISO_DATETIME.match(v):
            raise ValueError("ts must be an ISO 8601 date-time, e.g. 2026-11-15T09:30:00-05:00")
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"ts is not a valid date-time: {exc}") from exc
        return v


class AnswerIn(_Body):
    path: str = Field(min_length=1)
    value: Any


class SkipIn(_Body):
    path: str = Field(min_length=1)


class DecisionIn(_Body):
    approved: StrictBool
    max_pct: Annotated[float, Field(gt=0, le=100, strict=True)] | None = None


# ---------------------------------------------------------------- fixtures and working copies

def _fixture_dirs() -> dict[str, Path]:
    """{meta.profile_id: fixture dir} for every fixture dir holding profile.json + events.jsonl + portfolio.json."""
    out: dict[str, Path] = {}
    root = config.FIXTURES_DIR
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if not all((d / f).is_file() for f in ("profile.json", "events.jsonl", "portfolio.json")):
            continue
        try:
            pid = json.loads((d / "profile.json").read_text(encoding="utf-8"))["meta"]["profile_id"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if isinstance(pid, str) and pid:
            out.setdefault(pid, d)
    return out


def _workdir(profile_id: str) -> tuple[Path, Path]:
    """(working dir, fixture dir); copies the fixture on first access. 404 for an unknown id."""
    fixture = _fixture_dirs().get(profile_id)
    if fixture is None:
        raise HTTPException(status_code=404, detail=f"unknown profile {profile_id!r}")
    work = store.root() / "profiles" / profile_id
    with _LOCK:
        if not (work / "profile.json").exists():
            work.mkdir(parents=True, exist_ok=True)
            for pattern in COPY_PATTERNS:
                for src in sorted(fixture.glob(pattern)):
                    dst = work / src.name
                    if src.is_file() and not dst.exists():
                        dst.write_bytes(src.read_bytes())
    return work, fixture


def _load(work: Path) -> dict:
    return json.loads((work / "profile.json").read_text(encoding="utf-8"))


def _save(work: Path, profile: dict) -> None:
    """Validate against profile.v2.json, then replace the working profile.json (atomic rename)."""
    try:
        pipeline.validate_profile(profile, "profile")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    tmp = work / "profile.json.tmp"
    tmp.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, work / "profile.json")


def _allowed_intake_paths(profile: dict) -> set[str]:
    """Paths a user may answer or skip: the intake checklist, plus the number of a parsed theme rule without one."""
    allowed = {p for paths in intake.REQUIRED.values() for p in paths}
    allowed.update(p for p in intake.missing(profile, "contraintes") if _THEME_NUMBER_PATH.match(p))
    return allowed


def _check_intake_path(profile: dict, path: str) -> None:
    if path not in _allowed_intake_paths(profile):
        raise HTTPException(status_code=422, detail=f"{path!r} is not an intake path "
                            "(limits, limits_trace and llm: fields can never be written through the API)")


# ---------------------------------------------------------------- health, profiles, intake

@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/profiles")
def list_profiles() -> list[dict]:
    out = []
    for pid, d in _fixture_dirs().items():
        profile = json.loads((d / "profile.json").read_text(encoding="utf-8"))
        name = (profile.get("identity") or {}).get("name") or profile["meta"].get("name") or d.name.capitalize()
        out.append({"profile_id": pid, "name": str(name)})
    return out


@app.get("/profiles/{profile_id}")
def get_profile(profile_id: str) -> dict:
    work, _ = _workdir(profile_id)
    return _load(work)


@app.get("/profiles/{profile_id}/intake")
def get_intake(profile_id: str, locale: Literal["fr-CA", "en-CA"] | None = Query(default=None)) -> dict:
    work, _ = _workdir(profile_id)
    profile = _load(work)
    loc = locale or profile["meta"].get("locale") or DEFAULT_LOCALE
    if loc not in intake.LOCALES:
        loc = DEFAULT_LOCALE
    return {f: {"missing": intake.missing(profile, f), "next_question": intake.next_question(profile, f, loc)}
            for f in intake.FACTORS}


@app.post("/profiles/{profile_id}/intake/answer")
def post_intake_answer(profile_id: str, body: AnswerIn) -> dict:
    work, _ = _workdir(profile_id)
    with _LOCK:
        profile = _load(work)
        _check_intake_path(profile, body.path)
        if body.value is None:
            raise HTTPException(status_code=422, detail="value must not be null (use /intake/skip to skip a question)")
        if _THEME_NUMBER_PATH.match(body.path):
            v = body.value
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= 100:
                raise HTTPException(status_code=422, detail="a theme share is a percentage in (0, 100]")
        updated = intake.answer(profile, body.path, body.value)
        _save(work, updated)
    return updated


@app.post("/profiles/{profile_id}/intake/skip")
def post_intake_skip(profile_id: str, body: SkipIn) -> dict:
    work, _ = _workdir(profile_id)
    with _LOCK:
        profile = _load(work)
        _check_intake_path(profile, body.path)
        updated = intake.skip(profile, body.path)
        _save(work, updated)
    return updated


# ---------------------------------------------------------------- events (§3.4)

def _next_event_id(events: list[dict], fixture: Path) -> str:
    """ev_<name>_NNNN with NNNN = highest existing number + 1 (ids can have gaps)."""
    name, highest = fixture.name, 0
    for e in events:
        m = _EVENT_ID.match(str(e.get("event_id", "")))
        if m:
            name, highest = m.group(1), max(highest, int(m.group(2)))
    return f"ev_{name}_{highest + 1:04d}"


def _append_event(work: Path, event: dict) -> None:
    path = work / "events.jsonl"
    if path.exists() and path.stat().st_size and not path.read_bytes().endswith(b"\n"):
        with open(path, "ab") as f:
            f.write(b"\n")
    store.append_event(path, event)


FLOW_A_EVENTS = ("question", "quiz_result", "profile_edit", "note")


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _computed(node: Any) -> bool:
    """A system-computed object `{value, by, ...}` (rule: or llm:). `user:*` stamps (a skipped answer) are declared."""
    return isinstance(node, dict) and "by" in node and not str(node["by"]).startswith("user:")


def _holds_computed(node: Any) -> bool:
    if _computed(node):
        return True
    if isinstance(node, dict):
        return any(_holds_computed(v) for v in node.values())
    return False


def _kind(v: Any) -> str:
    return ("bool" if isinstance(v, bool) else "number" if _is_number(v) else "str" if isinstance(v, str)
            else "list" if isinstance(v, list) else "dict" if isinstance(v, dict) else "null")


def _quiz_score(payload: dict | None) -> float:
    """§3.4 {score, quiz_id}; §3.1 quiz_score is a 0-1 fraction. 422 for anything else, before anything is written."""
    score = (payload or {}).get("score")
    if not _is_number(score) or not 0 <= score <= 1:
        raise HTTPException(status_code=422, detail="a quiz_result payload needs `score`, a number from 0 to 1 (§3.4)")
    quiz_id = (payload or {}).get("quiz_id")
    if quiz_id is not None and not isinstance(quiz_id, str):
        raise HTTPException(status_code=422, detail="quiz_id must be a string")
    return score


def _apply_profile_edit(profile: dict, payload: dict | None) -> dict:
    """The profile with a `profile_edit` payload `{path, old, new}` applied (a copy). Only declared fields: the path
    names an existing key under identity / situation / objectives / risk / knowledge / values / preferences and no
    segment on the way is a computed `{value, by}` object (rule: or llm:), so limits, limits_trace, meta and every
    computed or llm: field are refused (422, inv. 1-3). A stale `old` is a 409. Schema failures are caught by `_save`'s
    validation (422) before the edit lands."""
    p = payload or {}
    path, new = p.get("path"), p.get("new")
    if not isinstance(path, str) or not path.strip() or "path" not in p or "new" not in p or "old" not in p:
        raise HTTPException(status_code=422, detail="a profile_edit payload needs `path`, `old` and `new` (§3.4)")
    parts = path.split(".")
    if len(parts) < 2 or parts[0] not in _EDITABLE_ROOTS or not all(parts):
        raise HTTPException(status_code=422, detail=f"{path!r} is not a user-editable declared field "
                            "(limits, limits_trace, meta and computed fields can never be written through the API)")
    out = copy.deepcopy(profile)
    parent: Any = out
    for seg in parts[:-1]:
        if not isinstance(parent, dict) or seg not in parent or _computed(parent[seg]):
            raise HTTPException(status_code=422, detail=f"{path!r} is not a user-editable declared field")
        parent = parent[seg]
    key = parts[-1]
    if not isinstance(parent, dict) or key not in parent or _holds_computed(parent[key]):
        raise HTTPException(status_code=422, detail=f"{path!r} is not a user-editable declared field")
    current = parent[key]
    if p["old"] != current:
        raise HTTPException(status_code=409, detail=f"the value at {path!r} is not the one the edit was made on "
                            "(stale `old`); nothing was changed")
    skipped = isinstance(current, dict) and str(current.get("by", "")).startswith("user:")
    if not skipped and current is not None and _kind(new) != _kind(current):
        raise HTTPException(status_code=422, detail=f"the value at {path!r} must be a {_kind(current)}")
    parent[key] = copy.deepcopy(new)
    return out


_EDITABLE_ROOTS = ("identity", "situation", "objectives", "risk", "knowledge", "values", "preferences")


def _flow_a(work: Path, profile_id: str, fixture: Path, body: EventIn) -> dict:
    """§4 Flow A for a question / quiz_result / profile_edit / note event, on the working copy. Everything is
    validated before anything is written; a failure after the append restores events.jsonl and profile.json."""
    path = work / "events.jsonl"
    stored = _load(work)
    edited = None
    if body.type == "quiz_result":
        score = _quiz_score(body.payload)
        edited = copy.deepcopy(stored)
        edited["knowledge"]["quiz_score"] = score
    elif body.type == "profile_edit":
        edited = _apply_profile_edit(stored, body.payload)
    if edited is not None:
        try:
            pipeline.validate_profile(edited, "edited profile")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    events = store.read_events(path)
    event = {"event_id": _next_event_id(events, fixture), "profile_id": profile_id, "ts": body.ts,
             "type": body.type, "text": body.text, "payload": body.payload, "source": body.source}
    if body.parent_event_id:
        event["parent_event_id"] = body.parent_event_id
    events_before = path.read_bytes() if path.exists() else b""
    profile_before = (work / "profile.json").read_bytes()
    try:
        _append_event(work, event)
        try:
            res = pipeline.refresh(work, edited=edited)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        audit = res["audit"]
        if audit["profile_version_out"] != audit["profile_version_in"]:
            _save(work, res["profile"])
    except BaseException:
        path.write_bytes(events_before)
        (work / "profile.json").write_bytes(profile_before)
        raise
    return {"event": event, "run_id": res["run_id"], "profile_diff": res["profile_diff"],
            "profile_version": audit["profile_version_out"]}


@app.post("/profiles/{profile_id}/events")
def post_event(profile_id: str, body: EventIn) -> dict:
    work, fixture = _workdir(profile_id)
    if body.type == "intent_confirmed" and not body.payload:
        raise HTTPException(status_code=422, detail="intent_confirmed needs the confirmed intent as payload (§3.4)")
    path = work / "events.jsonl"
    with _LOCK:
        if body.type in FLOW_A_EVENTS:
            return _flow_a(work, profile_id, fixture, body)
        events = store.read_events(path)
        event = {"event_id": _next_event_id(events, fixture), "profile_id": profile_id, "ts": body.ts,
                 "type": body.type, "text": body.text, "payload": body.payload, "source": body.source}
        if body.parent_event_id:
            event["parent_event_id"] = body.parent_event_id
        _append_event(work, event)
        if body.type == "order_intent":
            return {"event": event, "intent": _draft_intent(work, event)}
    return event


# ---------------------------------------------------------------- Flow B: draft intent (B1, B2) and confirm (B3-B9)

class FundingIn(_Body):
    """The funding the user states (SPEC §3.6): a kind and the one parameter that kind needs."""
    kind: Literal["specific", "pro_rata_all", "pro_rata_class", "cash_contribution"]
    ticker: str | None = Field(default=None, min_length=1)
    asset_class: str | None = Field(default=None, min_length=1)
    amount_cad: Annotated[float, Field(gt=0, strict=True)] | None = None

    @model_validator(mode="after")
    def _kind_parameter(self) -> "FundingIn":
        needs = {"specific": "ticker", "pro_rata_class": "asset_class", "cash_contribution": "amount_cad"}.get(self.kind)
        given = {k for k in ("ticker", "asset_class", "amount_cad") if getattr(self, k) is not None}
        if given != ({needs} if needs else set()):
            raise ValueError(f"funding {self.kind!r} takes " + (f"exactly {needs!r}" if needs else "no other field"))
        return self

    def as_dict(self) -> dict:
        out: dict = {"kind": self.kind}
        for k in ("ticker", "asset_class", "amount_cad"):
            v = getattr(self, k)
            if v is not None:
                out[k] = _plain_number(v) if k == "amount_cad" else v
        return out


class ConfirmIn(_Body):
    """The user-edited intent. Only these three fields: nothing else (limits, limits_trace, llm: ...) is accepted."""
    ticker: str = Field(min_length=1)
    target_weight_pct: Annotated[float, Field(gt=0, le=100, strict=True)]
    funding: FundingIn


def _plain_number(v: float) -> int | float:
    return int(v) if float(v).is_integer() else v


def _read_json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write_json(path: Path, doc: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _known_intent_ids() -> list[int]:
    """Numbers of every intent id in the fixtures and in every working copy (ids are unique across profiles)."""
    nums: list[int] = []
    dirs = list(_fixture_dirs().values()) + [store.root() / "profiles" / pid for pid in _fixture_dirs()]
    for d in dirs:
        try:
            records = _read_json(d / "intents.json", [])
        except (OSError, ValueError):
            continue
        for rec in records if isinstance(records, list) else []:
            m = _INTENT_ID.match(str(rec.get("intent_id", "")))
            if m:
                nums.append(int(m.group(1)))
    return nums


def _draft_intent(work: Path, event: dict) -> dict:
    """B1 (N4 through the gate) + B2 without auto-confirm: store the draft in the working copy and return it.

    D10: the draft holds only what the user stated. A field N4 listed as ambiguous (a default or a guess), and every
    field of a draft that failed the gate or never came back (cache miss), is null. Nothing is pre-filled."""
    intents_path = work / "intents.json"
    intents = _read_json(intents_path, [])
    intent_id = f"in_{max(_known_intent_ids(), default=0) + 1:04d}"
    blank = {k: None for k in pipeline.INTENT_KEYS}
    record = {"intent_id": intent_id, "event_id": event["event_id"], **blank, "status": "draft"}
    _write_json(intents_path, intents + [record])             # B1 reads the intent and its event from the working copy
    state = pipeline.b1_parse_intent(work, intent_id)
    parsed = state["draft"] or {}
    ambiguous = set(parsed.get("ambiguous_fields") or []) if parsed else set(pipeline.INTENT_KEYS)
    stated = {k: (None if k in ambiguous else parsed.get(k)) for k in pipeline.INTENT_KEYS}
    record = {**record, **stated}
    _write_json(intents_path, intents + [record])
    sidecar = work / "intent_drafts.json"
    _write_json(sidecar, {**_read_json(sidecar, {}), intent_id: {
        "draft_gate": state["draft_gate"], "llm_calls": state["llm_calls"], "llm_errors": state["llm_errors"],
        "draft": {**record} if state["draft"] else None}})
    return {**record, "unstated_fields": [k for k in pipeline.INTENT_KEYS if stated[k] is None]}


def _find_intent(intent_id: str) -> tuple[str, Path, dict]:
    """(profile_id, working copy, record) of a draft made through this API; 404 when no working copy holds it."""
    if _INTENT_ID.match(intent_id):
        for pid in sorted(_fixture_dirs()):
            work = store.root() / "profiles" / pid
            try:
                records = _read_json(work / "intents.json", [])
            except (OSError, ValueError):
                continue
            for rec in records if isinstance(records, list) else []:
                if rec.get("intent_id") == intent_id:
                    return pid, work, rec
    raise HTTPException(status_code=404, detail=f"unknown intent {intent_id!r}")


@app.post("/intents/{intent_id}/confirm")
def confirm_intent(intent_id: str, body: ConfirmIn) -> dict:
    """B2 (the user's confirmed values win over the draft) then B3-B9. The trade is only simulated, never executed.

    Everything is validated before anything is written. Confirming an intent twice is a 409 (no second event, no
    second run)."""
    profile_id, work, record = _find_intent(intent_id)
    fixture = _fixture_dirs()[profile_id]
    with _LOCK:
        records = _read_json(work / "intents.json", [])
        record = next(r for r in records if r.get("intent_id") == intent_id)
        if record.get("status") != "draft":
            raise HTTPException(status_code=409, detail=f"intent {intent_id!r} is already confirmed; "
                                "its run is stored and a second confirmation is not recorded")
        assets = data.load_assets()
        tickers = set(assets.index)
        funding = body.funding.as_dict()
        if body.ticker not in tickers:
            raise HTTPException(status_code=422, detail=f"ticker {body.ticker!r} is not in the asset universe")
        if funding.get("ticker") is not None and funding["ticker"] not in tickers:
            raise HTTPException(status_code=422, detail=f"funding ticker {funding['ticker']!r} is not in the universe")
        if funding.get("asset_class") is not None and funding["asset_class"] not in set(assets["asset_class"]):
            raise HTTPException(status_code=422, detail=f"unknown asset class {funding['asset_class']!r}")
        confirmed_at = pipeline._now()
        intent = {"intent_id": intent_id, "event_id": record["event_id"], "action": "buy", "ticker": body.ticker,
                  "target_weight_pct": _plain_number(body.target_weight_pct), "funding": funding,
                  "status": "confirmed", "confirmed_at": confirmed_at}
        pf = data.load_portfolio(work / "portfolio.json", assets)
        try:
            intent_mod.apply(pf["weights_pct"], intent, pf["value_cad"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"this trade cannot be applied: {exc}") from exc

        events_path, intents_path = work / "events.jsonl", work / "intents.json"
        events_before, intents_before = events_path.read_bytes(), intents_path.read_bytes()
        events = store.read_events(events_path)
        order_event = next((e for e in events if e["event_id"] == record["event_id"]), None)
        if order_event is None:
            raise HTTPException(status_code=409, detail=f"the event {record['event_id']!r} of this intent is missing")
        side = _read_json(work / "intent_drafts.json", {}).get(intent_id) or {}
        try:
            _append_event(work, {"event_id": _next_event_id(events, fixture), "profile_id": profile_id,
                                 "ts": confirmed_at, "type": "intent_confirmed",
                                 "text": f"Intent {intent_id} confirmed by the user.", "payload": intent,
                                 "source": DEFAULT_SOURCE, "parent_event_id": record["event_id"]})
            _write_json(intents_path, [intent if r.get("intent_id") == intent_id else r for r in records])
            state = {"fixture_dir": str(work), "intent_id": intent_id, "run_id": store.new_run_id(),
                     "started_at": pipeline._now(),
                     "event": {"event_id": order_event["event_id"], "text": order_event["text"]},
                     "fixture_intent": intent, "draft": side.get("draft"),
                     "draft_gate": side.get("draft_gate", "rejected:schema"),
                     "llm_calls": list(side.get("llm_calls") or []), "llm_errors": list(side.get("llm_errors") or [])}
            state = pipeline.b2_confirm(state, auto_confirm=True)
            for step in pipeline.FLOW_B_STEPS:
                state = step(state)
        except BaseException:
            events_path.write_bytes(events_before)
            intents_path.write_bytes(intents_before)
            raise
    return state["report"]


# ---------------------------------------------------------------- parsed-rule decisions (D7 point 1)

@app.post("/profiles/{profile_id}/parsed-rules/{index}/decision")
def post_decision(profile_id: str, index: int, body: DecisionIn) -> dict:
    work, _ = _workdir(profile_id)
    with _LOCK:
        profile = _load(work)
        rules = ((profile.get("values") or {}).get("parsed_notes") or {}).get("value")
        if not isinstance(rules, list) or not 0 <= index < len(rules):
            raise HTTPException(status_code=404, detail=f"no parsed rule at index {index}")
        rule = rules[index]
        if body.max_pct is not None:
            params = rule.get("params") if isinstance(rule.get("params"), dict) else {}
            current = params.get(intake.THEME_NUMBER_KEY)
            if rule.get("rule_kind") != "max_theme_weight":
                raise HTTPException(status_code=422, detail="max_pct only applies to a max_theme_weight rule")
            if isinstance(current, (int, float)) and not isinstance(current, bool):
                raise HTTPException(status_code=409, detail="this rule already has a number")
            rule["params"] = {**params, intake.THEME_NUMBER_KEY: body.max_pct}
        rule["approved_by_user"] = body.approved
        _save(work, profile)
    return profile


# ---------------------------------------------------------------- review, reports, audit

def _adopt_refreshed_profile(work: Path, report: dict) -> None:
    """The pipeline saves a refreshed profile (limits recomputed) as profile.v<N>.json in this very directory when a
    value changed. Make it the working profile so GET /profiles/{id} serves recomputed limits."""
    audit = store.load_audit(report["run_id"])
    if audit["profile_version_out"] != audit["profile_version_in"]:
        refreshed = work / f"profile.v{audit['profile_version_out']}.json"
        if refreshed.exists():
            _save(work, json.loads(refreshed.read_text(encoding="utf-8")))


@app.post("/profiles/{profile_id}/review")
def post_review(profile_id: str) -> dict:
    work, _ = _workdir(profile_id)
    with _LOCK:
        try:
            report = pipeline.review(work)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _adopt_refreshed_profile(work, report)
    return report


def _stored(loader, run_id: str, what: str) -> dict:
    if not _RUN_ID.match(run_id):
        raise HTTPException(status_code=404, detail=f"no {what} for run {run_id!r}")
    try:
        return loader(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"no {what} for run {run_id!r}") from exc


@app.get("/reports/{run_id}")
def get_report(run_id: str) -> dict:
    return _stored(store.load_report, run_id, "report")


@app.get("/audit/{run_id}")
def get_audit(run_id: str) -> dict:
    return _stored(store.load_audit, run_id, "audit record")
