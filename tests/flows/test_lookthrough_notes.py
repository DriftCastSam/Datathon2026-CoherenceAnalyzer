"""The 'no look-through' assumption follows the engine's `lookthrough` flag (SPEC §19, §7.3 H6/H10).

With sourced sector weights covering every held equity ETF the engine adds its own look-through line; the report must
not also say look-through is unavailable. Flow B checks both sides (the trade can add an uncovered ETF).
"""
from coherence_analyzer import pipeline


def test_note_only_when_lookthrough_is_false():
    assert pipeline._lookthrough_notes({"lookthrough": False}) == [pipeline.NO_LOOKTHROUGH]
    assert pipeline._lookthrough_notes({"lookthrough": True}) == []
    assert pipeline._lookthrough_notes({}) == [pipeline.NO_LOOKTHROUGH]          # missing flag: say it is not there


def test_flow_b_needs_both_sides_covered():
    assert pipeline._lookthrough_notes({"lookthrough": True}, {"lookthrough": False}) == [pipeline.NO_LOOKTHROUGH]
    assert pipeline._lookthrough_notes({"lookthrough": True}, {"lookthrough": True}) == []
    assert pipeline._lookthrough_notes({"lookthrough": True}, None) == []


def test_static_flow_c_list_no_longer_claims_it():
    assert not any("look-through" in a.lower() for a in pipeline.FLOW_C_ASSUMPTIONS)
