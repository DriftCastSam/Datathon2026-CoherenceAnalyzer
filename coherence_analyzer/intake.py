"""Intake per factor: deterministic checklist + fixed questions (docs/spec-factors.md §20.6, D7 point 1). P0 step 10.

No LLM writes a question. `REQUIRED` is the checklist per factor (profile paths the user answers, never `llm:`
or `rule:` fields). `missing` and `next_question` are pure. A skipped path is `{value: None, by: 'user:skipped'}`:
it is no longer missing and the related checks report `unknown`.

Besides the static paths, `contraintes` also asks for the number of a parsed `max_theme_weight` rule that has none
(Amira's "pas tout en tech"). Those paths point into the parsed rule list: `values.parsed_notes.value[i].params.max_pct`.
"""
from __future__ import annotations

import copy
import re
from typing import Any

FACTORS = ("objectif", "horizon", "risque", "contraintes", "preferences")
LOCALES = ("fr-CA", "en-CA")
SKIPPED_BY = "user:skipped"
PARSED_RULES_PATH = "values.parsed_notes.value"
THEME_NUMBER_KEY = "max_pct"

# Intake checklist per factor (§20.6). P0.9 wires it into the factor modules (`REQUIRED` of §20.3).
# P5.INTAKEFULL: every user-declared input read by `rules/registry.REGISTRY` and `rules/limits.LIMIT_INPUTS` is asked
# here, except the OPTIONAL ones below. `identity.age` feeds `identity.life_stage` (objectif);
# `identity.employment_type` feeds `situation.income_stability`, hence `risk.capacity` / `risk.effective` and every
# table limit (contraintes); `knowledge.declared_level` feeds `knowledge.score` (contraintes). `knowledge.quiz_score`
# (quiz events) and `knowledge.question_quality` (N2) are event-fed, not questions.
REQUIRED: dict[str, tuple[str, ...]] = {
    "objectif": ("objectives.primary", "objectives.goals", "identity.age"),
    "horizon": ("objectives.horizon_years", "identity.retirement_target_age", "situation.planned_withdrawals"),
    "risque": ("risk.declared", "risk.max_acceptable_drawdown_pct", "risk.reaction_to_20pct_drop"),
    "contraintes": ("situation.emergency_fund", "situation.monthly_expenses", "identity.employer_sector",
                    "identity.employment_type", "knowledge.declared_level", "preferences.asset_types_refused"),
    "preferences": ("preferences.prefers_dividends", "preferences.prefers_large_caps", "preferences.home_bias_ok"),
}

# Declared inputs the rules and limits read that are NOT asked. Absent means "no constraint": `limits` only gains
# `max_fee_mer_pct` when it is declared, and an absent exclusions list excludes nothing.
OPTIONAL: dict[str, str] = {
    "preferences.max_fee_mer_pct": "absent = no fee cap (a cap of 0 would mean no product allowed)",
    "values.environment.exclusions": "absent = no environmental exclusion",
    "values.social.exclusions": "absent = no social exclusion",
    "values.governance.exclusions": "absent = no governance exclusion",
}

QUESTIONS: dict[str, dict[str, str]] = {
    "identity.age": {
        "fr-CA": "Quel est votre âge, en années?",
        "en-CA": "How old are you, in years?"},
    "identity.employment_type": {
        "fr-CA": "Quel est votre type d'emploi: salarié, travailleur autonome, contractuel, à commission, sans emploi ou retraité?",
        "en-CA": "What is your type of employment: salaried, self-employed, contract, commission, unemployed or retired?"},
    "knowledge.declared_level": {
        "fr-CA": "Comment décririez-vous votre niveau de connaissance des placements: débutant, intermédiaire ou avancé?",
        "en-CA": "How would you describe your knowledge of investing: beginner, intermediate or advanced?"},
    "objectives.primary": {
        "fr-CA": "Quel est votre objectif principal avec ce portefeuille (par exemple préserver le capital, "
                 "générer un revenu ou faire croître le capital)?",
        "en-CA": "What is your main goal for this portfolio (for example preserving capital, earning income "
                 "or growing capital)?"},
    "objectives.goals": {
        "fr-CA": "Quels sont vos projets chiffrés (nom, date, montant en $ CA), par exemple une retraite ou une mise de fonds?",
        "en-CA": "What are your dated goals (name, date, amount in CAD), for example retirement or a down payment?"},
    "objectives.horizon_years": {
        "fr-CA": "Dans combien d'années comptez-vous avoir besoin de cet argent?",
        "en-CA": "In how many years do you expect to need this money?"},
    "identity.retirement_target_age": {
        "fr-CA": "À quel âge prévoyez-vous prendre votre retraite?",
        "en-CA": "At what age do you plan to retire?"},
    "situation.planned_withdrawals": {
        "fr-CA": "Prévoyez-vous des retraits importants (date, montant, raison)? Répondez par une liste vide s'il n'y en a pas.",
        "en-CA": "Do you plan any large withdrawals (date, amount, purpose)? Answer with an empty list if there are none."},
    "risk.declared": {
        "fr-CA": "Comment décririez-vous votre tolérance au risque: faible, faible à modérée, modérée, modérée à élevée ou élevée?",
        "en-CA": "How would you describe your risk tolerance: low, low-moderate, moderate, moderate-high or high?"},
    "risk.max_acceptable_drawdown_pct": {
        "fr-CA": "Quelle baisse maximale de votre portefeuille pouvez-vous accepter, en pourcentage (par exemple -12)?",
        "en-CA": "What is the largest drop in your portfolio you could accept, as a percentage (for example -12)?"},
    "risk.reaction_to_20pct_drop": {
        "fr-CA": "Si votre portefeuille perdait 20 %, que feriez-vous: tout vendre, vendre une partie, ne rien faire ou acheter plus?",
        "en-CA": "If your portfolio lost 20%, what would you do: sell everything, sell some, do nothing or buy more?"},
    "situation.emergency_fund": {
        "fr-CA": "Quel montant avez-vous en fonds d'urgence, en $ CA?",
        "en-CA": "How much do you hold as an emergency fund, in CAD?"},
    "situation.monthly_expenses": {
        "fr-CA": "Quelles sont vos dépenses mensuelles, en $ CA?",
        "en-CA": "What are your monthly expenses, in CAD?"},
    "identity.employer_sector": {
        "fr-CA": "Dans quel secteur travaille votre employeur?",
        "en-CA": "Which sector does your employer operate in?"},
    "preferences.asset_types_refused": {
        "fr-CA": "Quels types de produits refusez-vous de détenir (par exemple cryptomonnaies, options, marge)? "
                 "Répondez par une liste vide si aucun.",
        "en-CA": "Which product types do you refuse to hold (for example crypto, options, margin)? "
                 "Answer with an empty list if none."},
    "preferences.prefers_dividends": {
        "fr-CA": "Préférez-vous les titres qui versent des dividendes (oui ou non)?",
        "en-CA": "Do you prefer dividend-paying holdings (yes or no)?"},
    "preferences.prefers_large_caps": {
        "fr-CA": "Préférez-vous les grandes capitalisations (oui ou non)?",
        "en-CA": "Do you prefer large-cap companies (yes or no)?"},
    "preferences.home_bias_ok": {
        "fr-CA": "Acceptez-vous une surpondération des titres canadiens (oui ou non)?",
        "en-CA": "Are you comfortable with an overweight in Canadian holdings (yes or no)?"},
}

# Parsed theme rule with no number (D7 point 1). `{theme}` and `{quote}` come from the parsed rule.
THEME_NUMBER_QUESTION: dict[str, str] = {
    "fr-CA": "Vous avez écrit « {quote} ». Quelle part maximale du portefeuille (en %) souhaitez-vous "
             "pour le thème « {theme} »?",
    "en-CA": "You wrote \"{quote}\". What is the maximum share of your portfolio (in %) you want in the "
             "\"{theme}\" theme?",
}

_STEP = re.compile(r"^([A-Za-z_][A-Za-z_0-9]*)(?:\[(\d+)\])?$")
_ABSENT = object()


# ---------------------------------------------------------------- path access (pure)

def _steps(path: str) -> list[str | int]:
    out: list[str | int] = []
    for part in path.split("."):
        m = _STEP.match(part)
        if not m:
            raise ValueError(f"bad profile path: {path!r}")
        out.append(m.group(1))
        if m.group(2) is not None:
            out.append(int(m.group(2)))
    return out


def _lookup(profile: dict, path: str) -> Any:
    node: Any = profile
    for step in _steps(path):
        if isinstance(step, int):
            if not isinstance(node, list) or step >= len(node):
                return _ABSENT
        elif not isinstance(node, dict) or step not in node:
            return _ABSENT
        node = node[step]
    return node


def is_skipped(node: Any) -> bool:
    """True if the node is the skipped marker {value: None, by: 'user:skipped'}."""
    return isinstance(node, dict) and node.get("by") == SKIPPED_BY


def _is_answered(node: Any) -> bool:
    if node is _ABSENT or node is None:
        return False
    if is_skipped(node):
        return True
    if isinstance(node, dict) and "value" in node:      # {value, by, ...} wrapper
        return _is_answered(node["value"])
    if isinstance(node, str):
        return bool(node.strip())
    return True                                           # 0, False, [] and {} are real answers


def _theme_rules_without_number(profile: dict) -> list[tuple[str, dict]]:
    rules = _lookup(profile, PARSED_RULES_PATH)
    if not isinstance(rules, list):
        return []
    out = []
    for i, r in enumerate(rules):
        if isinstance(r, dict) and r.get("rule_kind") == "max_theme_weight":
            params = r.get("params") if isinstance(r.get("params"), dict) else {}
            number = params.get(THEME_NUMBER_KEY, _ABSENT)
            if not is_skipped(number) and not (isinstance(number, (int, float)) and not isinstance(number, bool)):
                out.append((f"{PARSED_RULES_PATH}[{i}].params.{THEME_NUMBER_KEY}", r))
    return out


# ---------------------------------------------------------------- public API

def missing(profile: dict, factor: str) -> list[str]:
    """Paths still to ask for this factor, in checklist order. Pure.

    A path is missing when it is absent, null (unanswered) or an empty string. The explicit skip marker counts
    as answered. `contraintes` also lists the `max_pct` of each parsed `max_theme_weight` rule that has no number.
    """
    if factor not in REQUIRED:
        raise ValueError(f"unknown factor {factor!r}; expected one of {FACTORS}")
    out = [p for p in REQUIRED[factor] if not _is_answered(_lookup(profile, p))]
    if factor == "contraintes":
        out.extend(path for path, _ in _theme_rules_without_number(profile))
    return out


def next_question(profile: dict, factor: str, locale: str = "fr-CA") -> dict | None:
    """Fixed template question for the first missing path, or None. No LLM. Pure.

    Returns {factor, path, text, locale}.
    """
    if locale not in LOCALES:
        raise ValueError(f"unknown locale {locale!r}; expected one of {LOCALES}")
    todo = missing(profile, factor)
    if not todo:
        return None
    path = todo[0]
    if path in QUESTIONS:
        text = QUESTIONS[path][locale]
    else:
        rule = dict(_theme_rules_without_number(profile))[path]
        text = THEME_NUMBER_QUESTION[locale].format(quote=rule.get("quote", ""),
                                                    theme=(rule.get("params") or {}).get("theme", ""))
    return {"factor": factor, "path": path, "text": text, "locale": locale}


def _assign(profile: dict, path: str, value: Any) -> dict:
    out = copy.deepcopy(profile)
    steps = _steps(path)
    node: Any = out
    for step in steps[:-1]:
        if isinstance(step, int):
            node = node[step]
        else:
            node = node.setdefault(step, {})
    node[steps[-1]] = value
    return out


def skip(profile: dict, path: str) -> dict:
    """Mark a REQUIRED path as {value: None, by: 'user:skipped'}; returns a new profile."""
    return _assign(profile, path, {"value": None, "by": SKIPPED_BY})


def answer(profile: dict, path: str, value: Any) -> dict:
    """Store a user-declared answer at `path` (plain value, §3.3 convention); returns a new profile."""
    return _assign(profile, path, copy.deepcopy(value))


# ---------------------------------------------------------------- intake answers as events (§20.6, P3.FREETEXT)

# Paths whose answer is free text for N3 (§6.2 N3 input = values.notes). They are answerable but not part of the
# checklist, and not skippable. Every other answerable path is a declared structured value (no LLM).
FREE_TEXT_PATHS = ("values.notes",)


def is_free_text(path: str) -> bool:
    return path in FREE_TEXT_PATHS


def current_value(profile: dict, path: str) -> Any:
    """What the user saw at `path` (the `old` of the event): the value, the skip marker, or None when absent. Pure."""
    node = _lookup(profile, path)
    return None if node is _ABSENT else copy.deepcopy(node)


def event_text(path: str, skipped: bool = False) -> str:
    """Fixed description of the recorded answer or skip (D10: describes, never prescribes). No LLM."""
    return f"Intake question skipped for {path}." if skipped else f"Intake answer recorded for {path}."
