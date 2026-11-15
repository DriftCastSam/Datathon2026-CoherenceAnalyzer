"""P3.S4: S4 reminds about every parsed rule that is not promoted into limits, and the dead explain stubs are gone.

Sources: SPEC §7.4 S4 ("Unapproved or non-checkable parsed values rules": info reminder "you wrote: '<quote>'"),
§6.2 N3 + the §5 promotion note (only approved `exclude_activity` / `max_theme_weight` rules are promoted into
`limits`; "other kinds stay soft reminders (S4)"), docs/spec-factors.md §20.2 (preferences row: S4, info only),
docs/spec-decisions.md D5 (section facts and `template_section` replaced `build_payload` / `template_explain`),
D10 (describe, never prescribe).

Offline and deterministic: COHERENCE_ANALYZER_LLM=mock, no network, no clock.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from coherence_analyzer import explain
from coherence_analyzer.coherence import report, soft
from coherence_analyzer.rules import derive, limits as limits_mod

ROOT = Path(__file__).resolve().parents[2]
AS_OF = "2026-11-12"
ROBERT_VALUE = 400000

Q_TAG = "no oil"
Q_THEME = "not too much tech"
Q_THEME_NUM = "at most 40% in tech"
Q_PREFER = "I prefer businesses I understand"
Q_AVOID = "I avoid speculative things"
Q_OTHER = "I like round numbers"


@pytest.fixture(autouse=True)
def _mock_llm(monkeypatch):
    monkeypatch.setenv("COHERENCE_ANALYZER_LLM", "mock")


def _rule(kind: str, params: dict, quote: str, approved: bool, applies_to: str = "all") -> dict:
    return {"rule_kind": kind, "params": params, "applies_to": applies_to, "quote": quote,
            "approved_by_user": approved}


def _profile(rules: list[dict]) -> dict:
    return {"values": {"notes": " ".join(r["quote"] for r in rules),
                       "parsed_notes": {"value": rules, "confidence": 0.8, "by": "llm:values_parser"}}}


def _s4(rules: list[dict]) -> list[dict]:
    return [c for c in soft.s4(_profile(rules)) if c["id"] == "S4"]


def _quotes(checks: list[dict]) -> list[str]:
    # SPEC-AMBIGUITY: §7.4 gives only the rendered form "you wrote: '<quote>'"; the existing contract carries the
    # quote in `subject`, and the reminder is rendered from it. Pin that (stricter than "somewhere in the check").
    return [c["subject"] for c in checks]


# ------------------------------------------------------------------ S4 emission rules

def test_unapproved_rule_of_every_kind_gets_an_info_reminder():
    """§7.4 S4: one info reminder per unapproved parsed rule, whatever its kind (unchanged behaviour)."""
    rules = [_rule("exclude_activity", {"tag": "fossil_fuel_producer"}, Q_TAG, False),
             _rule("max_theme_weight", {"theme": "tech_heavy", "max_pct": 40}, Q_THEME_NUM, False),
             _rule("prefer_attribute", {"attribute": "understandable_business"}, Q_PREFER, False),
             _rule("avoid_attribute", {"attribute": "speculative"}, Q_AVOID, False),
             _rule("other", {}, Q_OTHER, False)]
    out = _s4(rules)
    assert sorted(_quotes(out)) == sorted(r["quote"] for r in rules)
    for c in out:
        assert c["kind"] == "soft" and c["status"] == "info"


@pytest.mark.parametrize("rule", [
    _rule("prefer_attribute", {"attribute": "understandable_business"}, Q_PREFER, True, "single_stocks"),
    _rule("avoid_attribute", {"attribute": "speculative"}, Q_AVOID, True),
    _rule("other", {}, Q_OTHER, True),
], ids=["prefer_attribute", "avoid_attribute", "other"])
def test_approved_rule_that_is_not_promoted_still_gets_a_reminder(rule):
    """§7.4 S4 + §6.2 N3: approved prefer/avoid/other rules are not promoted into limits, so they stay S4 reminders."""
    out = _s4([rule])
    assert len(out) == 1
    c = out[0]
    assert _quotes(out) == [rule["quote"]]
    assert c["kind"] == "soft" and c["status"] == "info"


@pytest.mark.parametrize("rule", [
    _rule("exclude_activity", {"tag": "fossil_fuel_producer"}, Q_TAG, True),
    _rule("max_theme_weight", {"theme": "tech_heavy", "max_pct": 40}, Q_THEME_NUM, True),
], ids=["exclude_activity", "max_theme_weight"])
def test_approved_promoted_rule_gets_no_reminder(rule):
    """§5 promotion note: approved exclude_activity / max_theme_weight (with a number) live in limits (H11/H13): no S4."""
    assert _s4([rule]) == []


def test_approved_theme_rule_without_a_number_is_not_promoted_so_it_is_reminded():
    """§6.2 N3 (amira: max_theme_weight with no number): until the number exists nothing is promoted into limits,
    so the rule is still only a soft reminder."""
    rule = _rule("max_theme_weight", {"theme": "tech_heavy"}, Q_THEME, True)
    # SPEC-AMBIGUITY: the task says "every approved rule that is not promoted"; limits skips a theme rule with no
    # numeric max_pct, so the stricter reading is that it still gets the reminder.
    assert _quotes(_s4([rule])) == [Q_THEME]


def test_mixed_batch_emits_exactly_the_non_promoted_rules():
    """§7.4 S4: unapproved + approved-non-promoted are reminded; approved-promoted are not."""
    promoted = [_rule("exclude_activity", {"tag": "fossil_fuel_producer"}, Q_TAG, True),
                _rule("max_theme_weight", {"theme": "tech_heavy", "max_pct": 40}, Q_THEME_NUM, True)]
    reminded = [_rule("prefer_attribute", {"attribute": "understandable_business"}, Q_PREFER, True),
                _rule("avoid_attribute", {"attribute": "speculative"}, Q_AVOID, False),
                _rule("other", {}, Q_OTHER, True),
                _rule("exclude_activity", {"tag": "tobacco"}, "no tobacco", False)]
    out = _s4(promoted + reminded)
    assert sorted(_quotes(out)) == sorted(r["quote"] for r in reminded)


def test_reminder_quote_comes_from_the_rule_and_is_a_substring_of_the_notes():
    """§7.4 S4 'you wrote: <quote>' + §6.2 N3 (quote is an exact substring of notes): nothing is invented."""
    rules = [_rule("prefer_attribute", {"attribute": "understandable_business"}, Q_PREFER, True),
             _rule("avoid_attribute", {"attribute": "speculative"}, Q_AVOID, False)]
    p = _profile(rules)
    for c in soft.s4(p):
        assert c["subject"] in p["values"]["notes"]


def test_no_rules_or_no_parsed_notes_gives_no_reminder():
    """§7.4 S4: nothing parsed, nothing to remind about."""
    assert soft.s4({"values": {"notes": ""}}) == []
    assert _s4([]) == []


def test_s4_is_info_only_never_violation_and_not_in_the_verdict():
    """Invariant 3 + D3 + §20.2 (preferences: S4, info only): reminders never change the verdict."""
    rules = [_rule("prefer_attribute", {"attribute": "understandable_business"}, Q_PREFER, True),
             _rule("other", {}, Q_OTHER, False)]
    out = soft.run_all(_profile(rules), None)
    s4 = [c for c in out if c["id"] == "S4"]
    assert len(s4) == 2
    assert {c["status"] for c in s4} == {"info"}
    assert all(c["status"] in soft.ALLOWED for c in out)
    assert report.verdict(s4) == report.verdict([])


def test_s4_reminders_are_not_a_limits_input_and_limits_do_not_change():
    """Invariant 2: S4 reads the LLM-parsed field but only emits info; limits for approved prefer/avoid rules stay
    identical to the same profile with no parsed rules (they are reminders, not promoted)."""
    prof = json.loads((ROOT / "fixtures" / "robert" / "profile.json").read_text(encoding="utf-8"))

    def lim(rules):
        p = copy.deepcopy(prof)
        p["values"]["parsed_notes"]["value"] = rules
        derived = derive.derive_all(p, portfolio_value_cad=ROBERT_VALUE, as_of=AS_OF)
        block, _ = limits_mod.build_limits(derived, ROBERT_VALUE)
        return {k: v for k, v in block.items() if k not in ("_note", "by")}

    soft_rules = [_rule("prefer_attribute", {"attribute": "understandable_business"}, Q_PREFER, True),
                  _rule("avoid_attribute", {"attribute": "speculative"}, Q_AVOID, True)]
    assert lim(soft_rules) == lim([])
    assert len(_s4(soft_rules)) == 2


def test_reminder_text_in_the_check_does_not_prescribe():
    """D10: the reminder only echoes the user's own words; its message_key is a plain key, not advice."""
    out = _s4([_rule("prefer_attribute", {"attribute": "understandable_business"}, Q_PREFER, True)])
    assert len(out) == 1
    assert re.fullmatch(r"[a-z0-9_]+", out[0]["message_key"])
    assert explain.advisory_hits(out[0]["message_key"].replace("_", " ")) == []


# ------------------------------------------------------------------ dead explain stubs

def test_dead_explain_stubs_are_removed():
    """D5: build_payload / template_explain were replaced by section facts and template_section; the NotImplemented
    stubs are deleted (nothing in code or tests imports them)."""
    assert not hasattr(explain, "build_payload")
    assert not hasattr(explain, "template_explain")


def test_explain_keeps_the_live_entry_points():
    """D5: the removal leaves numcheck, template_section, verify_section, narrate_section and explain in place."""
    for name in ("numcheck", "template_section", "verify_section", "narrate_section", "explain"):
        assert callable(getattr(explain, name, None)), name


def test_nothing_in_the_package_or_tests_references_the_removed_names():
    """D5: grep-style guard. No module of the package, and no test other than this file, uses the removed names."""
    names = ("explain.build_payload", "explain.template_explain", "import build_payload", "import template_explain")
    here = Path(__file__).resolve()
    files = [f for d in (ROOT / "coherence_analyzer", ROOT / "tests") for f in d.rglob("*.py") if f.resolve() != here]
    hits = [f"{f.relative_to(ROOT)}: {n}" for f in files for n in names if n in f.read_text(encoding="utf-8")]
    assert hits == []
