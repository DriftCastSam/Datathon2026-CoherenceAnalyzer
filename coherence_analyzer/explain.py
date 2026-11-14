"""Explanation: facts payload -> N5 explainer -> post-checks -> template fallback (SPEC §9, docs/spec-factors.md §20.5).

P0.7 here: `numcheck` (V2) and `template_section` (the deterministic fallback that always passes V1-V5). The narration
loop (`verify_section`, `explain`) is P0.8. No LLM is called in this module yet.
"""
from __future__ import annotations

from .config import DISCLAIMER
from .numfacts import numcheck as _numcheck

ADVISORY_BLOCKLIST = ("tu devrais acheter", "tu devrais vendre", "je recommande", "nous recommandons",
                      "you should buy", "you should sell", "we recommend", "i recommend")

MAX_SEGMENTS = 4          # V5
MAX_SEGMENT_CHARS = 300   # V5


def build_payload(report: dict, profile: dict, events: list[dict], locale: str = "fr-CA") -> dict:
    raise NotImplementedError


def numcheck(text: str, payload: dict) -> list[str]:
    """Return numbers in `text` that don't match a payload number (empty list = pass). SPEC §9.3 item 2."""
    return _numcheck(text, payload)


def template_explain(payload: dict) -> dict:
    """Deterministic FR/EN explanation from the same checks. Always available."""
    raise NotImplementedError


# ---------------------------------------------------------------- template_section (§20.5)

_FR = {
    "H1": "{subject} fait partie des types d'actifs que vous excluez ({value} du portefeuille).",
    "H2": "Les cryptoactifs pèsent {value}, au-delà du maximum de {limit}.",
    "H3": "{subject} pèse {value}, au-delà du maximum de {limit} par action.",
    "H4": "Le revenu fixe pèse {value}, sous le minimum de {limit}.",
    "H5": "Les actifs en devises étrangères pèsent {value}, au-delà du maximum de {limit}.",
    "H6": "Le secteur de votre employeur ({subject}) pèse {value}, au-delà du maximum de {limit}.",
    "H7": "La volatilité historique est de {value}, au-dessus de la bande de {limit}.",
    "H7/info": "La volatilité historique est de {value}, sous la bande de {limit}: plus prudent que votre profil.",
    "H8": "Le repli historique maximal est de {value}, plus profond que la limite de {limit}.",
    "H9": "Le scénario bas (centile 5) à l'horizon est de {value}, sous le plancher de {limit}.",
    "H10": "Le secteur {subject} compte pour {value} du risque, au-delà du maximum de {limit}.",
    "H11": "{subject} est lié à une activité que vous excluez.",
    "H12": "Les frais de {subject} sont de {value}, au-delà du maximum de {limit}.",
    "H13": "Le thème {subject} pèse {value}, au-delà du maximum de {limit}.",
    "H14": "Le repli historique est de {value}, plus profond que {limit}, alors que vous dites vendre en cas de forte baisse.",
    "S1": "Votre comportement observé dépasse votre niveau de risque effectif de {value} niveau(x).",
    "S2": "Votre score de connaissances est faible par rapport à la complexité de certains placements détenus.",
    "S3": "La demande ressemble à un achat motivé par l'engouement du moment.",
    "S4": "Une règle écrite dans vos notes ne peut pas être vérifiée automatiquement.",
    "G1": "Les probabilités d'atteinte des objectifs sont indicatives et reposent sur des hypothèses.",
    "O1": "Votre objectif est la préservation du capital; le repli historique est de {value}.",
    "H6/unknown": "H6: la part des FNB ({value}) n'est pas ventilée par secteur; le plafond de {limit} pour l'employeur "
                  "({subject}) ne peut pas être vérifié.",
    "H10/unknown": "H10: la part « {subject} » ({value} du risque) n'est pas ventilée par secteur; le plafond de "
                   "{limit} ne peut pas être vérifié pour cette part.",
    "?/unknown": "Le contrôle {id} ne peut pas être vérifié{on}.",
    "?": "Le contrôle {id} est au statut {status}{on}.",
}
_EN = {
    "H1": "{subject} is an asset type you exclude ({value} of the portfolio).",
    "H2": "Crypto assets weigh {value}, above the maximum of {limit}.",
    "H3": "{subject} weighs {value}, above the maximum of {limit} per stock.",
    "H4": "Fixed income weighs {value}, below the minimum of {limit}.",
    "H5": "Foreign-currency assets weigh {value}, above the maximum of {limit}.",
    "H6": "Your employer's sector ({subject}) weighs {value}, above the maximum of {limit}.",
    "H7": "Historical volatility is {value}, above the band limit of {limit}.",
    "H7/info": "Historical volatility is {value}, below the band limit of {limit}: more conservative than your profile.",
    "H8": "The maximum historical drawdown is {value}, deeper than the limit of {limit}.",
    "H9": "The low scenario (5th percentile) at the horizon is {value}, below the floor of {limit}.",
    "H10": "The {subject} sector accounts for {value} of the risk, above the maximum of {limit}.",
    "H11": "{subject} is linked to an activity you exclude.",
    "H12": "The fees of {subject} are {value}, above the maximum of {limit}.",
    "H13": "The {subject} theme weighs {value}, above the maximum of {limit}.",
    "H14": "The historical drawdown is {value}, deeper than {limit}, while you say you would sell after a large drop.",
    "S1": "Your observed behaviour exceeds your effective risk level by {value} level(s).",
    "S2": "Your knowledge score is low for the complexity of some holdings.",
    "S3": "The request looks like a purchase driven by current hype.",
    "S4": "A rule written in your notes cannot be checked automatically.",
    "G1": "Goal probabilities are indicative and rest on stated assumptions.",
    "O1": "Your goal is capital preservation; the historical drawdown is {value}.",
    "H6/unknown": "H6: the ETF share ({value}) has no sector breakdown; the {limit} cap for your employer's sector "
                  "({subject}) cannot be verified.",
    "H10/unknown": "H10: the \"{subject}\" share ({value} of the risk) has no sector breakdown; the {limit} cap "
                   "cannot be verified for it.",
    "?/unknown": "Check {id} cannot be verified{on}.",
    "?": "Check {id} has status {status}{on}.",
}
_NONE = {"fr-CA": "Aucun écart relevé pour ce facteur.", "en-CA": "No gap found for this factor."}
_WHY = {"fr-CA": "Cette limite découle de votre profil: {path} = {value}.",
        "en-CA": "This limit comes from your profile: {path} = {value}."}
_DASH = "\u2014"
_INFERENCE_CHECKS = ("S1", "S3")     # soft checks that rest on an llm: field or an event


def _line(e: dict, loc: str) -> str:
    table = _FR if loc == "fr-CA" else _EN
    cid, status = e["id"], e["status"]
    subject = e.get("subject") or ""
    fields = {"id": cid, "status": status, "subject": subject, "value": e.get("value", _DASH),
              "limit": e.get("limit", _DASH), "on": f" ({subject})" if subject else ""}
    if status == "unknown":
        candidates, fallback = (f"{cid}/unknown",), "?/unknown"
    else:
        candidates, fallback = (f"{cid}/{status}", cid), "?"
    key = next((k for k in candidates if k in table), fallback)
    return table[key].format(**fields)


def _why_segment(facts: dict, vw: list[dict], loc: str) -> tuple[str, list[str]] | None:
    """Name the profile field the first violated limit is derived from, only when facts show that field."""
    for e in vw:
        df = e.get("derived_from", [])
        for path in df[1:2]:                      # the direct input of the limit (`limits.x` is df[0])
            for bucket in ("derived", "declared"):
                node = facts[bucket].get(path)
                value = node["value"] if bucket == "derived" and node is not None else node
                if isinstance(value, str):
                    return _WHY[loc].format(path=path, value=value), [f"{bucket}.{path}"]
    return None


def _merge(lines: list[tuple[str, list[str]]]) -> tuple[str, list[str]]:
    """Join lines into one segment of at most MAX_SEGMENT_CHARS; refs of every line are kept (V3 coverage)."""
    text = ""
    refs: list[str] = []
    for t, r in lines:
        refs += [x for x in r if x not in refs]
        cand = f"{text} {t}".strip()
        if len(cand) <= MAX_SEGMENT_CHARS:
            text = cand
        elif not text:
            text = t[:MAX_SEGMENT_CHARS - 1] + "\u2026"
    return text, refs


def template_section(facts: dict) -> dict:
    """Deterministic section text from its facts (§20.5): same facts -> byte-identical output, every number is copied
    from `facts` (so numcheck passes), every violation/warning check id is referenced (V3), at most 4 segments of
    at most 300 characters (V5), no advisory wording (V4), `inference` only where V5 allows it."""
    loc = facts["locale"]
    entries = facts["checks"]
    allow_inference = bool(facts["evidence_events"]) or any(
        str(d.get("by", "")).startswith("llm:") for d in facts["derived"].values())
    vw = [e for e in entries if e["status"] in ("violation", "warning")]
    info = [e for e in entries if e["status"] == "info"]
    unk = [e for e in entries if e["status"] == "unknown"]

    def kind_of(e: dict) -> str:
        return "inference" if e["id"] in _INFERENCE_CHECKS and allow_inference else "fact"

    segments: list[dict] = []
    room = MAX_SEGMENTS - (1 if unk else 0)
    if vw:
        slots = min(len(vw), room)
        lines = [(_line(e, loc), [e["id"]], kind_of(e)) for e in vw]
        for i in range(slots):
            chunk = lines[i:i + 1] if i < slots - 1 else lines[i:]
            text, refs = _merge([(t, r) for t, r, _ in chunk])
            kinds = {k for _, _, k in chunk}
            segments.append({"kind": "inference" if kinds == {"inference"} else "fact", "text": text, "refs": refs})
        room -= slots
        why = _why_segment(facts, vw, loc) if room > 0 else None
        if why:
            segments.append({"kind": "fact", "text": why[0], "refs": why[1]})
            room -= 1
    if info and room > 0:
        text, refs = _merge([(_line(e, loc), [e["id"]]) for e in info])
        segments.append({"kind": "fact", "text": text, "refs": refs})
    if unk:
        text, refs = _merge([(_line(e, loc), [e["id"]]) for e in unk])
        segments.append({"kind": "unknown", "text": text, "refs": refs})
    if not segments:
        segments.append({"kind": "fact", "text": _NONE[loc], "refs": ["status"]})
    return {"mode": "template", "segments": segments}


def verify_section(output: dict, facts: dict) -> list[str]:
    """Verifier V1-V5 (§20.5). Empty list = pass."""
    raise NotImplementedError


def explain(payload: dict) -> dict:
    """llm -> llm_retry -> template. Returns {mode, headline, segments, disclaimer}."""
    raise NotImplementedError


def disclaimer(locale: str = "fr-CA") -> str:
    return DISCLAIMER[locale]
