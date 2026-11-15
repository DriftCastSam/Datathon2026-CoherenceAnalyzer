"""T1: skip functionality now allows all paths (loop/p0 change).

Tests that any intake path can be skipped, arrays and enums can be skipped, and _c3 handles skipped values.
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


# ---------------------------------------------------------------- all paths skippable

def test_skip_allows_any_path(robert):
    # loop/p0: all intake paths are skippable
    for path in ["objectives.horizon_years", "risk.declared", "situation.emergency_fund"]:
        p = _without(robert, path)
        out = intake.skip(p, path)
        assert _get(out, path) == {"value": None, "by": "user:skipped"}


def test_skip_works_for_array_fields(robert):
    # arrays (planned_withdrawals, asset_types_refused) can be skipped
    for path in ("situation.planned_withdrawals", "preferences.asset_types_refused"):
        p = _without(robert, path)
        out = intake.skip(p, path)
        assert _get(out, path) == {"value": None, "by": "user:skipped"}


def test_skip_works_for_enum_field(robert):
    # enum (risk.declared) can be skipped
    path = "risk.declared"
    p = _without(robert, path)
    out = intake.skip(p, path)
    assert _get(out, path) == {"value": None, "by": "user:skipped"}


def test_next_question_no_skippable_field(robert):
    # loop/p0: next_question no longer returns a skippable field
    test_cases = [
        ("objectives.horizon_years", "horizon"),
        ("identity.retirement_target_age", "horizon"),
        ("situation.planned_withdrawals", "horizon"),
        ("risk.declared", "risque"),
        ("risk.max_acceptable_drawdown_pct", "risque"),
        ("situation.emergency_fund", "contraintes"),
        ("situation.monthly_expenses", "contraintes"),
        ("preferences.asset_types_refused", "contraintes"),
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
        assert "skippable" not in q, f"{path} question should not have skippable field"


# ---------------------------------------------------------------- _c3 guard

def test_c3_handles_skipped_primary_without_crashing(robert):
    # _c3 with skipped primary returns empty list (no crash from dict in frozenset)
    p = copy.deepcopy(robert)
    p["objectives"]["primary"] = {"value": None, "by": "user:skipped"}
    result = internal._c3(p)
    assert result == []


def test_c3_handles_skipped_declared_without_crashing(robert):
    # _c3 with skipped declared returns empty list
    p = copy.deepcopy(robert)
    p["risk"]["declared"] = {"value": None, "by": "user:skipped"}
    result = internal._c3(p)
    assert result == []


def test_c3_still_detects_contradiction_when_both_present(robert):
    # _c3 still works when both fields are present
    p = copy.deepcopy(robert)
    p["objectives"]["primary"] = "growth"
    p["risk"]["declared"] = "low"
    result = internal._c3(p)
    assert len(result) == 1
    assert result[0]["id"] == "C3"
