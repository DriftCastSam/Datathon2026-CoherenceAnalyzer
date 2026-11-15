"""Pipeline review locale override: en-CA vs fr-CA headline and disclaimer."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from coherence_analyzer import config, intake, pipeline  # noqa: E402

ROBERT = ROOT / "fixtures" / "robert"


def test_review_with_en_ca_locale_produces_english_headline():
    rep = pipeline.review(ROBERT, locale="en-CA")

    headline = rep["explanation"]["headline"]
    assert "portfolio" in headline.lower()
    assert "portefeuille" not in headline.lower()


def test_review_with_en_ca_locale_has_en_ca_disclaimer():
    rep = pipeline.review(ROBERT, locale="en-CA")

    disclaimer = rep["explanation"]["disclaimer"]
    assert disclaimer == config.DISCLAIMER["en-CA"]
    assert "Educational simulation" in disclaimer


def test_review_default_locale_stays_profile_locale():
    rep = pipeline.review(ROBERT)

    headline = rep["explanation"]["headline"]
    assert "portefeuille" in headline.lower()


def test_review_invalid_locale_raises():
    with pytest.raises(ValueError, match="locale must be one of"):
        pipeline.review(ROBERT, locale="es-MX")


def test_review_locale_none_uses_profile_locale():
    rep = pipeline.review(ROBERT, locale=None)

    headline = rep["explanation"]["headline"]
    assert "portefeuille" in headline.lower()


def test_review_accepts_both_valid_locales():
    for loc in intake.LOCALES:
        rep = pipeline.review(ROBERT, locale=loc)
        assert rep["explanation"]["disclaimer"] == config.DISCLAIMER[loc]
