"""P2.D10C oracle: D10 "Describe, never prescribe" on the dashboard code path (docs/spec-decisions.md D10; SPEC inv. 7,
§8, §9.2, §9.3 item 3, §12 UI, §13, §18; docs/spec-factors.md §20.7).

Scope: `deploy/site/index.html` (reads `headroom`, never `alternatives`), `deploy/preview_report.py` (no `option`
segments, no "within your own limits ... would fit up to" text, headroom line = §8 template, flow B only) and
`deploy/build_site.py` (built reports carry `headroom`, not `alternatives`).

Offline and deterministic: COHERENCE_ANALYZER_LLM=mock, the HTML is read as text, the builds write to tmp dirs and use
the repo's fixed seeds. No network, no clock, no browser.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

import pytest

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
INDEX = DEPLOY / "site" / "index.html"
PREVIEW = DEPLOY / "preview_report.py"
BUILD_SITE = DEPLOY / "build_site.py"
SCHEMA = ROOT / "coherence_analyzer" / "schemas" / "report.json"

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
DISCLAIMER_EN = ("Educational simulation with fictional profiles. This is not personalised financial advice. "
                 "No transaction is executed.")
# The Recherche refusal message is exempt from the blocklist (task note); it starts with this sentence.
REFUSAL_FR = "Je ne peux pas classer ni recommander d'actifs."

# Keys that would carry an alternative, a ranking or a suggestion (D10 items 1-2).
FORBIDDEN_KEYS = {"alternatives", "alternative", "options", "option", "best", "fits", "rank", "ranking",
                  "recommended", "recommendation", "suggested", "suggestion", "next_step", "funding_label"}
SEGMENT_KINDS = {"fact", "assumption", "inference", "unknown"}          # SPEC §9.2 (`option` removed by D10 item 2)
HEADROOM_EN = re.compile(r"With the funding you gave \((?P<fund>[^)]+)\), this trade crosses your limit "
                         r"(?P<hid>H\d+) above (?P<x>\d+(?:\.\d+)?) ?%\.")      # SPEC §8, EN template


def norm(s: str) -> str:
    """Lowercase, strip accents, unify apostrophes and whitespace."""
    s = unicodedata.normalize("NFKD", s.replace("\u2019", "'").replace("\xa0", " ").replace("\u202f", " "))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", s)


NORM_BLOCKLIST = tuple(norm(p) for p in BLOCKLIST)


def blocklist_hits(s: str) -> list[str]:
    low = norm(s)
    return [p for p in NORM_BLOCKLIST if p in low]


def _load(name: str, path: Path):
    sys.path.insert(0, str(DEPLOY))        # build_site does `import whatif`; preview_report is self-contained
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(DEPLOY))
    return mod


def all_keys(obj) -> set[str]:
    out: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(str(k))
            out |= all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= all_keys(v)
    return out


def strings(obj, skip_keys: frozenset[str] = frozenset({"disclaimer"})) -> list[str]:
    """Every string value in a JSON tree, minus the subtrees under `skip_keys` (the fixed disclaimer)."""
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k not in skip_keys:
                out += strings(v, skip_keys)
    elif isinstance(obj, list):
        for v in obj:
            out += strings(v, skip_keys)
    elif isinstance(obj, str):
        out.append(obj)
    return out


# --------------------------------------------------------------------------------------------- index.html

@pytest.fixture(scope="module")
def page() -> str:
    return INDEX.read_text(encoding="utf-8")


def _visible_source(page: str) -> str:
    """index.html with the fixed disclaimer and the Recherche refusal sentence removed."""
    # SPEC-AMBIGUITY: the Recherche chip « Lequel est le meilleur pour moi? » is a sample USER question that triggers
    # the refusal message (task note exempts that message); it is the user's voice, not product advice, so it is
    # exempt from the ranking-vocabulary scan too.
    return page.replace(DISCLAIMER_FR, " ").replace(REFUSAL_FR, " ").replace("Lequel est le meilleur pour moi?", " ")


def test_page_reads_headroom_not_alternatives(page):
    """D10 item 2 / SPEC §13: the page reads the report key `headroom` and no longer mentions `alternatives`."""
    low = page.lower()
    assert re.search(r"\.headroom\b|\[\s*[\"']headroom[\"']\s*\]|\{[^}]*\bheadroom\b[^}]*\}\s*=\s*", page), \
        "index.html must read `headroom` from the report"
    assert "alternatives" not in low
    assert "alternative" not in low


def test_page_has_no_option_segment_kind_or_tag(page):
    """D10 item 2: explanation segment kind `option` is removed: not in the KIND map, no `k-option` style."""
    m = re.search(r"const KIND\s*=\s*\{(?P<body>[^}]*)\}", page)
    assert m, "KIND map (segment kind -> label) not found"
    kinds = set(re.findall(r"(\w+)\s*:", m.group("body")))
    assert kinds == SEGMENT_KINDS
    assert "k-option" not in page
    assert not re.search(r"kind\s*[=!]==?\s*[\"']option[\"']", page)
    # HTML <option>/<optgroup> of the selects are widgets, not wording: strip them, nothing else may say "option".
    stripped = re.sub(r"</?(option|optgroup)\b", " ", page, flags=re.I)
    stripped = re.sub(r"\bconst opt\b|\bopt\(", " ", stripped)
    assert not re.search(r"\boptions?\b", stripped, re.I)


def test_page_headroom_line_uses_spec_8_fr_template_wording(page):
    """SPEC §8 FR template: « Avec le financement indiqué (ZST.TO), la transaction franchit votre limite H9 au-delà
    de X %. » (funding, check id and X come from the headroom object; the fixed wording is in the page)."""
    low = norm(page)
    for frag in ("avec le financement indique (", "), la transaction franchit votre limite ", " au-dela de "):
        assert frag in low, frag
    at = low.index("la transaction franchit votre limite")
    # SPEC-AMBIGUITY: "the dashboard reads headroom" is not tied to a function name; stricter reading: the template
    # text sits next to code that reads `headroom` (so it is built from the report, not typed as a constant).
    assert "headroom" in low[max(0, at - 1500):at + 400]


def test_page_headroom_line_is_conditional_on_the_report(page):
    """SPEC §9.2 / §20.7: the headroom line is flow B only: the page renders it only when `headroom` is non-null."""
    assert "la transaction franchit votre limite" in page
    at = page.index("la transaction franchit votre limite")
    window = page[max(0, at - 1500):at]
    assert re.search(r"headroom\s*(\?|&&|\)|!==?\s*null|!=\s*null)|if\s*\(\s*!?\s*[\w.]*headroom|"
                     r"[\w.]*headroom\s*\?|[\w.]*headroom\s*&&", window), "headroom line must be guarded"


def test_page_headroom_line_has_no_action_framing(page):
    """D10 items 1-2: the headroom is a fact; the text around the template carries no 'jusqu'à' / 'tient dans' framing."""
    low = norm(page)
    at = low.index("la transaction franchit votre limite")
    around = low[max(0, at - 300):at + 300]
    for bad in ("jusqu'a", "tient dans vos limites", "dans vos limites", "peut aller", "pourrait"):
        assert bad not in around, bad


def test_page_text_passes_the_advisory_blocklist(page):
    """SPEC §9.3 item 3 / D10 item 4: no advisory phrase anywhere in the page (text, templates, attributes), apart
    from the fixed disclaimer and the Recherche refusal message."""
    assert blocklist_hits(_visible_source(page)) == []


def test_page_does_not_rank_or_propose_choices(page):
    """D10 item 1: no 'best / ideal / next step' vocabulary in the page's user-facing strings."""
    low = norm(_visible_source(page))
    for pat in (r"\ble meilleur\b", r"\bla meilleure\b", r"\bmieux adapte", r"\bplus adapte", r"\bideal\b",
                r"\bprochaine etape\b", r"\bnext step\b", r"\bbest\b", r"\bmost suitable\b", r"\bfits? within\b",
                r"\bce qui tient dans vos limites\b", r"\bwould fit\b"):
        assert re.search(pat, low) is None, pat


# --------------------------------------------------------------------------------------------- preview_report.py

@pytest.fixture(scope="module")
def preview(tmp_path_factory) -> dict[str, dict]:
    """deploy/preview_report.py `build()` into a temp dir (real engine, fixed seed, no LLM); {name: report}."""
    out = tmp_path_factory.mktemp("preview_reports")
    mod = _load("preview_report_under_test", PREVIEW)
    paths = mod.build(out)
    reports = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in paths}
    assert {"robert-nvda", "robert-review", "robert-review-nvda"} <= set(reports)
    return reports


@pytest.fixture(scope="module")
def flow_b(preview) -> dict:
    return preview["robert-nvda"]


def test_preview_source_has_no_alternatives_options_or_ranking():
    """D10 item 2 / SPEC §8: the preview code no longer builds `alternatives`, `option` segments or the 'would fit
    up to' sentence, and does not sort anything by `max_weight_pct`."""
    src = PREVIEW.read_text(encoding="utf-8")
    tree = ast.parse(src)
    consts = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    low = [norm(c) for c in consts]
    assert not [c for c in low if "alternatives" in c or c == "option" or "would fit" in c
                or "within your own limits" in c or "binding check:" in c]
    assert "alternatives" not in {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    ranked = [ast.unparse(n)[:80] for n in ast.walk(tree) if isinstance(n, ast.Call)
              and ast.unparse(n.func) in ("sorted", "max", "min") and "max_weight_pct" in ast.unparse(n)]
    assert ranked == []


def test_preview_reports_carry_headroom_and_no_alternatives(preview):
    """SPEC §13 / D10 item 2: every report has the key `headroom` (null | object) and no `alternatives` key, nor
    any option / ranking / suggestion key anywhere in the tree."""
    for name, rep in preview.items():
        assert "headroom" in rep, name
        assert "alternatives" not in rep, name
        assert rep["headroom"] is None or isinstance(rep["headroom"], dict), name
        assert all_keys(rep).isdisjoint(FORBIDDEN_KEYS), (name, all_keys(rep) & FORBIDDEN_KEYS)


def test_preview_headroom_is_flow_b_only(preview):
    """SPEC §8, §9.2, §20.7: headroom exists only for a proposed trade (flow B); flow C reports have headroom null
    and no explanation segment states a trade crossing a limit."""
    assert preview["robert-nvda"]["flow"] == "B"
    assert isinstance(preview["robert-nvda"]["headroom"], dict)
    for name in ("robert-review", "robert-review-nvda"):
        rep = preview[name]
        assert rep["flow"] == "C" and rep["intent"] is None
        assert rep["headroom"] is None, name
        assert not [s for s in rep["explanation"]["segments"] if "this trade crosses your limit" in s["text"]]


def test_preview_headroom_object_is_the_users_own_funding_only(flow_b):
    """SPEC §8 step 4 / D10 item 2: `{funding, max_weight_pct, binding_check, metrics}` (or `none_fit` +
    `binding_checks`) where `funding` is the user's funding unchanged; Robert + NVDA from ZST.TO binds below 5 % on
    H9 (D2)."""
    h, intent = flow_b["headroom"], flow_b["intent"]
    assert h["funding"] == intent["funding"]
    assert intent["funding"]["ticker"] == "ZST.TO"
    if h.get("none_fit"):
        assert h["none_fit"] is True and h["binding_checks"] and "max_weight_pct" not in h
        return
    assert {"funding", "max_weight_pct", "binding_check", "metrics"} <= set(h)
    assert 0 <= h["max_weight_pct"] < 5.0                       # D2: below 5 %, not the §8 "5 %" estimate
    assert h["max_weight_pct"] < intent["target_weight_pct"]    # the proposed trade is incoherent: it is above
    # SPEC-AMBIGUITY: D2 says "binding H9 for funding from ZST.TO"; stricter reading: exactly the id "H9".
    assert h["binding_check"] == "H9"
    assert isinstance(h["metrics"], dict) and h["metrics"]


def test_preview_what_if_grid_uses_only_the_users_funding(flow_b):
    """D10 item 2: the pro_rata_* / class funding modes tried on its own are dropped; any grid kept in the report
    is for the funding the user stated."""
    grid = flow_b.get("whatif")
    # SPEC-AMBIGUITY: D10 drops the self-chosen funding modes from the resizer; stricter reading: the preview's
    # what-if grid (stand-in for the resizer) is dropped or limited to the user's funding too.
    if not grid:
        return
    assert len(grid) == 1
    for entry in grid.values():
        assert entry["funding"] == flow_b["intent"]["funding"]
    assert "pro_rata" not in json.dumps(grid)


def test_preview_explanation_has_no_option_segments(preview):
    """D10 item 2 / SPEC §9.2: segment kinds are fact|assumption|inference|unknown only; no 'would fit up to' or
    'within your own limits' text in any report."""
    for name, rep in preview.items():
        for seg in rep["explanation"]["segments"]:
            assert seg["kind"] in SEGMENT_KINDS, (name, seg)
            assert seg["refs"], (name, seg)
        text = norm(json.dumps(rep, ensure_ascii=False))
        assert "would fit" not in text and "within your own limits" not in text, name
        assert "binding check:" not in text, name


def test_preview_headroom_segment_is_the_spec_8_template_and_last(flow_b):
    """SPEC §8 EN template and §9.2 order: « With the funding you gave (ZST.TO), this trade crosses your limit H9
    above X %. » as one `fact` segment citing `headroom`, after the 'unknown' segment (disclaimer excluded)."""
    h, segs = flow_b["headroom"], flow_b["explanation"]["segments"]
    hits = [s for s in segs if "this trade crosses your limit" in s["text"]]
    assert len(hits) == 1
    seg = hits[0]
    assert seg["kind"] == "fact" and "headroom" in seg["refs"]
    assert segs[-1] is seg
    assert [s["kind"] for s in segs[:-1]].count("unknown") >= 1
    if h.get("none_fit"):
        return
    m = HEADROOM_EN.fullmatch(seg["text"])
    assert m, seg["text"]
    assert m["fund"] == "ZST.TO" and m["hid"] == h["binding_check"]
    assert float(m["x"]) == pytest.approx(h["max_weight_pct"], abs=0.05)     # SPEC §9.3 item 2 tolerance for %


def test_preview_user_facing_strings_pass_the_blocklist(preview):
    """SPEC §9.3 item 3 / D10 item 4: no advisory phrase in any string of any preview report, apart from the fixed
    disclaimer (FR/EN)."""
    for name, rep in preview.items():
        hits = [(s[:80], blocklist_hits(s)) for s in strings(rep) if blocklist_hits(s)]
        assert hits == [], name


def test_preview_text_has_no_option_or_alternative_wording(preview):
    """D10 item 2 / SPEC §8: 'options' and 'alternatives' are not allowed wording in explanation, preview banner,
    sections or headline."""
    for name, rep in preview.items():
        texts = strings(rep["explanation"]) + strings(rep.get("preview", {})) + strings(rep.get("sections", []))
        bad = [t[:80] for t in texts if re.search(r"\b(options?|alternatives?)\b", t, re.I)]
        assert bad == [], name


# --------------------------------------------------------------------------------------------- build_site.py

@pytest.fixture(scope="module")
def site(tmp_path_factory) -> dict:
    """One real build (mock LLM, temp store); the What-if grid is cut to one target size to stay fast."""
    out = tmp_path_factory.mktemp("d10c_site") / "site"
    saved_env = os.environ.get("COHERENCE_ANALYZER_STORE_DIR")
    saved_mod = sys.modules.get("whatif")
    sys.path.insert(0, str(DEPLOY))
    try:
        wi = _load("whatif", DEPLOY / "whatif.py")
        wi.TARGETS = (5.0,)
        sys.modules["whatif"] = wi                       # build_site does `import whatif`
        mod = _load("build_site_d10c", BUILD_SITE)
        built = Path(mod.build(out) or out)
    finally:
        sys.path.remove(str(DEPLOY))
        if saved_mod is None:
            sys.modules.pop("whatif", None)
        else:
            sys.modules["whatif"] = saved_mod
        if saved_env is None:
            os.environ.pop("COHERENCE_ANALYZER_STORE_DIR", None)
        else:
            os.environ["COHERENCE_ANALYZER_STORE_DIR"] = saved_env
    rdir = built / "reports"
    index = json.loads((rdir / "index.json").read_text(encoding="utf-8"))
    reports = {e["file"]: json.loads((rdir / e["file"]).read_text(encoding="utf-8")) for e in index}
    return {"out": built, "index": index, "reports": reports,
            "universe": json.loads((rdir / "universe.json").read_text(encoding="utf-8")),
            "whatif": json.loads((rdir / "whatif.json").read_text(encoding="utf-8")),
            "html": (built / "index.html").read_text(encoding="utf-8")}


def test_schema_has_headroom_and_no_alternatives():
    """SPEC §13 / D10 item 2: the report schema declares `headroom` (null | object) and no longer `alternatives`;
    `headroom` is required."""
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert "headroom" in schema["properties"] and "headroom" in schema["required"]
    assert "alternatives" not in schema["properties"] and "alternatives" not in schema["required"]
    hr = schema["properties"]["headroom"]
    branches = hr.get("oneOf") or hr.get("anyOf") or [hr]  # `type: [null, object]` or `oneOf` branches: same meaning
    types: set = set()
    for b in branches:
        bt = b.get("type")
        types |= set(bt if isinstance(bt, list) else [bt])
    assert types == {"null", "object"}


def test_built_reports_carry_headroom_null_for_flow_c(site):
    """SPEC §13, §8: every report the dashboard opens is a flow C review: `headroom` present and null, no
    `alternatives` key, no option / ranking / suggestion key anywhere in the report."""
    assert len(site["reports"]) == 4
    for name, rep in site["reports"].items():
        assert rep["flow"] == "C", name
        assert "headroom" in rep and rep["headroom"] is None, name
        assert "alternatives" not in rep, name
        assert all_keys(rep).isdisjoint(FORBIDDEN_KEYS), (name, all_keys(rep) & FORBIDDEN_KEYS)


def test_built_reports_validate_against_the_schema(site):
    """SPEC §13: each built report is schema-valid with the D10 key (`headroom`)."""
    from jsonschema import Draft202012Validator
    validator = Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))
    for name, rep in site["reports"].items():
        errors = [e.message for e in validator.iter_errors(rep)]
        assert errors == [], (name, errors[:3])


def test_built_explanations_have_only_fact_assumption_inference_unknown_segments(site):
    """D10 item 2 / SPEC §9.2: no `option` segment kind in any built explanation or factor section; flow C reports
    carry no trade headroom sentence."""
    for name, rep in site["reports"].items():
        segs = list(rep["explanation"]["segments"])
        for sec in rep.get("sections", []):
            segs += sec.get("segments", [])
        assert segs, name
        assert {s["kind"] for s in segs} <= SEGMENT_KINDS, name
        assert not [s for s in segs if "this trade crosses your limit" in s["text"]
                    or "la transaction franchit votre limite" in s["text"]], name


def test_built_text_passes_the_advisory_blocklist(site):
    """SPEC §9.3 item 3 / D10 item 4: no advisory phrase in the built report text, the picker labels or the What-if
    narration (case/accent-insensitive), apart from the fixed disclaimer."""
    # SPEC-AMBIGUITY: user-written notes are the user's own words, not product output; they are not scanned.
    skip = frozenset({"disclaimer", "notes"})
    bad = [(name, s[:80]) for name, rep in site["reports"].items() for s in strings(rep, skip) if blocklist_hits(s)]
    assert bad == []
    assert [e["label"] for e in site["index"] if blocklist_hits(e["label"])] == []
    wi_bad = [s[:80] for s in strings(site["whatif"], skip) if blocklist_hits(s)]
    assert wi_bad == []


def test_built_whatif_and_universe_have_no_option_or_ranking_fields(site):
    """D10 items 1-2: the precomputed files expose no alternatives / ranking / suggestion keys."""
    assert all_keys(site["whatif"]).isdisjoint(FORBIDDEN_KEYS)
    assert all_keys(site["universe"]).isdisjoint(FORBIDDEN_KEYS)
    assert all_keys(site["index"]).isdisjoint(FORBIDDEN_KEYS)


def test_built_dashboard_is_the_headroom_page(site):
    """The page copied into the site is the one that reads `headroom` and never `alternatives` (SPEC §13)."""
    assert site["html"] == INDEX.read_text(encoding="utf-8")
    assert "headroom" in site["html"] and "alternatives" not in site["html"].lower()


def test_build_site_source_has_no_alternatives_or_options():
    """D10 item 2: build_site.py neither builds nor passes through `alternatives` / `option` wording."""
    src = BUILD_SITE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    consts = [norm(n.value) for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert not [c for c in consts if "alternatives" in c or c == "option" or "would fit" in c]
    assert not [c for c in consts if blocklist_hits(c)]
