"""Hand-written N5 `explainer` cache entries for Amira and Mateo (P4.CACHE; SPEC §16 "hand-write plausible responses",
§9.2-§9.3 verifier V1-V5, §6.1 mock cache, §1 inv. 5-7; docs/spec-factors.md §20.5; docs/spec-decisions.md D5, D10).

Before this task `cli demo` (mock) ended every Amira and Mateo section in `template`, with LLMCacheMiss for explainer.v2:
  amira  (amira-main)  horizon (violation), contraintes (violation), preferences (info)
  mateo  (mateo-main)  contraintes (unknown), preferences (info)
Now each of those sections must have a hand-written entry (model `fixture:hand-written`, same `cache_key` as Robert's
entries, keyed by the exact section facts) that passes `explain.verify_section` on the first attempt, so the section ends
in mode `llm`. `pass` sections never reach the writer. Robert's modes are unchanged (Robert + NVDA contraintes stays
`template` by design).

Offline and deterministic: COHERENCE_ANALYZER_LLM=mock, the reports are produced by `pipeline.review` in a temp working
directory (the store never touches the repo), the reference date is `portfolio.as_of` (D1), fixed engine seed.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

from coherence_analyzer import config, explain, pipeline, store  # noqa: E402
from coherence_analyzer.llm import bedrock, extractors  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
NODE = "explainer"
MOCK_MODEL = "fixture:hand-written"
REVIEW_PORTFOLIO = "portfolio_review.json"

# (persona, portfolio file) -> {factor: status}: the sections of the demo (cli.demo), in display order.
DEMO_STATUSES = {
    ("amira", None): {"objectif": "pass", "horizon": "violation", "risque": "pass", "contraintes": "violation",
                      "preferences": "info"},
    ("mateo", None): {"objectif": "pass", "horizon": "pass", "risque": "pass", "contraintes": "unknown",
                      "preferences": "info"},
}
PERSONA_CASES = sorted(DEMO_STATUSES, key=lambda k: k[0])
NON_PASS = {k: [f for f, s in v.items() if s != "pass"] for k, v in DEMO_STATUSES.items()}
# Robert's modes before and after this task (SPEC §16 / test_explain.py / test_pipeline_offline.py).
ROBERT_MODES = {
    None: {"objectif": "template", "horizon": "template", "risque": "template", "contraintes": "llm",
           "preferences": "template"},
    REVIEW_PORTFOLIO: {"objectif": "template", "horizon": "llm", "risque": "llm_retry", "contraintes": "template",
                       "preferences": "template"},
}


# ---------------------------------------------------------------- the demo's reviews (mock cache only)

@pytest.fixture(scope="module")
def demo_runs(tmp_path_factory):
    """{(persona, portfolio file): {"rep": report, "audit": audit record}} for the personas of `cli demo`."""
    mp = pytest.MonkeyPatch()
    mp.setattr(bedrock, "COHERENCE_ANALYZER_LLM", "mock")        # recorded / hand-written responses only
    mp.chdir(tmp_path_factory.mktemp("persona_cache_store"))
    runs: dict = {}
    try:
        for persona, portfolio in [("amira", None), ("mateo", None), ("robert", None), ("robert", REVIEW_PORTFOLIO)]:
            rep = pipeline.review(FIXTURES / persona, portfolio)
            runs[(persona, portfolio)] = {"rep": rep, "audit": store.load_audit(rep["run_id"])}
    finally:
        mp.undo()
    return runs


def _sections(run: dict) -> dict[str, dict]:
    return {s["factor"]: s for s in run["rep"]["sections"]}


def _cache_entry(facts: dict, required: bool = True) -> dict | None:
    key = bedrock.cache_key(NODE, extractors.prompt_version(NODE), bedrock.MOCK_MODEL_ID, facts)
    p = Path(config.LLM_CACHE_DIR) / f"{NODE}.{key}.json"
    assert p.exists() or not required, f"no hand-written {NODE} entry {p.name} for these section facts"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _texts(output: dict) -> list[str]:
    return [seg["text"] for seg in output["segments"]]


def _non_pass_cases():
    return [(persona, portfolio, factor) for (persona, portfolio) in PERSONA_CASES for factor in NON_PASS[(persona, portfolio)]]


# ---------------------------------------------------------------- the demo's section modes

@pytest.mark.parametrize("persona,portfolio", PERSONA_CASES)
def test_demo_section_statuses_are_the_audited_ones(demo_runs, persona, portfolio):
    """The sections the task covers are exactly the non-pass ones of the audit gap (SPEC §16, §20.4 status)."""
    got = {f: s["status"] for f, s in _sections(demo_runs[(persona, portfolio)]).items()}
    assert got == DEMO_STATUSES[(persona, portfolio)]


@pytest.mark.parametrize("persona,portfolio,factor", _non_pass_cases())
def test_every_non_pass_persona_section_ends_in_llm(demo_runs, persona, portfolio, factor):
    """SPEC §16 / §20.5: the hand-written entry is accepted on the first attempt: mode `llm`, verifier passed."""
    sec = _sections(demo_runs[(persona, portfolio)])[factor]
    nar = sec["narrative"]
    assert nar["mode"] == "llm", (persona, factor, nar["mode"])
    assert nar["verify"] == {"passed": True, "failures": []}
    assert nar["segments"], "an accepted narration has at least one segment"


@pytest.mark.parametrize("persona,portfolio", PERSONA_CASES)
def test_pass_sections_stay_template_and_never_reach_the_writer(demo_runs, persona, portfolio):
    """§20.5: `pass` sections are narrated by the template, with no N5 call."""
    run = demo_runs[(persona, portfolio)]
    passing = [f for f, s in DEMO_STATUSES[(persona, portfolio)].items() if s == "pass"]
    assert passing
    for factor in passing:
        assert _sections(run)[factor]["narrative"]["mode"] == "template"
    called = {c.get("section") for c in run["audit"]["llm_calls"] if c["node"] == NODE}
    assert called.isdisjoint(passing)


@pytest.mark.parametrize("persona,portfolio", PERSONA_CASES)
def test_report_and_audit_modes_agree_and_explanation_is_llm(demo_runs, persona, portfolio):
    """§10 / §20.5: audit.section_modes mirrors the report; the report mode is the worst non-pass mode (`llm`)."""
    run = demo_runs[(persona, portfolio)]
    expected = {f: s["narrative"]["mode"] for f, s in _sections(run).items()}
    assert run["audit"]["section_modes"] == expected
    assert run["rep"]["explanation"]["mode"] == "llm"
    assert run["audit"]["explanation_mode"] == "llm"


@pytest.mark.parametrize("persona,portfolio", PERSONA_CASES)
def test_no_explainer_cache_miss_left_in_the_audit(demo_runs, persona, portfolio):
    """The audit gap: `llm_errors` no longer lists LLMCacheMiss for explainer.v2 (SPEC §10, §6.1)."""
    errors = demo_runs[(persona, portfolio)]["audit"]["llm_errors"]
    assert [e for e in errors if e.get("node") == NODE] == []
    assert not any("LLMCacheMiss" in str(e.get("error")) for e in errors if e.get("node") == NODE)


@pytest.mark.parametrize("persona,portfolio", PERSONA_CASES)
def test_one_hand_written_call_per_non_pass_section(demo_runs, persona, portfolio):
    """First attempt only: exactly one N5 call per non-pass section, from the hand-written model (§6.1, §9.3)."""
    run = demo_runs[(persona, portfolio)]
    calls = [c for c in run["audit"]["llm_calls"] if c["node"] == NODE]
    assert sorted(c["section"] for c in calls) == sorted(NON_PASS[(persona, portfolio)])
    for c in calls:
        assert c["model_id"] == MOCK_MODEL
        assert c["prompt_version"] == extractors.prompt_version(NODE)
        assert c["cache_hit"] is True
        assert c["gate"] == "accepted"
    for factor in NON_PASS[(persona, portfolio)]:
        attempts = _sections(run)[factor]["narrative"].get("attempts")
        if attempts is not None:
            assert len(attempts) == 1 and attempts[0]["failures"] == []


# ---------------------------------------------------------------- the cache entries themselves

@pytest.mark.parametrize("persona,portfolio,factor", _non_pass_cases())
def test_entry_is_keyed_like_robert_on_the_exact_section_facts(demo_runs, persona, portfolio, factor):
    """Same `cache_key` function as Robert's entries; payload = the section facts unchanged (no retry_failures)."""
    facts = _sections(demo_runs[(persona, portfolio)])[factor]["facts"]
    entry = _cache_entry(facts)
    assert entry is not None, f"no hand-written {NODE} entry for {persona} / {factor}"
    assert entry["node"] == NODE
    assert entry["model_id"] == MOCK_MODEL
    assert entry["prompt_version"] == extractors.prompt_version(NODE)
    assert entry["cache_key"] == bedrock.cache_key(NODE, entry["prompt_version"], MOCK_MODEL, facts)
    assert entry["input_hash"] == bedrock.input_hash(facts)
    assert entry["user_payload"] == facts
    assert "retry_failures" not in entry["user_payload"]
    assert entry["call_log"]["hand_written"] is True


@pytest.mark.parametrize("persona,portfolio,factor", _non_pass_cases())
def test_entry_output_is_schema_valid_and_passes_v1_to_v5(demo_runs, persona, portfolio, factor):
    """SPEC §9.2 schema + §9.3 V1-V5 on the first attempt: refs resolve, numbers in the facts, size limits."""
    facts = _sections(demo_runs[(persona, portfolio)])[factor]["facts"]
    out = _cache_entry(facts)["output"]
    Draft202012Validator(extractors.load_schema(NODE)).validate(out)
    assert explain.verify_section(out, facts) == []
    assert 1 <= len(out["segments"]) <= explain.MAX_SEGMENTS
    for seg in out["segments"]:
        assert seg["refs"], "every segment cites at least one facts key (V1)"
        assert len(seg["text"]) <= explain.MAX_SEGMENT_CHARS
        assert explain.numcheck(seg["text"], facts) == []         # every number exists in the facts (CLAUDE.md)


@pytest.mark.parametrize("persona,portfolio,factor", _non_pass_cases())
def test_entry_describes_never_prescribes(demo_runs, persona, portfolio, factor):
    """D10 / SPEC inv. 7 / §9.3 item 3: no advisory wording, no option-style segment kind."""
    facts = _sections(demo_runs[(persona, portfolio)])[factor]["facts"]
    out = _cache_entry(facts)["output"]
    for seg in out["segments"]:
        assert explain.advisory_hits(seg["text"]) == [], seg["text"]
        assert seg["kind"] in {"fact", "assumption", "inference", "unknown"}
    assert explain.advisory_hits(" ".join(_texts(out))) == []


@pytest.mark.parametrize("persona,portfolio,factor", _non_pass_cases())
def test_entry_covers_every_failing_or_unknown_check(demo_runs, persona, portfolio, factor):
    """V3 (SPEC §9.3): each violation / warning check id is cited; `unknown` checks are named by `unknown` segments."""
    facts = _sections(demo_runs[(persona, portfolio)])[factor]["facts"]
    out = _cache_entry(facts)["output"]
    cited = {r for seg in out["segments"] for r in seg["refs"]}
    for c in facts["checks"]:
        if c["status"] in ("violation", "warning"):
            assert c["id"] in cited, (factor, c["id"])
    unknown_ids = {c["id"] for c in facts["checks"] if c["status"] == "unknown"}
    unknown_cited = {r for seg in out["segments"] if seg["kind"] == "unknown" for r in seg["refs"]}
    # SPEC-AMBIGUITY: an unknown check could be cited in a `fact` segment too; stricter reading: an `unknown` segment names it.
    assert unknown_ids <= unknown_cited, (factor, unknown_ids - unknown_cited)


@pytest.mark.parametrize("persona,portfolio,factor", _non_pass_cases())
def test_entry_is_a_hand_written_narration_not_the_template(demo_runs, persona, portfolio, factor):
    """SPEC §16: plausible hand-written responses; the stored answer is not a copy of the deterministic template."""
    facts = _sections(demo_runs[(persona, portfolio)])[factor]["facts"]
    out = _cache_entry(facts)["output"]
    assert out["segments"] != explain.template_section(facts)["segments"]


def test_no_entry_exists_for_a_pass_section(demo_runs):
    """§20.5: `pass` sections never reach the writer, so no hand-written entry is keyed on their facts."""
    for (persona, portfolio) in PERSONA_CASES:
        for factor, st in DEMO_STATUSES[(persona, portfolio)].items():
            if st == "pass":
                facts = _sections(demo_runs[(persona, portfolio)])[factor]["facts"]
                assert _cache_entry(facts, required=False) is None, (persona, factor)


# ---------------------------------------------------------------- Robert unchanged

@pytest.mark.parametrize("portfolio", [None, REVIEW_PORTFOLIO])
def test_robert_modes_are_unchanged(demo_runs, portfolio):
    """Robert current: contraintes `llm` (unknown H6/H10); Robert + NVDA: horizon llm, risque llm_retry, contraintes
    template by design (SPEC §16, docs/spec-factors.md §20.5)."""
    modes = {f: s["narrative"]["mode"] for f, s in _sections(demo_runs[("robert", portfolio)]).items()}
    assert modes == ROBERT_MODES[portfolio]


def test_robert_nvda_contraintes_keeps_its_failure_trail(demo_runs):
    """Robert + NVDA contraintes stays `template` by design: two rejected attempts (V2, then V4), then the template."""
    nar = _sections(demo_runs[("robert", REVIEW_PORTFOLIO)])["contraintes"]["narrative"]
    assert nar["mode"] == "template"
    if "attempts" in nar:
        assert len(nar["attempts"]) == 2 and all(a["failures"] for a in nar["attempts"])
