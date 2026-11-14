"""Write the hand-written N5 `explainer` responses for the Flow B trade check (Robert in_0001, P2.FB) into
fixtures/llm_cache (SPEC §16: until Bedrock access works, hand-write plausible responses that pass the schemas).

    python scripts/handwrite_flow_b_cache.py          # write the entries and check the narration modes
    python scripts/handwrite_flow_b_cache.py --dump   # print the three non-pass section facts, write nothing

Flow B narrates the PROPOSED checks, so its section facts (intent, proposed metrics, S1/S3 evidence) differ from the
Flow C ones of `handwrite_explainer_cache.py`: they need their own cache keys. One good answer per non-pass section
(horizon, risque, contraintes), each passing the verifier V1-V5, so the offline Flow B demo narrates in mode `llm`.
Every number in the text is copied from the facts; the entries are keyed by the real cache_key() of the payload the
narration loop sends, model_id `fixture:hand-written`. They are replaced by real recordings with
COHERENCE_ANALYZER_LLM=record (P1.11).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

from jsonschema import Draft202012Validator  # noqa: E402

from coherence_analyzer import config, explain, factors, pipeline  # noqa: E402
from coherence_analyzer.coherence import report as report_mod  # noqa: E402
from coherence_analyzer.llm import bedrock, extractors  # noqa: E402

NODE = "explainer"
CALL_LOG = {"hand_written": True, "tokens_in": 0, "tokens_out": 0, "latency_ms": 0}
INTENT_ID = "in_0001"


def flow_b_sections() -> list[dict]:
    """The five sections of Flow B on Robert in_0001 (B1-B6, then the section builder of B8), run in a scratch store."""
    state = pipeline.b2_confirm(pipeline.b1_parse_intent(ROOT / "fixtures" / "robert", INTENT_ID), True)
    cwd = os.getcwd()
    os.chdir(tempfile.mkdtemp())          # B3 may save a profile version: keep it out of the repo
    try:
        for step in (pipeline.b3_refresh, pipeline.b4_apply, pipeline.b5_analyse, pipeline.b6_checks_and_diff):
            state = step(state)
    finally:
        os.chdir(cwd)
    p = state["profile"]
    ctx = factors.SectionContext(
        profile=p, limits=p["limits"], limits_trace=p["limits_trace"], checks_current=state["checks_current"],
        checks_proposed=state["checks_proposed"], metrics_current=state["metrics_current"],
        metrics_proposed=state["metrics_proposed"], intent=state["intent"], events=state["events"])
    return report_mod.build_sections(ctx)


def _check(facts: dict, cid: str, status: str = "violation") -> dict:
    return next(c for c in facts["checks"] if c["id"] == cid and c["status"] == status)


def horizon_good(facts: dict) -> dict:
    h9 = _check(facts, "H9")
    eff = facts["derived"]["objectives.effective_horizon_years"]["value"]
    withdrawal = facts["declared"]["situation.planned_withdrawals"][0]
    prop = facts["metrics"]["proposed"]["p5_T"]
    return {"segments": [
        {"kind": "fact", "refs": ["H9", "metrics.proposed.p5_T"],
         "text": f"H9: avec cet achat, le scénario bas (centile 5) à l'horizon est de {prop}, sous le plancher de "
                 f"{h9['limit']}."},
        {"kind": "fact", "refs": ["derived.objectives.effective_horizon_years", "declared.situation.planned_withdrawals"],
         "text": f"Ce plancher découle d'un horizon effectif de {eff} ans: le premier retrait prévu, "
                 f"{withdrawal['amount']}, est en {withdrawal['date']}."},
    ]}


def risque_good(facts: dict) -> dict:
    h4, h7, h8 = (_check(facts, c) for c in ("H4", "H7", "H8"))
    s1, s3 = _check(facts, "S1", "warning"), _check(facts, "S3", "warning")
    beh = facts["derived"]["risk.behavioural"]
    return {"segments": [
        {"kind": "fact", "refs": ["H4", "H7", "metrics.proposed.vol"],
         "text": f"H4: le revenu fixe pèse {h4['value']}, sous le minimum de {h4['limit']}. "
                 f"H7: la volatilité historique passe à {h7['value']}, au-dessus de la bande de {h7['limit']}."},
        {"kind": "fact", "refs": ["H8", "metrics.proposed.mdd", "declared.risk.max_acceptable_drawdown_pct"],
         "text": f"H8: le repli historique maximal est de {h8['value']}, plus profond que la limite de {h8['limit']}."},
        {"kind": "fact", "refs": ["derived.risk.effective", "derived.risk.capacity"],
         "text": "Ces limites découlent du risque effectif (low_moderate), qui retient le plus prudent du risque "
                 "déclaré et de la capacité de risque; celle-ci dépend de l'horizon effectif de 3,6 ans."},
        {"kind": "inference", "refs": ["S1", "S3", "derived.risk.behavioural", "ev_robert_0007"],
         "text": f"Inférence: vos questions et cette demande suggèrent un comportement plus risqué ({beh['value']}, "
                 f"confiance {beh['confidence']}) et un achat motivé par l'engouement du moment (S1, S3); "
                 f"ce n'est pas un fait vérifié."},
    ]}


def contraintes_good(facts: dict) -> dict:
    segs = []
    for cid in dict.fromkeys(c["id"] for c in facts["checks"] if c["status"] == "violation"):
        c = _check(facts, cid)
        what = f" ({c['subject']})" if c["subject"] else ""
        segs.append({"kind": "fact", "refs": [cid],
                     "text": f"{cid}{what}: valeur {c['value']}, limite {c['limit']}."})
    unk = list(dict.fromkeys(c["id"] for c in facts["checks"] if c["status"] == "unknown"))
    if unk:
        segs.append({"kind": "unknown", "refs": unk,
                     "text": f"{', '.join(unk)}: ne peut pas être vérifié (part de FNB non ventilée par secteur)."})
    return {"segments": segs}


GOOD = {"horizon": horizon_good, "risque": risque_good, "contraintes": contraintes_good}


def main() -> None:
    sections = flow_b_sections()
    if "--dump" in sys.argv:
        for s in sections:
            if s["status"] != "pass":
                print(s["factor"], json.dumps(s["facts"], ensure_ascii=False, indent=1))
        return
    validator = Draft202012Validator(extractors.load_schema(NODE))
    version = extractors.prompt_version(NODE)
    for s in sections:
        if s["factor"] not in GOOD:
            continue
        out = GOOD[s["factor"]](s["facts"])
        validator.validate(out)
        failures = explain.verify_section(out, s["facts"])
        assert not failures, (s["factor"], failures)
        p = bedrock.write_entry(config.LLM_CACHE_DIR, NODE, version, bedrock.MOCK_MODEL_ID, s["facts"], out, CALL_LOG)
        print(p.relative_to(ROOT))
    modes = {s["factor"]: explain.narrate_section(s["facts"])["mode"] for s in sections}
    print(modes)
    assert modes == {"objectif": "template", "horizon": "llm", "risque": "llm", "contraintes": "llm",
                     "preferences": "template"}, modes


if __name__ == "__main__":
    main()
