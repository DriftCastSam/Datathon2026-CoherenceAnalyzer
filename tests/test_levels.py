"""Risk-level ordering + helpers (SPEC §3.3, §7.1 risk.effective, §11 movement clamp)."""
import pytest

from coherence_analyzer import levels

SPEC_ORDER = ["low", "low_moderate", "moderate", "moderate_high", "high"]


def test_order_and_index():
    assert list(levels.LEVELS) == SPEC_ORDER
    for i, name in enumerate(SPEC_ORDER):
        assert levels.idx(name) == i
        assert levels.at(i) == name


def test_at_clamps_to_valid_range():
    assert levels.at(-1) == "low"
    assert levels.at(-10) == "low"
    assert levels.at(5) == "high"
    assert levels.at(99) == "high"


@pytest.mark.parametrize("a", SPEC_ORDER)
@pytest.mark.parametrize("b", SPEC_ORDER)
def test_lower_is_min_by_index_and_symmetric(a, b):
    expected = SPEC_ORDER[min(SPEC_ORDER.index(a), SPEC_ORDER.index(b))]
    assert levels.lower(a, b) == expected
    assert levels.lower(b, a) == expected


def test_step_floors_and_caps():
    assert levels.step("low", -1) == "low"
    assert levels.step("high", 1) == "high"
    assert levels.step("moderate", -1) == "low_moderate"
    assert levels.step("moderate", 2) == "high"
    assert levels.step("moderate", 0) == "moderate"


def test_order_and_clamp():
    assert levels.lower("low_moderate", "high") == "low_moderate"
    # SPEC §15 test_gate: behavioural jump low_moderate -> high is clamped to moderate (§11 step 4).
    assert levels.clamp_move("low_moderate", "high") == "moderate"
    assert levels.clamp_move("moderate", "low") == "low_moderate"
    assert levels.step("low", -1) == "low"


def test_clamp_move_within_one_step_is_unchanged():
    assert levels.clamp_move("moderate", "moderate") == "moderate"
    assert levels.clamp_move("moderate", "moderate_high") == "moderate_high"
    assert levels.clamp_move("moderate", "low_moderate") == "low_moderate"


def test_clamp_move_without_previous_value_takes_new():
    assert levels.clamp_move(None, "high") == "high"


def test_clamp_move_never_exceeds_one_level():
    for old in SPEC_ORDER:
        for new in SPEC_ORDER:
            moved = levels.clamp_move(old, new)
            assert abs(levels.idx(moved) - levels.idx(old)) <= 1
            # moves toward the target, never away from it
            assert (levels.idx(moved) - levels.idx(old)) * (levels.idx(new) - levels.idx(old)) >= 0


def test_unknown_level_raises():
    # SPEC-AMBIGUITY: §3.3 defines a closed enum; strict reading = unknown names are rejected, not mapped.
    with pytest.raises(ValueError):
        levels.idx("very_high")
