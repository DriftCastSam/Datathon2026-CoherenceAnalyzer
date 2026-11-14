"""Write the hand-written N1, N3, N4 and N6 responses into fixtures/llm_cache (SPEC §16: "until Bedrock access works,
hand-write plausible responses that pass the schemas"): Robert N1 + N4, N3 for Robert / Amira / Mateo, N6 for the three
personas (P1.1 added Mateo's N3 and Amira's / Mateo's N6; it never rewrites an entry with different content).

    python scripts/handwrite_llm_cache.py

Entries are keyed by the real cache_key() of the real payload (built by the same extractor code the pipeline uses) with
model_id `fixture:hand-written`. They are replaced by real recordings with COHERENCE_ANALYZER_LLM=record (P1.11).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jsonschema import Draft202012Validator  # noqa: E402

from coherence_analyzer import config, data, gate  # noqa: E402
from coherence_analyzer.coherence import internal  # noqa: E402
from coherence_analyzer.llm import bedrock, extractors  # noqa: E402

ROBERT = ROOT / "fixtures" / "robert"
AMIRA = ROOT / "fixtures" / "amira"
MATEO = ROOT / "fixtures" / "mateo"
CALL_LOG = {"hand_written": True, "tokens_in": 0, "tokens_out": 0, "latency_ms": 0}

N1_OUTPUT = {
    "abstain": False,
    "value": "moderate_high",
    "confidence": 0.55,
    "evidence": [
        {"event_id": "ev_robert_0007", "signal": "hype_chasing",
         "note": "asks to put 10% in one stock because everyone is talking about it"},
        {"event_id": "ev_robert_0004", "signal": "hype_chasing", "note": "asks which AI stock is the next big one"},
    ],
}

# N2 (SPEC §6.2 N2 sanity checks): the XBB-duration question scores 10/12 = 0.83 (>= 0.75), "next big AI stock" 1/12 = 0.08
# (<= 0.35). Quotes are exact substrings of the question text.
N2_OUTPUTS = {
    "ev_robert_0004": {"event_id": "ev_robert_0004", "quote": "Which AI stock is the next big one?",
                       "specificity": 1, "correct_concepts": 0, "risk_awareness": 0, "horizon_awareness": 0,
                       "is_investing_question": True,
                       "rationale": "Asks for the next winning stock: names a theme only, no concept, risk or time frame."},
    "ev_robert_0005": {"event_id": "ev_robert_0005", "quote": "XBB's duration",
                       "specificity": 3, "correct_concepts": 3, "risk_awareness": 2, "horizon_awareness": 2,
                       "is_investing_question": True,
                       "rationale": "Names a fund and a precise concept (duration) and ties it to a rate scenario."},
}

N4_OUTPUT = {
    "action": "buy",
    "ticker": "NVDA",
    "target_weight_pct": 10,
    "funding": {"kind": "pro_rata_all"},
    "ambiguous_fields": ["funding"],
    "unresolved_mentions": [],
}


N3_ROBERT_OUTPUT = {"rules": [
    {"rule_kind": "prefer_attribute", "params": {"attribute": "understandable_business"},
     "applies_to": "single_stocks", "quote": "I prefer companies I understand."},
    {"rule_kind": "avoid_attribute", "params": {"attribute": "hype"}, "applies_to": "single_stocks",
     "quote": "No hype stocks unless I can explain the business."},
]}

# Mateo's notes (SPEC §16): both controversy tags (the ones he declared in values.environment / values.social), the
# "new tech if it fits" half as a non-checkable `other` rule, and a soft `speculative` reminder (S4).
N3_MATEO_NOTES = ("Avoid companies with major environmental or social controversies. "
                  "Open to new tech if it fits, nothing speculative.")
_MATEO_CONTROVERSY = "Avoid companies with major environmental or social controversies."
N3_MATEO_OUTPUT = {"rules": [
    {"rule_kind": "exclude_activity", "params": {"tag": "significant_environmental_controversy"}, "applies_to": "all",
     "quote": _MATEO_CONTROVERSY},
    {"rule_kind": "exclude_activity", "params": {"tag": "significant_social_controversy"}, "applies_to": "all",
     "quote": _MATEO_CONTROVERSY},
    {"rule_kind": "other", "params": {"note": "open to new tech if it fits"}, "applies_to": "all",
     "quote": "Open to new tech if it fits"},
    {"rule_kind": "avoid_attribute", "params": {"attribute": "speculative"}, "applies_to": "all",
     "quote": "nothing speculative."},
]}

# Amira's notes (SPEC §16): a theme rule with NO number, so the intake form asks for it (D7 point 1).
N3_AMIRA_NOTES = "I love AI and new tech but I don't want everything in tech."
N3_AMIRA_OUTPUT = {"rules": [
    {"rule_kind": "prefer_attribute", "params": {"attribute": "ai_and_new_tech"}, "applies_to": "all",
     "quote": "I love AI and new tech"},
    {"rule_kind": "max_theme_weight", "params": {"theme": "tech_heavy"}, "applies_to": "all",
     "quote": "I don't want everything in tech."},
]}

# N6 (docs/spec-factors.md 20.9): Robert's interests vs his notes. Quotes are the values at their paths / a notes substring.
N6_ROBERT_OUTPUT = {"abstain": False, "findings": [
    {"kind": "tension", "fields": ["preferences.interests", "values.notes"],
     "quotes": ["new_opportunities", "No hype stocks unless I can explain the business."],
     "reason": "Interest in new opportunities may pull against the wish to avoid hype stocks."},
]}

# Amira: her interest in AI / new tech sits against her own "not everything in tech" note (SPEC §16).
N6_AMIRA_OUTPUT = {"abstain": False, "findings": [
    {"kind": "tension", "fields": ["preferences.interests", "values.notes"],
     "quotes": ["new_tech", "I don't want everything in tech."],
     "reason": "Interest in new tech may pull against the wish not to hold everything in tech."},
]}

# Mateo: his declared exclusions, interests and notes agree; the N6 call finds nothing to flag (and does not abstain).
N6_MATEO_OUTPUT = {"abstain": False, "findings": []}


def _write_n6(fixture: Path, output: dict) -> None:
    profile = json.loads((fixture / "profile.json").read_text(encoding="utf-8"))
    as_of = json.loads((fixture / "portfolio.json").read_text())["as_of"]
    payload = internal.n6_payload(profile, internal.rule_findings(profile, as_of))
    Draft202012Validator(extractors.load_schema("profile_consistency")).validate(output)
    context = {"event_ids": set(), "notes": profile["values"]["notes"], "profile": profile,
               "model_id": bedrock.MOCK_MODEL_ID, "prompt_version": extractors.prompt_version("profile_consistency"),
               "input_hash": bedrock.input_hash(payload)}
    assert gate.gate("profile_consistency", None, output, context)[1] == "accepted"
    p = bedrock.write_entry(config.LLM_CACHE_DIR, "profile_consistency", extractors.prompt_version("profile_consistency"),
                            bedrock.MOCK_MODEL_ID, payload, output, CALL_LOG)
    print(p.relative_to(ROOT))


def _write_n2(events: list[dict]) -> None:
    """N2 (SPEC §6.2 N2): one entry per Robert question event; each passes the schema and the gate."""
    schema = Draft202012Validator(extractors.load_schema("question_rubric"))
    by_id = {e["event_id"]: e for e in events}
    for ev in extractors.n2_events(events):
        output = N2_OUTPUTS[ev["event_id"]]
        schema.validate(output)
        payload = extractors.n2_payload(ev)
        context = {"event_ids": set(by_id), "events_by_id": by_id, "event_id": ev["event_id"],
                   "model_id": bedrock.MOCK_MODEL_ID, "prompt_version": extractors.prompt_version("question_rubric"),
                   "input_hash": bedrock.input_hash(payload)}
        assert gate.gate("question_rubric", None, output, context)[1] == "accepted"
        p = bedrock.write_entry(config.LLM_CACHE_DIR, "question_rubric", extractors.prompt_version("question_rubric"),
                                bedrock.MOCK_MODEL_ID, payload, output, CALL_LOG)
        print(p.relative_to(ROOT))


def main() -> None:
    events = [json.loads(line) for line in (ROBERT / "events.jsonl").read_text().splitlines() if line.strip()]
    _write_n2(events)
    profile = json.loads((ROBERT / "profile.json").read_text(encoding="utf-8"))
    n3_cases = [(profile["values"]["notes"], N3_ROBERT_OUTPUT), (N3_AMIRA_NOTES, N3_AMIRA_OUTPUT),
                (N3_MATEO_NOTES, N3_MATEO_OUTPUT)]
    for notes, out in n3_cases:
        Draft202012Validator(extractors.load_schema("values_parser")).validate(out)
        assert all(r["quote"] in notes for r in out["rules"])
        p = bedrock.write_entry(config.LLM_CACHE_DIR, "values_parser", extractors.prompt_version("values_parser"),
                                bedrock.MOCK_MODEL_ID, extractors.n3_payload(notes), out, CALL_LOG)
        print(p.relative_to(ROOT))

    for fixture, n6_output in ((ROBERT, N6_ROBERT_OUTPUT), (AMIRA, N6_AMIRA_OUTPUT), (MATEO, N6_MATEO_OUTPUT)):
        _write_n6(fixture, n6_output)

    events = [json.loads(line) for line in (ROBERT / "events.jsonl").read_text().splitlines() if line.strip()]
    weights = json.loads((ROBERT / "portfolio.json").read_text())["weights_pct"]
    assets = data.load_assets()
    universe = [{"ticker": t, "name": n} for t, n in assets["name"].items()]

    n1_payload = {"events": extractors.n1_events(events)}
    Draft202012Validator(extractors.load_schema("behaviour_classifier")).validate(N1_OUTPUT)
    assert {e["event_id"] for e in N1_OUTPUT["evidence"]} <= {e["event_id"] for e in n1_payload["events"]}
    p1 = bedrock.write_entry(config.LLM_CACHE_DIR, "behaviour_classifier",
                             extractors.prompt_version("behaviour_classifier"),
                             bedrock.MOCK_MODEL_ID, n1_payload, N1_OUTPUT, CALL_LOG)

    text = next(e["text"] for e in events if e["event_id"] == "ev_robert_0007")
    n4_payload = extractors.n4_payload(text, universe, weights)
    Draft202012Validator(extractors.n4_schema(universe)).validate(N4_OUTPUT)
    p2 = bedrock.write_entry(config.LLM_CACHE_DIR, "intent_parser", extractors.prompt_version("intent_parser"),
                             bedrock.MOCK_MODEL_ID, n4_payload, N4_OUTPUT, CALL_LOG)
    for p in (p1, p2):
        print(p.relative_to(ROOT))


if __name__ == "__main__":
    main()
