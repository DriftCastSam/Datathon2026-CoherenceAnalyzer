"""Explanation: facts payload -> N5 explainer -> verifier V1-V5 -> retry once -> template (docs/spec-factors.md §20.5).

P0.7: `numcheck` (V2) and `template_section` (the deterministic fallback that always passes V1-V5).
P0.8: `verify_section` (V1-V5), `narrate_section` (llm -> llm_retry -> template, at most 2 N5 calls per section, none for
`pass` sections) and `explain` (headline, segments and disclaimer are template text; only section segments may be LLM
text, and only after the verifier passed). Every LLM call goes through `bedrock.call_tool` (see `extractors`).
"""
from __future__ import annotations

import copy
import unicodedata
from concurrent.futures import ThreadPoolExecutor

from .config import DISCLAIMER, DISCLAIMER_USER, USER_ORIGIN
from .llm import bedrock, extractors
from .numfacts import numcheck as _numcheck

# Lower case. SPEC §9.3 item 3 / D10: suggestions as well as orders, FR + EN. Several entries are stems
# ("envisage", "rééquilibr", "rebalanc", "consider") so every inflection matches. Use `advisory_hits` to match:
# it is case- and accent-insensitive.
ADVISORY_BLOCKLIST = (
    # FR
    "tu devrais acheter", "tu devrais vendre", "tu devrais", "vous devriez acheter", "vous devriez vendre",
    "vous devriez", "tu pourrais", "vous pourriez", "envisage", "il serait judicieux", "il serait préférable",
    "je recommande", "nous recommandons", "je conseille", "nous conseillons", "je suggère", "nous suggérons",
    "rééquilibr", "prochaine étape",
    # EN
    "you should buy", "you should sell", "you should", "you could", "you may want to", "you might want to",
    "consider", "it would be wise", "it would be better", "we recommend", "i recommend", "we suggest",
    "i suggest you", "i suggest", "we advise", "rebalanc", "next step",
)


def _fold(text: str) -> str:
    """NFKD, strip combining marks, casefold (SPEC §9.3 item 3): `rééquilibrer` and `REEQUILIBRER` match the same entry."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).casefold()


def advisory_hits(text: str) -> list[str]:
    """Blocklist entries found in `text` (case- and accent-insensitive). Empty list = no advisory wording."""
    low = _fold(text)
    return [p for p in ADVISORY_BLOCKLIST if _fold(p) in low]

MAX_SEGMENTS = 4          # V5
MAX_SEGMENT_CHARS = 300   # V5


def numcheck(text: str, payload: dict) -> list[str]:
    """Return numbers in `text` that don't match a payload number (empty list = pass). SPEC §9.3 item 2."""
    return _numcheck(text, payload)


# ---------------------------------------------------------------- template_section (§20.5)

_FR = {
    "H1": "{subject} fait partie des types d'actifs que vous excluez ({value} du portefeuille).",
    "H2": "Les cryptoactifs pèsent {value}, au-delà du maximum de {limit}.",
    "H3": "{subject} pèse {value}, au-delà du maximum de {limit} par action.",
    "H4": "Le revenu fixe pèse {value}, sous le minimum de {limit}.",
    "H5": "Les actifs en devises étrangères pèsent {value}, au-delà du maximum de {limit}.",
    "H6": "Le secteur de votre employeur ({subject}) pèse {value}, au-delà du maximum de {limit}.",
    "H7": "La volatilité historique est de {value}, au-dessus de la bande de {limit}.",
    "H7/info": "La volatilité historique est de {value}, sous la borne inférieure de {limit} de la bande de votre profil.",
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
    "H7/info": "Historical volatility is {value}, below the lower bound of {limit} of the band for your profile.",
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
    allow_inference = _allow_inference(facts)
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


# ---------------------------------------------------------------- verifier V1-V5 (§20.5)

KINDS = ("fact", "assumption", "inference", "unknown")
MODE_RANK = {"llm": 0, "llm_retry": 1, "template": 2}      # explanation.mode = the worst section mode


def _allow_inference(facts: dict) -> bool:
    """V5: `inference` segments only when the section has an `llm:`-derived field or evidence events."""
    return bool(facts.get("evidence_events")) or any(
        str(d.get("by", "")).startswith("llm:") for d in facts.get("derived", {}).values() if isinstance(d, dict))


def _path_exists(node: object, path: str) -> bool:
    """Dotted key path into `facts`. Keys may themselves contain dots (`declared.objectives.horizon_years`), so every
    key that prefixes the remaining path is tried. List items are addressed by index."""
    if isinstance(node, dict):
        for key in node:
            k = str(key)
            if path == k or (k and path.startswith(k + ".") and _path_exists(node[key], path[len(k) + 1:])):
                return True
        return False
    if isinstance(node, list):
        head, _, rest = path.partition(".")
        if head.isdigit() and int(head) < len(node):
            return rest == "" or _path_exists(node[int(head)], rest)
    return False


def _ref_resolves(ref: object, facts: dict, check_ids: set[str], event_ids: set[str]) -> bool:
    return isinstance(ref, str) and bool(ref) and (ref in check_ids or ref in event_ids or _path_exists(facts, ref))


def verify_section(output: dict, facts: dict) -> list[str]:
    """Verifier V1-V5 (§20.5), deterministic and pure. Empty list = pass. Every failure string starts with its rule id
    (`V1`..`V5`); all rules are evaluated, so a bad answer lists every rule it breaks."""
    check_ids = {c["id"] for c in facts.get("checks", [])}
    event_ids = {e["event_id"] for e in facts.get("evidence_events", []) if isinstance(e, dict) and "event_id" in e}
    raw = output.get("segments") if isinstance(output, dict) else None
    segments = raw if isinstance(raw, list) else []
    v1: list[str] = []
    v2: list[str] = []
    v4: list[str] = []
    v5: list[str] = []
    cited: set[str] = set()

    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_SEGMENTS:
        v5.append(f"V5: {len(segments)} segment(s), expected 1 to {MAX_SEGMENTS}")
    allow_inference = _allow_inference(facts)
    for i, seg in enumerate(segments, 1):
        if not isinstance(seg, dict):
            v5.append(f"V5: segment {i} is not an object")
            continue
        text, kind, refs = seg.get("text"), seg.get("kind"), seg.get("refs")
        if kind not in KINDS:
            v5.append(f"V5: segment {i} has kind {kind!r}, expected one of {', '.join(KINDS)}")
        elif kind == "inference" and not allow_inference:
            v5.append(f"V5: segment {i} is an inference but the section has no llm-derived field or evidence event")
        if not isinstance(text, str) or not text.strip():
            v5.append(f"V5: segment {i} has no text")
            text = ""
        elif len(text) > MAX_SEGMENT_CHARS:
            v5.append(f"V5: segment {i} has {len(text)} characters, maximum {MAX_SEGMENT_CHARS}")
        if not isinstance(refs, list) or not refs:
            v1.append(f"V1: segment {i} has no ref")
        else:
            for ref in refs:
                if _ref_resolves(ref, facts, check_ids, event_ids):
                    cited.add(ref)
                else:
                    v1.append(f"V1: segment {i} ref {ref!r} is no check id, key path or event_id of the facts")
        bad = _numcheck(text, facts)
        if bad:
            v2.append(f"V2: segment {i} has number(s) not in the facts: {', '.join(bad)}")
        v4 += [f"V4: segment {i} has advisory wording {p!r}" for p in advisory_hits(text)]

    v3 = [f"V3: check {cid} ({status}) is not referenced by any segment"
          for cid, status in dict.fromkeys((c["id"], c["status"]) for c in facts.get("checks", [])
                                           if c["status"] in ("violation", "warning"))
          if cid not in cited]
    return v1 + v2 + v3 + v4 + v5


# ---------------------------------------------------------------- narration loop (§20.5)

def _narrative(mode: str, segments: list[dict], facts: dict, attempts: list[dict], error: str | None = None) -> dict:
    failures = verify_section({"segments": segments}, facts)
    narrative = {"mode": mode, "segments": segments, "verify": {"passed": not failures, "failures": failures},
                 "attempts": attempts}
    if error:
        narrative["llm_error"] = error
    return narrative


def _template_narrative(facts: dict, attempts: list[dict], error: str | None = None) -> dict:
    return _narrative("template", template_section(facts)["segments"], facts, attempts, error)


def narrate_section(facts: dict) -> dict:
    """One section: N5 -> verify -> pass `llm`; else N5 again with `retry_failures` -> pass `llm_retry`; else the
    template. Never more than 2 LLM calls. `pass` sections never reach the writer. Output that failed verification is
    never returned. A missing recording (mock) or an output that stays schema-invalid ends in the template."""
    attempts: list[dict] = []
    if facts["status"] == "pass":
        return _template_narrative(facts, attempts)
    retry_failures: list[str] | None = None
    for attempt in (1, 2):
        try:
            result = extractors.explainer_call(facts, retry_failures)
        except bedrock.LLMCacheMiss as exc:                   # nothing recorded for this input: no point in retrying
            return _template_narrative(facts, attempts, f"LLMCacheMiss: {exc}")
        except bedrock.GuardrailIntervened as exc:           # blocked, not malformed: no retry (SPEC §6.1, §9.3)
            attempts.append({"failures": [f"guardrail: {exc}"], "call_log": exc.call_log})
            return _template_narrative(facts, attempts, f"GuardrailIntervened: {exc}")
        except bedrock.LLMOutputInvalid as exc:
            failures = [f"V5: the output did not match the explainer schema: {exc}"]
            attempts.append({"failures": failures, "call_log": None})
        else:
            output = result["output"]
            failures = verify_section(output, facts)
            attempts.append({"failures": failures, "call_log": result["call_log"]})
            if not failures:
                segments = copy.deepcopy(output["segments"])
                return _narrative("llm" if attempt == 1 else "llm_retry", segments, facts, attempts)
        retry_failures = failures
    return _template_narrative(facts, attempts)


_HEAD = {"fr-CA": {"coherent": "Portefeuille cohérent avec votre profil",
                   "coherent_with_warnings": "Portefeuille cohérent avec votre profil, avec avertissements",
                   "incoherent": "Portefeuille incohérent avec votre profil"},
         "en-CA": {"coherent": "Portfolio consistent with your profile",
                   "coherent_with_warnings": "Portfolio consistent with your profile, with warnings",
                   "incoherent": "Portfolio inconsistent with your profile"}}
_HEAD_IDS = {"fr-CA": "écarts", "en-CA": "gaps"}
MAX_HEADLINE_CHARS = 140       # SPEC §9.2


def headline(verdict: str, sections: list[dict], locale: str) -> str:
    """Template headline from the verdict and the failing (violation / warning) check ids, in display order."""
    ids = list(dict.fromkeys(c["id"] for s in sections for c in s["facts"]["checks"]
                             if c["status"] in ("violation", "warning")))
    base = _HEAD[locale][verdict]
    if not ids:
        return base + "."
    while ids:
        text = f"{base}: {_HEAD_IDS[locale]} {', '.join(ids)}."
        if len(text) <= MAX_HEADLINE_CHARS:
            return text
        ids.pop()
    return base + "."


LANE_WORKERS = 5          # §20.10: one lane per factor, at most five at once


def _narrate_lane(facts: dict) -> dict:
    """One lane: `narrate_section`; an exception ends in the template for that section only (as for a cache miss)."""
    try:
        return narrate_section(facts)
    except Exception as exc:          # noqa: BLE001 - a failing lane must not lose the others
        return _template_narrative(facts, [], f"{type(exc).__name__}: {exc}")


def _narrate_all(sections: list[dict]) -> list[dict]:
    """§20.10 / D8.2: non-pass sections are narrated concurrently (ThreadPoolExecutor, max_workers=5); `pass` sections
    never reach the writer and are narrated inline. Results are collected in display order, so the output does not
    depend on which lane finishes first."""
    lanes: dict[str, object] = {}
    non_pass = [s for s in sections if s["status"] != "pass"]
    if non_pass:
        with ThreadPoolExecutor(max_workers=LANE_WORKERS) as pool:
            for s in non_pass:
                lanes[s["factor"]] = pool.submit(_narrate_lane, s["facts"])
    out = []
    for s in sections:
        fut = lanes.get(s["factor"])
        narrative = fut.result() if fut is not None else narrate_section(s["facts"])
        out.append({"factor": s["factor"], "narrative": narrative})
    return out


def explain(payload: dict) -> dict:
    """The report's explanation (§20.5): `{mode, headline, segments, disclaimer, sections}`.

    `payload` = `{verdict, locale, sections}` with `sections` from `factors.build_sections`. Each section is narrated
    on its own facts; `segments` concatenates the section segments in the order given (display order), each with a
    `factor` key; the headline and the disclaimer are template text. `mode` = worst mode among the non-pass sections
    (`template > llm_retry > llm`); with none, `template`."""
    sections = payload["sections"]
    out_sections = _narrate_all(sections)
    modes = [n["narrative"]["mode"] for s, n in zip(sections, out_sections) if s["status"] != "pass"]
    locale = payload["locale"]
    return {
        "mode": max(modes, key=MODE_RANK.__getitem__) if modes else "template",
        "headline": headline(payload["verdict"], sections, locale),
        "segments": [{"factor": n["factor"], **copy.deepcopy(seg)} for n in out_sections
                     for seg in n["narrative"]["segments"]],
        "disclaimer": disclaimer(locale, getattr(sections, "origin", None)),   # D16: set by report.build_sections
        "sections": out_sections,
    }


def disclaimer(locale: str = "fr-CA", origin: str | None = None) -> str:
    """D16: the fixed disclaimer of the profile's origin. `origin == "user"` -> the text without the
    fictional-profiles clause; anything else (seed personas, absent, unknown) -> the SPEC §9.4 text."""
    return (DISCLAIMER_USER if origin == USER_ORIGIN else DISCLAIMER)[locale]


# ---------------------------------------------------------------- D13: one plain statement per crossed limit

_STATEMENT_LABELS = {
    "en-CA": {
        "H1": "an asset type you exclude, {subject}", "H2": "maximum crypto weight",
        "H3": "maximum weight for {subject}", "H4": "minimum fixed-income weight",
        "H5": "maximum foreign-currency weight", "H6": "maximum weight of your employer's sector, {subject}",
        "H7": "maximum annual volatility", "H8": "maximum historical drawdown",
        "H9": "minimum value of the low scenario at your horizon",
        "H10": "maximum share of the risk for the {subject} sector",
        "H11": "an activity you exclude, held through {subject}",
        "H12": "maximum fee for {subject}", "H13": "maximum weight for the {subject} theme",
        "H14": "maximum drawdown given that you say you would sell after a large drop",
        "?": "limit {id}",
    },
    "fr-CA": {
        "H1": "un type d'actif que vous excluez, {subject}", "H2": "poids maximal en cryptoactifs",
        "H3": "poids maximal pour {subject}", "H4": "poids minimal du revenu fixe",
        "H5": "poids maximal en devises étrangères", "H6": "poids maximal du secteur de votre employeur, {subject}",
        "H7": "volatilité annuelle maximale", "H8": "repli historique maximal",
        "H9": "valeur minimale du scénario bas à votre horizon",
        "H10": "part maximale du risque pour le secteur {subject}",
        "H11": "une activité que vous excluez, détenue via {subject}",
        "H12": "frais maximaux pour {subject}", "H13": "poids maximal pour le thème {subject}",
        "H14": "repli historique maximal, compte tenu de votre réaction à une forte baisse",
        "?": "limite {id}",
    },
}
_STATEMENT = {
    "en-CA": {"head": "Making this trade takes you outside your profile: {label}{limit}. "
                      "Facts: {value} after the trade{before}.",
              "before": ", {before} before the trade", "quote": " From what you told us: '{quote}' ({source})."},
    "fr-CA": {"head": "Cette transaction vous place hors de votre profil\u00a0: {label}{limit}. "
                      "Faits\u00a0: {value} après la transaction{before}.",
              "before": ", {before} avant la transaction",
              "quote": " Selon ce que vous nous avez dit\u00a0: «\u00a0{quote}\u00a0» ({source})."},
}


def trade_statement(check: dict, before: dict | None, locale: str, quote: tuple[str, str] | None = None) -> str:
    """D13: the one plain statement for a hard limit the trade crosses (FR / EN template, no question, no suggestion).
    `check` = the proposed violation, `before` = the same check on the current side when it was already breached
    (the value before the trade is then shown), `quote` = (verbatim quote, source) only when the limit traces to the
    user's own words. Every number is the check's own value / limit formatted by its unit (so numcheck passes)."""
    from .factors import fmt        # lazy: factors imports the report module
    loc = locale if locale in _STATEMENT else "fr-CA"
    t, unit = _STATEMENT[loc], check.get("unit")
    labels = _STATEMENT_LABELS[loc]
    label = labels.get(check["id"], labels["?"]).format(id=check["id"], subject=check.get("subject") or "")
    limit = check.get("limit")
    limit_txt = f" ({fmt.check_value(limit, unit, loc)})" if limit is not None else ""
    before_txt = ""
    if before is not None and before.get("value") is not None:
        before_txt = t["before"].format(before=fmt.check_value(before["value"], before.get("unit", unit), loc))
    text = t["head"].format(label=label, limit=limit_txt, before=before_txt,
                            value=fmt.check_value(check.get("value"), unit, loc))
    if quote is not None:
        text += t["quote"].format(quote=quote[0], source=quote[1])
    return text
