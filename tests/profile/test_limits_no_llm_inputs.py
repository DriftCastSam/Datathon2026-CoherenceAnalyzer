"""Invariant 2 (SPEC §1, §7.1, §7.2, §15) as amended by docs/spec-decisions.md D11 (owner, 2026-11-15): no `llm:` field
feeds `limits`, directly or transitively, with ONE exception: a gated, unapproved N3 `max_theme_weight` rule with a
number is promoted as a PROVISIONAL limit (flagged `provisional: true`, traced as `values.parsed_notes[provisional]`,
checks against it capped at warning). Everything else (firm limits, unapproved rules without a number, every other llm:
field) is exactly as before.
"""
import copy
import json
from dataclasses import replace

import pytest

from coherence_analyzer.rules import derive, limits as limits_mod, registry

AS_OF = "2026-11-12"  # portfolio.as_of (docs/spec-decisions.md D1)
ROBERT_VALUE = 400000
META_KEYS = {"_note", "by"}
_MISSING = object()

# Fields the spec marks llm: (§3.3, §6.2) and the rule: field that mixes one in (§7.1 knowledge.score).
LLM_FIELDS = {"risk.behavioural", "knowledge.question_quality", "values.parsed_notes"}
NOT_LIMIT_INPUTS = LLM_FIELDS | {"knowledge.score"}


@pytest.fixture()
def robert(root) -> dict:
    return json.loads((root / "fixtures" / "robert" / "profile.json").read_text())


def _derive(profile: dict) -> dict:
    return derive.derive_all(profile, portfolio_value_cad=ROBERT_VALUE, as_of=AS_OF)


def _build(profile: dict) -> tuple[dict, dict]:
    return limits_mod.build_limits(_derive(profile), ROBERT_VALUE)


def _strip(block: dict) -> dict:
    return {k: v for k, v in block.items() if k not in META_KEYS}


PROVISIONAL_PATH = "values.parsed_notes[provisional]"   # D11


def _is_provisional(entry) -> bool:
    return isinstance(entry, dict) and entry.get("provisional") is True


def _firm(block: dict) -> dict:
    """The limits without D11 provisional entries (an emptied max_theme_weight counts as absent)."""
    out = _strip(block)
    themes = out.get("max_theme_weight")
    if isinstance(themes, dict):
        kept = {t: v for t, v in themes.items() if not _is_provisional(v)}
        out["max_theme_weight"] = kept or None
    elif not themes:
        out["max_theme_weight"] = None
    return out


def _firm_trace(trace: dict) -> dict:
    """Every trace entry except max_theme_weight, whose inputs D11 lets switch between [approved_by_user] and
    [provisional] (the provisional oracle requires [approved_by_user] absent when only a provisional rule exists)."""
    theme = trace.get("max_theme_weight") or []
    assert set(theme) <= {"values.parsed_notes[approved_by_user]", PROVISIONAL_PATH}, theme
    return {k: v for k, v in trace.items() if k != "max_theme_weight"}


def _resolve(profile: dict, path: str):
    node = profile
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _llm_paths(trace: dict, *profiles: dict) -> list[tuple[str, str]]:
    """(limit, path) pairs where `path` resolves to a field whose `by` starts with `llm:` in any profile."""
    bad = []
    for name, paths in trace.items():
        for p in paths:
            for prof in profiles:
                node = _resolve(prof, p)
                if isinstance(node, dict) and str(node.get("by", "")).startswith("llm:"):
                    bad.append((name, p))
                    break
    return bad


def test_no_limit_input_resolves_to_llm_field(robert):
    _, trace = _build(robert)
    assert trace, "limits_trace must not be empty"
    assert _llm_paths(trace, robert, _derive(robert)) == []


def test_known_llm_fields_never_in_trace(robert):
    _, trace = _build(robert)
    for name, paths in trace.items():
        assert not (set(paths) & NOT_LIMIT_INPUTS), name


def test_registry_walk_from_limits_reaches_no_llm_field(robert):
    """Walk the registry from every trace path; the transitive closure stays clear of llm: fields."""
    _, trace = _build(robert)
    derived = _derive(robert)
    for name, paths in trace.items():
        closure = set(paths)
        for p in paths:
            closure |= set(registry.expand(p))
        assert not (closure & NOT_LIMIT_INPUTS), name
        assert _llm_paths({name: sorted(closure)}, robert, derived) == [], name


def test_no_registered_rule_outputs_an_llm_field():
    assert not (set(registry.REGISTRY) & LLM_FIELDS)
    for out, r in registry.REGISTRY.items():
        assert r.output == out


def test_knowledge_score_depends_on_llm_but_is_not_a_limit_input(robert):
    """§7.1: knowledge.score mixes question_quality (llm:) and feeds only S2, never limits."""
    assert "knowledge.question_quality" in registry.expand("knowledge.score")
    _, trace = _build(robert)
    assert all("knowledge.score" not in paths for paths in trace.values())


def test_injected_llm_dependency_shows_up_in_trace(robert, monkeypatch):
    """The trace is transitive and registry-driven, so an llm: input sneaking into a rule is detectable."""
    cap = registry.REGISTRY["risk.capacity"]
    monkeypatch.setitem(registry.REGISTRY, "risk.capacity",
                        replace(cap, inputs=cap.inputs + ("risk.behavioural",)))
    _, trace = _build(robert)
    assert ("min_fixed_income_weight", "risk.behavioural") in _llm_paths(trace, robert)


def _perturb_llm_fields(robert: dict) -> dict:
    p = copy.deepcopy(robert)
    p["risk"]["behavioural"]["value"] = "high"
    p["risk"]["behavioural"]["confidence"] = 0.8
    p["knowledge"]["question_quality"]["value"] = 0.0
    p["knowledge"]["score"]["value"] = 5
    p["values"]["parsed_notes"]["value"] = [
        {"rule_kind": "exclude_activity", "params": {"tag": "fossil_fuel_producer"}, "applies_to": "all",
         "quote": "I prefer companies I understand.", "approved_by_user": False},
        {"rule_kind": "max_theme_weight", "params": {"theme": "tech_heavy", "max_pct": 50}, "applies_to": "all",
         "quote": "No hype stocks", "approved_by_user": False},
    ]
    p["values"]["parsed_notes"]["confidence"] = 0.99
    return p


def test_llm_fields_do_not_change_limits(robert):
    """D11: the perturbed llm: fields change no FIRM limit and no firm trace; only the provisional entry may appear."""
    base, base_trace = _build(robert)
    lim, trace = _build(_perturb_llm_fields(robert))
    assert _firm(lim) == _firm(base)
    assert _firm_trace(trace) == _firm_trace(base_trace)


def test_llm_fields_do_not_change_limits_without_rederive(robert):
    base, _ = limits_mod.build_limits(_derive(robert), ROBERT_VALUE)
    p = _derive(robert)
    perturbed = _perturb_llm_fields(p)
    lim, _ = limits_mod.build_limits(perturbed, ROBERT_VALUE)
    assert _firm(lim) == _firm(base)


def test_unapproved_parsed_rules_do_not_appear_in_limits_except_as_provisional(robert):
    """D11: an unapproved rule without a number (exclude_activity) never reaches limits; an unapproved numeric theme
    rule appears only as a provisional entry, traced as values.parsed_notes[provisional]."""
    p = _perturb_llm_fields(robert)
    lim, trace = _build(p)
    assert "fossil_fuel_producer" not in json.dumps(lim, default=str)
    assert sorted(lim["excluded_activities"]) == ["accounting_fraud_last_5y", "tobacco"]
    themes = lim.get("max_theme_weight") or {}
    assert set(themes) == {"tech_heavy"}                      # D11.1: promoted at once ...
    assert _is_provisional(themes["tech_heavy"])              # ... and only as provisional
    assert PROVISIONAL_PATH in trace["max_theme_weight"]
