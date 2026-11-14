"""numcheck: every number in an explanation must exist in the facts payload (SPEC §9.3, §15 test_numcheck).

Rules pinned (SPEC §9.3 item 2): the regex handles `7,4 %`, `7.4%`, `360 000 $`, `$360,000` and dates.
A text number matches a payload number within +-0.05 absolute for percentages, +-0.5 % relative for
amounts, exactly for dates and years. Small integers <= 10 used as counts are exempt.
`numcheck(text, payload)` returns the offending numbers (empty list = pass).
"""
import copy
import unicodedata

import pytest

from coherence_analyzer import explain

NBSP = "\u00a0"
NNBSP = "\u202f"


def _bad(text: str, payload: dict) -> list[str]:
    return explain.numcheck(text, payload)


def _flagged(text: str, payload: dict, needle: str) -> bool:
    """True if `needle` (digits as written in the text) appears in some reported offender."""
    return any(needle in str(x) for x in _bad(text, payload))


# ---------------------------------------------------------------- accepted formats (§9.3, §15)

@pytest.mark.parametrize("text", ["7,4 %", "7.4%", "7,4%", "7.4 %", f"7,4{NBSP}%"])
@pytest.mark.parametrize("payload_value", ["7,4 %", "7.4 %"])
def test_percent_accepted_in_fr_and_en_notation(text, payload_value):
    """`7,4 %` and `7.4%` both match a payload percentage written either way."""
    payload = {"metrics": {"proposed": {"vol": payload_value}}}
    assert _bad(f"La volatilité est de {text} par année.", payload) == []


@pytest.mark.parametrize("text", ["360 000 $", f"360{NBSP}000{NBSP}$", f"360{NNBSP}000{NBSP}$", "$360,000",
                                  "360,000 $", "360000 $"])
@pytest.mark.parametrize("payload_value", ["360 000 $", "$360,000"])
def test_amount_accepted_in_fr_and_en_notation(text, payload_value):
    """`360 000 $` and `$360,000` both match a payload amount written either way."""
    payload = {"checks": [{"id": "H9", "limit": payload_value}]}
    assert _bad(f"Le plancher est de {text}.", payload) == []


def test_negative_percent_accepted():
    payload = {"checks": [{"id": "H8", "value": "-13,4 %", "limit": "-12,0 %"}]}
    assert _bad("Le repli historique est de -13,4 % pour une limite de -12,0 %.", payload) == []


def test_comma_decimal_is_not_a_thousands_separator_and_vice_versa():
    """`7,4 %` is 7.4 (not 74), `$360,000` is 360 000 (not 360)."""
    assert _flagged("Un repli de 74 %.", {"v": "7,4 %"}, "74")
    assert _bad("Un montant de 360 $.", {"v": "$360,000"}) != []


# ---------------------------------------------------------------- rejected numbers

def test_rejects_number_not_in_payload():
    payload = {"metrics": {"current": {"vol": "6,1 %"}, "proposed": {"vol": "9,8 %"}}}
    out = _bad("La volatilité passe de 6,1 % à 12,9 %.", payload)
    assert len(out) == 1
    assert "12,9" in str(out[0])


def test_returns_every_offender():
    out = _bad("7,4 % puis 12,9 % et 41,3 % et 361 000 $.", {"a": "7,4 %", "b": "360 000 $"})
    joined = " | ".join(str(x) for x in out)
    assert "12,9" in joined and "41,3" in joined
    assert "7,4" not in joined and "361" not in joined      # 361 000 is within 0.5 % of 360 000
    assert len(out) == 2


def test_text_without_numbers_passes():
    assert _bad("Aucun chiffre dans cette phrase.", {"a": "7,4 %"}) == []
    assert _bad("", {"a": "7,4 %"}) == []


def test_a_different_value_for_the_same_check_is_rejected():
    payload = {"checks": [{"id": "H4", "value": "25,0 %", "limit": "30,0 %"}]}
    assert _flagged("Le minimum est de 35,0 %.", payload, "35,0")


# ---------------------------------------------------------------- tolerances (§9.3)

def test_percent_tolerance_is_0_05_absolute():
    payload = {"v": "7,4 %"}
    assert _bad("Soit 7,44 %.", payload) == []              # |7.44 - 7.4| = 0.04 <= 0.05
    assert _bad("Soit 7,36 %.", payload) == []              # 0.04
    assert _flagged("Soit 7,5 %.", payload, "7,5")          # 0.10 > 0.05
    assert _flagged("Soit 7,3 %.", payload, "7,3")
    assert _flagged("Soit 8,4 %.", payload, "8,4")


def test_amount_tolerance_is_half_a_percent_relative():
    payload = {"v": "360 000 $"}
    assert _bad("Environ 361 000 $.", payload) == []        # +0.28 %
    assert _bad("Environ 359 000 $.", payload) == []        # -0.28 %
    assert _flagged("Environ 362 000 $.", payload, "362")   # +0.56 % > 0.5 %
    assert _flagged("Environ 340 000 $.", payload, "340")


def test_amount_tolerance_is_relative_not_absolute():
    """+-0.05 absolute applies to percentages only; a 1 CAD amount is not matched by 360 000 +- 0.05."""
    assert _bad("Environ 360 001 $.", {"v": "360 000 $"}) == []   # 0.0003 % relative
    assert _flagged("Environ 36 000 $.", {"v": "360 000 $"}, "36")


def test_dates_and_years_are_exact():
    payload = {"date": "2030-06", "asof": "2026-11-12"}
    assert _bad("Le retrait prévu est en 2030-06.", payload) == []
    assert _bad("Données au 2026-11-12.", payload) == []
    assert _bad("Le retrait prévu est en 2031-06.", payload) != []
    assert _bad("Données au 2026-11-13.", payload) != []
    assert _bad("En 2031, le retrait.", payload) != []


def test_year_is_not_matched_by_amount_tolerance():
    """2031 is within 0.5 % of an amount 2 030 $, but years are exact."""
    assert _bad("En 2031, le retrait.", {"v": "2 030 $"}) != []


# ---------------------------------------------------------------- small integers (§9.3)

@pytest.mark.parametrize("n", [1, 2, 3, 5, 10])
def test_small_integer_counts_are_exempt(n):
    assert _bad(f"{n} contrôles sont concernés.", {"v": "7,4 %"}) == []


def test_integer_above_ten_is_not_exempt():
    assert _bad("11 contrôles sont concernés.", {"v": "7,4 %"}) != []


def test_small_decimals_are_not_exempt():
    """Only integers <= 10 are exempt: 5,5 % and 3,6 are numbers that must exist in the payload."""
    assert _flagged("Soit 5,5 %.", {"v": "7,4 %"}, "5,5")
    assert _flagged("Soit 3,6 ans.", {"v": "7,4 %"}, "3,6")
    assert _bad("Soit 3,6 ans.", {"h": "3,6 ans"}) == []


def test_small_integer_percent_is_still_checked():
    """SPEC-AMBIGUITY: §9.3 exempts small integers 'used as counts'. A percentage is not a count, so '5 %' must exist."""
    assert _flagged("Soit 5 % du portefeuille.", {"v": "7,4 %"}, "5")
    assert _bad("Soit 5 % du portefeuille.", {"v": "5 %"}) == []


# ---------------------------------------------------------------- payload walking, purity

def test_numbers_found_in_nested_dicts_and_lists():
    payload = {"checks": [{"id": "H8", "derived_from": ["limits.max_drawdown_pct"],
                           "value": "-13,4 %", "limit": "-12,0 %"}],
               "evidence_events": [{"event_id": "ev_robert_0007", "date": "2026-11-14", "text": "10 % en une action"}],
               "metrics": {"current": {"p5_T": "345 618 $"}}}
    text = "-13,4 % contre -12,0 %; 345 618 $; le 2026-11-14; 10 % en une action."
    assert _bad(text, payload) == []


def test_numcheck_does_not_mutate_payload():
    payload = {"a": ["7,4 %", {"b": "360 000 $"}]}
    before = copy.deepcopy(payload)
    _bad("7,4 % et 12,9 %", payload)
    assert payload == before


def test_numcheck_is_deterministic():
    payload = {"a": "7,4 %"}
    assert _bad("7,4 % et 12,9 % et 41,3 %", payload) == _bad("7,4 % et 12,9 % et 41,3 %", payload)


# ---------------------------------------------------------------- advisory phrases (§9.3 item 3, §15)

def test_advisory_blocklist_covers_fr_and_en():
    """§15 'rejects advisory phrases': the blocklist the verifier (V4) uses holds the §9.3 examples, FR + EN."""
    bl = {p.lower() for p in explain.ADVISORY_BLOCKLIST}
    for phrase in ("tu devrais acheter", "je recommande", "you should buy", "we recommend"):
        assert phrase in bl or any(b in phrase for b in bl), phrase      # D10.4: shorter stems cover the old long forms
    assert any(p.startswith(("tu devrais", "je recommande", "nous recommandons")) for p in bl)
    assert any(p.startswith(("you should", "we recommend", "i recommend")) for p in bl)
    assert all(p == p.lower() for p in explain.ADVISORY_BLOCKLIST)


def _fold(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)).casefold()


# SPEC §9.3 item 3, verbatim ("at least"): suggestions as well as orders, FR + EN.
SPEC_ADVISORY_PHRASES = (
    "tu devrais", "vous devriez", "tu pourrais", "vous pourriez", "envisage", "il serait judicieux",
    "il serait préférable", "je recommande", "nous recommandons", "je conseille", "nous conseillons", "je suggère",
    "rééquilibr", "you should", "you could", "you may want to", "you might want to", "consider", "it would be wise",
    "it would be better", "we recommend", "i recommend", "we suggest", "i suggest", "we advise", "rebalanc",
    "next step", "prochaine étape")


@pytest.mark.parametrize("phrase", SPEC_ADVISORY_PHRASES)
def test_blocklist_holds_every_phrase_of_spec_9_3_item_3(phrase):
    """D10.4 / §9.3 item 3: the blocklist holds at least every listed phrase (compared in the folded form, so the
    entry may be stored with or without accents)."""
    folded = {_fold(p) for p in explain.ADVISORY_BLOCKLIST}
    assert _fold(phrase) in folded, phrase


def test_blocklist_is_not_weakened_to_the_old_narrow_list():
    """D10.4: the old list only had buy/sell imperatives; the new one has the suggestion forms too."""
    folded = {_fold(p) for p in explain.ADVISORY_BLOCKLIST}
    assert len(folded) >= len(SPEC_ADVISORY_PHRASES)
    assert {"you could", "vous pourriez", "consider", "next step"} <= folded

