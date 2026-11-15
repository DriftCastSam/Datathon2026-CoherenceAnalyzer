"""D10 "Describe, never prescribe" on the site copy (docs/spec-decisions.md D10; SPEC inv. 7, §8, §9.3 item 3,
§12 UI; docs/spec-factors.md §20.7).

Scope: `deploy/site/design.html` (product-screens design) and `deploy/whatif.py` (what-if precompute).
Offline, deterministic: HTML is parsed as text, whatif's pure helpers run on tiny inputs, no network.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import unicodedata
from html.parser import HTMLParser
from pathlib import Path

import pytest

os.environ.setdefault("COHERENCE_ANALYZER_LLM", "mock")

ROOT = Path(__file__).resolve().parents[2]
DESIGN = ROOT / "deploy" / "site" / "design.html"
WHATIF = ROOT / "deploy" / "whatif.py"

# SPEC §9.3 item 3, verbatim list (matched case- and accent-insensitively).
BLOCKLIST = (
    "tu devrais", "vous devriez", "tu pourrais", "vous pourriez", "envisage", "il serait judicieux",
    "il serait préférable", "je recommande", "nous recommandons", "je conseille", "nous conseillons",
    "je suggère", "rééquilibr", "you should", "you could", "you may want to", "you might want to", "consider",
    "it would be wise", "it would be better", "we recommend", "i recommend", "we suggest", "i suggest",
    "we advise", "rebalanc", "next step", "prochaine étape",
)

DISCLAIMER_FR = ("Simulation éducative avec des profils fictifs. Ceci n'est pas un conseil financier personnalisé. "
                 "Aucune transaction n'est exécutée.")

# Fields / labels that would rank, select or label a choice (D10 item 1-2).
FORBIDDEN_KEYS = {
    "best", "fits", "fit", "fitting", "none_fit", "option", "options", "alternative", "alternatives", "rank", "ranks",
    "ranking", "rank_by", "order", "order_by", "score", "scores", "recommended", "recommendation", "preferred",
    "suggested", "suggestion", "next_step", "winner", "top",
}
FORBIDDEN_WORD = re.compile(r"\b(best|fits|fitting|options?|alternatives?|rank(ing|ed)?|recommended|preferred|"
                            r"suggested|winner)\b|none_fit|best_|_best|_option", re.I)


def norm(s: str) -> str:
    """Lowercase, strip accents, unify apostrophes and whitespace."""
    s = unicodedata.normalize("NFKD", s.replace("\u2019", "'").replace("\xa0", " ").replace("\u202f", " "))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", s)


NORM_BLOCKLIST = tuple(norm(p) for p in BLOCKLIST)


BLOCK_TAGS = {"br", "p", "div", "li", "ul", "h1", "h2", "h3", "h4", "h5", "button", "section", "td", "tr", "hr"}


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        if tag in BLOCK_TAGS:
            self.chunks.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        if tag in BLOCK_TAGS:
            self.chunks.append(" ")

    def handle_data(self, data):
        if not self._skip:
            self.chunks.append(data)


def html_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    return re.sub(r"\s+", " ", "".join(p.chunks)).strip()


@pytest.fixture(scope="module")
def raw() -> str:
    return DESIGN.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def text(raw) -> str:
    """Visible text of design.html with the fixed disclaimer (SPEC §9.4) removed."""
    t = html_text(raw)
    t = t.replace(DISCLAIMER_FR, " ")
    return t


def _board(raw: str, n: int) -> str:
    """Slice of the HTML belonging to board `n` (`<div class="board-label"><b>n · ...`)."""
    marks = [(m.start(), int(m.group(1))) for m in re.finditer(r'board-label"><b>(\d+) ·', raw)]
    assert marks, "no board-label markers found in design.html"
    for i, (pos, num) in enumerate(marks):
        if num == n:
            end = marks[i + 1][0] if i + 1 < len(marks) else len(raw)
            return raw[pos:end]
    raise AssertionError(f"board {n} not found")


# --------------------------------------------------------------------------- design.html


def test_design_text_has_no_advisory_phrase(text):
    """SPEC §9.3 item 3 / D10 item 4: no blocklist phrase anywhere in the visible design copy."""
    low = norm(text)
    hits = [p for p in NORM_BLOCKLIST if p in low]
    assert hits == []


def test_design_source_has_no_advisory_phrase_in_attributes(raw):
    """Same blocklist over the whole source (alt/title/aria attributes, comments), minus script/style."""
    src = re.sub(r"<(script|style)\b.*?</\1>", " ", raw, flags=re.S | re.I)
    low = norm(src)
    assert [p for p in NORM_BLOCKLIST if p in low] == []


def test_design_has_no_option_or_alternative_wording(text, raw):
    """D10 item 2 / SPEC §8: 'options' and 'alternatives' are not allowed; k-option style and tag removed."""
    low = norm(text)
    assert re.search(r"\boptions?\b", low) is None
    assert re.search(r"\balternatives?\b", low) is None
    # Stricter reading: also the markup (class names, attributes, comments), CSS included.
    src = norm(raw)
    assert "k-option" not in src
    assert "option" not in src
    assert "alternative" not in src


def test_design_removed_fits_in_limits_block_and_card(text, raw):
    """D10: the 'Ce qui tient dans vos limites' block and the 'Jusqu'à 2,0 %' card are gone."""
    low = norm(text)
    for phrase in ("ce qui tient dans vos limites", "tient dans vos limites", "dans vos limites",
                   "jusqu'a 2,0 %", "jusqu'a 2.0 %", "jusqu'a 2,0%"):
        # SPEC-AMBIGUITY: "dans vos limites" alone could be harmless prose; stricter reading bans it since the
        # removed block/card used exactly that wording to present sizes as acceptable.
        assert phrase not in low, phrase
    assert re.search(r"jusqu'a \d+[,.]\d+ ?%", low) is None
    # The three variant cards (pro rata all / pro rata class / from ZST) are gone from the page.
    assert "prorata, tous les placements" not in low
    assert "prorata, fnb d'actions" not in low
    assert "class=\"opts\"" not in raw and "card opt" not in raw


def test_design_no_text_suggests_a_funding_variant_or_size(text):
    """D10 item 1: no 'Prorata, ...' funding variant is offered and no 'Depuis ZST.TO <size>' card remains."""
    low = norm(text)
    assert re.search(r"\bprorata\b", low) is None
    assert re.search(r"\bpro rata\b", low) is None
    assert re.search(r"depuis zst\.to", low) is None


HEADROOM_FR = re.compile(
    r"Avec le financement indiqué \(ZST\.TO\), la transaction franchit votre limite H\d+ au-delà de "
    r"\d+(?:[.,]\d+)?\s?%\.")


def test_design_has_factual_headroom_line_in_flow_b_report(raw):
    """SPEC §8 template form: FR « Avec le financement indiqué (ZST.TO), la transaction franchit votre limite H9
    au-delà de X %. » shown in the flow-B report board (board 2)."""
    board = html_text(_board(raw, 2)).replace("\xa0", " ").replace("\u202f", " ")
    m = HEADROOM_FR.search(board)
    assert m is not None, "headroom line (SPEC §8 FR template) missing from board 2"
    # The starter data binds on H9 for Robert + NVDA from ZST (D2).
    assert "limite H9 au-delà de" in m.group(0)


def test_design_headroom_line_is_flow_b_only(raw):
    """SPEC §9.2 / §20.7: headroom is flow B only: absent from the home board and the flow-A approval board."""
    for n in (0, 4):
        board = html_text(_board(raw, n))
        assert HEADROOM_FR.search(board) is None, n
        assert "la transaction franchit votre limite" not in board, n
        assert "au-delà de" not in board, n


def test_design_headroom_line_has_no_action_framing(raw):
    """The headroom line is a fact: no leading 'Option'/'Jusqu'à' framing and no verbs of action around it."""
    board = html_text(_board(raw, 2))
    m = HEADROOM_FR.search(board)
    assert m is not None
    low = norm(board)
    assert "jusqu'a" not in low[max(0, low.find("avec le financement indique") - 40):]
    assert re.search(r"\b(tient|tiennent|convient|acceptable|maximum recommande|ideal)\b", norm(m.group(0))) is None


def test_design_does_not_rank_or_prefer_choices(text):
    """D10 item 1: no ranking / 'better' / 'most suitable' claims in the copy."""
    low = norm(text)
    for pat in (r"\ble meilleur\b", r"\bla meilleure\b", r"\bmieux adapte", r"\bplus adapte", r"\bde preference\b",
                r"\bideal\b", r"\bprochaine etape\b", r"\bnext step\b", r"\bbest\b", r"\bmost suitable\b"):
        assert re.search(pat, low) is None, pat


# --------------------------------------------------------------------------- whatif.py


@pytest.fixture(scope="module")
def whatif():
    spec = importlib.util.spec_from_file_location("deploy_whatif_d10", WHATIF)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def whatif_tree() -> ast.Module:
    return ast.parse(WHATIF.read_text(encoding="utf-8"))


def _doc_and_strings(tree: ast.Module) -> list[str]:
    """Every docstring and every string constant (dict keys, labels, f-string parts) in the module."""
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
    return out


def _all_keys(obj) -> set[str]:
    keys: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(str(k))
            keys |= _all_keys(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            keys |= _all_keys(v)
    return keys


def test_whatif_strings_and_docstrings_have_no_option_ranking_or_advice(whatif_tree):
    """D10 item 1-2 / SPEC §8: no 'best'/'fits'/'option'/'alternative'/ranking wording in docstrings, labels, keys."""
    strings = _doc_and_strings(whatif_tree)
    assert [s for s in strings if FORBIDDEN_WORD.search(s)] == []
    hits = [(p, norm(s)[:60]) for s in strings for p in NORM_BLOCKLIST if p in norm(s)]
    assert hits == []


def test_whatif_docstrings_present_scenarios_as_user_chosen(whatif_tree):
    """D10: scenarios are inputs the user picks (asset, size, funding); the docstring must say so."""
    doc = norm(ast.get_docstring(whatif_tree) or "")
    assert doc, "whatif.py needs a module docstring"
    # SPEC-AMBIGUITY: exact wording is not specified; require that the docstring attributes the choice of asset,
    # size and funding to the user, and states the tool does not recommend or rank.
    assert re.search(r"user[- ](chosen|picked|selected|stated|chooses|picks|selects)|chosen by the user|"
                     r"(picked|selected|stated) by the user|the user (picks|chooses|selects|states)", doc)
    for word in ("asset", "size", "funding"):
        assert word in doc, word
    assert re.search(r"no (recommendation|ranking)|not (recommend|rank)|never (recommend|rank)|"
                     r"does not (recommend|rank)|nothing is (recommended|ranked)", doc)
    # The old framing described the grid as something the script proposes: "put it at 5 % or 10 %".
    assert "put it at" not in doc


def test_whatif_does_not_sort_or_rank_by_outcome(whatif_tree):
    """D10 / SPEC §8: no ordering of scenarios by outcome (verdict, metrics, checks, cone)."""
    outcome = ("verdict", "metrics", "m1", "c1", "p5_t", "loss_prob", "vol", "mdd", "cone", "status", "checks",
               "after", "width", "p50_t", "p95_t")
    offenders = []
    for node in ast.walk(whatif_tree):
        if not isinstance(node, ast.Call):
            continue
        fn = ast.unparse(node.func)
        if fn in ("sorted", "max", "min") or fn.endswith((".sort", ".nlargest", ".nsmallest")):
            src = norm(ast.unparse(node))
            if any(re.search(rf"\b{re.escape(o)}\b", src) for o in outcome):
                offenders.append(ast.unparse(node)[:80])
    assert offenders == []


def test_whatif_pure_helpers_emit_no_ranking_or_option_fields(whatif):
    """Pure helpers on a tiny input (no network): outputs carry only facts, no option/ranking/fit fields."""
    import pandas as pd
    assets = pd.DataFrame({"asset_class": ["equity", "etf_bond", "etf_equity"]}, index=["AAA", "BBB", "CCC"])
    shares = whatif._shares({"AAA": 10.0, "BBB": 30.0, "CCC": 60.0, "ZZZ": 5.0}, assets)
    assert set(shares) == {"single_stocks", "bonds"}
    assert shares["single_stocks"] == pytest.approx(0.10, abs=1e-9)
    assert shares["bonds"] == pytest.approx(0.30, abs=1e-9)

    cone = whatif._cone({"cone": {k: [1.2, 3.7] for k in ("p5", "p25", "p50", "p75", "p95")}})
    assert set(cone) == {"p5", "p25", "p50", "p75", "p95"}
    assert cone["p5"] == [1, 4]

    assert whatif._val({"value": 3}) == 3
    assert whatif._val(4) == 4

    keys = _all_keys({"shares": shares, "cone": cone})
    assert {k.lower() for k in keys}.isdisjoint(FORBIDDEN_KEYS)


def test_whatif_module_names_have_no_option_or_ranking_vocabulary(whatif):
    """Names defined by the module (functions, constants) carry no option/ranking/fit vocabulary."""
    names = {n.lower().lstrip("_") for n in vars(whatif) if not n.startswith("__")}
    assert names.isdisjoint(FORBIDDEN_KEYS)
    assert not [n for n in names if re.search(r"(best|rank|option|alternative|fits?$|recommend|suggest)", n)]


def test_whatif_build_output_schema_and_narrative(whatif, tmp_path, monkeypatch):
    """whatif.json / universe.json from the real engine (offline, mock LLM, one target size to stay fast):
    scenarios keyed by the user-selectable inputs (size|funding), no ranking/option/fit field anywhere, no
    ordering by outcome, and every emitted narrative segment passes the widened §9.3 blocklist (D10 item 4)."""
    monkeypatch.setattr(whatif, "TARGETS", (5.0,))
    base = whatif.build(tmp_path)
    uni = json.loads((tmp_path / "universe.json").read_text(encoding="utf-8"))
    wi = json.loads((tmp_path / "whatif.json").read_text(encoding="utf-8"))

    assert {k.lower() for k in _all_keys(uni) | _all_keys(wi) | _all_keys(base)}.isdisjoint(FORBIDDEN_KEYS)
    assert "NVDA" in wi
    scen = wi["NVDA"]["scenarios"]
    assert scen, "no scenarios emitted"
    for key, s in scen.items():
        assert re.fullmatch(r"\d+\|(pro_rata_all|specific:[A-Z.]+)", key), key
        assert "verdict" in s or "error" in s
    # Scenario keys follow the input grid (size, then funding), not an outcome order.
    sizes = [int(k.split("|")[0]) for k in scen]
    assert sizes == sorted(sizes)
    # Universe is ordered by name (an input label), not by any outcome.
    names = [a["name"].lower() for a in uni["assets"]]
    assert names == sorted(names)

    for s in scen.values():
        for sec in s.get("sections", []):
            for seg in sec["segments"]:
                low = norm(seg["text"])
                assert [p for p in NORM_BLOCKLIST if p in low] == [], seg["text"]
                assert FORBIDDEN_WORD.search(seg["text"]) is None, seg["text"]
    assert not any(FORBIDDEN_WORD.search(f["label"]) for f in uni["base"]["fundings"])
