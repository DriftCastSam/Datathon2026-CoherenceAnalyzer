"""Narration loop: N5 explainer per section, verifier V1-V5, one retry, template fallback (docs/spec-factors.md §20.5, §20.8
test_explain; SPEC §1 inv. 5-7, 9, §6.1, §9.3, §9.4; docs/spec-decisions.md D5, D6, D7).

Offline by construction (COHERENCE_ANALYZER_LLM=mock): the Bedrock wrapper is pointed at a temporary cache directory
holding hand-written entries, written with `bedrock.write_entry` and keyed by the real `cache_key` of the exact payload
the loop must send (node `explainer`, `extractors.prompt_version("explainer")`, the mock model id). A spy around
`bedrock.call_tool` counts calls. Nothing is written to `fixtures/llm_cache`.

Cases (Robert + NVDA 10%, flow C review, profile derived with as_of 2026-11-12; sections measured in test_factors.py):
horizon violation (H9), risque violation (H4, H7, H8), contraintes violation (H3, H10) + unknown (H6, H10),
objectif pass, preferences pass.

Contract these tests pin down (the stubs name `explain.verify_section(output, facts)` and `explain.explain(payload)`):
  * `explain.explain(payload)` takes `{"verdict", "locale", "sections": [section, ...]}` where each section is a
    `factors.build_sections` item (it carries `facts`), and returns the report's explanation:
    `{"mode", "headline", "segments", "disclaimer", "sections"}`; `result["sections"]` holds, in display order,
    `{"factor", "narrative": {"mode", "segments", "verify": {"passed", "failures"}}}` (§20.4 narrative object).
  * the first N5 call's `user_payload` is the section's facts unchanged; the retry call's is the facts plus
    `retry_failures` (the failure list `verify_section` returned for the first answer).
"""
import copy
import json
import re

import pytest
from jsonschema import Draft202012Validator

from coherence_analyzer import config, engine, explain, factors
from coherence_analyzer.coherence import report as report_mod
from coherence_analyzer.coherence import hard
from coherence_analyzer.llm import bedrock, extractors
from coherence_analyzer.rules import derive, limits as limits_mod
from coherence_analyzer import data

AS_OF = "2026-11-12"
ROBERT_VALUE = 400_000
ROBERT_HORIZON_M = 48
META_NAMES = ("asset_meta", "etf_sector_weights", "activity_tags")
DISPLAY = ("objectif", "horizon", "risque", "contraintes", "preferences")
NON_PASS = ("horizon", "risque", "contraintes")       # Robert + NVDA: the sections that call the writer
PASS_FACTORS = ("objectif", "preferences")
NODE = "explainer"
KINDS = {"fact", "assumption", "inference", "unknown"}
MODE_RANK = {"llm": 0, "llm_retry": 1, "template": 2}  # §20.5: worst = template > llm_retry > llm
FAILING_IDS = ("H3", "H4", "H7", "H8", "H9", "H10")     # violations of Robert + NVDA (test_factors.py)

DISCLAIMER_FR = ("Simulation éducative avec des profils fictifs. Ceci n'est pas un conseil financier personnalisé. "
                 "Aucune transaction n'est exécutée.")                                           # SPEC §9.4
DISCLAIMER_EN = ("Educational simulation with fictional profiles. This is not personalised financial advice. "
                 "No transaction is executed.")                                                  # SPEC §9.4


# ---------------------------------------------------------------- fixtures: Robert + NVDA sections

def _build_payload(root, market, locale: str) -> dict:
    raw = json.loads((root / "fixtures" / "robert" / "profile.json").read_text())
    profile = derive.derive_all(copy.deepcopy(raw), portfolio_value_cad=ROBERT_VALUE, as_of=AS_OF)
    lim, trace = limits_mod.build_limits(profile, ROBERT_VALUE)
    profile["limits"], profile["limits_trace"] = lim, trace
    metrics = engine.analyse(market, engine.ROBERT_NVDA, ROBERT_HORIZON_M, ROBERT_VALUE)
    checks = hard.run_all({t: v / 100 for t, v in engine.ROBERT_NVDA.items()}, metrics, profile,
                          data.load_assets(), {n: data.load_meta(n) for n in META_NAMES})
    ctx = factors.SectionContext(profile=profile, limits=lim, limits_trace=trace, checks_current=checks,
                                 metrics_current=metrics, locale=locale)
    sections = factors.build_sections(ctx)
    verdict = report_mod.verdict_from_statuses(c["status"] for c in checks)
    return {"verdict": verdict, "locale": locale, "sections": sections}


@pytest.fixture(scope="module")
def payload_fr(root, market) -> dict:
    return _build_payload(root, market, "fr-CA")


@pytest.fixture(scope="module")
def payload_en(root, market) -> dict:
    return _build_payload(root, market, "en-CA")


def _facts(payload: dict, factor: str) -> dict:
    return next(s for s in payload["sections"] if s["factor"] == factor)["facts"]


def _subset(payload: dict, factor_ids) -> dict:
    return {**payload, "sections": [s for s in payload["sections"] if s["factor"] in factor_ids]}


# ---------------------------------------------------------------- hand-written N5 outputs

def _vw_ids(facts: dict) -> list[str]:
    """Check ids with status violation or warning, in order, no duplicates (V3 must cover them)."""
    return list(dict.fromkeys(c["id"] for c in facts["checks"] if c["status"] in ("violation", "warning")))


def _unknown_ids(facts: dict) -> list[str]:
    return list(dict.fromkeys(c["id"] for c in facts["checks"] if c["status"] == "unknown"))


def _good_output(facts: dict, variant: str = "a") -> dict:
    """A schema-valid answer that passes V1-V5 against `facts`: every number is copied from the facts strings."""
    fr = facts["locale"] == "fr-CA"
    segs = []
    for cid in _vw_ids(facts):
        c = next(x for x in facts["checks"] if x["id"] == cid and x["status"] in ("violation", "warning"))
        subj = f" ({c['subject']})" if c.get("subject") else ""
        if fr:
            text = (f"{cid}{subj}: la valeur est {c['value']} pour une limite de {c['limit']}." if variant == "b"
                    else f"{cid}{subj}: valeur {c['value']}, limite {c['limit']}.")
        else:
            text = (f"{cid}{subj}: the value is {c['value']} against a limit of {c['limit']}." if variant == "b"
                    else f"{cid}{subj}: value {c['value']}, limit {c['limit']}.")
        segs.append({"kind": "fact", "text": text, "refs": [cid]})
    unk = _unknown_ids(facts)
    if unk:
        text = (f"{', '.join(unk)}: ne peut pas être vérifié." if fr else f"{', '.join(unk)}: cannot be verified.")
        segs.append({"kind": "unknown", "text": text, "refs": unk})
    return {"segments": segs}


def _bad_output(facts: dict, rule: str, variant: str = "") -> dict:
    """A crafted answer that breaks exactly one verifier rule (and nothing else)."""
    out = copy.deepcopy(_good_output(facts))
    segs = out["segments"]
    if rule == "V1":
        if variant == "no_refs":
            segs[0]["refs"] = []
        else:
            segs[0]["refs"] = segs[0]["refs"] + ["no_such_key"]
    elif rule == "V2":
        segs[0]["text"] += " Le repli pourrait atteindre 77,7 %."
    elif rule == "V3":
        # SPEC-AMBIGUITY: the uncovered check keeps a segment that cites a real key path (`status`), so only V3 fires.
        last = _vw_ids(facts)[-1]
        idx = next(i for i, s in enumerate(segs) if s["refs"] == [last])
        segs[idx] = {"kind": "fact", "text": "Le statut de cette section est en violation.", "refs": ["status"]}
    elif rule == "V4":
        segs[0]["text"] = "Je recommande de réduire cette position. " + segs[0]["text"]
    elif rule == "V5":
        if variant == "too_many":
            while len(segs) < 5:
                segs.append(copy.deepcopy(segs[0]))
        elif variant == "too_long":
            segs[0]["text"] += " " + "mot " * 80
        elif variant == "inference":
            segs[0]["kind"] = "inference"
        elif variant == "bad_kind":
            segs[0]["kind"] = "option"
        else:
            raise AssertionError(variant)
    else:
        raise AssertionError(rule)
    return out


def _rules(failures: list[str]) -> set[str]:
    """Rule tags of a failure list.
    SPEC-AMBIGUITY: §20.5 says `verify_section` returns a list[str] of failures without fixing the wording. The strict
    reading used here: each failure string starts with its rule id (`V1`..`V5`), as test_factors.py's local helper does."""
    return {m.group(0) for f in failures if (m := re.match(r"V[1-5]", f))}


# ---------------------------------------------------------------- scenario harness (mock cache + call spy)

@pytest.fixture
def llm(monkeypatch, tmp_path):
    """Mock mode on an empty temporary cache + a spy on `bedrock.call_tool`. Returns a handle with `.calls`/`.cache`."""
    monkeypatch.setenv("COHERENCE_ANALYZER_LLM", "mock")
    for mod in (config, bedrock):
        monkeypatch.setattr(mod, "COHERENCE_ANALYZER_LLM", "mock", raising=False)
        monkeypatch.setattr(mod, "BEDROCK_MODEL_ID", "", raising=False)      # hand-written entries use MOCK_MODEL_ID
        monkeypatch.setattr(mod, "LLM_CACHE_DIR", tmp_path, raising=False)
    calls: list[dict] = []
    real = bedrock.call_tool

    def spy(*args, **kwargs):
        bound = dict(zip(("node", "schema", "system", "user_payload", "prompt_version"), args))
        bound.update(kwargs)
        calls.append(copy.deepcopy(bound))
        return real(*args, **kwargs)

    monkeypatch.setattr(bedrock, "call_tool", spy)

    class Handle:
        pass

    h = Handle()
    h.calls, h.cache = calls, tmp_path
    return h


def _write(cache, facts: dict, output: dict, retry_failures: list[str] | None = None) -> None:
    """Hand-write one llm_cache entry for the payload the loop must send (facts, or facts + retry_failures)."""
    user_payload = dict(facts) if retry_failures is None else {**facts, "retry_failures": retry_failures}
    bedrock.write_entry(cache, NODE, extractors.prompt_version(NODE), bedrock.MOCK_MODEL_ID, user_payload, output,
                        {"tokens_in": 0, "tokens_out": 0})


def _install(h, payload: dict, plan: dict) -> None:
    """plan: factor -> "good" | ("retry", rule[, variant]) | "bad2". Only non-pass sections get entries.
    good: facts -> good answer. retry: facts -> bad answer, facts+retry_failures -> good answer (variant b).
    bad2: facts -> bad answer, facts+retry_failures -> another bad answer (never a third entry)."""
    for s in payload["sections"]:
        if s["status"] == "pass":
            continue
        facts = s["facts"]
        spec = plan[s["factor"]]
        if spec == "good":
            _write(h.cache, facts, _good_output(facts))
            continue
        if spec == "bad2":
            first, second = _bad_output(facts, "V2"), _bad_output(facts, "V4")
        else:
            first, second = _bad_output(facts, *spec[1:]), _good_output(facts, "b")
        _write(h.cache, facts, first)
        _write(h.cache, facts, second, retry_failures=explain.verify_section(first, facts))


def _narratives(result: dict) -> dict:
    # SPEC-AMBIGUITY: the stub's `explain(payload)` is the only per-report entry point; the §20.4 narrative objects
    # are read from `result["sections"][i]["narrative"]` (display order, 5 items for a 5-section payload).
    return {s["factor"]: s["narrative"] for s in result["sections"]}


def _calls_for(h, factor: str) -> list[dict]:
    return [c for c in h.calls if c["user_payload"].get("factor") == factor]


ALL_GOOD = {f: "good" for f in NON_PASS}


# ================================================================ verifier V1-V5 (§20.5, §20.8)

@pytest.mark.parametrize("fid", NON_PASS)
def test_verify_accepts_a_good_answer(fid, payload_fr):
    """§20.5: an answer with refs that resolve, copied numbers, full coverage and a valid shape returns no failure."""
    facts = _facts(payload_fr, fid)
    assert explain.verify_section(_good_output(facts), facts) == []
    assert explain.verify_section(_good_output(facts, "b"), facts) == []


@pytest.mark.parametrize("loc", ["fr", "en"])
@pytest.mark.parametrize("fid", DISPLAY)
def test_verify_accepts_the_template(fid, loc, payload_fr, payload_en):
    """§20.5: `template_section(facts)` always passes V1-V5, in both locales, including `pass` sections."""
    facts = _facts(payload_fr if loc == "fr" else payload_en, fid)
    assert explain.verify_section(explain.template_section(facts), facts) == []


def test_verify_v1_unresolved_ref_is_rejected(payload_fr):
    """V1: a ref that is no check id, no dotted key path of the facts and no evidence event_id is rejected."""
    facts = _facts(payload_fr, "risque")
    fails = explain.verify_section(_bad_output(facts, "V1"), facts)
    assert _rules(fails) == {"V1"}


def test_verify_v1_segment_without_ref_is_rejected(payload_fr):
    """V1: every segment has >= 1 ref."""
    facts = _facts(payload_fr, "risque")
    fails = explain.verify_section(_bad_output(facts, "V1", "no_refs"), facts)
    assert "V1" in _rules(fails)


def test_verify_v1_event_id_must_be_in_evidence_events(payload_fr):
    """V1 / inv. 5: an event_id resolves only when it is in `facts.evidence_events`; the same id is fine once it is."""
    facts = copy.deepcopy(_facts(payload_fr, "horizon"))
    out = copy.deepcopy(_good_output(facts))
    out["segments"][0]["refs"].append("ev_robert_0007")
    assert "V1" in _rules(explain.verify_section(out, facts))              # not in facts.evidence_events
    facts["evidence_events"] = [{"event_id": "ev_robert_0007", "date": "2026-11-14", "text": "10 % de NVDA"}]
    assert explain.verify_section(out, facts) == []


@pytest.mark.parametrize("ref", ["metrics.current.p5_T", "declared.objectives.horizon_years",
                                 "derived.objectives.effective_horizon_years", "H9"])
def test_verify_v1_accepts_check_ids_and_dotted_key_paths(ref, payload_fr):
    """V1: a check id of `facts.checks` or a dotted key path in `facts` (keys may themselves contain dots) resolves."""
    facts = _facts(payload_fr, "horizon")
    out = copy.deepcopy(_good_output(facts))
    out["segments"][0]["refs"].append(ref)
    assert explain.verify_section(out, facts) == []


def test_verify_v2_number_not_in_facts_is_rejected(payload_fr):
    """V2: a number that does not exist in the facts is rejected (numcheck, §9.3 item 2)."""
    facts = _facts(payload_fr, "risque")
    bad = _bad_output(facts, "V2")
    assert explain.numcheck(bad["segments"][0]["text"], facts) == ["77,7 %"]   # precondition: numcheck names it
    assert _rules(explain.verify_section(bad, facts)) == {"V2"}


def test_verify_v2_checks_every_segment(payload_fr):
    """V2: the number is caught whichever segment carries it."""
    facts = _facts(payload_fr, "risque")
    out = _good_output(facts)
    out["segments"][-1]["text"] += " La perte pourrait atteindre 77,7 %."
    assert "V2" in _rules(explain.verify_section(out, facts))


@pytest.mark.parametrize("fid", ["horizon", "risque"])
def test_verify_v3_uncovered_violation_is_rejected(fid, payload_fr):
    """V3: every violation / warning check id of `facts.checks` must be referenced by some segment."""
    facts = _facts(payload_fr, fid)
    fails = explain.verify_section(_bad_output(facts, "V3"), facts)
    assert _rules(fails) == {"V3"}
    assert any(_vw_ids(facts)[-1] in f for f in fails)       # the failure names the uncovered check


def test_verify_v3_covers_warnings_too(payload_fr):
    """V3: a `warning` check is covered like a violation (SPEC §1 inv. 3: only the check kind differs)."""
    facts = copy.deepcopy(_facts(payload_fr, "risque"))
    facts["checks"][0]["status"] = "warning"
    out = _good_output(facts)
    assert explain.verify_section(out, facts) == []
    out["segments"] = [s for s in out["segments"] if s["refs"] != [facts["checks"][0]["id"]]]
    assert "V3" in _rules(explain.verify_section(out, facts))


def test_verify_v3_unknown_needs_no_ref(payload_fr):
    """V3 only demands violation / warning ids: dropping the `unknown` segment of contraintes still passes."""
    facts = _facts(payload_fr, "contraintes")
    out = _good_output(facts)
    out["segments"] = [s for s in out["segments"] if s["kind"] != "unknown"]
    assert explain.verify_section(out, facts) == []


@pytest.mark.parametrize("phrase", ["Je recommande de réduire cette position.", "Tu devrais vendre NVDA.",
                                    "You should sell NVDA.", "We recommend trimming this position.",
                                    "YOU SHOULD BUY more bonds."])
def test_verify_v4_advisory_wording_is_rejected(phrase, payload_fr):
    """V4 / inv. 7: the advisory blocklist (FR + EN, case-insensitive) rejects buy/sell recommendations."""
    facts = _facts(payload_fr, "risque")
    out = _good_output(facts)
    out["segments"][0]["text"] = phrase + " " + out["segments"][0]["text"]
    assert _rules(explain.verify_section(out, facts)) == {"V4"}


def test_verify_v4_each_blocklist_entry_is_rejected(payload_fr):
    """V4: every phrase of `explain.ADVISORY_BLOCKLIST` is rejected."""
    facts = _facts(payload_fr, "horizon")
    for phrase in explain.ADVISORY_BLOCKLIST:
        out = _good_output(facts)
        out["segments"][0]["text"] = f"{phrase} ceci. " + out["segments"][0]["text"]
        assert "V4" in _rules(explain.verify_section(out, facts)), phrase


@pytest.mark.parametrize("variant", ["too_many", "too_long", "inference", "bad_kind"])
def test_verify_v5_shape_is_rejected(variant, payload_fr):
    """V5: 1-4 segments, each <= 300 chars, kind in fact|assumption|inference|unknown, `inference` only with an
    llm-derived field or evidence events (horizon has neither)."""
    facts = _facts(payload_fr, "horizon")
    assert not any(d["by"].startswith("llm:") for d in facts["derived"].values()) and not facts["evidence_events"]
    fails = explain.verify_section(_bad_output(facts, "V5", variant), facts)
    assert _rules(fails) == {"V5"}


def test_verify_v5_empty_segment_list_is_rejected(payload_fr):
    """V5: zero segments is out of the 1-4 range."""
    facts = _facts(payload_fr, "horizon")
    assert "V5" in _rules(explain.verify_section({"segments": []}, facts))


def test_verify_v5_boundaries_are_accepted(payload_fr):
    """V5: exactly 300 characters and exactly 4 segments are allowed."""
    facts = _facts(payload_fr, "horizon")
    out = _good_output(facts)
    pad = 300 - len(out["segments"][0]["text"]) - 1
    out["segments"][0]["text"] += " " + "m" * pad
    assert len(out["segments"][0]["text"]) == 300
    while len(out["segments"]) < 4:
        out["segments"].append(copy.deepcopy(out["segments"][0]))
    assert explain.verify_section(out, facts) == []


def test_verify_v5_inference_allowed_with_llm_derived_field(payload_fr):
    """V5: `risque` shows `risk.behavioural` (by llm:behaviour_classifier), so an `inference` segment is allowed."""
    facts = _facts(payload_fr, "risque")
    assert any(d["by"].startswith("llm:") for d in facts["derived"].values())
    out = _good_output(facts)
    out["segments"][0]["kind"] = "inference"
    assert explain.verify_section(out, facts) == []


def test_verify_v5_inference_allowed_with_evidence_events(payload_fr):
    """V5: evidence events also allow `inference` (horizon has none by default, see the rejection test above)."""
    facts = copy.deepcopy(_facts(payload_fr, "horizon"))
    facts["evidence_events"] = [{"event_id": "ev_robert_0007", "date": "2026-11-14", "text": "10 % de NVDA"}]
    out = _good_output(facts)
    out["segments"][0]["kind"] = "inference"
    assert explain.verify_section(out, facts) == []


def test_verify_reports_every_broken_rule(payload_fr):
    """§20.5 'all must pass': an answer that breaks V2 and V4 and V5 gets all three tags, not only the first."""
    facts = _facts(payload_fr, "risque")
    out = _bad_output(facts, "V2")
    out["segments"][0]["text"] = "Je recommande ceci. " + out["segments"][0]["text"] + " " + "mot " * 80
    assert _rules(explain.verify_section(out, facts)) == {"V2", "V4", "V5"}


def test_verify_is_pure(payload_fr):
    """verify_section is deterministic and does not mutate its inputs."""
    facts = _facts(payload_fr, "risque")
    out = _bad_output(facts, "V2")
    before = (copy.deepcopy(out), copy.deepcopy(facts))
    first = explain.verify_section(out, facts)
    assert explain.verify_section(out, facts) == first
    assert (out, facts) == before


# ================================================================ writer assets: schema + prompt (§20.5)

def test_explainer_schema_matches_the_spec():
    """§20.5: `schemas/explainer.json` is a draft 2020-12 schema of {segments: 1-4 x {kind, text <= 300, refs}}."""
    schema = json.loads((config.LLM_SCHEMAS_DIR / f"{NODE}.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    v = Draft202012Validator(schema)
    seg = {"kind": "fact", "text": "H9: valeur 316 971 $.", "refs": ["H9"]}
    assert v.is_valid({"segments": [seg]})
    assert v.is_valid({"segments": [seg] * 4})
    assert not v.is_valid({"segments": []})                                   # 1 to 4 segments
    assert not v.is_valid({"segments": [seg] * 5})
    assert not v.is_valid({"segments": [{**seg, "kind": "option"}]})          # kind enum (no `option` in §20.5)
    assert not v.is_valid({"segments": [{**seg, "text": "x" * 301}]})         # <= 300 chars
    assert not v.is_valid({"segments": [{"kind": "fact", "text": "x"}]})      # refs is required
    assert not v.is_valid({})
    for kind in KINDS:
        assert v.is_valid({"segments": [{**seg, "kind": kind}]})


def test_explainer_prompt_is_versioned_and_has_one_block_per_factor():
    """§20.5 + CLAUDE.md: `prompts/explainer.v<N>.md` (common rules + one guidance block per factor)."""
    version = extractors.prompt_version(NODE)
    assert re.fullmatch(r"explainer\.v\d+", version)
    text = (config.PROMPTS_DIR / f"{version}.md").read_text(encoding="utf-8").lower()
    for fid in DISPLAY:
        assert fid in text, fid


# ================================================================ the loop: llm / llm_retry / template

def test_good_answer_is_mode_llm_one_call_per_non_pass_section(llm, payload_fr):
    """§20.5: pass at the first verification -> mode `llm`; one N5 call per non-pass section, with the facts as payload."""
    _install(llm, payload_fr, ALL_GOOD)
    result = explain.explain(payload_fr)
    narr = _narratives(result)
    assert [c["node"] for c in llm.calls] == [NODE] * 3
    assert {c["user_payload"]["factor"] for c in llm.calls} == set(NON_PASS)
    for c in llm.calls:
        assert c["prompt_version"] == extractors.prompt_version(NODE)
        # SPEC-AMBIGUITY: "the only thing the writer sees for that section" read strictly: payload == facts, key for key.
        assert c["user_payload"] == _facts(payload_fr, c["user_payload"]["factor"])
        assert "retry_failures" not in c["user_payload"]
    for fid in NON_PASS:
        facts = _facts(payload_fr, fid)
        assert narr[fid]["mode"] == "llm"
        assert narr[fid]["segments"] == _good_output(facts)["segments"]       # the answer is used as written
        assert narr[fid]["verify"] == {"passed": True, "failures": []}
    assert result["mode"] == "llm"


@pytest.mark.parametrize("rule", [("V1",), ("V2",), ("V3",), ("V4",), ("V5", "too_long")], ids=lambda r: r[0])
def test_bad_then_good_is_mode_llm_retry(rule, llm, payload_fr):
    """§20.5: each of V1-V5 failing at the first answer -> second call with `retry_failures` (own cache key) -> `llm_retry`."""
    plan = {**ALL_GOOD, "risque": ("retry", *rule)}
    _install(llm, payload_fr, plan)
    facts = _facts(payload_fr, "risque")
    first_bad = _bad_output(facts, *rule)
    result = explain.explain(payload_fr)
    narr = _narratives(result)
    risque_calls = _calls_for(llm, "risque")
    assert len(risque_calls) == 2
    assert "retry_failures" not in risque_calls[0]["user_payload"]
    assert risque_calls[0]["user_payload"] == facts
    failures = risque_calls[1]["user_payload"]["retry_failures"]
    assert isinstance(failures, list) and failures and all(isinstance(f, str) for f in failures)
    # SPEC-AMBIGUITY: the retry payload is `facts + retry_failures`, and `retry_failures` is exactly the list that
    # `verify_section` returned for the first answer (the strict reading of "the failure list appended").
    assert failures == explain.verify_section(first_bad, facts)
    assert rule[0] in _rules(failures)
    assert {k: v for k, v in risque_calls[1]["user_payload"].items() if k != "retry_failures"} == facts
    assert narr["risque"]["mode"] == "llm_retry"
    assert narr["risque"]["segments"] == _good_output(facts, "b")["segments"]
    assert narr["risque"]["verify"]["passed"] is True
    assert narr["horizon"]["mode"] == narr["contraintes"]["mode"] == "llm"
    assert len(llm.calls) == 4
    assert result["mode"] == "llm_retry"


def test_bad_twice_falls_back_to_template_after_two_calls(llm, payload_fr):
    """§20.5: failing twice -> `template_section(facts)`, mode `template`; never more than 2 calls for the section."""
    plan = {**ALL_GOOD, "contraintes": "bad2"}
    _install(llm, payload_fr, plan)
    facts = _facts(payload_fr, "contraintes")
    result = explain.explain(payload_fr)
    narr = _narratives(result)
    assert len(_calls_for(llm, "contraintes")) == 2
    assert len(llm.calls) == 1 + 1 + 2
    assert narr["contraintes"]["mode"] == "template"
    assert narr["contraintes"]["segments"] == explain.template_section(facts)["segments"]
    # the template is what ships: it passes V1-V5 whatever the writer said
    assert explain.verify_section({"segments": narr["contraintes"]["segments"]}, facts) == []
    assert narr["contraintes"]["verify"]["passed"] is True
    assert narr["horizon"]["mode"] == narr["risque"]["mode"] == "llm"
    assert result["mode"] == "template"


def test_unverified_text_never_ships(llm, payload_fr):
    """§1 inv. 6-7 + §20.5: answers that failed V2 (invented number) then V4 (advisory wording) never reach the report."""
    plan = {f: "bad2" for f in NON_PASS}
    _install(llm, payload_fr, plan)
    result = explain.explain(payload_fr)
    narr = _narratives(result)
    shipped = " ".join(s["text"] for s in result["segments"])
    assert "77,7" not in shipped                                  # the invented number of the V2 answer
    assert "je recommande" not in shipped.lower()                 # the advisory wording of the V4 answer
    for fid in NON_PASS:
        assert narr[fid]["mode"] == "template"
        assert explain.verify_section({"segments": narr[fid]["segments"]}, _facts(payload_fr, fid)) == []
    assert len(llm.calls) == 2 * len(NON_PASS)


def test_mixed_modes_explanation_mode_is_the_worst(llm, payload_fr):
    """§20.5: explanation.mode = worst section mode (template > llm_retry > llm); each section keeps its own mode."""
    plan = {"horizon": "good", "risque": ("retry", "V2"), "contraintes": "bad2"}
    _install(llm, payload_fr, plan)
    result = explain.explain(payload_fr)
    narr = _narratives(result)
    assert (narr["horizon"]["mode"], narr["risque"]["mode"], narr["contraintes"]["mode"]) == \
        ("llm", "llm_retry", "template")
    assert result["mode"] == "template"
    assert len(llm.calls) == 1 + 2 + 2
    assert len(llm.calls) <= 2 * len(NON_PASS)                    # cost bound: at most 2 calls per non-pass section
    modes = [narr[f]["mode"] for f in NON_PASS]
    assert result["mode"] == max(modes, key=MODE_RANK.__getitem__)


# ================================================================ pass sections never call the writer

def test_pass_sections_make_no_llm_call(llm, payload_fr):
    """§20.5: only `status != pass` sections call the writer; objectif and preferences (pass) use the template."""
    _install(llm, payload_fr, ALL_GOOD)
    result = explain.explain(payload_fr)
    narr = _narratives(result)
    assert {c["user_payload"]["factor"] for c in llm.calls} == set(NON_PASS)
    assert _calls_for(llm, "objectif") == [] and _calls_for(llm, "preferences") == []
    for fid in PASS_FACTORS:
        assert narr[fid]["mode"] == "template"
        assert narr[fid]["segments"] == explain.template_section(_facts(payload_fr, fid))["segments"]


def test_payload_with_only_pass_sections_calls_nothing(llm, payload_fr):
    """§20.5: no non-pass section -> zero N5 calls and no cache lookup (the mock cache is empty: a lookup would raise)."""
    only_pass = _subset(payload_fr, PASS_FACTORS)
    assert {s["status"] for s in only_pass["sections"]} == {"pass"}
    result = explain.explain(only_pass)
    assert llm.calls == []
    assert list(llm.cache.iterdir()) == []
    # SPEC-AMBIGUITY: with no section written by the LLM the "worst section mode" is the template mode (only
    # non-pass sections count, otherwise `llm` would be unreachable for Robert + NVDA, where two sections pass).
    assert result["mode"] == "template"
    assert result["disclaimer"] == DISCLAIMER_FR
    assert {s["factor"] for s in result["segments"]} == set(PASS_FACTORS)


# ================================================================ explanation: headline, segments, disclaimer

def _run(llm, payload, plan):
    _install(llm, payload, plan)
    return explain.explain(payload)


def test_explanation_segments_are_the_section_segments_in_display_order(llm, payload_fr):
    """§20.5: `explanation.segments` = the section segments in display order, each with a `factor` key; kinds labelled."""
    plan = {"horizon": "good", "risque": ("retry", "V4"), "contraintes": "bad2"}
    result = _run(llm, payload_fr, plan)
    assert [s["factor"] for s in result["sections"]] == list(DISPLAY)
    expected = [(s["factor"], seg["kind"], seg["text"], seg["refs"])
                for s in result["sections"] for seg in s["narrative"]["segments"]]
    assert [(g["factor"], g["kind"], g["text"], g["refs"]) for g in result["segments"]] == expected
    assert {g["factor"] for g in result["segments"]} == set(DISPLAY)
    order = [DISPLAY.index(g["factor"]) for g in result["segments"]]
    assert order == sorted(order)
    assert all(g["kind"] in KINDS for g in result["segments"])                       # inv. 9: labelled everywhere
    assert all(g["refs"] for g in result["segments"])


@pytest.mark.parametrize("plan_name", ["good", "bad2"])
def test_explanation_covers_every_failing_check_and_passes_numcheck(plan_name, llm, payload_fr):
    """§1 inv. 6 + §20.5 V2/V3 at report level: every violation id is referenced; every number exists in the payload."""
    result = _run(llm, payload_fr, {f: plan_name for f in NON_PASS})
    refs = {r for g in result["segments"] for r in g["refs"]}
    assert set(FAILING_IDS) <= refs
    text = " ".join([result["headline"]] + [g["text"] for g in result["segments"]])
    assert explain.numcheck(text, payload_fr) == []
    low = text.lower()
    assert not any(p in low for p in explain.ADVISORY_BLOCKLIST)


def test_headline_is_template_text_independent_of_the_writer(llm, payload_fr, monkeypatch, tmp_path):
    """§20.5: `explanation.headline` is a template from the verdict and the failing check ids, never LLM text."""
    good = _run(llm, payload_fr, ALL_GOOD)
    second = tmp_path / "second_cache"
    second.mkdir()
    for mod in (config, bedrock):
        monkeypatch.setattr(mod, "LLM_CACHE_DIR", second, raising=False)
    llm.cache = second
    template = _run(llm, payload_fr, {f: "bad2" for f in NON_PASS})
    assert good["mode"] == "llm" and template["mode"] == "template"
    assert good["headline"] == template["headline"]
    head = good["headline"]
    assert isinstance(head, str) and 0 < len(head) <= 140                           # SPEC §9.2 headline bound
    for cid in FAILING_IDS:
        assert re.search(rf"\b{cid}\b", head), (cid, head)
    assert head not in {seg["text"] for seg in good["segments"]}


@pytest.mark.parametrize("plan_name", ["good", "bad2"])
def test_disclaimer_is_the_fixed_text(plan_name, llm, payload_fr):
    """SPEC §9.4: the disclaimer is the fixed config text (fr-CA), never produced by the writer."""
    result = _run(llm, payload_fr, {f: plan_name for f in NON_PASS})
    assert result["disclaimer"] == DISCLAIMER_FR == config.DISCLAIMER["fr-CA"] == explain.disclaimer("fr-CA")


def test_english_locale_runs_the_loop_and_uses_the_english_disclaimer(llm, payload_en):
    """SPEC §9.4 + §20.4: en-CA facts go through the same loop; the disclaimer is the fixed English text."""
    assert all(s["facts"]["locale"] == "en-CA" for s in payload_en["sections"])
    result = _run(llm, payload_en, ALL_GOOD)
    assert result["mode"] == "llm"
    assert result["disclaimer"] == DISCLAIMER_EN == config.DISCLAIMER["en-CA"] == explain.disclaimer("en-CA")
    assert len(llm.calls) == 3
    assert explain.numcheck(" ".join(g["text"] for g in result["segments"]), payload_en) == []


def test_same_inputs_same_explanation(llm, payload_fr):
    """§1 inv. 8: the same payload and the same recorded responses give the same explanation, call for call."""
    plan = {"horizon": "good", "risque": ("retry", "V2"), "contraintes": "bad2"}
    first = _run(llm, payload_fr, plan)
    first_calls = copy.deepcopy(llm.calls)
    llm.calls.clear()
    second = explain.explain(payload_fr)
    assert second == first
    assert llm.calls == first_calls
