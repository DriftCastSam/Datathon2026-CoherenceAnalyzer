"""Write the hand-written N5 `explainer` responses for Robert + NVDA 10% into fixtures/llm_cache (SPEC §16: until Bedrock
access works, hand-write plausible responses that pass the schemas; docs/spec-factors.md §20.5).

    python scripts/handwrite_explainer_cache.py

The three non-pass sections each show one path of the narration loop, so the offline demo exercises all of them:
  horizon      one good answer                                                  -> mode `llm`
  risque       a bad answer (invented number, V2), then a good one on the retry -> mode `llm_retry`
  contraintes  a bad answer (V2), then another bad answer (V4 advisory wording) -> mode `template`
Entries are keyed by the real cache_key() of the exact payload the loop sends (facts, or facts + retry_failures; the
retry_failures are what `explain.verify_section` returns for the first answer). model_id `fixture:hand-written`.
They are replaced by real recordings with COHERENCE_ANALYZER_LLM=record (P1.11).
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jsonschema import Draft202012Validator  # noqa: E402

from coherence_analyzer import config, data, engine, explain, factors  # noqa: E402
from coherence_analyzer.coherence import hard  # noqa: E402
from coherence_analyzer.llm import bedrock, extractors  # noqa: E402
from coherence_analyzer.rules import derive, limits as limits_mod  # noqa: E402

NODE = "explainer"
AS_OF = "2026-11-12"
VALUE = 400_000
HORIZON_MONTHS = 48
META_NAMES = ("asset_meta", "etf_sector_weights", "activity_tags")
CALL_LOG = {"hand_written": True, "tokens_in": 0, "tokens_out": 0, "latency_ms": 0}
INVENTED = " Le repli pourrait atteindre 77,7 %."      # a number that is in no facts payload: V2


def robert_nvda_sections(locale: str = "fr-CA") -> list[dict]:
    """The five sections of Robert + NVDA 10% (flow C review), built like tests/test_explain.py."""
    raw = json.loads((ROOT / "fixtures" / "robert" / "profile.json").read_text(encoding="utf-8"))
    profile = derive.derive_all(copy.deepcopy(raw), portfolio_value_cad=VALUE, as_of=AS_OF)
    lim, trace = limits_mod.build_limits(profile, VALUE)
    profile["limits"], profile["limits_trace"] = lim, trace
    market = engine.load_market(str(ROOT / "data" / "starter"))
    metrics = engine.analyse(market, engine.ROBERT_NVDA, HORIZON_MONTHS, VALUE)
    checks = hard.run_all({t: v / 100 for t, v in engine.ROBERT_NVDA.items()}, metrics, profile, data.load_assets(),
                          {n: data.load_meta(n) for n in META_NAMES})
    ctx = factors.SectionContext(profile=profile, limits=lim, limits_trace=trace, checks_current=checks,
                                 metrics_current=metrics, locale=locale)
    return factors.build_sections(ctx)


def _check(facts: dict, cid: str, status: str = "violation") -> dict:
    return next(c for c in facts["checks"] if c["id"] == cid and c["status"] == status)


def horizon_good(facts: dict) -> dict:
    h9 = _check(facts, "H9")
    eff = facts["derived"]["objectives.effective_horizon_years"]["value"]
    withdrawal = facts["declared"]["situation.planned_withdrawals"][0]
    return {"segments": [
        {"kind": "fact", "refs": ["H9", "metrics.current.p5_T"],
         "text": f"H9: le scénario bas (centile 5) à l'horizon est de {h9['value']}, sous le plancher de {h9['limit']}."},
        {"kind": "fact", "refs": ["derived.objectives.effective_horizon_years", "declared.situation.planned_withdrawals"],
         "text": f"Ce plancher découle d'un horizon effectif de {eff} ans: le premier retrait prévu, "
                 f"{withdrawal['amount']}, est en {withdrawal['date']}."},
    ]}


def risque_good(facts: dict) -> dict:
    h4, h7, h8 = (_check(facts, c) for c in ("H4", "H7", "H8"))
    beh = facts["derived"]["risk.behavioural"]
    return {"segments": [
        {"kind": "fact", "refs": ["H4", "H7", "metrics.current.vol"],
         "text": f"H4: le revenu fixe pèse {h4['value']}, sous le minimum de {h4['limit']}. "
                 f"H7: la volatilité historique est de {h7['value']}, au-dessus de la bande de {h7['limit']}."},
        {"kind": "fact", "refs": ["H8", "metrics.current.mdd", "declared.risk.max_acceptable_drawdown_pct"],
         "text": f"H8: le repli historique maximal est de {h8['value']}, plus profond que la limite de {h8['limit']}."},
        {"kind": "fact", "refs": ["derived.risk.effective", "derived.risk.capacity"],
         "text": "Ces limites découlent du risque effectif (low_moderate), qui retient le plus prudent du risque "
                 "déclaré et de la capacité de risque; celle-ci dépend de l'horizon effectif de 3,6 ans."},
        {"kind": "inference", "refs": ["derived.risk.behavioural"],
         "text": f"Inférence: vos questions récentes suggèrent un comportement plus risqué ({beh['value']}, "
                 f"confiance {beh['confidence']}); ce n'est pas un fait vérifié."},
    ]}


def contraintes_base(facts: dict) -> dict:
    """Base of the two bad answers (this section ends in the template, so no good answer is stored)."""
    segs = []
    for cid in dict.fromkeys(c["id"] for c in facts["checks"] if c["status"] == "violation"):
        c = _check(facts, cid)
        segs.append({"kind": "fact", "refs": [cid],
                     "text": f"{cid} ({c['subject']}): valeur {c['value']}, limite {c['limit']}."})
    unk = list(dict.fromkeys(c["id"] for c in facts["checks"] if c["status"] == "unknown"))
    segs.append({"kind": "unknown", "refs": unk,
                 "text": f"{', '.join(unk)}: ne peut pas être vérifié (part de FNB non ventilée par secteur)."})
    return {"segments": segs}


def _with(out: dict, prefix: str = "", suffix: str = "") -> dict:
    out = copy.deepcopy(out)
    out["segments"][0]["text"] = f"{prefix}{out['segments'][0]['text']}{suffix}"
    return out


def main() -> None:
    facts_by = {s["factor"]: s["facts"] for s in robert_nvda_sections()}
    validator = Draft202012Validator(extractors.load_schema(NODE))
    version = extractors.prompt_version(NODE)
    hor, ris, con = facts_by["horizon"], facts_by["risque"], facts_by["contraintes"]
    base = contraintes_base(con)
    plan = [(hor, horizon_good(hor), None),
            (ris, _with(risque_good(ris), suffix=INVENTED), risque_good(ris)),
            (con, _with(base, suffix=INVENTED), _with(base, prefix="Je recommande de réduire cette position. "))]
    for facts, first, second in plan:
        validator.validate(first)
        failures = explain.verify_section(first, facts)
        entries = [(facts, first)]
        if second is None:
            assert not failures, failures
        else:
            validator.validate(second)
            assert failures, "the first answer must fail verification"
            entries.append(({**facts, "retry_failures": failures}, second))
        for payload, out in entries:
            p = bedrock.write_entry(config.LLM_CACHE_DIR, NODE, version, bedrock.MOCK_MODEL_ID, payload, out, CALL_LOG)
            print(p.relative_to(ROOT))
    modes = {s["factor"]: explain.narrate_section(s["facts"])["mode"] for s in robert_nvda_sections()}
    print(modes)
    assert modes == {"objectif": "template", "horizon": "llm", "risque": "llm_retry", "contraintes": "template",
                     "preferences": "template"}, modes


if __name__ == "__main__":
    main()
