"""P2.SITE oracle: the static dashboard built by `deploy/build_site.py` (SPEC §12 UI requirements, §13, §19, §7.3 G1,
§17 steps 13, 14, 16; docs/spec-factors.md §20.4, §20.9; docs/spec-decisions.md D3, D7).

Retarget note (P8.UNIFY): `deploy/site/index.html` (French, single-page) is being retired in favour of
`deploy/site/app.html` (English, multi-screen, modular JS under `deploy/site/js/`). This file used to scan
`index.html` as one blob of inline JS; it now scans the real modules app.html loads (`js/core/shell.js`,
`js/screens/summary/summary.js`, `js/screens/pillars/{objectives,preferences}.js`), reading the English (`en-CA`)
reports build (`reports/en-CA/`) instead of the French one. Same test names, same guarantees, except where the
new architecture genuinely changes how a guarantee is met (documented per test below) or where a guarantee had no
surface at all in app.html and was closed by a small, additive port of the retired renderer (stress / unknowns /
known-limitations cards on the Summary screen, G1 assumption labels on the Objectives screen, and the static-mode
parsed-rules list on the Preferences screen) rather than silently dropped.

The site is built from REAL `pipeline.review` reports in mock mode into a temp dir; the tests read the written JSON
and the JS source app.html loads. No browser, no network, no clock: JS is checked as text (function bodies, string
literals), the same way the already-retargeted dashboard oracles do (tests/dashboard/test_d10_dashboard.py).
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
SITE_DIR = DEPLOY / "site"
JS = SITE_DIR / "js"
SHELL = JS / "core" / "shell.js"
DATA_SOURCE = JS / "core" / "data_source.js"
SUMMARY = JS / "screens" / "summary" / "summary.js"
OBJECTIVES = JS / "screens" / "pillars" / "objectives.js"
PREFERENCES = JS / "screens" / "pillars" / "preferences.js"
SCHEMA = json.loads((ROOT / "coherence_analyzer" / "schemas" / "report.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA)

EN_DISCLAIMER = ("Educational simulation with fictional profiles. This is not personalised financial advice. "
                 "No transaction is executed.")                                              # SPEC §9.4, en-CA
STATUSES = {"pass", "violation", "warning", "info", "unknown"}
KINDS = {"fact", "assumption", "inference", "unknown"}                                       # SPEC §9.2 / §12
VERDICTS = {"coherent", "coherent_with_warnings", "incoherent"}
# persona key -> expected verdict; D3, D7 and tests/test_pipeline_offline.py measured context
EXPECTED = {"robert": "coherent", "robert-nvda": "incoherent", "amira": "incoherent", "mateo": "coherent"}
ADVISORY = ("tu devrais acheter", "tu devrais vendre", "je recommande", "nous recommandons", "you should buy",
            "you should sell", "we recommend", "i recommend", "vous devriez acheter", "vous devriez vendre",
            "je conseille", "nous conseillons", "i suggest you", "we advise", "achetez", "vendez", "buy now")
# Equivalent of the old single-page hooks, each pinned to the module that now owns it.
HOOKS = {
    "stressHtml": SUMMARY, "unknownsHtml": SUMMARY, "limitationsHtml": SUMMARY,
    "goalsAssumptionsHtml": OBJECTIVES, "parsedRuleHtml": PREFERENCES,
    "assumptionsHtml": SHELL, "segmentsHtml": SHELL, "checkRow": SHELL,
}
NEW_RENDERERS = ("stressHtml", "unknownsHtml", "limitationsHtml", "goalsAssumptionsHtml", "assumptionsHtml")
# The real pillar navigation (deploy/site/app.html's Summary-screen analysis menu); the old PAGE_LABELS were
# index.html's seven French tabs on one page.
MENU_LABELS = ("Summary", "Objectives and horizon", "Risk", "Constraints", "Preferences", "Profile coherence",
               "Check a trade")


# ---------------------------------------------------------------- tiny JS scanner (comment/string-aware braces)
# Ported from tests/dashboard/test_d10_dashboard.py: app.html's modules build markup by string concatenation, not
# template literals, so there is no `${}` slot-drilling to do (unlike the retired index.html).

def _balanced(s: str, start: int, open_ch: str, close_ch: str) -> int:
    depth, i, n = 0, start, len(s)
    while i < n:
        c = s[i]
        if c in "\"'`":
            j = i + 1
            while s[j] != c:
                j += 2 if s[j] == "\\" else 1
            i = j + 1
            continue
        if s.startswith("//", i):
            nl = s.find("\n", i)
            i = nl if nl != -1 else n
            continue
        if s.startswith("/*", i):
            i = s.index("*/", i) + 2
            continue
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError(f"unbalanced {open_ch!r}")


def function_source(src: str, name: str) -> str:
    """Comment/string-aware body of `function <name>(...) {...}`, failing the test if not defined exactly once."""
    found = list(re.finditer(rf"function\s+{re.escape(name)}\s*\(", src))
    assert len(found) == 1, f"must define `function {name}(` exactly once (found {len(found)})"
    i = _balanced(src, found[0].end() - 1, "(", ")")
    brace_open = src.index("{", i)
    end = _balanced(src, brace_open, "{", "}")
    return src[brace_open + 1:end - 1]


def hook_source(name: str) -> str:
    return function_source(HOOKS[name].read_text(encoding="utf-8"), name)


def string_literals(src: str) -> list[str]:
    """Every quoted/backtick string literal's raw text (escapes kept, comments skipped); used to check no digit
    is typed into a shown sentence (`typed_digits` below), the same intent as the old `_scan_template`."""
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in "\"'`":
            j = i + 1
            buf = []
            while src[j] != c:
                if src[j] == "\\":
                    buf.append(src[j:j + 2])
                    j += 2
                else:
                    buf.append(src[j])
                    j += 1
            out.append("".join(buf))
            i = j + 1
            continue
        if src.startswith("//", i):
            nl = src.find("\n", i)
            i = nl if nl != -1 else n
            continue
        if src.startswith("/*", i):
            i = src.index("*/", i) + 2
            continue
        i += 1
    return out


def typed_digits(texts: list[str]) -> list[str]:
    """Digits left in string-literal text once tags/attributes and entities are removed."""
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
    rdir = out / "reports" / "en-CA"
    index = json.loads((rdir / "index.json").read_text(encoding="utf-8"))
    reports = {e["file"]: json.loads((rdir / e["file"]).read_text(encoding="utf-8")) for e in index}
    js_src = {str(p.relative_to(JS)): p.read_text(encoding="utf-8") for p in JS.rglob("*.js")}
    return {"out": out, "rdir": rdir, "index": index, "reports": reports,
            "html": (out / "app.html").read_text(encoding="utf-8"), "js": js_src, "watched": watched}


def _key(rep: dict) -> str:
    """Persona key of a report: robert / robert-nvda / amira / mateo."""
    who = rep["profile_id"].removeprefix("persona-")
    if who == "robert" and Path(rep["site"]["portfolio_file"]).name == "portfolio_review.json":
        return "robert-nvda"
    return who


def by_key(site) -> dict[str, dict]:
    return {_key(r): r for r in site["reports"].values()}


def all_js(site) -> str:
    return "\n".join(site["js"].values())


# ---------------------------------------------------------------- JSON written by the build

def test_build_writes_the_site_files(site):
    """The build writes the app, the design board and the English report JSON the app reads (SPEC §12)."""
    out = site["out"]
    for rel in ("app.html", "design.html", "reports/en-CA/index.json", "reports/en-CA/intake.json"):
        assert (out / rel).is_file(), rel


def test_index_lists_every_complete_persona(site):
    """reports/en-CA/index.json lists Robert current, Robert + NVDA, Amira and Mateo, each entry opening a real
    report (English index shape: persona_id, name, file, label, portfolio, verdict, profile)."""
    idx = site["index"]
    assert isinstance(idx, list) and len(idx) == 4
    for e in idx:
        assert {"persona_id", "name", "file", "label", "portfolio", "verdict", "profile"} <= set(e)
        assert isinstance(e["label"], str) and e["label"].strip()
        assert (site["rdir"] / e["file"]).is_file()
    assert len({e["file"] for e in idx}) == 4 and len({e["label"] for e in idx}) == 4
    assert set(by_key(site)) == set(EXPECTED)               # four distinct (persona, portfolio) pairs
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
    """D3 / SPEC §7.5: `unknowns` = the `unknown` checks; Mateo is `coherent` with a non-empty list.

    D19: with real ETF look-through data, H6/H10 resolve for every persona, and Amira's holdings don't trigger
    an H11 (ESG exclusion tag) unknown either, so her report now has no unknowns at all -- still correct per D3
    (an empty list is fine; it must just never affect the verdict, which stays `incoherent` on her hard violations).
    """
    for key, rep in by_key(site).items():
        assert all(u["status"] == "unknown" for u in rep["unknowns"])
        listed = [c for c in rep["checks_current"] if c["status"] == "unknown"]
        assert sorted((u["id"], u.get("subject") or "") for u in rep["unknowns"]) == \
               sorted((c["id"], c.get("subject") or "") for c in listed)
    mateo = by_key(site)["mateo"]
    assert mateo["verdict"] == "coherent" and mateo["unknowns"]
    amira = by_key(site)["amira"]
    assert amira["verdict"] == "incoherent" and amira["unknowns"] == []


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
    """SPEC §9.4: the English report carries the fixed en-CA disclaimer verbatim; the app shows it from a fixed
    footer string, not a per-report template (see test_disclaimer_is_shown_verbatim_from_the_report below for why
    that is still faithful to the guarantee: the text never varies)."""
    for key, rep in by_key(site).items():
        assert rep["disclaimer"] == EN_DISCLAIMER, key
        assert rep["explanation"]["disclaimer"] == EN_DISCLAIMER, key


def test_known_limitations_come_from_the_report(site):
    """SPEC §19: the report's `assumptions` carry the limitations the app lists (window, drift, survivorship, no
    look-through); the two non-numeric ones (noisy behaviour, team-calibrated limits vs AMF/CIRO) are in the report
    or the Summary screen's `limitationsHtml` (deploy/site/js/screens/summary/summary.js), ported additively from
    the retired index.html's `renderLimitations` since app.html had no known-limitations surface at all before
    this change. The page may not type a limitation as a number (checked in test_new_renderers_type_no_numbers)."""
    summary_src = site["js"]["screens/summary/summary.js"]
    for key, rep in by_key(site).items():
        text = " ".join(rep["assumptions"])
        low = text.lower()
        assert "survivorship" in low, key
        assert "drift" in low and "assumed" in low, key
        assert "data window" in low, key
        if not rep["metrics_current"]["lookthrough"]:
            assert "look-through" in low, key
        # SPEC-AMBIGUITY: §19 bullets 4-5 are not in `assumptions` today (and coherence_analyzer/ is not ours to
        # change); strict reading: they must still be shown, from either source. Ported into summary.js's
        # limitationsHtml as fixed, non-numeric sentences (never from the report, so this is JS-source text here).
        shown = text + " " + summary_src
        assert "AMF" in shown and "CIRO" in shown, key
        assert re.search(r"noisy|bruit", shown, re.I), key


def test_build_leaves_repo_state_alone(site):
    """The build writes only into its out dir: fixtures untouched, no `activity_tags.csv` written (invariant 6)."""
    for path, before in site["watched"].items():
        now = path.read_bytes() if path.exists() else None
        assert (None if now is None else hashlib.sha256(now).hexdigest()) == \
               (None if before is None else hashlib.sha256(before).hexdigest()), path
    assert not (site["rdir"] / "activity_tags.csv").exists()


# ---------------------------------------------------------------- app.html + its JS modules: render hooks

def test_page_defines_and_uses_every_render_hook(site):
    """Each P2 / SPEC §12 renderer is a function defined once, in the module that now owns it, and called from
    somewhere else in the site's JS (its own module and/or a caller in another one, via the shared `App` object
    for shell.js's helpers)."""
    for name, path in HOOKS.items():
        function_source(path.read_text(encoding="utf-8"), name)
        hits = len(re.findall(rf"\b{name}\b", all_js(site)))
        assert hits >= 2, f"{name} is defined but never used"


def test_page_keeps_the_tabs_the_picker_and_reads_only_report_json(site):
    """Real look kept: the seven pillar screens listed on the Summary analysis menu, the persona picker, and the
    static source's reads limited to its own prebuilt directory (reports/en-CA/); live mode additionally reads a
    local API (deploy/site/js/core/data_source.js), which the retired, static-only index.html never had."""
    html = site["html"]
    for label in MENU_LABELS:
        assert f">{label}<" in html, label
    assert 'data-persona' in html
    data_source = site["js"]["core/data_source.js"]
    assert 'const REPORT_DIR = "reports/en-CA/";' in data_source
    assert re.search(r"REPORT_DIR\s*\+", data_source), "every static report/universe/whatif/intake read is scoped"
    scripts = re.findall(r'<script src="([^"]+)"', html)
    assert scripts and all(s.startswith("js/") for s in scripts), "same-origin modules only, no external script"


def test_stress_renderer_shows_scenarios_and_the_assumption_badge(site):
    """`stress`: rate-scenarios table from `scenarios`, `sourced` flag and `assumptions` shown through the shared
    assumption badge, status info (SPEC §17 step 16, report schema `stress`). Ported into summary.js's `stressHtml`
    (Summary screen): the retired index.html's single-page `renderStress` had no equivalent in app.html until now."""
    body = hook_source("stressHtml")
    for token in ("scenarios", "shock_bp", "impact", "sourced", "assumptions", "status"):
        assert token in body, token
    assert "App.assumptionsHtml(" in body
    assert "<table" in body and "<th" in body
    assert re.search(r"report\s*&&\s*report\.stress", body), "reads report.stress"


def test_goals_renderer_shows_g1_probabilities_as_info_with_assumptions(site):
    """`goals`: G1 probabilities (`goal_probs`) always `info`, each with its assumption labels (SPEC §7.3 G1).
    objectives.js's `goalsTable` already showed the probabilities; `goalsAssumptionsHtml` (new, additive) shows the
    assumption labels the retired index.html drew through `assumptionBadge(g.assumptions, ...)`."""
    objectives_src = site["js"]["screens/pillars/objectives.js"]
    goals_table = function_source(objectives_src, "goalsTable")
    for token in ("probs", "prob", "name", "amount"):
        assert token in goals_table, token
    assumptions_body = hook_source("goalsAssumptionsHtml")
    assert "goalAssumptions" in assumptions_body and "App.assumptionsHtml(" in assumptions_body
    assert re.search(r"goals\.assumptions", objectives_src), "reads report.goals.assumptions"
    # info only: no violation / warning styling decided inside the G1 renderers (invariant 3)
    assert not re.search(r"s-(violation|warning)|c-(violation|warning)", goals_table + assumptions_body)


def test_lookthrough_renderer_says_etf_sectors_are_not_looked_through(site):
    """`metrics.lookthrough`: when false, the ETF parts of H6 / H10 go `unknown` (SPEC §19, §7.3; verified at the
    report level in test_lookthrough_flag_and_its_unknowns). Architecture note: app.html has no screen that reads
    `metrics_current.lookthrough` directly -- no pillar screen branches on the flag the way the retired
    index.html's dedicated `renderLookthrough` did. The sentence a reader needs ("the ETF share has no sector
    breakdown, so this cannot be verified") still always shows, though: it is shell.js's generic per-check
    fallback template for H6/H10 in `unknown` status (`CHECK_TEMPLATES["H6/unknown"]`, `["H10/unknown"]`), which
    every pillar screen's check list renders through `App.checkRow` whenever that check is unknown, which is
    exactly when the flag is false (checks stay `pass`/other otherwise). This is a genuine, if indirect,
    equivalent: it is tied to the check's own status rather than to the flag's name, so it would need updating by
    hand if a future unknown reason for H6/H10 stopped being look-through-related."""
    shell_src = SHELL.read_text(encoding="utf-8")
    m = re.search(r'"H6/unknown":\s*((?:"[^"]*"\s*\+?\s*)+)', shell_src)
    assert m, "shell.js must keep the H6/unknown fallback template"
    h6_text = re.sub(r'"\s*\+\s*"', "", m.group(1)).strip().strip('"')
    low = h6_text.lower()
    for word in ("etf", "sector", "cannot be verified"):
        assert word in low, word
    assert '"H10/unknown"' in shell_src


def test_derived_from_chain_is_clickable_on_each_check(site):
    """SPEC §12: a check row opens its `derived_from` chain. Architecture note: the retired index.html wired a
    `[data-check]` click handler to its own `renderDerivedFrom`; shell.js's `checkRow` instead wraps the row's
    `derived_from` trace in a native `<details><summary>` disclosure (`derivedFromHtml`), which is clickable by
    the browser itself with no JS event listener needed -- the same user-visible guarantee (click the check, see
    where it comes from), reached a different, equally valid way."""
    body = hook_source("checkRow")
    assert "derivedFromHtml(check)" in body
    shell_src = SHELL.read_text(encoding="utf-8")
    trace_body = function_source(shell_src, "derivedFromHtml")
    assert "derived_from" in trace_body
    assert 'chk--traced' in shell_src and "<details>" in shell_src and "<summary>" in shell_src


def test_segment_tags_on_every_explanation_segment(site):
    """SPEC §12 / §9.2: a kind label (fact / assumption / inference / unknown) on every explanation segment, in
    the report-level explanation and in each factor narrative. shell.js's shared `segmentsHtml` (used by every
    pillar screen's detail card) is the sole place this is drawn, through `SEGMENT_KIND_LABEL`."""
    shell_src = SHELL.read_text(encoding="utf-8")
    m = re.search(r"const\s+SEGMENT_KIND_LABEL\s*=\s*\{([^}]*)\}", shell_src)
    assert m, "SEGMENT_KIND_LABEL map (segment kind -> label) not found in shell.js"
    kinds = set(re.findall(r"(\w+)\s*:", m.group(1)))
    assert kinds == KINDS
    for label in ("Fact", "Assumption", "Inference", "Unknown"):
        assert f'"{label}"' in shell_src, label
    body = hook_source("segmentsHtml")
    assert "seg__kind" in body and "SEGMENT_KIND_LABEL" in body
    # used by every pillar's detail card plus the Summary executive summary and the Coherence screen
    hits = len(re.findall(r"\bsegmentsHtml\(", all_js(site)))
    assert hits >= 4, "definition + at least three callers"


def test_unknowns_list_is_titled_what_we_cannot_verify(site):
    """D3 / SPEC §7.5: `unknowns` is its own list titled "What we cannot verify", fed by report.unknowns. Ported
    into summary.js's `unknownsHtml` (Summary screen), additive: app.html had no such list before this change."""
    body = hook_source("unknownsHtml")
    assert "report.unknowns" in body
    assert "What we cannot verify" in body
    assert re.search(r"\breport\.unknowns\b", body)


def test_limitations_renderer_reads_the_report_assumptions(site):
    """SPEC §19: `limitationsHtml` lists the limitations from `report.assumptions`; no figure is typed into any of
    its own shown sentences. Ported additively into summary.js (Summary screen)."""
    body = hook_source("limitationsHtml")
    assert "report.assumptions" in body or "report && Array.isArray(report.assumptions)" in body
    assert "Known limitations" in body
    assert not typed_digits(string_literals(body))


def test_values_rules_have_display_only_approve_and_reject_buttons(site):
    """SPEC §12 (N3): the static build does not decide (the API does). Architecture note: this app omits the
    Approve/Reject buttons entirely in static mode rather than rendering them `disabled` -- a deliberate,
    already-shipped strengthening of the retired index.html's guarantee (tests/dashboard/test_ui_flow_b.py pins
    the same "omitted outright" behaviour for the live-vs-no-API case, the identical `App.source.mode` branch),
    so there is no element to even attempt a request from. The rule itself (quote, summary, approval state,
    provisional flag) still renders -- that's the gap this closes, where preferences.js's static path used to
    render nothing at all (`parsedNotesHtml` returned `""` outside live mode)."""
    body = hook_source("parsedRuleHtml")
    assert 'data-rule-action="approve"' in body and 'data-rule-action="reject"' in body
    assert "approved_by_user" in body
    assert re.search(r"App\.source\.mode\s*===\s*[\"']live[\"']\s*\?.*?data-rule-action.*?:\s*\"\"", body, re.S), \
        "the buttons must be omitted outright (not merely disabled) outside live mode"
    assert "fetch(" not in body and "XMLHttpRequest" not in body
    prefs_src = site["js"]["screens/pillars/preferences.js"]
    assert "parsedRulesFromReport" in prefs_src and "values.parsed_notes" in prefs_src
    static_body = function_source(prefs_src, "staticNotesHtml")
    assert "fetch(" not in static_body and "XMLHttpRequest" not in static_body


def test_disclaimer_is_shown_verbatim_from_the_report(site):
    """SPEC §9.4: the footer shows the fixed disclaimer text verbatim. Architecture note: app.html hardcodes the
    fixed en-CA sentence in its footer instead of reading `rep.disclaimer` through a template, because it is a
    static shell shared by every persona and the sentence never varies by report (SPEC §9.4: fixed text); this
    still fully satisfies the guarantee, checked here both ways -- the report's own `disclaimer` field equals the
    fixed text (test_disclaimer_is_the_fixed_text) and the page's literal footer text equals it exactly too."""
    html = site["html"]
    found = re.findall(r"Educational simulation[^<]*", html)
    assert found, "the fixed disclaimer sentence must appear in the footer"
    for text in found:
        assert text.strip() == EN_DISCLAIMER


def test_new_renderers_type_no_numbers(site):
    """Every number shown comes from the report JSON: no digit in the literal string-literal text of the P2 / §12
    renderers (tags, attributes and entities are ignored; these modules build markup by concatenation, so there
    is no `${}` slot to strip)."""
    for name in NEW_RENDERERS:
        body = hook_source(name)
        bad = typed_digits(string_literals(body))
        assert not bad, (name, bad[:5])


def test_page_has_no_advisory_wording(site):
    """CLAUDE.md: no buy/sell recommendation anywhere in the page or its JS (SPEC §9.3 blocklist, FR + EN)."""
    low = (site["html"] + "\n" + all_js(site)).lower()
    assert not [p for p in ADVISORY if p in low]
