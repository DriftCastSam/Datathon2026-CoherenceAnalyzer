"""P4.CLIDRAFT oracle: the CLI draft and the audit `intent_edits` never show a field the user did not state.

docs/spec-decisions.md D10 point 1 (nothing proposes a ticker, funding source or size the user did not state),
SPEC §3.6 / §4 Flow B B1-B2 (N4 draft, the user confirms or edits at B2), §12 CLI (`check` stops at B2 without
--auto-confirm), §10 audit (`intent_edits`). Reproduced gap: `cli check fixtures/robert --intent in_0001` printed
"draft intent (N4, gate accepted): buy NVDA to 10 % (funding: pro_rata_all)" while the cached N4 output lists
ambiguous_fields ["funding"]; the API already blanks such a field, the CLI did not, and the audit compared the
confirmed intent with the un-blanked draft.

Offline: LLM=mock, no network, each run in a temp cwd on a copy of the fixture.
"""
import contextlib
import copy
import io
import json
import os
import shutil
from pathlib import Path

import pytest

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

from coherence_analyzer import cli, pipeline, store  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "fixtures" / "robert"
INTENT_ID = "in_0001"
INTENT_KEYS = ("action", "ticker", "target_weight_pct", "funding")
NOT_STATED = "not stated"       # the task's wording: the CLI prints an ambiguous field "as not stated"


def _capture(fn, *args, **kw) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        fn(*args, **kw)
    return out.getvalue()


def _draft_line(text: str) -> str:
    lines = [ln for ln in text.splitlines() if "draft intent" in ln]
    assert len(lines) == 1, text
    return lines[0]


def _res(draft, fixture_intent=None, gate="accepted") -> dict:
    """The dict `pipeline.check` returns when it stops at B2, built by hand."""
    intent = fixture_intent or {"intent_id": INTENT_ID, "action": "buy", "ticker": "NVDA", "target_weight_pct": 10,
                                "funding": {"kind": "specific", "ticker": "ZST.TO"}, "status": "confirmed"}
    return {"status": pipeline.AWAITING, "intent_id": INTENT_ID, "draft": draft, "draft_gate": gate,
            "intent": intent, "event": {"event_id": "ev_robert_0007", "text": "I want to put 10% in NVDA"}}


def _draft(ambiguous=("funding",), **over) -> dict:
    d = {"action": "buy", "ticker": "NVDA", "target_weight_pct": 10, "funding": {"kind": "pro_rata_all"},
         "ambiguous_fields": list(ambiguous), "unresolved_mentions": []}
    d.update(over)
    return d


@pytest.fixture
def store_env(tmp_path, monkeypatch):
    monkeypatch.setenv("COHERENCE_ANALYZER_LLM", "mock")
    monkeypatch.setenv("COHERENCE_ANALYZER_STORE", "local")
    monkeypatch.delenv("COHERENCE_ANALYZER_STORE_DIR", raising=False)
    cwd = tmp_path / "work"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    return tmp_path


# ------------------------------------------------------------------ the CLI draft (B2)

def test_cli_check_draft_line_does_not_show_the_ambiguous_funding(store_env):
    # D10 pt 1 / §4 B2: end to end (mock cache: N4 lists ambiguous_fields ["funding"], guessed pro_rata_all).
    out = _capture(cli.main, ["check", str(FIX), "--intent", INTENT_ID])
    line = _draft_line(out)
    assert "pro_rata_all" not in line
    assert "pro_rata" not in line
    # the fields the user did state are still shown
    assert "buy" in line and "NVDA" in line and "10" in line


def test_cli_check_draft_marks_the_ambiguous_field_as_not_stated(store_env):
    # D10 pt 1: the field is shown as not stated, not silently dropped, and no default is put in its place.
    out = _capture(cli.main, ["check", str(FIX), "--intent", INTENT_ID])
    line = _draft_line(out)
    assert NOT_STATED in line
    assert "funding" in line
    assert "pro_rata_all" not in out.split("intent in_0001 on file")[0]      # nothing before the on-file line


def test_cli_check_still_waits_for_confirmation_and_saves_nothing(store_env):
    # §12 / §4 B2: the fix changes what is printed only; the run still stops at B2 with no report or audit.
    out = _capture(cli.main, ["check", str(FIX), "--intent", INTENT_ID])
    assert "--auto-confirm" in out
    assert not (store_env / "work" / "reports").exists()
    assert not (store_env / "work" / "audit").exists()


def test_print_draft_blanks_every_field_listed_ambiguous():
    # D10 pt 1: same rule as api._draft_intent: each field in ambiguous_fields is not stated, whichever it is.
    res = _res(_draft(ambiguous=("target_weight_pct", "funding"), target_weight_pct=37))
    line = _draft_line(_capture(cli._print_draft, res))
    assert "37" not in line and "pro_rata_all" not in line
    assert line.count(NOT_STATED) >= 2
    assert "NVDA" in line and "buy" in line


def test_print_draft_blanks_ticker_and_action_when_ambiguous():
    res = _res(_draft(ambiguous=("action", "ticker"), ticker="TSLA"))
    line = _draft_line(_capture(cli._print_draft, res))
    assert "TSLA" not in line
    assert "pro_rata_all" in line                  # funding is not ambiguous here: shown as N4 returned it
    assert line.count(NOT_STATED) >= 2


def test_print_draft_without_ambiguous_fields_shows_the_whole_draft():
    # Regression: a draft N4 is sure about is printed as before.
    res = _res(_draft(ambiguous=()))
    line = _draft_line(_capture(cli._print_draft, res))
    assert "buy NVDA to 10 %" in line
    assert "pro_rata_all" in line
    assert NOT_STATED not in line


def test_print_draft_missing_ambiguous_key_is_treated_as_none_ambiguous():
    # Same rule as the API: `parsed.get("ambiguous_fields") or []`.
    d = _draft()
    del d["ambiguous_fields"]
    line = _draft_line(_capture(cli._print_draft, _res(d)))
    assert "buy NVDA to 10 %" in line and "pro_rata_all" in line


def test_print_draft_rejected_draft_prints_none_and_shows_no_value():
    # §6.2 N4 gate: a draft that failed the gate is None; nothing is invented to fill it.
    line = _draft_line(_capture(cli._print_draft, _res(None, gate="rejected:schema")))
    assert "none" in line and "rejected:schema" in line
    assert "pro_rata" not in line and "NVDA" not in line


def test_print_draft_does_not_mutate_the_result():
    # The audit and the caller keep the raw N4 draft; blanking is a display rule.
    res = _res(_draft())
    before = copy.deepcopy(res)
    _capture(cli._print_draft, res)
    assert res == before


def test_print_draft_uses_no_recommending_words():
    # D10 pt 1/4: describing, never prescribing, in the new text too.
    out = _capture(cli._print_draft, _res(_draft())).lower()
    for phrase in ("you should", "consider", "we recommend", "next step", "tu devrais", "vous devriez"):
        assert phrase not in out


# ------------------------------------------------------------------ B2: intent_edits against the blanked draft

def _state(draft, intent=None) -> dict:
    intent = intent or {"intent_id": INTENT_ID, "event_id": "ev_robert_0007", "action": "buy", "ticker": "NVDA",
                        "target_weight_pct": 10, "funding": {"kind": "pro_rata_all"}, "status": "confirmed"}
    return {"fixture_intent": intent, "draft": draft, "draft_gate": "accepted"}


def test_b2_a_field_the_user_filled_in_counts_as_an_edit_even_if_equal_to_the_guess():
    # D10 pt 1 / §10: N4 guessed pro_rata_all and listed funding as ambiguous; the user confirmed pro_rata_all.
    # Against the blanked draft (funding None) that is a user edit. Only funding: the other fields were stated.
    out = pipeline.b2_confirm(_state(_draft()), auto_confirm=True)
    assert out["intent_edits"] == ["funding"]


def test_b2_every_ambiguous_field_is_an_edit_when_the_user_filled_it():
    out = pipeline.b2_confirm(_state(_draft(ambiguous=("ticker", "target_weight_pct"))), auto_confirm=True)
    assert out["intent_edits"] == ["ticker", "target_weight_pct"]      # INTENT_KEYS order


def test_b2_no_ambiguous_field_and_equal_values_means_no_edit():
    # Regression: confirmed == draft on every stated field -> empty edits.
    out = pipeline.b2_confirm(_state(_draft(ambiguous=())), auto_confirm=True)
    assert out["intent_edits"] == []


def test_b2_a_stated_field_the_user_changed_is_still_an_edit():
    intent = {"intent_id": INTENT_ID, "event_id": "ev_robert_0007", "action": "buy", "ticker": "NVDA",
              "target_weight_pct": 5, "funding": {"kind": "pro_rata_all"}, "status": "confirmed"}
    out = pipeline.b2_confirm(_state(_draft(ambiguous=()), intent), auto_confirm=True)
    assert out["intent_edits"] == ["target_weight_pct"]


def test_b2_without_a_draft_every_filled_field_is_an_edit():
    # §6.2 N4: no draft (gate rejected) -> nothing was stated by N4; all four fields come from the user.
    out = pipeline.b2_confirm(_state(None), auto_confirm=True)
    assert out["intent_edits"] == list(INTENT_KEYS)


def test_b2_does_not_mutate_the_draft_kept_for_the_audit():
    # The audit stores `intent_draft` as N4 returned it (with ambiguous_fields); only the comparison blanks.
    st = _state(_draft())
    before = copy.deepcopy(st["draft"])
    out = pipeline.b2_confirm(st, auto_confirm=True)
    assert st["draft"] == before and out["draft"] == before


def test_b2_without_auto_confirm_still_stops_and_has_no_edits():
    out = pipeline.b2_confirm(_state(_draft()), auto_confirm=False)
    assert out["awaiting_confirmation"] is True
    assert "intent_edits" not in out


def test_audit_intent_edits_count_a_user_filled_ambiguous_field(store_env):
    # §10 audit end to end: a copy of Robert whose confirmed in_0001 funds NVDA with exactly what N4 guessed
    # (pro_rata_all, listed ambiguous). The audit's intent_edits is ["funding"]; the other fields were stated.
    fx = store_env / "fixtures" / "robert"
    shutil.copytree(FIX, fx)
    intents = json.loads((fx / "intents.json").read_text(encoding="utf-8"))
    intents[0]["funding"] = {"kind": "pro_rata_all"}
    (fx / "intents.json").write_text(json.dumps(intents), encoding="utf-8")
    rep = pipeline.check(fx, INTENT_ID, auto_confirm=True)
    audit = store.load_audit(rep["run_id"])
    assert audit["intent_edits"] == ["funding"]
    # SPEC-AMBIGUITY: §10 does not say whether the stored draft is blanked; stricter reading of "never show": the
    # raw N4 output keeps listing what it flagged, so the audit stays truthful about what N4 returned.
    assert "funding" in (audit["intent_draft"] or {}).get("ambiguous_fields", [])


def test_audit_intent_edits_on_the_shipped_robert_fixture(store_env):
    # Shipped in_0001 funds from ZST.TO (user value) while N4 flagged funding ambiguous -> funding is an edit.
    rep = pipeline.check(FIX, INTENT_ID, auto_confirm=True)
    audit = store.load_audit(rep["run_id"])
    assert audit["intent_edits"] == ["funding"]
