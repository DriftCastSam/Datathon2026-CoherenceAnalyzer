"""N1 gate context = exactly the events the model was shown (P4.N1WIN).

Sources: SPEC §11 step 2 (every cited `event_id` ∈ the context events, any miss -> `rejected:evidence`), §1 inv. 4
(a rejection keeps the previous value) and inv. 5 (evidence references real events), §6.2 N1 (input = the last 30 days
of `question` + `order_intent` events, max 20: `extractors.n1_events`), §4 Flow B (B3 N1 -> gate; S1/S3 read the gated
field), CLAUDE.md evidence rule; docs/spec-decisions.md D1 (window anchored on portfolio.as_of).

Audit gap: `pipeline._run_n1` built the gate's `event_ids` from every event up to the order intent, not from the N1
payload. An answer citing a `quiz_result`, an event older than the window or an event beyond the 20-event cap passed
the gate. Here the gate context must be exactly the payload's ids; the recorded Robert answer is still accepted.

Offline: COHERENCE_ANALYZER_LLM=mock, no network, fixed events and dates, no clock value asserted.
"""
import contextlib
import copy
import io
import json
import os
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

from coherence_analyzer import cli, gate, pipeline, store  # noqa: E402
from coherence_analyzer.llm import bedrock, extractors  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "fixtures" / "robert"
AS_OF = "2026-11-12"
N1 = "behaviour_classifier"
REJECTED = "rejected:evidence"      # the status gate.py names for an evidence miss (GateStatus)

SHOWN_ROBERT = ["ev_robert_0004", "ev_robert_0005", "ev_robert_0007"]   # question + order_intent in the window
QUIZ_EVENT = "ev_robert_0001"                                           # a real event, but a quiz_result
OLD_EVENT = "ev_robert_0002"                                            # a real question, older than the window

_REAL_SAVE_REPORT = store.save_report
_REAL_CALL_TOOL = bedrock.call_tool


# ---------------------------------------------------------------- helpers

def _read(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _robert_events() -> list[dict]:
    return [json.loads(line) for line in (FIX / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def _ev(i: int, day: date, typ: str = "question") -> dict:
    ts = datetime.combine(day, datetime.min.time()).replace(hour=10, minute=i % 50).isoformat() + "-05:00"
    return {"event_id": f"ev_win_{i:04d}", "profile_id": "persona-robert", "ts": ts, "type": typ,
            "text": f"question number {i} about my portfolio", "payload": None, "source": "ui_chat"}


def _answer(event_ids: list[str], *, value: str = "moderate_high", confidence: float = 0.55) -> dict:
    return {"abstain": False, "value": value, "confidence": confidence,
            "evidence": [{"event_id": e, "signal": "hype_chasing", "note": "cites this event"} for e in event_ids]}


def _fake_call(monkeypatch, output: dict) -> list[dict]:
    """Replace the N1 call by a canned answer; records the events the real payload builder would send."""
    seen: list[dict] = []

    def fake(events, as_of=None):
        shown = extractors.n1_events(events, as_of)
        seen.append({"shown": [e["event_id"] for e in shown]})
        return {"output": copy.deepcopy(output),
                "call_log": {"node": N1, "model_id": "test-model", "prompt_version": "behaviour_classifier.v1",
                             "input_hash": "sha256:test", "cache_hit": True, "latency_ms": 0, "tokens_in": 0,
                             "tokens_out": 0}}

    monkeypatch.setattr(extractors, "behaviour_classifier_call", fake)
    return seen


def _spy_gate(monkeypatch) -> list[dict]:
    """Capture the context the pipeline hands to gate.gate for N1."""
    contexts: list[dict] = []
    real = gate.gate

    def spy(node, old, out, context):
        if node == N1:
            contexts.append(copy.deepcopy(context))
        return real(node, old, out, context)

    monkeypatch.setattr(gate, "gate", spy)
    return contexts


def _run_n1(events: list[dict], monkeypatch, output: dict):
    """pipeline._run_n1 on Robert's stored profile with a canned N1 answer. Returns (before, after, calls, seen, ctx)."""
    run = getattr(pipeline, "_run_n1")
    profile = _read(FIX / "profile.json")
    seen = _fake_call(monkeypatch, output)
    contexts = _spy_gate(monkeypatch)
    out, calls, errors = run(copy.deepcopy(profile), events, AS_OF)
    assert errors == []
    return profile, out, calls, seen, contexts


# ---------------------------------------------------------------- the gate context is the payload

def test_gate_context_event_ids_are_exactly_the_n1_payload_ids(monkeypatch):
    """§11 step 2 + §6.2 N1: the ids the gate accepts are the ids of `extractors.n1_events` (the payload), nothing
    else: not the quiz_result, not an older event, not an event after the window."""
    events = _robert_events()
    _, _, calls, seen, contexts = _run_n1(events, monkeypatch, _answer(["ev_robert_0007"]))
    assert seen == [{"shown": SHOWN_ROBERT}]
    assert len(contexts) == 1
    assert set(contexts[0]["event_ids"]) == set(SHOWN_ROBERT)
    assert QUIZ_EVENT not in contexts[0]["event_ids"]
    assert [c["gate"] for c in calls] == ["accepted"]


def test_gate_context_excludes_events_older_than_the_30_day_window(monkeypatch):
    """§6.2 N1 + D1: an event older than 30 days before as_of is not shown to the model, so it is not a valid citation."""
    events = _robert_events()
    old = _ev(2, date(2026, 9, 1))
    old["event_id"] = OLD_EVENT
    events = [events[0], old] + events[1:]
    _, _, _, seen, contexts = _run_n1(events, monkeypatch, _answer(["ev_robert_0007"]))
    assert OLD_EVENT not in seen[0]["shown"]
    assert set(contexts[0]["event_ids"]) == set(SHOWN_ROBERT)


def test_gate_context_is_capped_at_the_20_events_shown(monkeypatch):
    """§6.2 N1: at most 20 events are sent (the newest). Events dropped by the cap are not in the gate context."""
    events = [_ev(i, date(2026, 10, 20) + timedelta(days=i % 15)) for i in range(1, 26)]
    shown = [e["event_id"] for e in extractors.n1_events(events, date.fromisoformat(AS_OF))]
    assert len(shown) == 20 and len(events) == 25
    dropped = sorted(set(e["event_id"] for e in events) - set(shown))
    assert len(dropped) == 5
    _, _, _, seen, contexts = _run_n1(events, monkeypatch, _answer([shown[-1]]))
    assert seen[0]["shown"] == shown
    assert set(contexts[0]["event_ids"]) == set(shown)
    assert not set(contexts[0]["event_ids"]) & set(dropped)


# ---------------------------------------------------------------- an answer citing an unseen event is rejected

@pytest.mark.parametrize("cited", [QUIZ_EVENT, OLD_EVENT, "ev_not_shown_dropped"])
def test_n1_citing_an_event_the_model_was_not_shown_is_rejected_and_keeps_the_previous_value(monkeypatch, cited):
    """§11 step 2 + inv. 4: citing a real event outside the payload (quiz_result, older than the window) is
    `rejected:evidence`; `risk.behavioural` is left exactly as it was."""
    events = _robert_events()
    old = _ev(2, date(2026, 9, 1))
    old["event_id"] = OLD_EVENT
    events = [events[0], old] + events[1:]
    ghost = {"event_id": "ev_not_shown_dropped", "profile_id": "persona-robert", "type": "note", "text": "x",
             "ts": "2026-11-11T09:00:00-05:00", "payload": None, "source": "ui_chat"}
    events.append(ghost)
    # a different level and a higher confidence than the stored one: if the gate accepted it the value would move
    out_ans = _answer(["ev_robert_0007", cited], value="low", confidence=0.7)
    before, after, calls, _, _ = _run_n1(events, monkeypatch, out_ans)
    assert [c["gate"] for c in calls] == [REJECTED]
    assert after["risk"]["behavioural"] == before["risk"]["behavioural"]
    assert after == before


def test_one_unseen_citation_among_valid_ones_rejects_the_whole_answer(monkeypatch):
    """§11 step 2: "every event_id ∈ the context events" - a single miss rejects the answer, valid ids do not save it."""
    events = _robert_events()
    before, after, calls, _, _ = _run_n1(events, monkeypatch,
                                         _answer(["ev_robert_0004", "ev_robert_0007", QUIZ_EVENT], value="low"))
    assert [c["gate"] for c in calls] == [REJECTED]
    assert after["risk"]["behavioural"] == before["risk"]["behavioural"]


def test_n1_citing_only_shown_events_is_still_accepted(monkeypatch):
    """The window must not over-reject: every shown id is citable and the value moves (one level at most)."""
    events = _robert_events()
    before, after, calls, _, _ = _run_n1(events, monkeypatch, _answer(SHOWN_ROBERT, value="moderate_high"))
    assert [c["gate"] for c in calls] == ["accepted"]
    ev = after["risk"]["behavioural"]
    assert ev["value"] == "moderate_high" and ev["by"] == "llm:behaviour_classifier"
    assert [e["event_id"] for e in ev["evidence"]] == SHOWN_ROBERT


# ---------------------------------------------------------------- Flow B end to end (mock cache)

def _run(argv: list[str], cwd: Path, *, patch_output=None) -> dict:
    """cli.main(argv) offline in cwd; patch_output(node, output) may rewrite a node's answer (the real mock cache
    is still read). Returns {report, audit, calls: [{node, payload, output}], text}."""
    mp = pytest.MonkeyPatch()
    texts: list[str] = []
    calls: list[dict] = []

    def save_report(rep):
        path = _REAL_SAVE_REPORT(rep)
        texts.append(Path(path).read_text(encoding="utf-8"))
        return path

    def call_tool(node, schema, system, user_payload, prompt_version):
        result = _REAL_CALL_TOOL(node, schema, system, user_payload, prompt_version)
        if patch_output is not None:
            result = dict(result, output=patch_output(node, copy.deepcopy(result["output"])))
        calls.append({"node": node, "payload": copy.deepcopy(user_payload), "output": copy.deepcopy(result["output"])})
        return result

    mp.setattr(store, "save_report", save_report)
    mp.setattr(bedrock, "call_tool", call_tool)
    mp.chdir(cwd)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(argv)
        assert len(texts) == 1, "one Flow B run writes exactly one report"
        rep = json.loads(texts[0])
        audit = store.load_audit(rep["run_id"])
    finally:
        mp.undo()
    return {"report": rep, "audit": audit, "calls": calls, "text": texts[0]}


def _argv(fixture: Path) -> list[str]:
    return ["check", str(fixture), "--intent", "in_0001", "--auto-confirm"]


def _n1_calls(run: dict) -> list[dict]:
    return [c for c in run["audit"]["llm_calls"] if c["node"] == N1]


def _soft(run: dict, cid: str, side: str) -> list[dict]:
    return [c for c in run["report"][side] if c["id"] == cid]


def _cite(event_ids: list[str], **top):
    def patch(node, out):
        if node == N1:
            out["evidence"] = [{"event_id": e, "signal": "hype_chasing", "note": "cites this event"} for e in event_ids]
            out.update(top)
        return out
    return patch


def test_recorded_robert_n1_answer_is_still_accepted_with_the_same_outcome(tmp_path):
    """The recorded N1 answer cites ev_robert_0007 / 0004, both in the payload: gate accepted, S1 and S3 warn on
    the cited events (SPEC §7.4); the model saw exactly the three question / order_intent events (no quiz_result)."""
    run = _run(_argv(FIX), tmp_path)
    n1 = [c for c in run["calls"] if c["node"] == N1]
    assert len(n1) == 1
    shown = [e["event_id"] for e in n1[0]["payload"]["events"]]
    assert shown == SHOWN_ROBERT
    assert {e["event_id"] for e in n1[0]["output"]["evidence"]} <= set(shown)
    assert [c["gate"] for c in _n1_calls(run)] == ["accepted"]
    for side in ("checks_current", "checks_proposed"):
        s1 = _soft(run, "S1", side)
        assert len(s1) == 1 and s1[0]["status"] == "warning", side
    s3 = _soft(run, "S3", "checks_proposed")
    assert len(s3) == 1 and s3[0]["status"] == "warning" and "ev_robert_0007" in json.dumps(s3[0])
    assert run["report"]["verdict"] == "incoherent"


def test_n1_citing_the_quiz_result_is_rejected_in_flow_b_and_s1_s3_follow_the_kept_value(tmp_path):
    """SPEC §11 step 2 + inv. 4/5: ev_robert_0001 is a real event (a quiz_result) that was not shown to N1. The gate
    says rejected:evidence, the stored behavioural value (moderate_high, 0.55) stays, so S1 / S3 are computed from it
    and the quiz event never reaches a check or the profile diff."""
    run = _run(_argv(FIX), tmp_path, patch_output=_cite(["ev_robert_0007", QUIZ_EVENT], value="low", confidence=0.7))
    n1 = [c for c in run["calls"] if c["node"] == N1]
    assert [e["event_id"] for e in n1[0]["payload"]["events"]] == SHOWN_ROBERT
    assert [c["gate"] for c in _n1_calls(run)] == [REJECTED]
    assert not [d for d in run["audit"]["profile_diff"] if str(d["path"]).startswith("risk.behavioural")]
    for side in ("checks_current", "checks_proposed"):
        s1 = _soft(run, "S1", side)
        assert len(s1) == 1 and s1[0]["status"] == "warning", side           # the kept moderate_high vs low_moderate
        assert s1[0]["confidence"] == pytest.approx(0.55, abs=1e-9), side    # the kept confidence, not the rejected 0.7
        assert QUIZ_EVENT not in json.dumps(s1[0]), side
    s3 = _soft(run, "S3", "checks_proposed")
    assert len(s3) == 1 and s3[0]["status"] == "warning" and QUIZ_EVENT not in json.dumps(s3[0])
    assert all(c["kind"] == "hard" for c in run["report"]["checks_proposed"] if c["status"] == "violation")


def test_n1_citing_an_event_older_than_the_window_is_rejected_in_flow_b(tmp_path):
    """SPEC §6.2 N1 + D1: a real question event dated before as_of - 30 days is not in the payload (so the recorded
    cache entry is still hit); citing it is rejected:evidence and the previous value is kept."""
    fixture = tmp_path / "robert"
    shutil.copytree(FIX, fixture)
    events = _robert_events()
    old = _ev(2, date(2026, 9, 1))
    old["event_id"] = OLD_EVENT
    rows = [events[0], old] + events[1:]
    (fixture / "events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    run = _run(_argv(fixture), work, patch_output=_cite(["ev_robert_0007", OLD_EVENT]))
    n1 = [c for c in run["calls"] if c["node"] == N1]
    assert [e["event_id"] for e in n1[0]["payload"]["events"]] == SHOWN_ROBERT
    assert [c["gate"] for c in _n1_calls(run)] == [REJECTED]
    assert not [d for d in run["audit"]["profile_diff"] if str(d["path"]).startswith("risk.behavioural")]
    assert _soft(run, "S1", "checks_proposed")[0]["status"] == "warning"
    assert OLD_EVENT not in json.dumps(_soft(run, "S1", "checks_proposed"))
