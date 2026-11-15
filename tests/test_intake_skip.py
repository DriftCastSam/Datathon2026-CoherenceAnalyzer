"""T1: skip functionality for NOT_SKIPPABLE paths (site-followups plan).

Tests that horizon cannot be skipped, arrays and enums can be skipped, and _c3 handles skipped values.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

from coherence_analyzer import intake  # noqa: E402
from coherence_analyzer.coherence import internal  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def robert() -> dict:
    return json.loads((ROOT / "fixtures" / "robert" / "profile.json").read_text(encoding="utf-8"))


def _without(profile: dict, *paths: str) -> dict:
    p = copy.deepcopy(profile)
    for path in paths:
        *parents, leaf = path.split(".")
        node = p
        for part in parents:
            node = node[part]
        del node[leaf]
    return p


def _get(profile: dict, path: str):
    node = profile
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


# ---------------------------------------------------------------- NOT_SKIPPABLE

def test_not_skippable_contains_all_derive_and_limits_inputs():
    # T1: NOT_SKIPPABLE contains all REQUIRED paths read by derive_all and build_limits.
    expected = {
        "objectives.horizon_years",
        "identity.retirement_target_age",
        "situation.planned_withdrawals",
        "situation.emergency_fund",
        "situation.monthly_expenses",
        "risk.declared",
        "risk.max_acceptable_drawdown_pct",
        "preferences.asset_types_refused",
    }
    assert intake.NOT_SKIPPABLE == expected


def test_skip_allows_not_skippable_in_pure_function(robert):
    # T1: pure intake.skip allows NOT_SKIPPABLE paths (API enforces NOT_SKIPPABLE, not the pure function).
    for path in ["objectives.horizon_years", "risk.declared", "situation.emergency_fund"]:
        p = _without(robert, path)
        out = intake.skip(p, path)
        assert _get(out, path) == {"value": None, "by": "user:skipped"}


def test_skip_works_for_array_fields(robert):
    # T1: arrays (planned_withdrawals, asset_types_refused) can be skipped and validate.
    for path in ("situation.planned_withdrawals", "preferences.asset_types_refused"):
        p = _without(robert, path)
        out = intake.skip(p, path)
        assert _get(out, path) == {"value": None, "by": "user:skipped"}


def test_skip_works_for_enum_field(robert):
    # T1: enum (risk.declared) can be skipped and validate.
    path = "risk.declared"
    p = _without(robert, path)
    out = intake.skip(p, path)
    assert _get(out, path) == {"value": None, "by": "user:skipped"}


def test_next_question_shows_not_skippable_paths_correctly(robert):
    # T1: next_question returns skippable=False for NOT_SKIPPABLE paths.
    test_cases = [
        ("objectives.horizon_years", "horizon"),
        ("identity.retirement_target_age", "horizon"),
        ("situation.planned_withdrawals", "horizon"),
        ("risk.declared", "risque"),
        ("risk.max_acceptable_drawdown_pct", "risque"),
        ("situation.emergency_fund", "contraintes"),
        ("situation.monthly_expenses", "contraintes"),
        ("preferences.asset_types_refused", "contraintes"),
    ]
    for path, factor in test_cases:
        p = _without(robert, path)
        q = intake.next_question(p, factor, "en-CA")
        assert q is not None, f"Expected question for {path}"
        assert q["path"] == path
        assert q["skippable"] is False, f"{path} should not be skippable"


def test_next_question_shows_skippable_fields_correctly(robert):
    # T1: next_question returns skippable=True for REQUIRED paths not in NOT_SKIPPABLE.
    test_cases = [
        ("objectives.primary", "objectif"),
        ("objectives.goals", "objectif"),
        ("risk.reaction_to_20pct_drop", "risque"),
        ("identity.employer_sector", "contraintes"),
        ("preferences.prefers_dividends", "preferences"),
    ]
    for path, factor in test_cases:
        p = _without(robert, path)
        q = intake.next_question(p, factor, "en-CA")
        assert q is not None, f"Expected question for {path}"
        assert q["path"] == path
        assert q["skippable"] is True, f"{path} should be skippable"


# ---------------------------------------------------------------- _c3 guard

def test_c3_handles_skipped_primary_without_crashing(robert):
    # T1: _c3 with skipped primary returns empty list (no crash from dict in frozenset).
    p = copy.deepcopy(robert)
    p["objectives"]["primary"] = {"value": None, "by": "user:skipped"}
    result = internal._c3(p)
    assert result == []


def test_c3_handles_skipped_declared_without_crashing(robert):
    # T1: _c3 with skipped declared returns empty list.
    p = copy.deepcopy(robert)
    p["risk"]["declared"] = {"value": None, "by": "user:skipped"}
    result = internal._c3(p)
    assert result == []


def test_c3_still_detects_contradiction_when_both_present(robert):
    # T1: _c3 still works when both fields are present.
    p = copy.deepcopy(robert)
    p["objectives"]["primary"] = "growth"
    p["risk"]["declared"] = "low"
    result = internal._c3(p)
    assert len(result) == 1
    assert result[0]["id"] == "C3"


# ---------------------------------------------------------------- pipeline guard

def test_pipeline_guard_raises_on_skipped_mandatory_path(robert):
    # T1: pipeline._check_mandatory_paths raises ValueError when a NOT_SKIPPABLE path is skipped.
    from coherence_analyzer import pipeline
    p = copy.deepcopy(robert)
    p["risk"]["declared"] = {"value": None, "by": "user:skipped"}
    try:
        pipeline._check_mandatory_paths(p)
        assert False, "Expected ValueError"
    except ValueError as e:
        assert "risk.declared" in str(e)
        assert "needed" in str(e).lower()
        assert "analysis" in str(e).lower()


def test_pipeline_guard_raises_on_multiple_skipped_mandatory_paths(robert):
    # T1: pipeline._check_mandatory_paths raises ValueError mentioning all skipped paths.
    from coherence_analyzer import pipeline
    p = copy.deepcopy(robert)
    p["risk"]["declared"] = {"value": None, "by": "user:skipped"}
    p["situation"]["emergency_fund"] = {"value": None, "by": "user:skipped"}
    try:
        pipeline._check_mandatory_paths(p)
        assert False, "Expected ValueError"
    except ValueError as e:
        assert "risk.declared" in str(e)
        assert "situation.emergency_fund" in str(e)


def test_pipeline_guard_passes_when_no_mandatory_paths_skipped(robert):
    # T1: pipeline._check_mandatory_paths passes when all NOT_SKIPPABLE paths are present.
    from coherence_analyzer import pipeline
    pipeline._check_mandatory_paths(robert)  # Should not raise
