"""P2.SITE oracle: the static dashboard built by `deploy/build_site.py` (SPEC §12 UI requirements, §13, §19, §7.3 G1,
§17 steps 13, 14, 16; docs/spec-factors.md §20.4, §20.9; docs/spec-decisions.md D3, D7).

The site is built from REAL `pipeline.review` reports in mock mode into a temp dir; the tests read the written JSON
and `index.html`. No browser, no network, no clock. The page is vanilla JS, so the HTML is checked for render hooks
(function names and attributes this oracle names) and the new render functions are scanned for typed numbers.

Hooks the page must define (each `function <name>(...)`, defined once and used at least once elsewhere):
  renderStress, renderGoals, renderLookthrough, renderDerivedFrom, segmentTag, renderUnknowns, renderLimitations,
  renderValuesRules, assumptionBadge.
Attributes: `data-check` (a check row whose click shows its `derived_from` chain), `data-action="approve"` and
`data-action="reject"` (display-only buttons for parsed values rules).
"""
import hashlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
SCHEMA = json.loads((ROOT / "coherence_analyzer" / "schemas" / "report.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA)

FR_DISCLAIMER = ("Simulation éducative avec des profils fictifs. Ceci n'est pas un conseil financier personnalisé. "
                 "Aucune transaction n'est exécutée.")                                       # SPEC §9.4
STATUSES = {"pass", "violation", "warning", "info", "unknown"}
KINDS = {"fact", "assumption", "inference", "unknown"}                                       # SPEC §9.2 / §12
VERDICTS = {"coherent", "coherent_with_warnings", "incoherent"}
# persona key -> (expected verdict, how many reports); D3, D7 and tests/test_pipeline_offline.py measured context
EXPECTED = {"robert": "coherent", "robert-nvda": "incoherent", "amira": "incoherent", "mateo": "coherent"}
ADVISORY = ("tu devrais acheter", "tu devrais vendre", "je recommande", "nous recommandons", "you should buy",
            "you should sell", "we recommend", "i recommend", "vous devriez acheter", "vous devriez vendre",
            "je conseille", "nous conseillons", "i suggest you", "we advise", "achetez", "vendez", "buy now")
HOOKS = ("renderStress", "renderGoals", "renderLookthrough", "renderDerivedFrom", "segmentTag", "renderUnknowns",
         "renderLimitations", "renderValuesRules", "assumptionBadge")
NEW_RENDERERS = ("renderStress", "renderGoals", "renderLookthrough", "renderLimitations", "renderUnknowns",
                 "renderValuesRules", "renderDerivedFrom")
PAGE_LABELS = ("Synthèse", "Facteurs", "Profil", "Portefeuille", "Recherche", "Simulation", "Sources")  # existing tabs


# ---------------------------------------------------------------- tiny JS scanner (strings, templates, braces)

def _skip_string(s: str, i: int) -> int:
    q = s[i]
    i += 1
    while s[i] != q:
        i += 2 if s[i] == "\\" else 1
    return i + 1


def _scan_template(s: str, i: int, out: list[str]) -> int:
    """s[i] is a backtick. Appends the literal text (with `${...}` replaced by a placeholder) of this template and
    of every template nested in its expressions to `out`; returns the index after the closing backtick."""
    i += 1
    buf: list[str] = []
    slot = len(out)
    out.append("")
    while s[i] != "`":
        if s[i] == "\\":
            buf.append(s[i:i + 2])
            i += 2
        elif s.startswith("${", i):
            buf.append("\u00a7")
            i = _scan_block(s, i + 2, out)
        else:
            buf.append(s[i])
            i += 1
    out[slot] = "".join(buf)
    return i + 1


def _scan_block(s: str, i: int, out: list[str]) -> int:
    """Scan JS from just after an opening brace to just after its matching close brace."""
    depth = 1
    while depth:
        c = s[i]
        if c == "`":
            i = _scan_template(s, i, out)
            continue
        if c in "\"'":
            i = _skip_string(s, i)
            continue
        if s.startswith("//", i):
            i = s.index("\n", i)
            continue
        if s.startswith("/*", i):
            i = s.index("*/", i) + 2
            continue
        depth += (c == "{") - (c == "}")
        i += 1
    return i


def function_source(html: str, name: str) -> tuple[str, list[str]]:
    """(body source, template-literal texts) of `function <name>(...) {...}`; fails the test if not defined."""
    found = list(re.finditer(rf"function\s+{re.escape(name)}\s*\(", html))
    assert len(found) == 1, f"index.html must define `function {name}(` exactly once (found {len(found)})"
    i = found[0].end()
    depth = 1
    while depth:                                            # parameter list
        depth += (html[i] == "(") - (html[i] == ")")
        i += 1
    i = html.index("{", i) + 1
    texts: list[str] = []
    try:
        end = _scan_block(html, i, texts)
    except (IndexError, ValueError):
        pytest.fail(f"cannot scan the body of {name}: unbalanced quotes, braces or backticks")
    return html[i:end - 1], texts


def typed_digits(texts: list[str]) -> list[str]:
    """Digits left in template-literal text once tags/attributes, entities and `${}` slots are removed."""
    bad = []
    for t in texts:
        t = re.sub(r"<[^>]*>", " ", t)
        t = re.sub(r"&#?\w+;", " ", t)
        bad += re.findall(r"\S*\d\S*", t)
    return bad


# ---------------------------------------------------------------- the built site (one build for the whole module)

@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("site_build") / "site"
    watched = {p: (p.read_bytes() if p.exists() else None)
               for p in [ROOT / "data" / "meta" / "activity_tags.csv", *sorted((ROOT / "fixtures").rglob("*.json*"))]}
    saved_env = os.environ.get("COHERENCE_ANALYZER_STORE_DIR")
    sys.path.insert(0, str(DEPLOY))                         # build_site does `import whatif` (deploy/whatif.py)
    try:
        spec = importlib.util.spec_from_file_location("build_site_under_test", DEPLOY / "build_site.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        built = mod.build(out)
    finally:
        sys.path.remove(str(DEPLOY))
        if saved_env is None:
            os.environ.pop("COHERENCE_ANALYZER_STORE_DIR", None)
        else:
            os.environ["COHERENCE_ANALYZER_STORE_DIR"] = saved_env
    out = Path(built or out)
    index = json.loads((out / "reports" / "index.json").read_text())
    reports = {e["file"]: json.loads((out / "reports" / e["file"]).read_text()) for e in index}
    return {"out": out, "index": index, "reports": reports, "html": (out / "index.html").read_text(),
            "watched": watched}


def _key(rep: dict) -> str:
    """Persona key of a report: robert / robert-nvda / amira / mateo."""
    who = rep["profile_id"].removeprefix("persona-")
    if who == "robert" and Path(rep["site"]["portfolio_file"]).name == "portfolio_review.json":
        return "robert-nvda"
    return who


def by_key(site) -> dict[str, dict]:
    return {_key(r): r for r in site["reports"].values()}


# ---------------------------------------------------------------- JSON written by the build

def test_build_writes_the_site_files(site):
    """The build writes the dashboard, the design board and the report JSON the page reads (SPEC §12)."""
    out = site["out"]
    for rel in ("index.html", "design.html", "reports/index.json", "reports/universe.json", "reports/whatif.json"):
        assert (out / rel).is_file(), rel


def test_index_lists_every_complete_persona(site):
    """reports/index.json lists Robert current, Robert + NVDA, Amira and Mateo, each entry opening a real report."""
    idx = site["index"]
    assert isinstance(idx, list) and len(idx) == 4
    for e in idx:
        assert {"file", "label", "verdict", "profile"} <= set(e)
        assert isinstance(e["label"], str) and e["label"].strip()
        assert (site["out"] / "reports" / e["file"]).is_file()
    assert len({e["file"] for e in idx}) == 4 and len({e["label"] for e in idx}) == 4
    assert set(by_key(site)) == set(EXPECTED)               # four distinct (persona, portfolio) pairs
    # the two Robert reports keep the names the page already used
    assert {"robert-review.json", "robert-review-nvda.json"} <= {e["file"] for e in idx}


def test_index_entries_match_their_report(site):
    """Each index entry repeats the report's verdict and profile_coherence status (D3: three verdicts only)."""
    for e in site["index"]:
        rep = site["reports"][e["file"]]
        assert e["verdict"] == rep["verdict"] and e["verdict"] in VERDICTS
        assert e["profile"] == rep["profile_coherence"]["status"]


def test_every_report_is_schema_valid(site):
    """Every written report validates against coherence_analyzer/schemas/report.json (SPEC §13, §20.4, §20.9)."""
    assert len(site["reports"]) == 4
    for name, rep in site["reports"].items():
        errors = [f"{'/'.join(map(str, e.absolute_path))}: {e.message[:120]}" for e in VALIDATOR.iter_errors(rep)]
        assert not errors, (name, errors[:5])
        assert rep["flow"] == "C" and len(rep["sections"]) == 5


def test_persona_verdicts(site):
    """Verdicts are the pipeline's, unchanged by the P2 blocks: stress, G1 and look-through never move them (D3, D7)."""
    got = {k: r["verdict"] for k, r in by_key(site).items()}
    assert got == EXPECTED


def test_robert_with_nvda_is_a_different_portfolio(site):
    """The NVDA review is the portfolio_review.json portfolio (D7 point 5): NVDA at 10 %, the current one has less."""
    reps = by_key(site)
    nvda, cur = reps["robert-nvda"]["site"], reps["robert"]["site"]
    assert nvda["portfolio_file"] != cur["portfolio_file"]
    assert nvda["weights_pct"]["NVDA"] == pytest.approx(10.0, abs=1e-9)
    assert cur["weights_pct"].get("NVDA", 0.0) < nvda["weights_pct"]["NVDA"]
    assert reps["robert-nvda"]["run_id"] != reps["robert"]["run_id"]


def test_site_extras_belong_to_their_persona(site):
    """`site` display extras are computed per persona, not copied from Robert (profile card, label, limits)."""
    for key, rep in by_key(site).items():
        s = rep["site"]
        who = key.split("-")[0]
        assert {"label", "as_of", "value_cad", "weights_pct", "limits", "profile_card", "portfolio_file"} <= set(s)
        assert who in s["profile_card"]["name"].lower(), (key, s["profile_card"]["name"])
        assert who in s["label"].lower()
        assert sum(s["weights_pct"].values()) == pytest.approx(100.0, abs=1e-6)
        assert isinstance(s["limits"], dict) and s["limits"]
        assert rep["portfolio_id"] and rep["profile_id"] == f"persona-{who}"


def test_stress_block_is_present_and_information_only(site):
    """P2 `stress` (SPEC §17 step 16): rate scenarios, status info, `sourced` flag, labelled assumptions, no limits."""
    for key, rep in by_key(site).items():
        st = rep["stress"]
        assert st["status"] == "info" and st["kind"] == "rate_scenario" and st["feeds_limits"] is False, key
        assert isinstance(st["sourced"], bool)
        assert st["assumptions"] and all(isinstance(a, str) and a.strip() for a in st["assumptions"])
        # SPEC-AMBIGUITY: "sourced: false when durations come from the ASSUMED table"; read strictly: unsourced
        # stress must say ASSUMED in its labelled assumptions so the badge has something to show.
        if not st["sourced"]:
            assert any("ASSUMED" in a for a in st["assumptions"]), key
        pars = [s for s in st["scenarios"] if s["kind"] == "parallel"]
        assert any(s["shock_bp"] == 100 for s in pars), "the +100 bp shock of SPEC §17 step 16"
        assert all(isinstance(s["impact"], (int, float)) and not isinstance(s["impact"], bool)
                   for s in st["scenarios"])
        # stress is not a check and not in the verdict (invariants 2, 3)
        assert all(c["id"] not in ("STRESS", "R1") and c.get("source") != "rule:stress" for c in rep["checks_current"])


def test_goals_block_g1_is_info_with_assumptions(site):
    """G1 (SPEC §7.3): goal probabilities, always `info`, with assumption labels, for each persona with goals."""
    for key, rep in by_key(site).items():
        g = rep["goals"]
        assert g["goal_probs"], f"{key}: every persona fixture declares at least one goal"
        assert g["horizon_months"] >= 1 and g["monthly_contribution"] >= 0
        assert g["assumptions"] and all(isinstance(a, str) and a.strip() for a in g["assumptions"])
        assert g["checks"] and all(c["id"] == "G1" and c["status"] == "info" for c in g["checks"])  # invariant 3
        by_subject = {c["subject"]: c for c in g["checks"]}
        for gp in g["goal_probs"]:
            assert 0.0 <= gp["prob"] <= 1.0
            c = by_subject[gp["name"]]                   # a goal past the horizon has a check but no probability
            assert c["value"] == pytest.approx(gp["prob"], abs=1e-9)
            assert c["assumptions"], "assumption labels travel with each G1 check"
        # the verdict is derived from the section statuses only; G1 stays outside it (spec-factors §20.4)
        assert not any(c["id"] == "G1" for c in rep["checks_current"])


def test_lookthrough_flag_and_its_unknowns(site):
    """`metrics_current.lookthrough` is a boolean; without look-through the ETF parts of H6 and H10 are `unknown`
    (SPEC §7.3 H6/H10, §19, spec-factors §20.9 context; D3: listed, never a verdict change)."""
    for key, rep in by_key(site).items():
        flag = rep["metrics_current"]["lookthrough"]
        assert isinstance(flag, bool), key
        if not flag:
            ids = {u["id"] for u in rep["unknowns"]}
            assert {"H6", "H10"} <= ids, (key, ids)
            assert any("look-through" in a.lower() for a in rep["assumptions"]), key


def test_unknowns_are_listed_apart_and_never_change_the_verdict(site):
    """D3 / SPEC §7.5: `unknowns` = the `unknown` checks; Mateo is `coherent` with a non-empty list."""
    for key, rep in by_key(site).items():
        assert rep["unknowns"], key
        assert all(u["status"] == "unknown" for u in rep["unknowns"])
        listed = [c for c in rep["checks_current"] if c["status"] == "unknown"]
        assert sorted((u["id"], u.get("subject") or "") for u in rep["unknowns"]) == \
               sorted((c["id"], c.get("subject") or "") for c in listed)
    mateo = by_key(site)["mateo"]
    assert mateo["verdict"] == "coherent" and mateo["unknowns"]


def test_every_check_carries_a_derived_from_chain(site):
    """SPEC §12: clicking a check shows its `derived_from` chain, so every check has one (SPEC §7.3, §13)."""
    for key, rep in by_key(site).items():
        assert rep["checks_current"], key
        for c in rep["checks_current"]:
            assert c["status"] in STATUSES
            assert isinstance(c["derived_from"], list) and c["derived_from"], (key, c["id"])
            assert all(isinstance(x, str) and x for x in c["derived_from"])
        for u in rep["unknowns"]:
            assert u["derived_from"], (key, u["id"])
        for gc in rep["goals"]["checks"]:
            assert gc["derived_from"], (key, "G1")


def test_every_explanation_segment_has_a_tag_and_a_ref(site):
    """SPEC §12 / §9.2: fact / assumption / inference / unknown on every explanation segment, each with refs."""
    for key, rep in by_key(site).items():
        segs = list(rep["explanation"]["segments"])
        for sec in rep["sections"]:
            segs += sec["narrative"]["segments"]
        assert segs, key
        for sg in segs:
            assert sg["kind"] in KINDS, (key, sg)
            assert sg["text"].strip() and sg["refs"], (key, sg)
        assert {sg["kind"] for sg in segs} <= KINDS


def test_disclaimer_is_the_fixed_text(site):
    """SPEC §9.4: the report carries the fixed FR disclaimer verbatim; the page shows it from the report."""
    for key, rep in by_key(site).items():
        assert rep["disclaimer"] == FR_DISCLAIMER, key
        assert rep["explanation"]["disclaimer"] == FR_DISCLAIMER, key


def test_known_limitations_come_from_the_report(site):
    """SPEC §19: the report's `assumptions` carry the limitations the page lists (window, drift, survivorship, no
    look-through); the two non-numeric ones (noisy behaviour, team-calibrated limits vs AMF/CIRO) are in the report or
    the page text. The page may not type them as numbers (checked in the HTML tests)."""
    for key, rep in by_key(site).items():
        text = " ".join(rep["assumptions"])
        low = text.lower()
        assert "survivorship" in low, key
        assert "drift" in low and "assumed" in low, key
        assert "data window" in low, key
        if not rep["metrics_current"]["lookthrough"]:
            assert "look-through" in low, key
        # SPEC-AMBIGUITY: §19 bullets 4-5 are not in `assumptions` today (and coherence_analyzer/ is not ours to
        # change); strict reading: they must still be on the page, from either source.
        shown = text + " " + site["html"]
        assert "AMF" in shown and "CIRO" in shown, key
        assert re.search(r"noisy|bruit", shown, re.I), key


def test_build_leaves_repo_state_alone(site):
    """The build writes only into its out dir: fixtures untouched, no `activity_tags.csv` written (invariant 6)."""
    for path, before in site["watched"].items():
        now = path.read_bytes() if path.exists() else None
        assert (None if now is None else hashlib.sha256(now).hexdigest()) == \
               (None if before is None else hashlib.sha256(before).hexdigest()), path
    assert not (site["out"] / "reports" / "activity_tags.csv").exists()


# ---------------------------------------------------------------- index.html: render hooks

def _body(site, name: str) -> str:
    return function_source(site["html"], name)[0]


def test_page_defines_and_uses_every_render_hook(site):
    """Each P2 / SPEC §12 render hook is a function defined once and called from elsewhere in the page."""
    for name in HOOKS:
        function_source(site["html"], name)
        assert len(re.findall(rf"\b{name}\b", site["html"])) >= 2, f"{name} is defined but never used"


def test_page_keeps_the_tabs_the_picker_and_reads_only_report_json(site):
    """Existing look kept: the seven French tabs, the report picker fed by reports/index.json, and fetches limited
    to reports/ (static site, vanilla JS reading reports/*.json)."""
    html = site["html"]
    for label in PAGE_LABELS:
        assert f'"{label}"' in html, label
    assert "reports/index.json" in html and 'id="pick"' in html
    targets = re.findall(r"fetch\(\s*[\"'`]([^\"'`]*)", html)
    assert targets and all(t.startswith("reports/") for t in targets), targets
    assert "<script src=" not in html, "no external script: vanilla JS only"


def test_stress_renderer_shows_scenarios_and_the_assumption_badge(site):
    """`stress`: rate-scenarios table from `scenarios`, `sourced` flag and `assumptions` shown through the shared
    assumption badge, status info (SPEC §17 step 16, report schema `stress`)."""
    body = _body(site, "renderStress")
    for token in ("scenarios", "shock_bp", "impact", "sourced", "assumptions", "status"):
        assert token in body, token
    assert "assumptionBadge(" in body
    assert "<table" in body and "<th" in body
    assert re.search(r"\.stress\b", site["html"]), "the page reads report.stress"


def test_goals_renderer_shows_g1_probabilities_as_info_with_assumptions(site):
    """`goals`: G1 probabilities (`goal_probs`) always `info`, each with its assumption labels (SPEC §7.3 G1)."""
    body = _body(site, "renderGoals")
    for token in ("goal_probs", "prob", "name", "amount", "assumptions"):
        assert token in body, token
    assert "assumptionBadge(" in body
    assert re.search(r"\.goals\b", site["html"]), "the page reads report.goals"
    # info only: no violation / warning styling decided inside the G1 renderer (invariant 3)
    assert not re.search(r"s-(violation|warning)|c-(violation|warning)", body)


def test_lookthrough_renderer_says_etf_sectors_are_not_looked_through(site):
    """`metrics.lookthrough`: when false the page says ETF sector exposure is not looked through and that the ETF
    parts of H6 / H10 are unknown (SPEC §19, §7.3). The ids come from `unknowns`, not from typed text."""
    body = _body(site, "renderLookthrough")
    assert "lookthrough" in body and "unknowns" in body
    assert re.search(r"!\s*[\w.\[\]\"']*lookthrough|lookthrough\s*(===?|!==?)\s*(false|true)", body), \
        "branches on the flag"
    low = body.lower()
    for word in ("fnb", "secteur", "inconnu"):
        assert word in low, word
    assert re.search(r"metrics_current", site["html"]) and "lookthrough" in site["html"]


def test_derived_from_chain_is_clickable_on_each_check(site):
    """SPEC §12: a check row (`data-check`) opens its `derived_from` chain via `renderDerivedFrom`."""
    html = site["html"]
    assert "data-check" in html
    body = _body(site, "renderDerivedFrom")
    assert "derived_from" in body
    assert re.search(r"addEventListener\(\s*[\"']click[\"']", html)
    assert re.search(r"\[data-check\]|dataset\.check", html), "click handler bound on the data-check rows"
    assert "renderDerivedFrom(" in html.replace(f"function renderDerivedFrom(", "")


def test_segment_tags_on_every_explanation_segment(site):
    """SPEC §12: `segmentTag` labels each segment fact / assumption / inference / unknown, in the report-level
    explanation and in each factor narrative."""
    html = site["html"]
    body = _body(site, "segmentTag")
    assert "kind" in body or "KIND" in body
    for kind in KINDS:
        assert re.search(rf"\b{kind}\b", html), kind
    for label in ("fait", "hypothèse", "inférence", "inconnu"):
        assert label in html, label
    # SPEC-AMBIGUITY: "every explanation segment" read strictly = both places that list segments
    assert len(re.findall(r"\bsegmentTag\(", html)) >= 3, "definition + explanation segments + narrative segments"
    assert "explanation" in html and "narrative" in html


def test_unknowns_list_is_titled_what_we_cannot_verify(site):
    """D3 / SPEC §7.5: `unknowns` is its own list titled « Ce qu'on ne peut pas vérifier », fed by report.unknowns."""
    html = site["html"]
    body = _body(site, "renderUnknowns")
    assert "unknowns" in body or "unk" in body
    assert "Ce qu'on ne peut pas vérifier" in html
    assert re.search(r"\.unknowns\b", html)


def test_limitations_renderer_reads_the_report_assumptions(site):
    """SPEC §19: `renderLimitations` lists the limitations from `assumptions`; no figure is typed in the page."""
    body, texts = function_source(site["html"], "renderLimitations")
    assert "assumptions" in body
    assert re.search(r"Limites", site["html"]), "a French title for the known limitations"
    assert not typed_digits(texts)


def test_values_rules_have_display_only_approve_and_reject_buttons(site):
    """SPEC §12 (N3): approve / reject buttons for parsed values rules; the static page does not decide (the API
    does), so the renderer makes no request."""
    html = site["html"]
    body = _body(site, "renderValuesRules")
    assert re.search(r"data-action=\\?[\"']approve", html) and re.search(r"data-action=\\?[\"']reject", html)
    assert "approved_by_user" in html or "S4" in body
    assert "fetch(" not in body and "XMLHttpRequest" not in body


def test_disclaimer_is_shown_verbatim_from_the_report(site):
    """SPEC §9.4: the footer prints `rep.disclaimer`; any literal disclaimer in the page equals the fixed text."""
    html = site["html"]
    assert re.search(r"\$\{\s*esc\(\s*rep\.disclaimer\s*\)\s*\}|rep\.disclaimer", html)
    for found in re.findall(r"Simulation éducative[^<`\"]*", html):
        assert found.strip() == FR_DISCLAIMER


def test_new_renderers_type_no_numbers(site):
    """Every number shown comes from the report JSON: no digit in the literal text of the P2 / §12 renderers
    (tags, attributes, entities and `${}` slots are ignored)."""
    for name in NEW_RENDERERS:
        _, texts = function_source(site["html"], name)
        assert not typed_digits(texts), (name, typed_digits(texts)[:5])


def test_page_has_no_advisory_wording(site):
    """CLAUDE.md: no buy/sell recommendation anywhere in the page (SPEC §9.3 blocklist, FR + EN)."""
    low = site["html"].lower()
    assert not [p for p in ADVISORY if p in low]
