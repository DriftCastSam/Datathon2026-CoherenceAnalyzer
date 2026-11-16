"""D10 "Describe, never prescribe" (docs/spec-decisions.md D10; SPEC inv. 7, §7.3 H7, §8, §9.1-9.3, §13, §15, §18).

What is pinned here (the resizer golden and the Flow B / Flow C report shape live in test_resizer.py, test_flow_b.py
and test_pipeline_offline.py; the V4 behaviour in test_explain.py; the blocklist contents in test_numcheck.py):
  * every deterministic template text passes V4 under the wide advisory list (D10.4, SPEC §9.3 item 3);
  * H7 below-band info states value and bound only (D10.3, SPEC §7.3 H7);
  * the explainer segment kind enum has no `option` (D10.2, SPEC §9.2, §20.5), in the schemas and the verifier;
  * the report schema carries `headroom` (null | object) and no `alternatives` (D10.2, SPEC §13);
  * the English headroom line (SPEC §8) and its absence of 'options' / 'alternatives' / 'you could' wording;
  * explainer prompt v2 with the D10 rule, the extractor points at it, the hand-written mock entries are keyed to it
    (CLAUDE.md: prompt changes bump the version and re-record the mock cache);
  * README "Assumptions" records D10.
Offline: COHERENCE_ANALYZER_LLM=mock, no network, no clock value asserted.
"""
import copy
import json
import os
import re
import shutil
import unicodedata
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

from coherence_analyzer import config, explain, pipeline  # noqa: E402
from coherence_analyzer.llm import bedrock, extractors  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "fixtures" / "robert"
CACHE = ROOT / "fixtures" / "llm_cache"
LOCALES = ("fr-CA", "en-CA")
STATUSES = ("violation", "warning", "info", "unknown")
# every check id of SPEC §7.3 / §7.4 + G1, O1 (docs/spec-factors.md §20.8) and one id no template knows (generic text)
CHECK_IDS = tuple(f"H{i}" for i in range(1, 15)) + ("S1", "S2", "S3", "S4", "G1", "O1", "X9")
KINDS = {"fact", "assumption", "inference", "unknown"}
NBSP = ("\u00a0", "\u202f")
NO_ADVICE_WORDS = re.compile(r"\b(options?|alternatives?|pourriez|you could)\b", re.IGNORECASE)   # SPEC §8
EN_HEADROOM = re.compile(r"With the funding you gave \(ZST\.TO\), this trade crosses your limit H9 above 1\.5 ?%\.")


def _fold(text: str) -> str:
    """V4 normal form (SPEC §9.3 item 3): NFKD, strip combining marks, casefold."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).casefold()


def _blocked(text: str) -> list[str]:
    """Blocklist entries found in `text`, both sides folded."""
    low = _fold(text)
    return [p for p in explain.ADVISORY_BLOCKLIST if _fold(p) in low]


def _facts(locale: str, entries: list[dict], status: str = "violation", **extra) -> dict:
    return {"factor": "risque", "locale": locale, "question": "?", "status": status, "verdict": "incoherent",
            "intent": None, "declared": {}, "derived": {}, "checks": entries,
            "metrics": {"current": {}, "proposed": {}}, "evidence_events": [], "unknowns": [], **extra}


def _entry(cid: str, status: str, locale: str, **extra) -> dict:
    val, lim = ("7,4 %", "9,0 %") if locale == "fr-CA" else ("7.4%", "9.0%")
    return {"id": cid, "status": status, "subject": "NVDA", "value": val, "limit": lim,
            "derived_from": ["limits.x", "risk.effective"], **extra}


def _v4_failures(output: dict, facts: dict) -> list[str]:
    return [f for f in explain.verify_section(output, facts) if f.startswith("V4")]


# ---------------------------------------------------------------- D10.4: templates pass V4 under the wide list

@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("cid", CHECK_IDS)
def test_every_check_template_text_passes_v4_under_the_wide_blocklist(cid, status, locale):
    """D10.4 + SPEC §9.3 item 3: the H-check / S-check / G1 / O1 templates (violation, warning, info, unknown, FR and
    EN) never contain a blocklisted phrase, e.g. French "considéré" / "envisagé" would match `consider` / `envisage`.
    The list is not weakened: the template wording changes instead."""
    facts = _facts(locale, [_entry(cid, status, locale)], status=status)
    seg = explain.template_section(facts)
    assert seg["mode"] == "template" and seg["segments"]
    for s in seg["segments"]:
        assert _blocked(s["text"]) == [], (cid, status, locale, s["text"])
    assert _v4_failures(seg, facts) == []
    assert all(s["kind"] in KINDS for s in seg["segments"])                  # no `option` kind in any template


@pytest.mark.parametrize("locale", LOCALES)
def test_section_template_extras_pass_v4_why_segment_and_no_gap_text(locale):
    """D10.4: the "why this limit exists" sentence and the "no gap" sentence of `template_section` pass V4."""
    derived = {"risk.effective": {"value": "low_moderate", "by": "rule:effective_risk",
                                  "because": ["risk.declared = moderate"]}}
    with_why = _facts(locale, [_entry("H4", "violation", locale)], derived=derived)
    no_gap = _facts(locale, [], status="pass")
    for facts in (with_why, no_gap):
        out = explain.template_section(facts)
        for s in out["segments"]:
            assert _blocked(s["text"]) == [], s["text"]
        assert _v4_failures(out, facts) == []
    assert len(explain.template_section(with_why)["segments"]) >= 2          # the why segment is really there


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("verdict", ["coherent", "coherent_with_warnings", "incoherent"])
def test_headline_template_passes_v4_under_the_wide_blocklist(verdict, locale):
    """D10.4: the template headline (verdict + failing check ids) has no advisory phrase, with or without ids."""
    for cids in ([], ["H3", "H4", "H7", "H8", "H9", "H10"]):
        sections = [{"facts": {"checks": [{"id": c, "status": "violation"} for c in cids]}}]
        text = explain.headline(verdict, sections, locale)
        assert text and len(text) <= explain.MAX_HEADLINE_CHARS
        assert _blocked(text) == [], text


@pytest.mark.parametrize("locale", LOCALES)
def test_disclaimer_is_fixed_and_passes_v4(locale):
    """SPEC §9.4 fixed text; D10.4: it contains no advisory phrase (so a V4 over the full report text stays clean)."""
    assert explain.disclaimer(locale) == config.DISCLAIMER[locale]
    assert _blocked(config.DISCLAIMER[locale]) == []


# ---------------------------------------------------------------- D10.3: H7 below-band info = value and bound only

@pytest.mark.parametrize("locale", LOCALES)
def test_h7_below_band_info_states_value_and_bound_only(locale):
    """D10.3 + SPEC §7.3 H7: "below the lower bound of the band for your profile": value and bound only, no
    judgement. The old "more conservative than your profile (needs)" / "plus prudent que votre profil" is gone."""
    entry = _entry("H7", "info", locale)
    facts = _facts(locale, [entry], status="info")
    segs = explain.template_section(facts)["segments"]
    text = " ".join(s["text"] for s in segs)
    assert entry["value"] in text and entry["limit"] in text
    low = _fold(text)
    for judgement in ("prudent", "conservative", "conservateur", "plus que", "needs", "besoin", "safer", "sur que"):
        assert judgement not in low, (judgement, text)
    assert "H7" in text or any("H7" in s["refs"] for s in segs)
    assert all(s["kind"] == "fact" for s in segs)
    assert explain.verify_section(explain.template_section(facts), facts) == []


@pytest.mark.parametrize("locale", LOCALES)
def test_h7_violation_template_is_unchanged_in_substance(locale):
    """D10.3 touches the info line only: the above-band violation still states value and bound."""
    entry = _entry("H7", "violation", locale)
    text = explain.template_section(_facts(locale, [entry]))["segments"][0]["text"]
    assert entry["value"] in text and entry["limit"] in text


# ---------------------------------------------------------------- D10.2: no `option` kind

def test_segment_kind_enum_has_no_option_in_schemas_verifier_and_templates():
    """D10.2 + SPEC §9.2 / §20.5: kind is fact|assumption|inference|unknown (no `option`), in the explainer tool
    schema, the report schema's segment and the verifier."""
    expl = json.loads((config.LLM_SCHEMAS_DIR / "explainer.json").read_text(encoding="utf-8"))
    rep = json.loads((config.SCHEMAS_DIR / "report.json").read_text(encoding="utf-8"))
    assert set(expl["properties"]["segments"]["items"]["properties"]["kind"]["enum"]) == KINDS
    assert set(rep["$defs"]["segment"]["properties"]["kind"]["enum"]) == KINDS
    assert set(explain.KINDS) == KINDS and "option" not in explain.KINDS
    facts = _facts("fr-CA", [_entry("H4", "violation", "fr-CA")])
    out = {"segments": [{"kind": "option", "text": "H4: valeur 7,4 %, limite 9,0 %.", "refs": ["H4"]}]}
    assert any(f.startswith("V5") for f in explain.verify_section(out, facts))
    assert not Draft202012Validator(expl).is_valid(out)


# ---------------------------------------------------------------- D10.2: report schema, `headroom` replaces `alternatives`

def test_report_schema_has_headroom_null_or_object_and_no_alternatives():
    """D10.2 + SPEC §13: `headroom` is required, null | object; `alternatives` is no longer part of the schema."""
    schema = json.loads((config.SCHEMAS_DIR / "report.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert "headroom" in schema["required"] and "headroom" in schema["properties"]
    assert "alternatives" not in schema["required"] and "alternatives" not in schema["properties"]
    sub = {"$defs": schema.get("$defs", {}), **schema["properties"]["headroom"]}
    v = Draft202012Validator(sub)
    assert v.is_valid(None)
    assert v.is_valid({"funding": {"kind": "specific", "ticker": "ZST.TO"}, "max_weight_pct": 1.5,
                       "binding_check": "H9", "metrics": {"p5_T": 360503.0}})
    assert v.is_valid({"none_fit": True, "binding_checks": ["H4"]})
    assert not v.is_valid([])                                                # the old list of options is not valid
    assert not v.is_valid([{"funding": {"kind": "pro_rata_all"}, "max_weight_pct": 2.0}])
    assert not v.is_valid("1.5")


def test_report_schema_text_has_no_options_or_alternatives_wording():
    """SPEC §8 / §18: no 'alternatives' or 'options' left in the schema (key names and descriptions)."""
    text = (config.SCHEMAS_DIR / "report.json").read_text(encoding="utf-8")
    assert not re.search(r"alternatives?|\boptions?\b", text, re.IGNORECASE)


# ---------------------------------------------------------------- SPEC §8: the headroom line, EN

@pytest.fixture(scope="module")
def en_report(tmp_path_factory) -> dict:
    """Flow B in English: a copy of the Robert fixture with `meta.locale = en-CA` (N4 / N1 answers come from the
    recorded mock cache; the explainer has no EN recording, so those sections fall back to the template)."""
    work = tmp_path_factory.mktemp("en") / "robert"
    shutil.copytree(FIX, work)
    path = work / "profile.json"
    profile = json.loads(path.read_text(encoding="utf-8"))
    assert profile["meta"]["locale"] == "fr-CA"
    profile["meta"]["locale"] = "en-CA"
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    mp = pytest.MonkeyPatch()
    mp.chdir(work.parent)
    try:
        return pipeline.check(work, "in_0001", auto_confirm=True)
    finally:
        mp.undo()


def test_english_headroom_line_is_the_fixed_factual_sentence(en_report):
    """SPEC §8 + D10.2: EN "With the funding you gave (ZST.TO), this trade crosses your limit H9 above X %."
    stated in the report; X is the golden 1.5; no 'options' / 'alternatives' / 'you could'."""
    # SPEC-AMBIGUITY: the key holding the line is not named; stricter reading: it is in the saved report somewhere.
    flat = json.dumps(en_report, ensure_ascii=False)
    for n in NBSP:
        flat = flat.replace(n, " ")
    found = EN_HEADROOM.search(flat)
    assert found, "English headroom line missing from the report"
    assert _blocked(found.group(0)) == []
    assert not NO_ADVICE_WORDS.search(found.group(0))
    assert en_report["headroom"]["max_weight_pct"] == pytest.approx(1.5, abs=1e-9)
    assert en_report["headroom"]["binding_check"] == "H9"
    assert "alternatives" not in en_report
    for text in en_report["assumptions"] + [en_report["explanation"]["headline"]]:
        assert not NO_ADVICE_WORDS.search(text), text


# ---------------------------------------------------------------- CLAUDE.md: prompt v2 + mock cache

def test_explainer_prompt_v2_carries_the_describe_never_prescribe_rule():
    """D10.4 + SPEC §9.2 prompt rules + CLAUDE.md (prompt change -> new version): `explainer.v2.md` exists, says
    'describe, never prescribe', rules out imperative / suggestive wording in any person or mood and any ranking, and
    allows stating the measured effect of the user's own trade."""
    path = config.PROMPTS_DIR / "explainer.v2.md"
    assert path.exists()
    low = path.read_text(encoding="utf-8").lower()
    assert "describe, never prescribe" in low
    for needle in ("mood", "person", "rank", "measured effect", "consider", "you could"):
        assert needle in low, needle
    for factor in ("objectif", "horizon", "risque", "contraintes", "preferences"):
        assert factor in low, factor


def test_extractor_points_at_explainer_v2_and_other_prompts_stay_v1():
    """CLAUDE.md: only the explainer prompt is bumped; N1-N4, N6 keep their version (their cache stays valid)."""
    assert extractors.prompt_version("explainer") == "explainer.v2"
    for node in ("behaviour_classifier", "question_rubric", "values_parser", "intent_parser", "profile_consistency"):
        assert extractors.prompt_version(node) == f"{node}.v1"
    assert extractors.load_prompt("explainer") == (config.PROMPTS_DIR / "explainer.v2.md").read_text(
        encoding="utf-8").strip()


def test_hand_written_explainer_entries_are_keyed_to_prompt_v2():
    """CLAUDE.md + task note: the hand-written (model `fixture:hand-written`) explainer entries are re-keyed to v2, so
    the offline demo still reads them; real recorded entries may stay v1 (cache misses, the template answers)."""
    hand = []
    for p in sorted(CACHE.glob("explainer.*.json")):
        entry = json.loads(p.read_text(encoding="utf-8"))
        if entry["model_id"] == bedrock.MOCK_MODEL_ID:
            hand.append((p, entry))
    assert hand, "no hand-written explainer entry left in fixtures/llm_cache"
    validator = Draft202012Validator(extractors.load_schema("explainer"))
    for p, entry in hand:
        assert entry["prompt_version"] == "explainer.v2", p.name
        key = bedrock.cache_key("explainer", "explainer.v2", bedrock.MOCK_MODEL_ID, entry["user_payload"])
        assert p.name == f"explainer.{key}.json" and entry["cache_key"] == key, p.name
        validator.validate(entry["output"])
    covered = {e["user_payload"]["factor"] for _, e in hand}
    assert {"horizon", "risque", "contraintes"} <= covered                   # the three non-pass Robert sections


def test_mock_cache_holds_no_hand_written_v1_explainer_entry():
    """No stale hand-written v1 entry stays next to the v2 ones (it would be dead weight and a stale answer)."""
    stale = [p.name for p in sorted(CACHE.glob("explainer.*.json"))
             if json.loads(p.read_text(encoding="utf-8"))["model_id"] == bedrock.MOCK_MODEL_ID
             and json.loads(p.read_text(encoding="utf-8"))["prompt_version"] != "explainer.v2"]
    assert stale == []


# ---------------------------------------------------------------- README "Assumptions"

def test_readme_assumptions_record_d10():
    """CLAUDE.md: every assumption goes in README -> "Assumptions". D10: the headroom is a measurement for the user's
    own funding (not advice), `alternatives` became `headroom`, the blocklist is wider."""
    text = (ROOT / "docs" / "assumptions.md").read_text(encoding="utf-8")
    start = text.index("## Assumptions")
    end = text.find("\n## ", start + 1)
    block = text[start:end if end != -1 else len(text)]
    assert "D10" in block
    low = block.lower()
    assert "headroom" in low
    assert "describe" in low and "prescribe" in low
