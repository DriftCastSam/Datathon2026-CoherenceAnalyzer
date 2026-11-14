"""P0.10: intake form per factor (deterministic), N3 values_parser schema/prompt, parsed-rule approval.

Sources: docs/spec-decisions.md D7 (points 1 and 4), docs/spec-factors.md §20.6, SPEC §6.2 N3, §7.2
(`excluded_activities`, `max_theme_weight`), §11 (gate), §1 inv. 2 and 5.

Offline and deterministic: COHERENCE_ANALYZER_LLM=mock (forced per test), no network, no clock.
Names that have no stub (intake.REQUIRED) are read with getattr inside the tests.
"""
from __future__ import annotations

import copy
import json
import re

import pytest
from jsonschema import Draft202012Validator

from coherence_analyzer import config, gate as gate_mod, intake
from coherence_analyzer.llm import bedrock, extractors
from coherence_analyzer.rules import derive, limits as limits_mod

FACTORS = ("objectif", "horizon", "risque", "contraintes", "preferences")
LOCALES = ("fr-CA", "en-CA")
AS_OF = "2026-11-12"        # portfolio.as_of (docs/spec-decisions.md D1)
ROBERT_VALUE = 400000
TAGS_CSV = config.META_DIR / "activity_tags.csv"
PATH_RE = re.compile(r"^[a-z_][a-z_0-9]*(\.[a-z_][a-z_0-9]*)+$")
_MISSING = object()

N3_CONTEXT = {"event_ids": set(), "prompt_version": "values_parser.v1", "model_id": "fixture:hand-written",
              "input_hash": "sha256:test", "updated_at": "2026-11-14T10:00:00-05:00"}


# ---------------------------------------------------------------- helpers

@pytest.fixture(autouse=True)
def _mock_llm(monkeypatch):
    monkeypatch.setattr(bedrock, "COHERENCE_ANALYZER_LLM", "mock")


@pytest.fixture()
def robert(root) -> dict:
    return json.loads((root / "fixtures" / "robert" / "profile.json").read_text(encoding="utf-8"))


def _required() -> dict:
    return getattr(intake, "REQUIRED")


def _get(profile: dict, path: str):
    node = profile
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _without(profile: dict, *paths: str) -> dict:
    p = copy.deepcopy(profile)
    for path in paths:
        *parents, leaf = path.split(".")
        node = p
        for part in parents:
            node = node[part]
        del node[leaf]
    return p


def _set(profile: dict, path: str, value) -> dict:
    p = copy.deepcopy(profile)
    *parents, leaf = path.split(".")
    node = p
    for part in parents:
        node = node[part]
    node[leaf] = value
    return p


def _text(q: dict) -> str:
    return q["text"]


def _all_required() -> list[tuple[str, str]]:
    return [(f, path) for f in FACTORS for path in _required()[f]]


def _n3_notes(profile: dict) -> str:
    return profile["values"]["notes"]


def _n3_output(notes: str) -> dict:
    return extractors.values_parser(notes)


def _rule(rule_kind: str, params: dict, quote: str, approved: bool, applies_to: str = "all") -> dict:
    return {"rule_kind": rule_kind, "params": params, "applies_to": applies_to, "quote": quote,
            "approved_by_user": approved}


def _with_parsed(profile: dict, rules: list[dict]) -> dict:
    p = copy.deepcopy(profile)
    p["values"]["parsed_notes"]["value"] = rules
    return p


def _build(profile: dict) -> tuple[dict, dict]:
    derived = derive.derive_all(profile, portfolio_value_cad=ROBERT_VALUE, as_of=AS_OF)
    return limits_mod.build_limits(derived, ROBERT_VALUE)


# ---------------------------------------------------------------- REQUIRED checklist (§20.3, §20.6)

def test_required_has_the_five_factor_ids_with_dotted_profile_paths():
    # §20.6: "Each factor has REQUIRED profile paths"; task notes: dict keyed by the five factor ids.
    req = _required()
    assert set(req) == set(FACTORS)
    for factor, paths in req.items():
        assert isinstance(paths, tuple) and paths, factor
        assert len(set(paths)) == len(paths), factor
        for path in paths:
            assert PATH_RE.match(path), (factor, path)


def test_required_paths_are_questions_for_the_user_never_llm_fields(robert):
    # §20.6 + invariant 2 spirit: the form asks the user; it never asks about an llm: field.
    for factor, path in _all_required():
        node = _get(robert, path)
        assert node is not _MISSING, (factor, path)
        if isinstance(node, dict):
            assert not str(node.get("by", "")).startswith("llm:"), (factor, path)


# ---------------------------------------------------------------- missing() (§20.6)

@pytest.mark.parametrize("factor", FACTORS)
def test_robert_has_nothing_missing_and_no_next_question(robert, factor):
    # Task notes: Robert fixture, missing() is empty for every factor.
    assert intake.missing(robert, factor) == []
    assert intake.next_question(robert, factor) is None


@pytest.mark.parametrize("factor", FACTORS)
def test_removing_a_required_path_makes_exactly_that_path_missing(robert, factor):
    for path in _required()[factor]:
        assert intake.missing(_without(robert, path), factor) == [path]


def test_a_null_answer_counts_as_missing(robert):
    # SPEC-AMBIGUITY: a bare null (unanswered) is missing, only the explicit skip marker is not.
    factor, path = _all_required()[0]
    assert intake.missing(_set(robert, path, None), factor) == [path]


@pytest.mark.parametrize("factor", FACTORS)
def test_missing_follows_required_order(robert, factor):
    paths = _required()[factor]
    p = _without(robert, *reversed(paths))
    assert intake.missing(p, factor) == list(paths)


def test_missing_and_next_question_are_pure(robert):
    factor, path = _all_required()[0]
    p = _without(robert, path)
    before = copy.deepcopy(p)
    intake.missing(p, factor)
    intake.next_question(p, factor)
    assert p == before
    assert intake.missing(p, factor) == intake.missing(copy.deepcopy(p), factor)
    assert intake.next_question(p, factor) == intake.next_question(copy.deepcopy(p), factor)


# ---------------------------------------------------------------- next_question() (§20.6)

@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("factor", FACTORS)
def test_next_question_returns_the_fixed_question_of_the_removed_path(robert, factor, locale):
    # §20.6: fixed template question for the first missing path, per locale.
    # SPEC-AMBIGUITY: the spec names no keys; the question dict carries `path` and `text`.
    for path in _required()[factor]:
        q = intake.next_question(_without(robert, path), factor, locale)
        assert isinstance(q, dict)
        assert q["path"] == path
        assert isinstance(_text(q), str) and _text(q).strip()


@pytest.mark.parametrize("factor", FACTORS)
def test_next_question_asks_the_first_missing_path_in_required_order(robert, factor):
    paths = _required()[factor]
    q = intake.next_question(_without(robert, *reversed(paths)), factor)
    assert q["path"] == paths[0]


def test_questions_are_fixed_templates_distinct_per_path_and_per_locale(robert):
    # §20.6: no LLM writes questions: same input -> identical question; fr-CA and en-CA differ.
    texts: dict[str, dict[str, str]] = {loc: {} for loc in LOCALES}
    for factor, path in _all_required():
        p = _without(robert, path)
        for loc in LOCALES:
            q = intake.next_question(p, factor, loc)
            assert q == intake.next_question(copy.deepcopy(p), factor, loc)
            texts[loc][f"{factor}:{path}"] = _text(q)
        assert texts["fr-CA"][f"{factor}:{path}"] != texts["en-CA"][f"{factor}:{path}"]
    for loc in LOCALES:
        assert len(set(texts[loc].values())) == len(texts[loc])


def test_default_locale_is_fr_ca(robert):
    factor, path = _all_required()[0]
    p = _without(robert, path)
    assert intake.next_question(p, factor) == intake.next_question(p, factor, "fr-CA")


# ---------------------------------------------------------------- skip() (§20.6)

def test_skip_stores_null_value_by_user_skipped_without_mutating_the_input(robert):
    for factor, path in _all_required():
        p = _without(robert, path)
        before = copy.deepcopy(p)
        out = intake.skip(p, path)
        assert p == before, "skip must return a new profile"
        assert _get(out, path) == {"value": None, "by": "user:skipped"}


def test_skipped_path_is_no_longer_missing_and_intake_moves_on(robert):
    # §20.6: "Stops when missing is empty or the user skips".
    for factor in FACTORS:
        paths = _required()[factor]
        p = _without(robert, *paths)
        for i, path in enumerate(paths):
            assert intake.next_question(p, factor)["path"] == path
            p = intake.skip(p, path)
            assert intake.missing(p, factor) == list(paths[i + 1:])
        assert intake.next_question(p, factor) is None


def test_skip_touches_only_the_skipped_path(robert):
    factor, path = _all_required()[0]
    p = _without(robert, path)
    out = intake.skip(p, path)
    assert _without(out, path) == p


# ---------------------------------------------------------------- showcase: theme rule without a number

NO_NUMBER_QUOTE = "I don't want everything in tech."


def _amira_like(robert: dict) -> dict:
    rules = [_rule("max_theme_weight", {"theme": "tech_heavy"}, NO_NUMBER_QUOTE, approved=False)]  # no max_pct
    p = _with_parsed(robert, rules)
    p["values"]["notes"] = "I love AI and new tech but " + NO_NUMBER_QUOTE
    return p


def test_parsed_max_theme_weight_without_a_number_makes_contraintes_ask_for_it(robert):
    # D7 point 1 / §20.6 showcase: Amira's "pas tout en tech" with no number -> contraintes asks for the number.
    p = _amira_like(robert)
    miss = intake.missing(p, "contraintes")
    assert len(miss) == 1
    # SPEC-AMBIGUITY: the spec fixes no path syntax for list items; require it to point into the parsed rule's max_pct.
    assert miss[0].startswith("values.parsed_notes") and "max_pct" in miss[0]
    for loc in LOCALES:
        q = intake.next_question(p, "contraintes", loc)
        assert q is not None and q["path"] == miss[0]
        assert _text(q).strip()
    assert intake.next_question(p, "contraintes", "fr-CA") != intake.next_question(p, "contraintes", "en-CA")


def test_theme_rule_with_a_number_or_other_kinds_ask_nothing(robert):
    p = _with_parsed(robert, [
        _rule("max_theme_weight", {"theme": "tech_heavy", "max_pct": 50}, NO_NUMBER_QUOTE, approved=False),
        _rule("prefer_attribute", {"attribute": "dividends"}, "I love AI", approved=False),
        _rule("exclude_activity", {"tag": "tobacco"}, "x", approved=False),
    ])
    assert intake.missing(p, "contraintes") == []
    assert intake.next_question(p, "contraintes") is None


# ---------------------------------------------------------------- N3 schema + prompt (§6.2 N3)

def test_n3_prompt_version_and_files_exist():
    # CLAUDE.md: prompts/<node>.v<N>.md; the fixture profile already stamps values_parser.v1.
    assert extractors.prompt_version("values_parser") == "values_parser.v1"
    prompt = extractors.load_prompt("values_parser")
    assert prompt.strip()
    assert "quote" in prompt.lower() and "substring" in prompt.lower()
    assert (config.PROMPTS_DIR / "values_parser.v1.md").is_file()
    assert (config.LLM_SCHEMAS_DIR / "values_parser.json").is_file()


def _validator() -> Draft202012Validator:
    schema = extractors.load_schema("values_parser")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _n3_instance(**over) -> dict:
    r = {"rule_kind": "avoid_attribute", "params": {"attribute": "hype"}, "applies_to": "single_stocks",
         "quote": "No hype stocks"}
    r.update(over)
    return {"rules": [r]}


def test_n3_schema_accepts_every_rule_kind_and_applies_to_of_the_spec():
    v = _validator()
    for kind in gate_mod.RULE_KINDS:
        assert v.is_valid(_n3_instance(rule_kind=kind)), kind
    for applies in gate_mod.APPLIES_TO:
        assert v.is_valid(_n3_instance(applies_to=applies)), applies
    assert v.is_valid({"rules": []})


def test_n3_schema_accepts_a_theme_rule_with_no_number():
    # §6.2 N3 / Amira: "should parse max_theme_weight {theme: tech_heavy} with no number".
    inst = _n3_instance(rule_kind="max_theme_weight", params={"theme": "tech_heavy"}, applies_to="all",
                        quote=NO_NUMBER_QUOTE)
    assert _validator().is_valid(inst)


def test_n3_schema_rejects_unknown_enum_values_and_missing_fields():
    v = _validator()
    assert not v.is_valid(_n3_instance(rule_kind="buy_stock"))
    assert not v.is_valid(_n3_instance(applies_to="bonds"))
    for field in ("rule_kind", "params", "applies_to", "quote"):
        inst = _n3_instance()
        del inst["rules"][0][field]
        assert not v.is_valid(inst), field
    assert not v.is_valid({})


# ---------------------------------------------------------------- N3 in mock mode on Robert's notes (§6.2 N3, §11)

def test_n3_mock_on_roberts_notes_returns_rules_with_exact_substring_quotes(robert):
    notes = _n3_notes(robert)
    out = _n3_output(notes)
    assert _validator().is_valid(out)
    assert out["rules"], "Robert's notes contain two statements; N3 must parse at least one rule"
    for r in out["rules"]:
        assert r["quote"] and r["quote"] in notes     # exact substring, no case or whitespace folding
        assert r["rule_kind"] in gate_mod.RULE_KINDS
        assert r["applies_to"] in gate_mod.APPLIES_TO
    assert out == _n3_output(notes)                    # mock replay is deterministic


def test_n3_output_passes_the_gate_and_rules_start_unapproved(robert):
    notes = _n3_notes(robert)
    out = _n3_output(notes)
    new, status = gate_mod.gate("values_parser", None, out, {**N3_CONTEXT, "notes": notes})
    assert status == "accepted"
    assert new["by"] == "llm:values_parser"
    assert [r["quote"] for r in new["value"]] == [r["quote"] for r in out["rules"]]
    assert all(r["approved_by_user"] is False for r in new["value"])


def test_n3_output_with_an_invented_quote_is_rejected_by_the_gate(robert):
    # §1 inv. 5 / §11 step 2: a quote that is not in the notes rejects the whole output; old value kept.
    notes = _n3_notes(robert)
    out = copy.deepcopy(_n3_output(notes))
    out["rules"][0]["quote"] = "I never want to own tobacco companies."
    old = {"value": [], "by": "llm:values_parser", "confidence": 0.8}
    new, status = gate_mod.gate("values_parser", copy.deepcopy(old), out, {**N3_CONTEXT, "notes": notes})
    assert status == "rejected:evidence"
    assert new == old


# ---------------------------------------------------------------- approval -> limits (§6.2 N3, §7.2, §1 inv. 2)

TAG_RULE = dict(rule_kind="exclude_activity", params={"tag": "fossil_fuel_producer"}, quote="no oil")
THEME_RULE = dict(rule_kind="max_theme_weight", params={"theme": "tech_heavy", "max_pct": 50}, quote=NO_NUMBER_QUOTE)


def test_unapproved_parsed_rules_are_ignored_by_build_limits(robert):
    base, base_trace = _build(robert)
    p = _with_parsed(robert, [_rule(approved=False, applies_to="all", **TAG_RULE),
                              _rule(approved=False, applies_to="all", **THEME_RULE)])
    lim, trace = _build(p)
    dumped = json.dumps(lim, default=str)
    assert "fossil_fuel_producer" not in dumped and "tech_heavy" not in dumped
    assert not lim.get("max_theme_weight")
    assert sorted(lim["excluded_activities"]) == sorted(base["excluded_activities"])
    assert trace == base_trace


def test_approved_exclude_activity_reaches_excluded_activities(robert):
    # §7.2: excluded_activities = union of values.*.exclusions + approved exclude_activity rules.
    p = _with_parsed(robert, [_rule(approved=True, applies_to="all", **TAG_RULE)])
    lim, _ = _build(p)
    assert sorted(lim["excluded_activities"]) == sorted(["tobacco", "accounting_fraud_last_5y",
                                                         "fossil_fuel_producer"])


def test_approved_max_theme_weight_reaches_limits_as_a_fraction(robert):
    # §7.2: max_theme_weight = approved max_theme_weight rules (Amira tech_heavy <= 50 %); fractions inside the code.
    p = _with_parsed(robert, [_rule(approved=True, applies_to="all", **THEME_RULE)])
    lim, _ = _build(p)
    assert lim["max_theme_weight"] == {"tech_heavy": 0.5}


def test_only_the_approved_rule_of_a_mixed_batch_is_promoted(robert):
    p = _with_parsed(robert, [_rule(approved=True, applies_to="all", **TAG_RULE),
                              _rule(approved=False, applies_to="all", **THEME_RULE)])
    lim, _ = _build(p)
    assert "fossil_fuel_producer" in lim["excluded_activities"]
    assert not lim.get("max_theme_weight")


def test_approved_soft_rules_never_change_limits(robert):
    # §6.2 N3: "Other kinds stay soft reminders (S4)". Robert's N3 rules (prefer/avoid), all approved, change nothing.
    base, base_trace = _build(robert)
    notes = _n3_notes(robert)
    new, status = gate_mod.gate("values_parser", None, _n3_output(notes), {**N3_CONTEXT, "notes": notes})
    assert status == "accepted"
    soft = [r for r in new["value"] if r["rule_kind"] not in ("exclude_activity", "max_theme_weight")]
    assert soft
    p = _with_parsed(robert, [{**r, "approved_by_user": True} for r in soft])
    lim, trace = _build(p)
    assert _strip(lim) == _strip(base)
    assert trace == base_trace


def _strip(block: dict) -> dict:
    return {k: v for k, v in block.items() if k not in ("_note", "by")}


def test_invariant_2_trace_names_only_approved_rules_never_the_llm_field(robert):
    # §1 inv. 2: the trace for the promoted limits points at the approved subset, never at values.parsed_notes itself.
    p = _with_parsed(robert, [_rule(approved=True, applies_to="all", **TAG_RULE),
                              _rule(approved=True, applies_to="all", **THEME_RULE)])
    _, trace = _build(p)
    assert "values.parsed_notes[approved_by_user]" in trace["excluded_activities"]
    assert trace["max_theme_weight"] == ["values.parsed_notes[approved_by_user]"]
    for name, paths in trace.items():
        assert "values.parsed_notes" not in paths, name


def test_approving_the_number_answers_the_intake_question(robert):
    # D7 point 1: once the user gives the number and approves, contraintes has nothing left to ask,
    # and the approved rule is what limits use.
    p = _amira_like(robert)
    assert intake.next_question(p, "contraintes") is not None
    answered = _with_parsed(p, [_rule(approved=True, applies_to="all", rule_kind="max_theme_weight",
                                      params={"theme": "tech_heavy", "max_pct": 50}, quote=NO_NUMBER_QUOTE)])
    assert intake.missing(answered, "contraintes") == []
    assert _build(answered)[0]["max_theme_weight"] == {"tech_heavy": 0.5}


# ---------------------------------------------------------------- humans only: activity_tags.csv (CLAUDE.md invariant)

def _csv_state() -> bytes | None:
    return TAGS_CSV.read_bytes() if TAGS_CSV.exists() else None


def test_intake_and_n3_never_write_activity_tags_csv(robert):
    before = _csv_state()
    notes = _n3_notes(robert)
    out = _n3_output(notes)
    gate_mod.gate("values_parser", None, out, {**N3_CONTEXT, "notes": notes})
    factor, path = _all_required()[0]
    p = _without(robert, path)
    intake.missing(p, factor)
    intake.next_question(p, factor)
    intake.skip(p, path)
    assert _csv_state() == before
