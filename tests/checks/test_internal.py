"""Internal coherence (docs/spec-factors.md §20.9, docs/spec-decisions.md D8, SPEC §1 inv. 2-4, §11, D1, D7 point 1).

Layer 1: contradiction rules C1-C5 (`coherence.internal.rule_findings`).
Layer 2: N6 `profile_consistency` (payload, schema/prompt, gate branch, mock cache) and `check_profile`.
Offline: COHERENCE_ANALYZER_LLM=mock; every fake LLM answer is injected by patching `bedrock.call_tool`.
"""
import copy
import json
import re

import pytest
from jsonschema import Draft202012Validator

from coherence_analyzer import config
from coherence_analyzer.coherence import internal
from coherence_analyzer.gate import gate
from coherence_analyzer.llm import bedrock, extractors
from coherence_analyzer.rules import derive, limits as limits_mod
from coherence_analyzer.rules.limits import LIMITS_TABLE

AS_OF = "2026-11-12"          # portfolio.as_of (D1); never the wall clock
LEVELS = ("low", "low_moderate", "moderate", "moderate_high", "high")
NODE = "profile_consistency"
NOTES = "I prefer companies I understand. No hype stocks unless I can explain the business."
HYPE = "No hype stocks unless I can explain the business."
C1_FIELDS = {"objectives.horizon_years", "objectives.goals[1].date"}
STATUS_RANK = {"pass": 0, "info": 1, "warning": 2}

# ---------------------------------------------------------------- helpers


@pytest.fixture()
def robert(root) -> dict:
    return json.loads((root / "fixtures" / "robert" / "profile.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch):
    monkeypatch.setattr(bedrock, "COHERENCE_ANALYZER_LLM", "mock")


_MISSING = object()


def _get(profile: dict, path: str):
    """Resolve 'a.b[1].c' in a profile (the path syntax of §20.9, e.g. `goals[1].date`)."""
    node = profile
    for part in path.split("."):
        m = re.fullmatch(r"(\w+)(?:\[(\d+)\])?", part)
        assert m, path
        if not isinstance(node, dict) or m.group(1) not in node:
            return _MISSING
        node = node[m.group(1)]
        if m.group(2) is not None:
            i = int(m.group(2))
            if not isinstance(node, list) or i >= len(node):
                return _MISSING
            node = node[i]
    return node


def _set(profile: dict, path: str, value) -> dict:
    *head, last = path.split(".")
    node = profile
    for part in head:
        node = node[part]
    node[last] = value
    return profile


def _del(profile: dict, path: str) -> dict:
    *head, last = path.split(".")
    node = profile
    for part in head:
        node = node[part]
    del node[last]
    return profile


def _ids(profile: dict, as_of: str = AS_OF) -> list[str]:
    return [f["id"] for f in internal.rule_findings(profile, as_of)]


def _walk(node):
    """Yield every dict and every key in a nested structure."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _finding(fields: list[str], quotes: list[str], reason: str = "The two statements pull in different directions.",
             kind: str = "tension") -> dict:
    return {"kind": kind, "fields": fields, "quotes": quotes, "reason": reason}


# Valid N6 findings on Robert (each quote equals the value at its path, or is a substring of values.notes).
F_INTERESTS = _finding(["preferences.interests", "values.notes"], ["new_opportunities", HYPE])
F_RISK = _finding(["risk.declared", "objectives.primary"], ["low_moderate", "capital_preservation_with_growth"])
F_DD = _finding(["risk.max_acceptable_drawdown_pct", "risk.reaction_to_20pct_drop"], ["-12", "sell_some"])
F_KNOW = _finding(["knowledge.declared_level", "knowledge.years_investing"], ["advanced", "22"])
F_AGE = _finding(["identity.age", "identity.retirement_target_age"], ["59", "63"])
F_SPARE = _finding(["situation.monthly_expenses", "situation.emergency_fund"], ["6500", "30000"])
FIVE = [F_INTERESTS, F_RISK, F_DD, F_KNOW, F_AGE]


def _out(*findings: dict, abstain: bool = False) -> dict:
    return {"abstain": abstain, "findings": [copy.deepcopy(f) for f in findings]}


def _ctx(profile: dict) -> dict:
    return {"event_ids": set(), "notes": profile["values"]["notes"], "profile": profile,
            "model_id": "fixture:hand-written", "prompt_version": "profile_consistency.v1",
            "input_hash": "sha256:" + "cd" * 32, "updated_at": "2026-11-14T10:03:00-05:00"}


def _old() -> dict:
    return {"value": [], "by": "llm:profile_consistency", "prompt_version": "profile_consistency.v0",
            "model_id": "fixture:old", "updated_at": "2026-11-01T00:00:00-05:00", "input_hash": "sha256:old"}


@pytest.fixture()
def fake_llm(monkeypatch):
    """Patch the Bedrock wrapper: `fake(output)` makes N6 return `output`; `fake.calls` records the payloads."""
    state = {"output": None, "raises": None, "calls": []}

    def call_tool(node, schema, system, user_payload, prompt_version):
        state["calls"].append({"node": node, "payload": copy.deepcopy(user_payload), "prompt_version": prompt_version})
        if state["raises"] is not None:
            raise state["raises"]
        return {"output": copy.deepcopy(state["output"]),
                "call_log": {"node": node, "model_id": "fixture:fake", "prompt_version": prompt_version,
                             "cache_hit": True, "latency_ms": 0, "tokens_in": 0, "tokens_out": 0}}

    monkeypatch.setattr(bedrock, "call_tool", call_tool)

    def fake(output=None, raises=None):
        state["output"], state["raises"] = output, raises
        return state

    fake.calls = state["calls"]
    return fake


def _clean(robert: dict) -> dict:
    """Robert with C1 fixed (horizon 6 years -> 2032-11 covers the 2032-01 goal): no rule fires."""
    p = copy.deepcopy(robert)
    p["objectives"]["horizon_years"] = 6
    assert _ids(p) == []
    return p


# ---------------------------------------------------------------- layer 1: rules


def test_module_constants():
    # §20.9: rule ids, growth objectives, 5 findings max, 200 chars max, kind -> status map (never violation).
    assert internal.RULE_IDS == ("C1", "C2", "C3", "C4", "C5")
    assert internal.GROWTH_OBJECTIVES == frozenset({"growth", "aggressive_growth"})
    assert internal.MAX_FINDINGS == 5
    assert internal.MAX_REASON_CHARS == 200
    assert internal.KIND_STATUS == {"contradiction": "warning", "tension": "info"}


def test_robert_yields_exactly_one_c1_and_no_c2_to_c5(robert):
    # §20.9: horizon 4 years from 2026-11 ends 2030-11; the goal dated 2032-01 is later.
    found = internal.rule_findings(robert, AS_OF)
    assert [f["id"] for f in found] == ["C1"]
    f = found[0]
    assert f["source"] == "rule:C1"
    assert f["status"] == "warning"
    assert f["kind"] == "contradiction"
    assert set(f["fields"]) == C1_FIELDS
    # SPEC-AMBIGUITY: `values` is read as the declared value at each field, in field order (compared as str).
    assert [str(v) for v in f["values"]] == [str(_get(robert, p)) for p in f["fields"]]
    assert robert["objectives"]["goals"][1]["name"] == "Help children with a down payment"
    assert "2032-01" in [str(v) for v in f["values"]] and "4" in [str(v) for v in f["values"]]


def test_c1_month_precision_boundary(robert):
    # §20.9 C1: "later than as_of + horizon_years (month precision)": equal month does not fire, one month later does.
    p = copy.deepcopy(robert)
    p["objectives"]["goals"][1]["date"] = "2030-11"      # == 2026-11 + 4 years
    assert _ids(p) == []
    p["objectives"]["goals"][1]["date"] = "2030-12"
    assert _ids(p) == ["C1"]


def test_c1_one_finding_per_late_goal_and_fields_name_the_goal(robert):
    p = copy.deepcopy(robert)
    p["objectives"]["goals"][0]["date"] = "2033-01"
    found = internal.rule_findings(p, AS_OF)
    assert [f["id"] for f in found] == ["C1", "C1"]
    assert {frozenset(f["fields"]) for f in found} == {
        frozenset({"objectives.horizon_years", "objectives.goals[0].date"}), frozenset(C1_FIELDS)}


def test_c1_uses_the_given_as_of_not_the_clock(robert):
    # D1: the reference date is the `as_of` argument. 2020-01 + 4y = 2024-01 -> both goals late; 2028-01 + 4y = 2032-01 -> none.
    assert _ids(robert, "2020-01-15") == ["C1", "C1"]
    assert _ids(robert, "2028-01-01") == []
    assert _ids(robert, "2027-12-31") == ["C1"]          # 2031-12 < 2032-01


@pytest.mark.parametrize("path", ["objectives.horizon_years"])
def test_c1_skips_when_horizon_missing(robert, path):
    assert _ids(_del(robert, path)) == []


def test_c1_skips_goal_without_date_and_missing_goals(robert):
    p = copy.deepcopy(robert)
    del p["objectives"]["goals"][1]["date"]
    assert _ids(p) == []
    q = copy.deepcopy(robert)
    del q["objectives"]["goals"]
    assert _ids(q) == []


@pytest.mark.parametrize("level", LEVELS)
def test_c2_uses_limits_table_neighbours(robert, level):
    # §20.9 C2: tighter than the level below's max_dd_pct or looser than the level above's; no neighbour = no bound.
    i = LEVELS.index(level)
    p = _clean(robert)
    p["risk"]["declared"] = level

    def fires(dd) -> bool:
        p["risk"]["max_acceptable_drawdown_pct"] = dd
        return "C2" in _ids(p)

    assert fires(LIMITS_TABLE[level]["max_dd_pct"]) is False
    if i > 0:
        tight = LIMITS_TABLE[LEVELS[i - 1]]["max_dd_pct"]
        assert fires(tight) is False          # equal to the neighbour: inside the closed interval
        assert fires(tight + 1) is True       # tighter (closer to zero)
    else:
        assert fires(-1) is False             # nothing below "low": no bound on that side
    if i < len(LEVELS) - 1:
        loose = LIMITS_TABLE[LEVELS[i + 1]]["max_dd_pct"]
        assert fires(loose) is False
        assert fires(loose - 1) is True       # looser (further from zero)
    else:
        assert fires(-95) is False            # nothing above "high"


def test_c2_robert_minus_12_is_inside_the_low_moderate_band(robert):
    # §20.9: low_moderate neighbours are low (-8) and moderate (-20); Robert's -12 is inside.
    assert (LIMITS_TABLE["low"]["max_dd_pct"], LIMITS_TABLE["moderate"]["max_dd_pct"]) == (-8, -20)
    assert robert["risk"]["max_acceptable_drawdown_pct"] == -12
    assert "C2" not in _ids(robert)


def test_c2_fires_on_minimal_edit_with_its_fields(robert):
    p = copy.deepcopy(robert)
    p["risk"]["max_acceptable_drawdown_pct"] = -5
    assert _ids(p) == ["C1", "C2"]
    c2 = internal.rule_findings(p, AS_OF)[1]
    assert (c2["source"], c2["status"]) == ("rule:C2", "warning")
    assert set(c2["fields"]) == {"risk.declared", "risk.max_acceptable_drawdown_pct"}


@pytest.mark.parametrize("path", ["risk.declared", "risk.max_acceptable_drawdown_pct"])
def test_c2_skips_when_a_field_is_missing(robert, path):
    p = copy.deepcopy(robert)
    p["risk"]["max_acceptable_drawdown_pct"] = -5      # would fire
    _del(p, path)
    assert "C2" not in _ids(p)


@pytest.mark.parametrize("primary,declared,dd,expected", [
    ("growth", "low_moderate", -12, True),
    ("growth", "low", -8, True),
    ("aggressive_growth", "low", -8, True),
    ("aggressive_growth", "low_moderate", -12, True),
    ("growth", "moderate", -20, False),
    ("aggressive_growth", "high", -40, False),
    ("capital_preservation_with_growth", "low", -8, False),
    ("income", "low_moderate", -12, False),
])
def test_c3_growth_objective_with_low_risk(robert, primary, declared, dd, expected):
    p = copy.deepcopy(robert)
    p["objectives"]["primary"] = primary
    p["risk"]["declared"], p["risk"]["max_acceptable_drawdown_pct"] = declared, dd
    assert ("C3" in _ids(p)) is expected
    if expected:
        assert _ids(p) == ["C1", "C3"]
        c3 = internal.rule_findings(p, AS_OF)[1]
        assert (c3["source"], c3["status"]) == ("rule:C3", "warning")
        assert set(c3["fields"]) == {"objectives.primary", "risk.declared"}


@pytest.mark.parametrize("path", ["objectives.primary", "risk.declared"])
def test_c3_skips_when_a_field_is_missing(robert, path):
    p = copy.deepcopy(robert)
    p["objectives"]["primary"] = "growth"
    _del(p, path)
    assert "C3" not in _ids(p)


def test_c4_shared_asset_type_fires_and_disjoint_does_not(robert):
    assert "C4" not in _ids(robert)
    p = copy.deepcopy(robert)
    p["preferences"]["asset_types_allowed"].append("crypto")
    assert _ids(p) == ["C1", "C4"]
    c4 = internal.rule_findings(p, AS_OF)[1]
    assert (c4["source"], c4["status"]) == ("rule:C4", "warning")
    assert set(c4["fields"]) == {"preferences.asset_types_allowed", "preferences.asset_types_refused"}


@pytest.mark.parametrize("path", ["preferences.asset_types_allowed", "preferences.asset_types_refused"])
def test_c4_skips_when_a_field_is_missing(robert, path):
    p = copy.deepcopy(robert)
    p["preferences"]["asset_types_allowed"].append("crypto")
    _del(p, path)
    assert "C4" not in _ids(p)


@pytest.mark.parametrize("reaction,declared,dd,expected", [
    ("buy_more", "low_moderate", -12, True),
    ("buy_more", "low", -8, True),
    ("buy_more", "moderate", -20, False),
    ("sell_all", "moderate_high", -30, True),
    ("sell_all", "high", -40, True),
    ("sell_all", "moderate", -20, False),
    ("sell_all", "low_moderate", -12, False),
    ("sell_some", "low", -8, False),
    ("sell_some", "high", -40, False),
])
def test_c5_reaction_to_drop_against_declared_level(robert, reaction, declared, dd, expected):
    p = copy.deepcopy(robert)
    p["risk"]["reaction_to_20pct_drop"] = reaction
    p["risk"]["declared"], p["risk"]["max_acceptable_drawdown_pct"] = declared, dd
    assert ("C5" in _ids(p)) is expected
    assert ("C2" in _ids(p)) is False       # the parametrised drawdowns keep C2 quiet: the edit is minimal
    if expected:
        assert _ids(p) == ["C1", "C5"]
        c5 = internal.rule_findings(p, AS_OF)[1]
        assert (c5["source"], c5["status"]) == ("rule:C5", "warning")
        assert set(c5["fields"]) == {"risk.reaction_to_20pct_drop", "risk.declared"}


@pytest.mark.parametrize("path", ["risk.reaction_to_20pct_drop", "risk.declared"])
def test_c5_skips_when_a_field_is_missing(robert, path):
    p = copy.deepcopy(robert)
    p["risk"]["reaction_to_20pct_drop"] = "buy_more"
    _del(p, path)
    assert "C5" not in _ids(p)


def _all_rules_profile(robert: dict) -> dict:
    p = copy.deepcopy(robert)
    p["objectives"]["primary"] = "growth"
    p["risk"]["max_acceptable_drawdown_pct"] = -5
    p["risk"]["reaction_to_20pct_drop"] = "buy_more"
    p["preferences"]["asset_types_allowed"].append("crypto")
    return p


def test_rules_skip_silently_when_whole_sections_are_missing(robert):
    # §20.9: a missing field makes the rule skip (no finding, no error), whatever is missing.
    p = _all_rules_profile(robert)
    assert _ids(p) == ["C1", "C2", "C3", "C4", "C5"]
    assert internal.rule_findings({}, AS_OF) == []
    for section, still_fire in (("objectives", ["C2", "C4", "C5"]), ("risk", ["C1", "C4"]),
                                ("preferences", ["C1", "C2", "C3", "C5"])):
        q = copy.deepcopy(p)
        del q[section]
        assert _ids(q) == still_fire, section
    for section in ("objectives", "risk", "preferences"):
        del p[section]
    assert internal.rule_findings(p, AS_OF) == []


def test_every_rule_finding_has_the_fixed_shape_and_no_violation(robert):
    # §20.9 finding shape; invariant 3: never `violation`.
    p = _all_rules_profile(robert)
    found = internal.rule_findings(p, AS_OF)
    assert [f["id"] for f in found] == ["C1", "C2", "C3", "C4", "C5"]          # rule order
    for f in found:
        assert f["source"] == f"rule:{f['id']}"
        assert f["status"] == "warning" and f["status"] != "violation"
        assert internal.KIND_STATUS[f["kind"]] == f["status"]
        assert len(f["fields"]) >= 2 and all(_get(p, x) is not _MISSING for x in f["fields"])
        assert len(f["values"]) == len(f["fields"])
        assert f["reason"] is None or isinstance(f["reason"], str)


def test_rule_questions_are_fixed_templates_per_rule(robert):
    # §20.9 / D7 point 1: one fr-CA / en-CA template per C-rule, written by code, never by the LLM.
    found = internal.rule_findings(_all_rules_profile(robert), AS_OF)
    for f in found:
        q = f["question"]
        assert set(q) == {"fr-CA", "en-CA"}
        assert all(isinstance(q[k], str) and q[k].strip() for k in q)
        assert q["fr-CA"] != q["en-CA"]
    for loc in ("fr-CA", "en-CA"):
        assert len({f["question"][loc] for f in found}) == 5          # one template per rule
    # SPEC-AMBIGUITY: "fixed" read strictly: the same rule gives the same text whatever the profile values.
    other = internal.rule_findings(robert, "2020-01-15")
    assert [f["id"] for f in other] == ["C1", "C1"]
    assert other[0]["question"] == other[1]["question"] == found[0]["question"]


def test_rules_are_pure_deterministic_and_do_not_mutate(robert):
    # §20.9, D1: pure function of (profile, as_of).
    p = _all_rules_profile(robert)
    before = copy.deepcopy(p)
    assert internal.rule_findings(p, AS_OF) == internal.rule_findings(p, AS_OF)
    assert p == before


# ---------------------------------------------------------------- N6 payload


def test_n6_payload_is_declared_fields_notes_and_rule_hits_only(robert):
    # §20.9: no {value, by} objects, no limits, no llm: fields; includes values.notes and the rule hits.
    hits = internal.rule_findings(robert, AS_OF)
    payload = internal.n6_payload(robert, hits)
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)          # also proves JSON-serialisable
    assert not [d for d in _walk(payload) if "by" in d]
    assert not [d for d in _walk(payload) if "value" in d and "by" in d]
    keys = {k for d in _walk(payload) for k in d}
    assert "limits" not in keys and "limits_trace" not in keys
    assert "llm:" not in blob
    # computed / llm-derived fields of Robert's profile are absent by name
    assert not keys & {"parsed_notes", "behavioural", "question_quality", "capacity", "effective",
                       "effective_horizon_years", "life_stage", "income_stability", "emergency_fund_months", "score"}
    assert NOTES in blob                                                    # values.notes, whole
    for declared in ("low_moderate", "new_opportunities", "capital_preservation_with_growth", "2032-01"):
        assert declared in blob
    assert "C1" in blob and all(path in blob for path in C1_FIELDS)         # the rule hit: id + fields


def test_n6_payload_ignores_computed_and_llm_fields(robert):
    # Invariant 2 spirit: the LLM input is the declared fields; changing any computed / llm: field changes nothing.
    hits = internal.rule_findings(robert, AS_OF)
    base = internal.n6_payload(robert, hits)
    p = copy.deepcopy(robert)
    p["limits"]["max_drawdown_pct"] = -99
    p["limits_trace"]["max_drawdown_pct"] = ["risk.declared"]
    p["risk"]["behavioural"]["value"] = "high"
    p["risk"]["capacity"]["value"] = "low"
    p["risk"]["effective"]["value"] = "low"
    p["objectives"]["effective_horizon_years"]["value"] = 99
    p["knowledge"]["question_quality"]["value"] = 0.01
    p["knowledge"]["score"]["value"] = 1
    p["values"]["parsed_notes"]["value"] = []
    assert internal.n6_payload(p, hits) == base


def test_n6_payload_follows_declared_fields_and_hits(robert):
    hits = internal.rule_findings(robert, AS_OF)
    base = internal.n6_payload(robert, hits)
    p = copy.deepcopy(robert)
    p["values"]["notes"] = "Only boring things."
    assert internal.n6_payload(p, hits) != base
    assert "Only boring things." in json.dumps(internal.n6_payload(p, hits))
    assert internal.n6_payload(robert, []) != base                          # the rule hits are part of the payload
    assert internal.n6_payload(robert, hits) == base                        # deterministic
    before = copy.deepcopy(robert)
    internal.n6_payload(robert, hits)
    assert robert == before


# ---------------------------------------------------------------- gate branch `profile_consistency`


def test_gate_accepts_a_good_output_and_stamps_it(robert):
    # §20.9 gate: accepted -> {value: findings, ...stamp}
    out = _out(F_INTERESTS)
    new, status = gate(NODE, None, out, _ctx(robert))
    assert status == "accepted"
    assert new["value"] == out["findings"]
    assert new["by"] == "llm:profile_consistency"
    assert new["prompt_version"] == "profile_consistency.v1"
    assert new["model_id"] == "fixture:hand-written"
    assert new["input_hash"] == "sha256:" + "cd" * 32
    assert new["updated_at"] == "2026-11-14T10:03:00-05:00"


def test_gate_accepts_every_quote_form_and_the_size_boundaries(robert):
    # scalars via str(), one element of a list, indexed paths, a notes substring, 200-char reason, 5 findings.
    shapes = [
        _finding(["objectives.goals[1].date", "objectives.horizon_years"], ["2032-01", "4"]),
        _finding(["preferences.asset_types_refused", "risk.declared"], ["crypto", "low_moderate"]),
        _finding(["values.notes", "preferences.prefers_dividends"], ["No hype stocks", "True"]),
        _finding(["values.notes", "risk.declared"], [NOTES, "low_moderate"]),
        _finding(["objectives.secondary", "objectives.primary"], ["income", "capital_preservation_with_growth"]),
        _finding(["risk.declared", "risk.max_acceptable_drawdown_pct", "risk.reaction_to_20pct_drop"],
                 ["low_moderate", "-12", "sell_some"]),
        _finding(["risk.declared", "identity.age"], ["low_moderate", "59"], reason="x" * 200),
    ]
    for s in shapes:
        new, status = gate(NODE, None, _out(s), _ctx(robert))
        assert status == "accepted", s
        assert new["value"] == [s]
    new, status = gate(NODE, None, _out(*FIVE), _ctx(robert))
    assert status == "accepted" and len(new["value"]) == 5
    new, status = gate(NODE, None, _out(), _ctx(robert))                     # nothing found is a valid answer
    assert (status, new["value"]) == ("accepted", [])


def test_gate_abstain_keeps_the_old_value_even_with_garbage_findings(robert):
    old = _old()
    assert gate(NODE, None, _out(F_INTERESTS), _ctx(robert))[1] == "accepted"      # the branch exists
    for out in (_out(abstain=True), _out(_finding(["nope.a", "nope.b"], ["x", "y"]), abstain=True)):
        new, status = gate(NODE, copy.deepcopy(old), out, _ctx(robert))
        assert status == "kept:abstain"
        assert new == old
    assert gate(NODE, None, _out(abstain=True), _ctx(robert)) == (None, "kept:abstain")


EVIDENCE_FAILURES = {
    "made_up_path": _finding(["risk.nonexistent", "risk.declared"], ["x", "low_moderate"]),
    "made_up_index": _finding(["objectives.goals[7].date", "risk.declared"], ["2032-01", "low_moderate"]),
    "made_up_section": _finding(["nowhere.field", "risk.declared"], ["x", "low_moderate"]),
    "value_by_effective": _finding(["risk.effective", "risk.declared"], ["low_moderate", "low_moderate"]),
    "value_by_horizon": _finding(["objectives.effective_horizon_years", "objectives.horizon_years"], ["3.6", "4"]),
    "value_by_llm_field": _finding(["risk.behavioural", "risk.declared"], ["moderate_high", "low_moderate"]),
    "value_by_parsed_notes": _finding(["values.parsed_notes", "values.notes"], [HYPE, HYPE]),
    # SPEC-AMBIGUITY: `limits.*` leaves are plain numbers but are computed, not declared; read strictly as not declarable.
    "limits_path": _finding(["limits.max_drawdown_pct", "risk.declared"], ["-12", "low_moderate"]),
    "quote_not_the_value": _finding(["risk.declared", "objectives.primary"],
                                    ["moderate", "capital_preservation_with_growth"]),
    "quote_is_only_part_of_the_value": _finding(["risk.declared", "objectives.primary"],
                                                ["low", "capital_preservation_with_growth"]),
    "quote_is_the_whole_list": _finding(["preferences.asset_types_refused", "risk.declared"],
                                        ["crypto, options", "low_moderate"]),
    "quote_for_wrong_path": _finding(["risk.declared", "objectives.primary"],
                                     ["capital_preservation_with_growth", "low_moderate"]),
    "notes_quote_wrong_case": _finding(["values.notes", "preferences.interests"],
                                       ["no hype stocks unless I can explain the business.", "new_opportunities"]),
    "notes_quote_whitespace_folded": _finding(["values.notes", "preferences.interests"],
                                              ["No hype  stocks", "new_opportunities"]),
    "notes_quote_invented": _finding(["values.notes", "preferences.interests"],
                                     ["I love lottery tickets.", "new_opportunities"]),
    "single_field": _finding(["risk.declared"], ["low_moderate"]),
    "same_path_twice": _finding(["risk.declared", "risk.declared"], ["low_moderate", "low_moderate"]),
    "fewer_quotes_than_paths": _finding(["risk.declared", "objectives.primary"], ["low_moderate"]),
    "more_quotes_than_paths": _finding(["risk.declared", "objectives.primary"],
                                       ["low_moderate", "capital_preservation_with_growth", "x"]),
}


@pytest.mark.parametrize("name", sorted(EVIDENCE_FAILURES))
def test_gate_rejects_bad_paths_and_quotes_as_evidence(robert, name):
    # §20.9 / §11 step 2 / inv. 5: invented or non-declared evidence -> rejected:evidence, previous value kept.
    old = _old()
    new, status = gate(NODE, copy.deepcopy(old), _out(EVIDENCE_FAILURES[name]), _ctx(robert))
    assert status == "rejected:evidence"
    assert new == old


def test_gate_drops_the_whole_output_when_one_finding_is_bad(robert):
    old = _old()
    out = _out(F_INTERESTS, EVIDENCE_FAILURES["made_up_path"], F_RISK)
    new, status = gate(NODE, copy.deepcopy(old), out, _ctx(robert))
    assert (status, new) == ("rejected:evidence", old)
    new, status = gate(NODE, None, out, _ctx(robert))
    assert (status, new) == ("rejected:evidence", None)


def _bad_reason(reason: str) -> dict:
    return _finding(["preferences.interests", "values.notes"], ["new_opportunities", HYPE], reason=reason)


def _advisory_phrases() -> tuple[str, ...]:
    from coherence_analyzer import explain
    return tuple(explain.ADVISORY_BLOCKLIST)


def _schema_failures() -> dict[str, dict]:
    bad = {
        "reason_with_a_digit": _out(_bad_reason("Only 2 statements clash here.")),
        "reason_with_a_digit_at_the_end": _out(_bad_reason("The two statements clash 3")),
        "reason_over_200_chars": _out(_bad_reason("x" * 201)),
        "reason_advisory_mixed_case": _out(_bad_reason("Perhaps You Should Buy something calmer.")),   # SPEC-AMBIGUITY: matched case-insensitively (explain V4 lowercases)
        "six_findings": _out(*FIVE, F_SPARE),
        "kind_violation": _out(_finding(["preferences.interests", "values.notes"], ["new_opportunities", HYPE],
                                        kind="violation")),
        "kind_missing": {"abstain": False, "findings": [{k: v for k, v in F_INTERESTS.items() if k != "kind"}]},
        "reason_missing": {"abstain": False, "findings": [{k: v for k, v in F_INTERESTS.items() if k != "reason"}]},
        "findings_not_a_list": {"abstain": False, "findings": "none"},
    }
    for phrase in _advisory_phrases():
        bad[f"reason_advisory:{phrase}"] = _out(_bad_reason(f"Perhaps {phrase} something calmer."))
    return bad


@pytest.mark.parametrize("name", sorted(_schema_failures()))
def test_gate_rejects_schema_violations_and_drops_the_whole_output(robert, name):
    # §20.9: reason with a digit / advisory phrase / > 200 chars, > 5 findings -> rejected:schema; nothing is kept.
    out = _schema_failures()[name]
    old = _old()
    assert gate(NODE, None, _out(F_INTERESTS), _ctx(robert))[1] == "accepted"      # the branch exists (not the unknown-node fallback)
    new, status = gate(NODE, copy.deepcopy(old), out, _ctx(robert))
    assert status == "rejected:schema"
    assert new == old
    # the same bad finding next to good ones still drops everything
    if name != "six_findings" and isinstance(out["findings"], list):
        mixed = {"abstain": False, "findings": [copy.deepcopy(F_RISK), *copy.deepcopy(out["findings"])]}
        new, status = gate(NODE, copy.deepcopy(old), mixed, _ctx(robert))
        assert (status, new) == ("rejected:schema", old)


def test_gate_status_is_always_one_of_the_known_values(robert):
    known = {"accepted", "clamped", "kept:abstain", "rejected:evidence", "rejected:schema"}
    outs = [_out(F_INTERESTS), _out(abstain=True), *[_out(f) for f in EVIDENCE_FAILURES.values()],
            *_schema_failures().values()]
    assert {gate(NODE, None, o, _ctx(robert))[1] for o in outs} <= known
    assert gate(NODE, None, outs[0], _ctx(robert))[1] == "accepted"          # the branch exists


# ---------------------------------------------------------------- schema, prompt, recorded mock


def test_n6_schema_and_prompt_exist_and_encode_the_contract():
    # §20.9: schema llm/schemas/profile_consistency.json, prompt llm/prompts/profile_consistency.v1.md.
    assert extractors.prompt_version(NODE) == "profile_consistency.v1"
    assert (config.LLM_SCHEMAS_DIR / "profile_consistency.json").is_file()
    assert (config.PROMPTS_DIR / "profile_consistency.v1.md").is_file()
    schema = extractors.load_schema(NODE)
    Draft202012Validator.check_schema(schema)
    v = Draft202012Validator(schema)
    for ok in (_out(F_INTERESTS), _out(), _out(abstain=True), _out(*FIVE)):
        assert not list(v.iter_errors(ok))
    for bad in (_out(_finding(["a.b", "c.d"], ["x", "y"], kind="violation")),            # inv. 3
                {"abstain": False, "findings": [{"kind": "tension", "fields": ["a.b", "c.d"], "quotes": ["x", "y"]}]},
                {"abstain": "no", "findings": []},
                {"abstain": False, "findings": "x"}):
        assert list(v.iter_errors(bad))
    prompt = extractors.load_prompt(NODE).lower()
    assert "abstain" in prompt and "200" in prompt and "5" in prompt


def _recorded_entry(payload: dict) -> dict:
    wanted = json.loads(json.dumps(payload))
    entries = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(config.LLM_CACHE_DIR.glob(f"{NODE}.*.json"))]
    hits = [e for e in entries if e["user_payload"] == wanted]
    assert len(hits) == 1, f"expected one recorded {NODE} response for Robert's payload, found {len(hits)}"
    return hits[0]


def test_recorded_mock_response_for_robert_is_hand_written_valid_and_gated(robert):
    # P0.11: scripts/handwrite_llm_cache.py records it (model id fixture:hand-written), as P0.10 did for N3.
    entry = _recorded_entry(internal.n6_payload(robert, internal.rule_findings(robert, AS_OF)))
    assert entry["model_id"] == "fixture:hand-written"
    assert entry["prompt_version"] == "profile_consistency.v1"
    assert entry["node"] == NODE
    out = entry["output"]
    assert not list(Draft202012Validator(extractors.load_schema(NODE)).iter_errors(out))
    new, status = gate(NODE, None, out, _ctx(robert))
    assert status == "accepted" and len(new["value"]) == 1
    f = out["findings"][0]
    assert f["kind"] == "tension"
    assert dict(zip(f["fields"], f["quotes"])) == {"preferences.interests": "new_opportunities", "values.notes": HYPE}


# ---------------------------------------------------------------- check_profile


def _n6_findings(result: dict) -> list[dict]:
    return [f for f in result["findings"] if f["source"] == "llm:profile_consistency"]


def test_check_profile_robert_in_mock_mode(robert):
    # §20.9: C1 first, then the hand-written N6 tension (interests vs notes) as info; status = worst = warning.
    before = copy.deepcopy(robert)
    res = internal.check_profile(robert, AS_OF)
    assert robert == before                                              # never mutates the profile
    assert res["status"] == "warning"
    assert [f["id"] for f in res["findings"]][0] == "C1"
    assert len(res["findings"]) == 2
    assert res["findings"][0] == internal.rule_findings(robert, AS_OF)[0]
    f = res["findings"][1]
    assert re.fullmatch(r"N6\.\d+", f["id"])
    assert f["source"] == "llm:profile_consistency"
    assert (f["kind"], f["status"]) == ("tension", "info")
    assert dict(zip(f["fields"], f["quotes"])) == {"preferences.interests": "new_opportunities", "values.notes": HYPE}
    assert isinstance(f["reason"], str) and 0 < len(f["reason"]) <= 200 and not re.search(r"\d", f["reason"])
    assert set(f["question"]) == {"fr-CA", "en-CA"}
    assert res["llm"]["gate_status"] == "accepted"
    assert res["llm"]["call_log"]["model_id"] == "fixture:hand-written"
    assert res["llm"]["call_log"]["cache_hit"] is True


def test_check_profile_cache_miss_keeps_rule_findings_only(robert):
    # §20.9: mock mode + LLMCacheMiss -> the rules' findings only, llm.gate_status "skipped:cache_miss".
    p = copy.deepcopy(robert)
    p["values"]["notes"] = "I have never been recorded in the mock cache."          # a genuine miss
    res = internal.check_profile(p, AS_OF)
    assert res["status"] == "warning"
    assert res["findings"] == internal.rule_findings(p, AS_OF)
    assert [f["id"] for f in res["findings"]] == ["C1"]
    assert res["llm"]["gate_status"] == "skipped:cache_miss"


def test_check_profile_cache_miss_on_a_clean_profile_is_a_pass(robert, fake_llm):
    fake_llm(raises=bedrock.LLMCacheMiss("no recording"))
    res = internal.check_profile(_clean(robert), AS_OF)
    assert (res["status"], res["findings"]) == ("pass", [])
    assert res["llm"]["gate_status"] == "skipped:cache_miss"


def test_check_profile_without_llm_makes_no_call(robert, fake_llm):
    fake_llm(_out(F_INTERESTS))
    res = internal.check_profile(robert, AS_OF, use_llm=False)
    assert fake_llm.calls == []
    assert res["llm"] is None
    assert res["status"] == "warning" and res["findings"] == internal.rule_findings(robert, AS_OF)
    clean = internal.check_profile(_clean(robert), AS_OF, use_llm=False)
    assert (clean["status"], clean["findings"], clean["llm"]) == ("pass", [], None)


def test_check_profile_sends_exactly_the_n6_payload_to_the_node(robert, fake_llm):
    fake_llm(_out())
    internal.check_profile(robert, AS_OF)
    assert len(fake_llm.calls) == 1
    call = fake_llm.calls[0]
    assert call["node"] == NODE and call["prompt_version"] == "profile_consistency.v1"
    assert call["payload"] == json.loads(json.dumps(internal.n6_payload(robert, internal.rule_findings(robert, AS_OF))))


def test_status_mapping_contradiction_warning_tension_info_never_violation(robert, fake_llm):
    # §20.9 / inv. 3: contradiction -> warning, tension -> info; status = worst finding status, `pass` if none.
    clean = _clean(robert)
    tension, contradiction = F_INTERESTS, _finding(F_DD["fields"], F_DD["quotes"], kind="contradiction")

    fake_llm(_out(tension))
    res = internal.check_profile(clean, AS_OF)
    assert [(f["kind"], f["status"]) for f in res["findings"]] == [("tension", "info")]
    assert res["status"] == "info"

    fake_llm(_out(contradiction))
    res = internal.check_profile(clean, AS_OF)
    assert [(f["kind"], f["status"]) for f in res["findings"]] == [("contradiction", "warning")]
    assert res["status"] == "warning"

    fake_llm(_out(tension, contradiction))
    res = internal.check_profile(robert, AS_OF)                             # + the rule warning C1
    assert [f["status"] for f in res["findings"]] == ["warning", "info", "warning"]
    assert [f["id"] for f in res["findings"]][0] == "C1"
    assert res["status"] == "warning"
    assert "violation" not in {f["status"] for f in res["findings"]}
    assert len({f["id"] for f in res["findings"]}) == 3                      # N6.<k> ids are distinct

    fake_llm(_out(abstain=True))
    res = internal.check_profile(clean, AS_OF)
    assert (res["status"], res["findings"], res["llm"]["gate_status"]) == ("pass", [], "kept:abstain")


def test_check_profile_status_is_the_worst_of_its_findings(robert, fake_llm):
    fake_llm(_out(F_INTERESTS))
    for profile in (robert, _clean(robert)):
        res = internal.check_profile(profile, AS_OF)
        worst = max((STATUS_RANK[f["status"]] for f in res["findings"]), default=0)
        assert STATUS_RANK[res["status"]] == worst


def test_n6_finding_with_a_rule_findings_path_set_is_dropped(robert, fake_llm):
    # §20.9: "a finding whose path set equals a C-finding's path set is dropped (rules win)".
    dup = _finding(["objectives.goals[1].date", "objectives.horizon_years"], ["2032-01", "4"])
    fake_llm(_out(dup, F_INTERESTS))
    res = internal.check_profile(robert, AS_OF)
    assert res["llm"]["gate_status"] == "accepted"
    assert [f["source"] for f in res["findings"]] == ["rule:C1", "llm:profile_consistency"]
    assert set(_n6_findings(res)[0]["fields"]) == {"preferences.interests", "values.notes"}
    # a strict superset of the C1 path set is a different finding and stays
    wider = _finding(["objectives.horizon_years", "objectives.goals[1].date", "objectives.goals[0].date"],
                     ["4", "2032-01", "2030-06"])
    fake_llm(_out(wider))
    assert len(_n6_findings(internal.check_profile(robert, AS_OF))) == 1


@pytest.mark.parametrize("name", ["made_up_path", "value_by_effective", "notes_quote_wrong_case", "single_field"])
def test_check_profile_drops_a_rejected_n6_output_and_keeps_the_rules(robert, fake_llm, name):
    # inv. 4: a gate failure keeps the previous state; the rule findings are unaffected.
    fake_llm(_out(F_INTERESTS, EVIDENCE_FAILURES[name]))
    res = internal.check_profile(robert, AS_OF)
    assert res["llm"]["gate_status"] == "rejected:evidence"
    assert res["findings"] == internal.rule_findings(robert, AS_OF)
    fake_llm(_out(F_INTERESTS, _bad_reason("Only 2 things.")))
    res = internal.check_profile(robert, AS_OF)
    assert res["llm"]["gate_status"] == "rejected:schema"
    assert res["findings"] == internal.rule_findings(robert, AS_OF)


def test_n6_questions_are_fixed_templates_never_llm_text(robert, fake_llm):
    # §20.9 / D7 point 1: one generic template naming the two first fields; the LLM's reason never reaches it.
    three = _finding(F_INTERESTS["fields"] + ["risk.declared"], F_INTERESTS["quotes"] + ["low_moderate"],
                     reason="Beta wording that differs entirely.")
    other = _finding(F_DD["fields"], F_DD["quotes"], reason="Gamma wording about drawdown.")
    first = _finding(F_INTERESTS["fields"], F_INTERESTS["quotes"], reason="Alpha wording about interests.")
    fake_llm(_out(first, three, other))
    res = internal.check_profile(robert, AS_OF)
    a, b, c = _n6_findings(res)
    assert [a["reason"], b["reason"], c["reason"]] == [first["reason"], three["reason"], other["reason"]]
    for loc in ("fr-CA", "en-CA"):
        assert set(a["question"]) == {"fr-CA", "en-CA"}
        assert all(isinstance(f["question"][loc], str) and f["question"][loc].strip() for f in (a, b, c))
        assert a["question"][loc] == b["question"][loc]             # same two first fields -> same text
        assert a["question"][loc] != c["question"][loc]             # different first fields -> different text
        for f in (a, b, c):
            assert not any(w in f["question"][loc] for w in ("Alpha", "Beta", "Gamma", "wording"))
            assert f["question"][loc] != res["findings"][0]["question"][loc]       # not a C1 template
    assert a["question"]["fr-CA"] != a["question"]["en-CA"]


def test_check_profile_is_deterministic_and_never_mutates_the_profile(robert, fake_llm):
    fake_llm(_out(F_INTERESTS, F_RISK))
    p = _all_rules_profile(robert)
    before = copy.deepcopy(p)
    first = internal.check_profile(p, AS_OF)
    assert p == before
    assert internal.check_profile(p, AS_OF) == first
    assert p == before
    assert [f["id"] for f in first["findings"]][:5] == ["C1", "C2", "C3", "C4", "C5"]      # rules first, in rule order
    assert len(first["findings"]) <= 5 + internal.MAX_FINDINGS


def test_internal_coherence_never_feeds_limits(robert, fake_llm):
    # Invariant 2: running the check (with accepted N6 findings) changes neither the profile nor the limits it yields.
    fake_llm(_out(F_INTERESTS, F_RISK))
    value = 400000

    def build(profile):
        derived = derive.derive_all(profile, portfolio_value_cad=value, as_of=AS_OF)
        return limits_mod.build_limits(derived, value)

    before_profile, before_limits = copy.deepcopy(robert), build(robert)
    res = internal.check_profile(robert, AS_OF)
    assert res["llm"]["gate_status"] == "accepted" and _n6_findings(res)
    assert robert == before_profile
    assert build(robert) == before_limits
