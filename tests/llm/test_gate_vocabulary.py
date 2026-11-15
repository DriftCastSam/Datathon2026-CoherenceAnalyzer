"""P3.GATE: the gate enforces the N3 vocabulary and range, limits skip a theme-less rule, N4 drafts are buy only.

SPEC §6.2 N3 (`exclude_activity` params `{tag}` from the activity_tags vocabulary; `max_theme_weight` `{theme, max_pct}`),
§3.2 (the tag vocabulary: tobacco, accounting_fraud_last_5y, significant_environmental_controversy,
significant_social_controversy, fossil_fuel_producer; themes come from asset_meta.csv), §11 step 3 (enum/range
re-asserted by the gate), §7.2 (limits), §6.2 N4 + §3.6 (an intent is a buy; intent.apply has no sell semantics),
§1 inv. 2, 3, 4 (a rejected output keeps the previous value).

Read-only: asset_meta.csv is read to know the theme vocabulary; activity_tags.csv is never read or written here.
"""
from __future__ import annotations

import copy
import csv
import json
from pathlib import Path

import pytest

from coherence_analyzer.gate import gate
from coherence_analyzer.rules import derive, limits as limits_mod

ROOT = Path(__file__).resolve().parents[2]
AS_OF = "2026-11-12"
ROBERT_VALUE = 400000

# SPEC §3.2 names exactly these activity tags.
SPEC_TAGS = ("tobacco", "accounting_fraud_last_5y", "significant_environmental_controversy",
             "significant_social_controversy", "fossil_fuel_producer")
NOTES = ("I don't want fossil fuels or tobacco. Keep tech under 30 percent. "
         "No more than 100 percent in any one theme, and some oil and gas is fine.")
CTX = {
    "event_ids": set(),
    "notes": NOTES,
    "model_id": "fixture:hand-written",
    "prompt_version": "values_parser.v1",
    "input_hash": "sha256:" + "cd" * 32,
}


def _themes() -> set[str]:
    """Themes present in data/meta/asset_meta.csv (read-only)."""
    out: set[str] = set()
    with (ROOT / "data" / "meta" / "asset_meta.csv").open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            out.update(t.strip() for t in (row.get("themes") or "").split(";") if t.strip())
    return out


def _rule(kind: str, params: dict, quote: str, applies_to: str = "all") -> dict:
    return {"rule_kind": kind, "params": params, "applies_to": applies_to, "quote": quote}


def _tag_rule(tag: object, quote: str = "I don't want fossil fuels") -> dict:
    return _rule("exclude_activity", {"tag": tag}, quote)


def _theme_rule(params: dict, quote: str = "Keep tech under 30 percent") -> dict:
    return _rule("max_theme_weight", params, quote)


def _old_field() -> dict:
    """A previously accepted N3 field with one user-approved rule, to check that a rejection keeps it."""
    return {"value": [{"rule_kind": "prefer_attribute", "params": {"attribute": "understandable_business"},
                       "applies_to": "all", "quote": "some oil and gas is fine", "approved_by_user": True}],
            "by": "llm:values_parser", "confidence": 0.8, "prompt_version": "values_parser.v0",
            "model_id": "fixture:old", "updated_at": "2026-11-01T00:00:00-05:00", "input_hash": "sha256:old"}


def _run(*rules: dict, old: dict | None = None) -> tuple[dict | None, str]:
    return gate("values_parser", copy.deepcopy(old), {"rules": list(rules)}, CTX)


def _assert_rejected_and_old_kept(new, status, old) -> None:
    # SPEC-AMBIGUITY: "rejected by the gate with a reason (the rule is dropped, never coerced)". §11 lists only the
    # statuses accepted|clamped|kept:abstain|rejected:evidence|rejected:schema and step 3 (enum/range) maps to
    # rejected:schema, which keeps the previous value (§1 inv. 4). Stricter reading: the whole output is rejected.
    assert status == "rejected:schema"
    assert new == old


# ---------------------------------------------------------------- exclude_activity tag vocabulary (§3.2, §6.2 N3)

@pytest.mark.parametrize("tag", SPEC_TAGS)
def test_every_tag_the_spec_names_is_accepted_and_starts_unapproved(tag):
    new, status = _run(_tag_rule(tag))
    assert status == "accepted"
    assert [r["params"] for r in new["value"]] == [{"tag": tag}]          # kept verbatim, not coerced
    assert all(r["approved_by_user"] is False for r in new["value"])


@pytest.mark.parametrize("tag", ["fossil fuels", "Fossil_Fuel_Producer", "tobacco ", "gambling", "", "oil_gas"])
def test_free_form_exclude_activity_tag_is_rejected_and_old_value_kept(tag):
    old = _old_field()
    new, status = _run(_tag_rule(tag), old=old)
    _assert_rejected_and_old_kept(new, status, old)


@pytest.mark.parametrize("params", [{}, {"tag": None}, {"tag": 7}, {"tag": ["tobacco"]}, {"theme": "tech_heavy"}])
def test_exclude_activity_without_a_string_tag_is_rejected(params):
    # SPEC-AMBIGUITY: a missing tag is "not one of the vocabulary"; stricter reading rejects it.
    old = _old_field()
    new, status = _run(_rule("exclude_activity", params, "I don't want fossil fuels"), old=old)
    _assert_rejected_and_old_kept(new, status, old)


def test_rejected_tag_is_never_coerced_to_a_vocabulary_tag():
    new, status = _run(_tag_rule("fossil fuels"))
    assert status == "rejected:schema"
    assert new is None                              # no previous value: nothing is created from the bad rule
    assert "fossil_fuel_producer" not in json.dumps(new)


def test_one_bad_tag_among_valid_rules_rejects_the_output():
    # SPEC-AMBIGUITY: same stricter reading as §1 inv. 5 (invented evidence rejects the whole output).
    old = _old_field()
    new, status = _run(_tag_rule("tobacco", "I don't want fossil fuels or tobacco"), _tag_rule("fossil fuels"), old=old)
    _assert_rejected_and_old_kept(new, status, old)


# ---------------------------------------------------------------- max_theme_weight theme + max_pct (§3.2, §6.2 N3)

def test_theme_vocabulary_comes_from_asset_meta():
    themes = _themes()
    assert {"tech_heavy", "oil_gas", "esg_screened"} <= themes      # sanity: the file the gate must agree with


def test_every_asset_meta_theme_is_accepted():
    for theme in sorted(_themes()):
        new, status = _run(_theme_rule({"theme": theme, "max_pct": 30}))
        assert status == "accepted", theme
        assert new["value"][0]["params"] == {"theme": theme, "max_pct": 30}
        assert new["value"][0]["approved_by_user"] is False


def test_theme_rule_without_a_number_is_accepted():
    # §6.2 N3 / spec-decisions D7: max_pct only when the user gave a number (Amira's "pas tout en tech").
    new, status = _run(_theme_rule({"theme": "tech_heavy"}))
    assert status == "accepted"
    assert new["value"][0]["params"] == {"theme": "tech_heavy"}


@pytest.mark.parametrize("theme", ["technology", "tech heavy", "TECH_HEAVY", "crypto_assets", "", "ai_and_new_tech"])
def test_theme_not_in_asset_meta_is_rejected_and_old_value_kept(theme):
    assert theme not in _themes()
    old = _old_field()
    new, status = _run(_theme_rule({"theme": theme, "max_pct": 30}), old=old)
    _assert_rejected_and_old_kept(new, status, old)


@pytest.mark.parametrize("params", [{"max_pct": 30}, {"theme": None, "max_pct": 30}, {"theme": 3, "max_pct": 30}, {}])
def test_max_theme_weight_without_a_string_theme_is_rejected(params):
    # SPEC-AMBIGUITY: a missing theme is "not one of the asset_meta themes"; stricter reading rejects it.
    old = _old_field()
    new, status = _run(_theme_rule(params), old=old)
    _assert_rejected_and_old_kept(new, status, old)


@pytest.mark.parametrize("max_pct", [0, 0.0, -5, -0.01, 100.01, 150, 1000])
def test_max_pct_outside_zero_exclusive_to_hundred_inclusive_is_rejected(max_pct):
    old = _old_field()
    new, status = _run(_theme_rule({"theme": "tech_heavy", "max_pct": max_pct}), old=old)
    _assert_rejected_and_old_kept(new, status, old)


@pytest.mark.parametrize("max_pct", ["30", "thirty", None, True, False, [30], {"v": 30}])
def test_max_pct_that_is_not_a_number_is_rejected(max_pct):
    old = _old_field()
    new, status = _run(_theme_rule({"theme": "tech_heavy", "max_pct": max_pct}), old=old)
    _assert_rejected_and_old_kept(new, status, old)


@pytest.mark.parametrize("max_pct", [0.01, 0.5, 30, 49.5, 100, 100.0])
def test_max_pct_inside_the_range_is_kept_as_given(max_pct):
    new, status = _run(_theme_rule({"theme": "tech_heavy", "max_pct": max_pct}))
    assert status == "accepted"
    assert new["value"][0]["params"]["max_pct"] == max_pct            # percent stays percent (conversion is limits')


def test_quote_check_still_runs_before_the_vocabulary_check():
    # §11 step order: evidence (2) before enum/range (3): an invented quote with a bad tag is rejected:evidence.
    old = _old_field()
    new, status = _run(_tag_rule("fossil fuels", "a sentence the user never wrote"), old=old)
    assert status == "rejected:evidence"
    assert new == old


def test_abstain_still_wins_over_the_vocabulary_check():
    old = _old_field()
    new, status = gate("values_parser", copy.deepcopy(old),
                       {"abstain": True, "rules": [_tag_rule("fossil fuels")]}, CTX)
    assert status == "kept:abstain"
    assert new == old


def test_other_rule_kinds_are_not_subject_to_the_tag_or_theme_vocabulary():
    # prefer/avoid/other carry free `attribute`/`note` params (§6.2 N3): they stay accepted.
    new, status = _run(
        _rule("prefer_attribute", {"attribute": "ai_and_new_tech"}, "some oil and gas is fine"),
        _rule("avoid_attribute", {"attribute": "speculative"}, "I don't want fossil fuels"),
        _rule("other", {"note": "free text"}, "I don't want fossil fuels"))
    assert status == "accepted"
    assert len(new["value"]) == 3


def test_a_vocabulary_valid_output_keeps_a_previously_approved_identical_rule_approved():
    # Existing behaviour (§3.3 #4): re-parsing must not silently drop a user action. The new check must not break it.
    rule = _tag_rule("fossil_fuel_producer")
    old = {**_old_field(), "value": [{**rule, "approved_by_user": True}]}
    new, status = _run(rule, old=old)
    assert status == "accepted"
    assert [r["approved_by_user"] for r in new["value"]] == [True]


# ---------------------------------------------------------------- limits never KeyError on a theme-less rule (§7.2)

def _robert() -> dict:
    return json.loads((ROOT / "fixtures" / "robert" / "profile.json").read_text(encoding="utf-8"))


def _limits_with(extra_rules: list[dict]) -> tuple[dict, dict]:
    profile = _robert()
    profile["values"]["parsed_notes"]["value"] = copy.deepcopy(extra_rules)
    derived = derive.derive_all(profile, portfolio_value_cad=ROBERT_VALUE, as_of=AS_OF)
    return limits_mod.build_limits(derived, ROBERT_VALUE)


def _approved(rule: dict) -> dict:
    return {**rule, "approved_by_user": True}


THEMELESS = [
    {"max_pct": 30},
    {},
    {"theme": None, "max_pct": 30},
    {"theme": "", "max_pct": 30},
    {"theme": 3, "max_pct": 30},
]


@pytest.mark.parametrize("params", THEMELESS)
def test_approved_theme_less_rule_does_not_raise_and_is_not_promoted(params):
    lim, _ = _limits_with([_approved(_theme_rule(params))])           # must not KeyError
    assert not lim.get("max_theme_weight")


@pytest.mark.parametrize("params", THEMELESS)
def test_approved_theme_less_rule_is_skipped_with_a_reason_in_the_trace(params):
    _, base_trace = _limits_with([])
    _, trace = _limits_with([_approved(_theme_rule(params))])
    extra = [p for p in trace["max_theme_weight"] if p not in base_trace["max_theme_weight"]]
    # SPEC-AMBIGUITY: §7.2 fixes no wording for the skip reason; assert that the trace gains an entry that names
    # the theme, rather than a particular string.
    assert extra, "the skip is traced"
    assert any("theme" in p.lower() for p in extra)


def test_a_theme_less_rule_does_not_block_a_valid_approved_theme_rule():
    good = _approved(_theme_rule({"theme": "tech_heavy", "max_pct": 50}))
    lim, _ = _limits_with([_approved(_theme_rule({"max_pct": 30})), good])
    assert lim["max_theme_weight"] == {"tech_heavy": pytest.approx(0.5, abs=1e-9)}   # percent -> fraction


def test_an_unapproved_theme_less_rule_leaves_limits_and_trace_unchanged():
    base, base_trace = _limits_with([])
    lim, trace = _limits_with([_theme_rule({"max_pct": 30})])
    assert {k: v for k, v in lim.items() if k not in ("_note", "by")} == \
        {k: v for k, v in base.items() if k not in ("_note", "by")}
    assert trace == base_trace


def test_approved_exclude_activity_rule_without_a_tag_does_not_raise():
    lim, _ = _limits_with([_approved(_rule("exclude_activity", {}, "I don't want fossil fuels"))])
    assert "fossil_fuel_producer" not in json.dumps(lim)


# ---------------------------------------------------------------- N4: a draft is buy only (§6.2 N4, §3.6)

N4_CTX = {**CTX, "prompt_version": "intent_parser.v1", "notes": "", "event_ids": {"ev_robert_0007"},
          "universe": ["NVDA", "ZST.TO", "XIU.TO"], "tickers": ["NVDA", "ZST.TO", "XIU.TO"]}


def _draft(action: str | None = "buy") -> dict:
    return {"action": action, "ticker": "NVDA", "target_weight_pct": 10,
            "funding": {"kind": "specific", "ticker": "ZST.TO"},
            "ambiguous_fields": [], "unresolved_mentions": []}


@pytest.mark.parametrize("action", ["sell", "Sell", "short", "hold", "", None])
def test_n4_draft_with_a_non_buy_action_is_rejected_and_never_carries_that_action(action):
    new, status = gate("intent_parser", None, _draft(action), N4_CTX)
    # SPEC-AMBIGUITY: §11 has no N4 status of its own; the existing draft gate (pipeline._gate_intent) uses
    # rejected:schema for a non-buy action, so that is the reason expected here.
    assert status == "rejected:schema"
    # "the draft carries action null": either no draft at all, or a draft whose action is null; never a sell.
    assert new is None or (isinstance(new, dict) and new.get("action") is None)


def test_n4_sell_draft_is_not_confirmable_in_any_returned_form():
    new, status = gate("intent_parser", None, _draft("sell"), N4_CTX)
    assert status.startswith("rejected")
    assert "sell" not in json.dumps(new)


def test_n4_rejected_sell_does_not_leak_into_a_previous_draft():
    old = _draft("buy")
    new, status = gate("intent_parser", copy.deepcopy(old), _draft("sell"), N4_CTX)
    assert status.startswith("rejected")
    assert "sell" not in json.dumps(new)


def test_n4_buy_draft_with_a_universe_ticker_passes_the_gate():
    # SPEC-AMBIGUITY: the N4 gate's context keys are not specified; the context offers the universe under both
    # plausible names and the gate may also read the real universe itself. NVDA/ZST.TO are in the universe.
    new, status = gate("intent_parser", None, _draft("buy"), N4_CTX)
    assert status == "accepted"
    assert isinstance(new, dict)
    assert new.get("action") == "buy" and new.get("ticker") == "NVDA"


def test_n4_gate_does_not_mutate_its_inputs():
    draft, ctx = _draft("sell"), copy.deepcopy(N4_CTX)
    before = copy.deepcopy(draft)
    gate("intent_parser", None, draft, ctx)
    assert draft == before
    assert ctx == N4_CTX
