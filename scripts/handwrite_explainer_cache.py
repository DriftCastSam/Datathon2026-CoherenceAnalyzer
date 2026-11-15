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

P4.CACHE: also one good first-attempt answer (mode `llm`) for every non-pass section of Amira (horizon, contraintes,
preferences) and Mateo (contraintes, preferences) in `cli demo`, built from the real section facts at run time
(`pipeline.review` in a temp dir, mock mode). `--dump` prints those facts instead of writing.
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"       # offline: the persona reviews below read the cache only
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


def robert_sections(weights_pct: dict, locale: str = "fr-CA") -> list[dict]:
    """The five sections of Robert on `weights_pct` (flow C review), built like tests/llm/test_explain.py and the pipeline."""
    raw = json.loads((ROOT / "fixtures" / "robert" / "profile.json").read_text(encoding="utf-8"))
    profile = derive.derive_all(copy.deepcopy(raw), portfolio_value_cad=VALUE, as_of=AS_OF)
    lim, trace = limits_mod.build_limits(profile, VALUE)
    profile["limits"], profile["limits_trace"] = lim, trace
    market = engine.load_market(str(ROOT / "data" / "starter"))
    metrics = engine.analyse(market, weights_pct, HORIZON_MONTHS, VALUE)
    checks = hard.run_all({t: v / 100 for t, v in weights_pct.items()}, metrics, profile, data.load_assets(),
                          {n: data.load_meta(n) for n in META_NAMES})
    ctx = factors.SectionContext(profile=profile, limits=lim, limits_trace=trace, checks_current=checks,
                                 metrics_current=metrics, locale=locale)
    return factors.build_sections(ctx)


def robert_nvda_sections(locale: str = "fr-CA") -> list[dict]:
    """Robert + NVDA 10% from ZST.TO (`engine.ROBERT_NVDA`)."""
    return robert_sections(engine.ROBERT_NVDA, locale)


def contraintes_unknown_good(facts: dict) -> dict:
    """Robert's current portfolio (`engine.ROBERT`): contraintes is `unknown` (H6, H10 have no ETF look-through)."""
    h6 = next(c for c in facts["checks"] if c["id"] == "H6" and c["status"] == "unknown")
    h10 = next(c for c in facts["checks"] if c["id"] == "H10" and c["status"] == "unknown")
    return {"segments": [
        {"kind": "unknown", "refs": ["H6"],
         "text": f"H6: la part des FNB ({h6['value']}) n'est pas ventilée par secteur; le plafond de {h6['limit']} "
                 f"pour le secteur de votre employeur ({h6['subject']}) ne peut donc pas être vérifié."},
        {"kind": "unknown", "refs": ["H10"],
         "text": f"H10: la part « {h10['subject']} » ({h10['value']} du risque) n'est pas ventilée par secteur; "
                 f"le plafond de {h10['limit']} ne peut pas être vérifié pour cette part."},
    ]}


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


def drop_stale_hand_written(version: str) -> None:
    """Remove hand-written explainer entries of an older prompt version (CLAUDE.md: a prompt bump re-records the mock
    cache). Real recordings (any other model_id) are left alone: under a new prompt version they are plain misses."""
    for p in sorted(config.LLM_CACHE_DIR.glob(f"{NODE}.*.json")):
        entry = json.loads(p.read_text(encoding="utf-8"))
        if entry.get("model_id") == bedrock.MOCK_MODEL_ID and entry.get("prompt_version") != version:
            p.unlink()
            print("removed", p.relative_to(ROOT))


def main() -> None:
    facts_by = {s["factor"]: s["facts"] for s in robert_nvda_sections()}
    validator = Draft202012Validator(extractors.load_schema(NODE))
    version = extractors.prompt_version(NODE)
    drop_stale_hand_written(version)
    hor, ris, con = facts_by["horizon"], facts_by["risque"], facts_by["contraintes"]
    base = contraintes_base(con)
    cur = {s["factor"]: s["facts"] for s in robert_sections(engine.ROBERT)}["contraintes"]    # current portfolio
    plan = [(hor, horizon_good(hor), None),
            (ris, _with(risque_good(ris), suffix=INVENTED), risque_good(ris)),
            (con, _with(base, suffix=INVENTED), _with(base, prefix="Je recommande de réduire cette position. ")),
            (cur, contraintes_unknown_good(cur), None)]
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
    cur_mode = explain.narrate_section(cur)["mode"]
    print("robert current, contraintes:", cur_mode)
    assert cur_mode == "llm", cur_mode
    write_persona_entries()


def _seg(kind: str, refs: list[str], text: str) -> dict:
    return {"kind": kind, "refs": refs, "text": text}


def _by_status(facts: dict, cid: str, status: str) -> list[dict]:
    return [c for c in facts["checks"] if c["id"] == cid and c["status"] == status]


def amira_horizon(facts: dict) -> dict:
    """Amira: H9 violation, no planned withdrawal, effective horizon = declared horizon."""
    h9 = _check(facts, "H9")
    cur = facts["metrics"]["current"]
    eff = facts["derived"]["objectives.effective_horizon_years"]["value"]
    return {"segments": [
        _seg("fact", ["H9", "metrics.current.p5_T"],
             f"H9: le scénario bas (centile 5) à l'horizon est de {h9['value']}, sous le plancher de {h9['limit']}; "
             f"le scénario médian est de {cur['p50_T']}."),
        _seg("fact", ["derived.objectives.effective_horizon_years", "declared.objectives.horizon_years",
                      "declared.situation.planned_withdrawals"],
             f"Le plancher se mesure à un horizon effectif de {eff} ans, égal à l'horizon que vous déclarez; "
             f"aucun retrait n'est planifié dans votre profil."),
        _seg("assumption", ["metrics.current.p5_T", "metrics.current.p95_T"],
             f"Hypothèse: ces scénarios sont une simulation fondée sur l'historique, pas une prévision; "
             f"l'écart entre le scénario bas et le haut ({cur['p95_T']}) est large ({cur['width']})."),
    ]}


def amira_contraintes(facts: dict) -> dict:
    """Amira: H2, H5, H6 violations, S2 warning, H6 / H10 unknown (ETF part without sector look-through)."""
    h2, h5, h6 = _check(facts, "H2"), _check(facts, "H5"), _check(facts, "H6")
    s2 = _check(facts, "S2", "warning")
    h6u, h10u = _by_status(facts, "H6", "unknown")[0], _by_status(facts, "H10", "unknown")[0]
    return {"segments": [
        _seg("fact", ["H2", "H5"],
             f"H2: les cryptoactifs pèsent {h2['value']}, au-delà du maximum de {h2['limit']}. "
             f"H5: les actifs en devises étrangères pèsent {h5['value']}, au-delà du maximum de {h5['limit']}."),
        _seg("fact", ["H6", "declared.identity.employer_sector"],
             f"H6: le secteur de votre employeur ({h6['subject']}) pèse {h6['value']}, au-delà du maximum "
             f"de {h6['limit']}."),
        _seg("fact", ["S2", "derived.knowledge.score"],
             f"S2 (avertissement): votre score de connaissances est de {s2['value']}, sous le seuil de {s2['limit']} "
             f"pour la complexité de certains placements détenus ({s2['subject']})."),
        _seg("unknown", ["H6", "H10"],
             f"H6: la part des FNB ({h6u['value']}) n'est pas ventilée par secteur; le plafond de {h6u['limit']} ne "
             f"peut pas être vérifié pour elle. H10: la part « {h10u['subject']} » ({h10u['value']} du risque) n'est "
             f"pas ventilée non plus; le plafond de {h10u['limit']} ne peut pas être vérifié."),
    ]}


def amira_preferences(facts: dict) -> dict:
    """Amira: S4 info, the "I love AI and new tech" note was parsed (llm:values_parser) but not approved."""
    s4 = _by_status(facts, "S4", "info")[0]
    interests = ", ".join(facts["declared"]["preferences.interests"])
    notes = facts["declared"]["values.notes"]
    assert s4["subject"] in notes
    return {"segments": [
        _seg("fact", ["declared.preferences.interests", "declared.values.notes"],
             f"Vos intérêts déclarés sont {interests}; vos notes disent: « {notes} »"),
        _seg("inference", ["S4", "derived.values.parsed_notes"],
             f"Inférence: la phrase « {s4['subject']} » a été lue comme une préférence (prefer_attribute) que vous "
             f"n'avez pas approuvée; S4 la signale sans la vérifier automatiquement."),
    ]}


def mateo_contraintes(facts: dict) -> dict:
    """Mateo: every check is `unknown` (H6, H10: ETF part; H11: ETF without look-through or no activity-tag rows)."""
    h6, h10 = _by_status(facts, "H6", "unknown")[0], _by_status(facts, "H10", "unknown")[0]
    h11 = _by_status(facts, "H11", "unknown")
    etf = sorted(c["subject"] for c in h11 if c["message_key"] == "excluded_activity_etf_no_lookthrough")
    bare = sorted(c["subject"] for c in h11 if c["message_key"] == "excluded_activity_no_tag_rows")
    assert etf and bare and len(etf) + len(bare) == len(h11)
    d = facts["declared"]
    refused = ", ".join(d["preferences.asset_types_refused"])
    return {"segments": [
        _seg("fact", ["declared.preferences.asset_types_refused", "declared.preferences.max_fee_mer_pct",
                      "declared.values.environment.exclusions", "declared.values.social.exclusions"],
             f"Vos limites déclarées: types d'actifs refusés ({refused}), frais maximaux de "
             f"{d['preferences.max_fee_mer_pct']}, exclusion des controverses environnementales et sociales "
             f"importantes."),
        _seg("unknown", ["H6"],
             f"H6: la part des FNB ({h6['value']}) n'est pas ventilée par secteur; le plafond de {h6['limit']} pour "
             f"le secteur de votre employeur ({h6['subject']}) ne peut pas être vérifié."),
        _seg("unknown", ["H10"],
             f"H10: la part « {h10['subject']} » ({h10['value']} du risque) n'est pas ventilée par secteur; le "
             f"plafond de {h10['limit']} ne peut pas être vérifié pour cette part."),
        _seg("unknown", ["H11"],
             f"H11: les activités exclues ne peuvent pas être vérifiées pour les FNB sans ventilation "
             f"({', '.join(etf)}) ni pour les titres sans ligne d'activité dans les données ({', '.join(bare)})."),
    ]}


def mateo_preferences(facts: dict) -> dict:
    """Mateo: two S4 info (parsed notes not approved)."""
    s4 = _by_status(facts, "S4", "info")
    d = facts["declared"]
    interests = ", ".join(d["preferences.interests"])
    assert d["preferences.prefers_dividends"] is True and d["preferences.prefers_large_caps"] is True
    quotes = " et ".join(f"« {c['subject']} »" for c in s4)
    return {"segments": [
        _seg("fact", ["declared.preferences.prefers_dividends", "declared.preferences.prefers_large_caps",
                      "declared.preferences.interests"],
             f"Vous indiquez préférer les dividendes et les grandes capitalisations; vos intérêts déclarés sont "
             f"{interests}. Importance déclarée: environnement {d['values.environment.importance']}, "
             f"social {d['values.social.importance']}."),
        _seg("inference", ["S4", "derived.values.parsed_notes"],
             f"Inférence: deux phrases de vos notes, {quotes}, ont été lues comme des règles que vous n'avez pas "
             f"approuvées; S4 les signale sans les vérifier automatiquement."),
    ]}


PERSONA_WRITERS = {
    ("amira", "horizon"): amira_horizon, ("amira", "contraintes"): amira_contraintes,
    ("amira", "preferences"): amira_preferences,
    ("mateo", "contraintes"): mateo_contraintes, ("mateo", "preferences"): mateo_preferences,
}


def write_persona_entries() -> None:
    """One hand-written answer per non-pass section of Amira and Mateo, keyed on the exact section facts, accepted by
    the verifier on the first attempt (mode `llm`). `pass` sections never reach the writer: no entry."""
    validator = Draft202012Validator(extractors.load_schema(NODE))
    version = extractors.prompt_version(NODE)
    for persona in ("amira", "mateo"):
        for sec in persona_sections(persona):
            facts = sec["facts"]
            if sec["status"] == "pass":
                continue
            out = PERSONA_WRITERS[(persona, sec["factor"])](facts)
            validator.validate(out)
            failures = explain.verify_section(out, facts)
            assert not failures, (persona, sec["factor"], failures)
            assert out["segments"] != explain.template_section(facts)["segments"]
            p = bedrock.write_entry(config.LLM_CACHE_DIR, NODE, version, bedrock.MOCK_MODEL_ID, facts, out, CALL_LOG)
            print(p.relative_to(ROOT))
    for persona in ("amira", "mateo"):
        modes = {s["factor"]: explain.narrate_section(s["facts"])["mode"] for s in persona_sections(persona)
                 if s["status"] != "pass"}
        print(persona, modes)
        assert set(modes.values()) == {"llm"}, modes


def persona_sections(persona: str) -> list[dict]:
    """The sections of `cli demo` for `persona` (flow C review of its own portfolio), produced by `pipeline.review` in a
    throw-away working directory so the store never touches the repo. Mock mode: no network, explainer misses fall back
    to the template, which does not change `facts`."""
    import os
    import tempfile

    from coherence_analyzer import pipeline

    here = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            return pipeline.review(ROOT / "fixtures" / persona, None)["sections"]
        finally:
            os.chdir(here)


if __name__ == "__main__":
    if "--dump" in sys.argv:
        for _p in ("amira", "mateo"):
            for _s in persona_sections(_p):
                if _s["status"] != "pass":
                    print("=====", _p, _s["factor"], _s["status"])
                    print(json.dumps(_s["facts"], ensure_ascii=False, indent=1))
    else:
        main()
