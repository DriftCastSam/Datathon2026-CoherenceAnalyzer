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

def test_not_skippable_constant_contains_horizon_years():
    # T1: horizon is NOT_SKIPPABLE.
    assert "objectives.horizon_years" in intake.NOT_SKIPPABLE


def test_skip_allows_horizon_in_pure_function(robert):
    # T1: pure intake.skip allows horizon (API enforces NOT_SKIPPABLE, not the pure function).
    p = _without(robert, "objectives.horizon_years")
    out = intake.skip(p, "objectives.horizon_years")
    assert _get(out, "objectives.horizon_years") == {"value": None, "by": "user:skipped"}


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


def test_next_question_shows_horizon_not_skippable(robert):
    # T1: next_question returns skippable=False for horizon.
    p = _without(robert, "objectives.horizon_years")
    q = intake.next_question(p, "horizon", "en-CA")
    assert q is not None
    assert q["path"] == "objectives.horizon_years"
    assert q["skippable"] is False


def test_next_question_shows_other_fields_skippable(robert):
    # T1: next_question returns skippable=True for other REQUIRED paths.
    for path in ("objectives.primary", "risk.declared", "situation.planned_withdrawals"):
        p = _without(robert, path)
        factor = [f for f, paths in intake.REQUIRED.items() if path in paths][0]
        q = intake.next_question(p, factor, "en-CA")
        assert q is not None
        assert q["path"] == path
        assert q["skippable"] is True


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
