"""P5.DASH oracle: the dashboard works for any user, not only the three personas (SPEC §12 UI + route table, §13;
docs/spec-factors.md §20.6 intake, §20.7 UI; docs/spec-decisions.md D7, D10; owner request 2026-11-14).

# STALE-ORACLE: tests/dashboard/test_ui_flow_b.py::test_post_requests_go_through_one_helper forbids `\\bPUT\\b` anywhere in
# the page, allows exactly one `method:` and exactly one quoted "POST" (written in P2.UI, before the P5.USERS route
# `PUT /profiles/{id}/portfolio` existed). That frozen test is kept; the portfolio write is made satisfiable with it:
# `apiPost(path, body, verb)` with `method: verb || "POST"` and the verb passed as the lowercase string "put" (fetch
# normalises it to PUT). If the implementation would rather change the helper's contract, that frozen test is the one
# to name in .claude/loop/BLOCKED.md.
# NOTE (not tested here, api.py belongs to P5.USERS): api.py's CORS middleware allows only GET and POST; a PUT from a page
# served from another origin needs `allow_methods` to include PUT.

What is pinned (all offline: the page is scanned as text like the other dashboard oracles, the build runs the real
pipeline in mock mode into temp dirs, no browser, no network, no clock)

  1. With an API base the picker lists `GET /profiles` (seeds + store users) and the page renders the live review
     (`POST /profiles/{id}/review`); the static demo keeps its reports/index.json picker and the separate flow B file.
  2. New profile: a form posts `POST /profiles` `{locale, display_name?}`; the intake form P3.UIINTAKE added then walks the
     missing answers; then a portfolio form sends `PUT /profiles/{id}/portfolio` `{as_of, value_cad, weights_pct}`;
     then the live review.
  3. `deploy/whatif.py` `build(out, profile_dir)` precomputes for any profile directory; `deploy/build_site.py` takes the
     profiles from its REPORTS list (no `WHATIF_PERSONA`); every built review carries `site.whatif` =
     `{"universe": <file>, "scenarios": <file>}` (files in reports/); the page's what-if note names no persona.
  4. No user-facing string names Robert, Amira or Mateo (labels come from data); every new string passes the SPEC §9.3
     blocklist and the D10 vocabulary.

Hooks the page must define (each `function <name>(`, exactly once, used at least once elsewhere):
  boot()                 entry point: with an API base -> loadProfiles(); without -> reports/index.json as today.
  loadProfiles()         plain GET `apiBase() + "/profiles"` (no `method`), fills the picker through pickerLive().
  pickerLive(profiles)   the picker's HTML for GET /profiles (profile_id + name) plus a `new-profile` entry. No flow B file.
  pickerStatic(index)    the static picker: the index entries plus the flow B entry (`flow-b`), no persona name typed.
  onPick(value)          the picker's change handler: `new-profile`, `flow-b`, a report file (static) or a profile id (live).
  currentProfileId()     the profile id of the report or of the onboarding in progress (used by the intake functions).
  loadLiveReview(id)     GET /profiles/{id} for the profile (limits, name) + apiPost `/profiles/${id}/review`, then the page.
  buildLiveSite(rep, profile)   the `site` display block the renderers read, built from the API data only.
  resetWhatif()          clears the what-if caches; called on every report switch.
  renderNewProfileForm() / createProfile(form)   the `new-profile-form` and its POST.
  renderOnboarding()     intake question(s), then the portfolio form, for a profile that has no review yet.
  renderPortfolioForm() / savePortfolio(form)    the `portfolio-form` and its PUT (through apiPost).
  whatifNote()           (was an arrow const) the note shown when a report has no precomputed what-if.
"""
from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
INDEX = DEPLOY / "site" / "index.html"
FIXTURES = ROOT / "fixtures"
FROZEN_FLOW_B = ROOT / "tests" / "dashboard" / "test_ui_flow_b.py"
FROZEN_INTAKE = ROOT / "tests" / "dashboard" / "test_ui_intake_confirm.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# The JS scanner, the SPEC §9.3 blocklist and the D10 vocabulary of the frozen oracles are reused, not copied.
H = _load_module("_ui_flow_b_helpers_for_any_user", FROZEN_FLOW_B)
I = _load_module("_ui_intake_helpers_for_any_user", FROZEN_INTAKE)
function_source, typed_digits, blocklist_hits, uses, norm = H.function_source, H.typed_digits, H.blocklist_hits, H.uses, H.norm
D10_WORDS = H.D10_WORDS

PERSONAS = ("Robert", "Amira", "Mateo")
PERSONA_RE = re.compile(r"\b(robert|amira|mateo)\b", re.I)
NEW_HOOKS = ("boot", "loadProfiles", "pickerLive", "pickerStatic", "onPick", "currentProfileId", "loadLiveReview",
             "buildLiveSite", "resetWhatif", "renderNewProfileForm", "createProfile", "renderOnboarding",
             "renderPortfolioForm", "savePortfolio", "whatifNote")
RENDERERS = ("pickerLive", "pickerStatic", "renderNewProfileForm", "renderOnboarding", "renderPortfolioForm", "whatifNote")


@pytest.fixture(scope="module")
def page() -> str:
    return INDEX.read_text(encoding="utf-8")


def _body(page: str, name: str) -> str:
    return function_source(page, name)[0]


def _code(page: str) -> str:
    """The page without its comments (HTML, CSS / JS block comments, JS line comments)."""
    s = re.sub(r"<!--.*?-->", " ", page, flags=re.S)
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.S)
    return re.sub(r"(?m)(^|\s)//[^\n]*", " ", s)


def _api_posts(body: str, path_re: str) -> list[list[str]]:
    return I.api_post_calls(body, path_re)


# ---------------------------------------------------------------- 0. hooks

def test_new_hooks_are_defined_once_and_used(page):
    """The picker, live review, new-profile, onboarding and portfolio hooks exist as `function`s, each defined exactly
    once (`whatifNote` is now a function, not an arrow const) and called from elsewhere in the page."""
    for name in NEW_HOOKS:
        function_source(page, name)
        assert uses(page, name) >= 2, f"{name} is defined but never called"


# ---------------------------------------------------------------- 1. picker: GET /profiles when an API is set

def test_boot_reads_the_api_only_when_one_is_configured(page):
    """Task (1)/(2): `boot()` tests apiBase() before anything else; with an API it calls loadProfiles(), without it it
    reads `reports/index.json` exactly as today; the live branch never needs the static index."""
    body = _body(page, "boot")
    assert "apiBase(" in body and "loadProfiles(" in body and "reports/index.json" in body
    assert body.index("apiBase(") < body.index("loadProfiles(") and body.index("apiBase(") < body.index("reports/index.json")
    assert "reports/index.json" not in _body(page, "loadProfiles")
    for live in ("loadProfiles", "pickerLive", "loadLiveReview"):
        assert "reports/index.json" not in _body(page, live), live
    assert "pickerStatic(" in body, "static branch: the picker is built from the index entries"
    assert "pickerLive(" in _body(page, "loadProfiles") or "pickerLive(" in body
    assert re.search(r"\bboot\s*\(\s*\)", page.replace("function boot(", "")), "the page starts with boot()"


def test_every_fetch_is_a_reports_file_or_an_api_route(page):
    """Frozen test_site.py reads fetch targets that start with a literal quote: only `reports/...` files. The API is read
    with `fetch(apiBase() + "/...")` (never a template that starts with `${apiBase()}`); the one other fetch is apiPost's."""
    code = _code(page)
    calls = [code[m.end():m.end() + 70] for m in re.finditer(r"\bfetch\(", code)]
    assert len(calls) >= 6
    for arg in calls:
        assert re.match(r"""\s*(["']reports/|apiBase\(\)\s*\+\s*["'`]/|base\s*\+\s*path\b)""", arg), arg
    assert not re.search(r"fetch\(\s*`", code), "no template-literal fetch: test_site.py would read it as a non-reports target"
    assert not re.search(r"""fetch\(\s*["'](?!reports/)""", code)


def test_load_profiles_is_a_plain_get_of_the_profiles_route(page):
    """SPEC §12 / task (1): `GET /profiles` through a plain fetch (the page keeps one fetch with an explicit method:
    apiPost), only with an API, the picker filled from the answer, failures shown, never swallowed."""
    body = _body(page, "loadProfiles")
    assert "apiBase(" in body
    assert re.search(r"""fetch\(\s*apiBase\(\)\s*\+\s*["']/profiles["']\s*[),]""", body), "GET apiBase() + \"/profiles\""
    assert not re.search(r"\bmethod\s*:", body) and "apiPost(" not in body
    assert "pickerLive(" in body and "#pick" in body, "the picker is filled from the list"
    assert "catch" in body and "state.error" in body
    assert "!r.ok" in body or "!res.ok" in body or ".ok" in body, "an HTTP error is an error, not an empty list"
    assert "reports/" not in body


def test_live_picker_lists_the_profiles_and_a_new_profile_entry(page):
    """Task (1): the live picker has one entry per `{profile_id, name}` of GET /profiles (id as value, name escaped as
    label) and a `new-profile` entry; no flow B entry, no persona typed, no report file."""
    body, texts = function_source(page, "pickerLive")
    literal = "\n".join(texts) + "\n" + body
    assert "<option" in literal and "profile_id" in body and re.search(r"\.name\b", body)
    assert re.search(r"esc\(", body), "labels and values are escaped"
    assert re.search(r"value=[\"']\$\{", body), "the profile id is a slot, not typed"
    assert re.search(r"value=[\"']new-profile[\"']", literal), "the entry that opens the new-profile form"
    for forbidden in ("flow-b", "reports/", "loadFlowB", ".file"):
        assert forbidden not in body, forbidden
    assert not PERSONA_RE.search(body) and not typed_digits(texts)


def test_static_picker_keeps_the_index_entries_and_the_flow_b_entry(page):
    """Task (2): without an API the picker is the 4 index entries (value = file, label from the index) plus the separate flow B
    report entry, as today, but its label names no persona (task (4): labels come from data)."""
    body, texts = function_source(page, "pickerStatic")
    literal = "\n".join(texts) + body
    assert re.search(r"\.file\b", body) and re.search(r"\.label\b", body) and "esc(" in body
    assert re.search(r"value=[\"']flow-b[\"']", literal), "the flow B demo entry"
    assert not PERSONA_RE.search(body), "no persona name typed in the picker"
    assert "profile_id" not in body and "new-profile" not in body, "no profile creation without an API"


def test_picker_change_routes_to_the_right_loader(page):
    """`onPick(value)`: `new-profile` -> the form (live only); `flow-b` -> loadFlowB() (static); a report file -> load();
    a profile id -> loadLiveReview() when an API is set. The #pick change listener calls it."""
    body = _body(page, "onPick")
    for token in ("apiBase(", "new-profile", "flow-b", "loadFlowB(", "loadLiveReview(", "load("):
        assert token in body, token
    assert body.index("apiBase(") < body.index("loadLiveReview(")
    assert "render(" in body or "route(" in body, "the new-profile branch shows its form"
    assert re.search(r"#pick.{0,200}addEventListener\(\s*[\"']change[\"'].{0,200}?onPick\(", page, re.S)
    assert uses(page, "loadFlowB") >= 2 and "reports/robert-nvda.json" in _body(page, "loadFlowB")


# ---------------------------------------------------------------- 1b. live review

def test_load_live_review_posts_the_review_route_and_builds_the_page_state(page):
    """SPEC §12: `POST /profiles/{id}/review` (no body: nothing is sent that could be a limit or an AI field), plus a plain
    GET of the profile for what the report does not carry (limits, name); `state.rep` gets a `site` block from
    buildLiveSite; per-report state is reset; the intake checklist is loaded; failures are shown."""
    body = _body(page, "loadLiveReview")
    assert "apiBase(" in body
    calls = _api_posts(body, r"/profiles/\$\{[^}]*\}/review")
    assert len(calls) == 1, "one POST to /profiles/${id}/review"
    assert len(calls[0]) == 2 and calls[0][1].strip() == "{}", "the review route takes no body"
    assert re.search(r"""fetch\(\s*apiBase\(\)\s*\+\s*["'`]/profiles/""", body), "plain GET of the profile"
    assert not re.search(r"\bmethod\s*:", body)
    assert "buildLiveSite(" in body and re.search(r"state\.rep\s*=", body)
    for reset in ("state.draft = null", "state.intake = null", "state.onboarding = null", "state.factor = null"):
        assert reset in body, reset
    assert "resetWhatif(" in body and "loadIntake(" in body
    assert "render(" in body or "route(" in body
    assert "catch" in body and "state.error" in body
    for forbidden in ("limits_trace", "llm:"):
        assert forbidden not in calls[0][1]


def test_build_live_site_gives_the_renderers_what_they_read(page):
    """The renderers read `rep.site` (label, as_of, value_cad, weights_pct, limits, limits_trace, profile_card,
    portfolio_file, whatif). The static build embeds it; for a live report it is built from the profile and the report: the
    limits are the profile's own (computed by the API's rules, never by the page), nothing typed, no persona name."""
    body, texts = function_source(page, "buildLiveSite")
    for key in ("label", "as_of", "value_cad", "weights_pct", "limits", "limits_trace", "profile_card", "portfolio_file",
                "whatif"):
        assert re.search(rf"\b{key}\b", body), key
    assert re.search(r"\bprofile\.limits\b", body) and re.search(r"\bprofile\.limits_trace\b", body)
    assert re.search(r"\bwhatif\s*:\s*(false|null)\b", body), "a live report has no precomputed what-if (the note is shown)"
    # SPEC-AMBIGUITY: no route returns the portfolio (value, date) today, and P5.USERS only promises the PUT; the source of
    # `value_cad` / `as_of` is left to the implementation, but it is read, never typed.
    assert not PERSONA_RE.search(body) and not typed_digits(texts)
    assert "limits" not in "".join(re.findall(r"apiPost\([^)]*\)", body))


def test_reset_whatif_clears_every_cache_on_each_report_switch(page):
    """What-if files are per report now: switching report must drop the cached universe, scenarios and tickers."""
    body = _body(page, "resetWhatif")
    assert re.search(r"\buniverse\s*=\s*null", body) and re.search(r"\bwiAll\s*=\s*null", body)
    assert re.search(r"\bwi\.cache\s*=\s*\{\s*\}", body)
    for name in ("load", "loadLiveReview"):
        assert "resetWhatif(" in _body(page, name), name


# ---------------------------------------------------------------- 2. new profile -> intake -> portfolio

def test_new_profile_form_is_a_locale_and_an_optional_name(page):
    """`POST /profiles {locale, display_name?}`: the form has a `locale` select (fr-CA / en-CA, the API's two locales), a
    `display_name` text input that is NOT required, a submit button; empty at start; offered only with an API; bound in render()."""
    body, texts = function_source(page, "renderNewProfileForm")
    literal = "\n".join(texts) + "\n" + body
    assert "apiBase(" in body, "no form without an API"
    assert re.search(r"<form\b[^>]*\bid=[\"']new-profile-form[\"']", literal)
    assert re.search(r"<select\b[^>]*\bname=[\"']locale[\"']", literal)
    for loc in ("fr-CA", "en-CA"):
        assert re.search(rf"value=[\"']{loc}[\"']", literal), loc
    tag = re.search(r"<input\b[^>]*\bname=[\"']display_name[\"'][^>]*>", literal)
    assert tag and "required" not in tag.group(0), "the display name is optional"
    assert re.search(r"<button\b[^>]*type=[\"']submit[\"']", literal)
    assert not re.search(r"\bvalue=[\"'](?!fr-CA|en-CA|\u00a7)[^\"']", "\n".join(texts)), "nothing prefilled"
    assert not typed_digits(texts) and not PERSONA_RE.search(literal)
    rendered = _body(page, "render")
    assert "renderNewProfileForm(" in rendered
    assert re.search(r"new-profile-form.{0,300}?addEventListener\(\s*[\"']submit[\"'].{0,300}?createProfile\(", rendered, re.S)


def test_create_profile_posts_locale_and_the_optional_name_only(page):
    """inv. 2: the body is `{locale}` or `{locale, display_name}`; nothing else (the API answers 422 for unknown keys, and
    never accepts limits or llm: fields). A blank name is not sent. The new profile id starts the onboarding; the picker is
    refreshed; failures are shown."""
    body = _body(page, "createProfile")
    assert "apiBase(" in body
    calls = _api_posts(body, r"""^["'`]/profiles["'`]$""")
    assert len(calls) == 1, "one POST to /profiles"
    keys, expr = I.body_keys(body, calls[0][1])
    assert "..." not in keys, f"a plain literal, nothing merged: {expr[:80]}"
    assert "locale" in keys and set(keys) <= {"locale", "display_name"}, keys
    assert "trim(" in body, "a blank name is never sent"
    assert "display_name" not in I.object_keys(I.body_expression(body, calls[0][1])) or re.search(
        r"\?\s*\{[^}]*display_name|if\s*\([^)]*\)[^;]*display_name|&&[^;]*display_name", body), \
        "display_name is only sent when typed"
    assert "profile_id" in body and re.search(r"state\.onboarding\s*=", body), "the new id starts the onboarding"
    assert "loadProfiles(" in body and "loadIntake(" in body
    assert "catch" in body and "state.error" in body and "render(" in body
    for forbidden in ("limits", "llm"):
        assert forbidden not in body, f"SPEC inv. 2: the UI never sends `{forbidden}`"


def test_onboarding_walks_the_intake_then_shows_the_portfolio_form(page):
    """Task (1): after the creation the intake form (P3.UIINTAKE) walks the missing answers, and only then the portfolio form
    is shown. The intake functions read the profile id through currentProfileId() so they work before any review exists
    (frozen test_ui_intake_confirm.py still needs the text `profile_id` inside loadIntake's body)."""
    body = _body(page, "renderOnboarding")
    assert "state.onboarding" in body and "state.intake" in body
    assert "renderIntake(" in body and "renderPortfolioForm(" in body
    assert body.index("renderIntake(") < body.index("renderPortfolioForm(")
    assert "missing" in body and body.index("missing") < body.index("renderPortfolioForm("), \
        "the portfolio form waits for the intake checklist to be empty"
    ident = _body(page, "currentProfileId")
    assert "state.onboarding" in ident and "state.rep" in ident and "profile_id" in ident
    for name in ("loadIntake", "answerIntake", "skipIntake"):
        assert "currentProfileId(" in _body(page, name), name
    assert "renderOnboarding(" in _body(page, "render")
    assert not re.search(r"!\s*state\.rep\b", _body(page, "loadIntake")), \
        "loadIntake must not stop for lack of a report: an onboarding has none yet"


def test_portfolio_form_has_a_date_a_value_and_weight_rows(page):
    """PUT body fields: `as_of` (date input), `value_cad` (number), weights as rows of `ticker` + `weight_pct` the user can add
    to; nothing prefilled (no clock, no typed ticker, no typed figure); bound in render()."""
    body, texts = function_source(page, "renderPortfolioForm")
    literal = "\n".join(texts) + "\n" + body
    assert re.search(r"<form\b[^>]*\bid=[\"']portfolio-form[\"']", literal)
    assert re.search(r"<input\b[^>]*\btype=[\"']date[\"'][^>]*\bname=[\"']as_of[\"']|<input\b[^>]*\bname=[\"']as_of[\"'][^>]*\btype=[\"']date[\"']", literal)
    assert re.search(r"<input\b[^>]*\bname=[\"']value_cad[\"']", literal)
    assert re.search(r"<input\b[^>]*\bname=[\"']ticker[\"']", literal) and re.search(r"<input\b[^>]*\bname=[\"']weight_pct[\"']", literal)
    assert re.search(r"<button\b[^>]*type=[\"']button[\"'][^>]*data-portfolio=[\"']add-row[\"']|"
                     r"<button\b[^>]*data-portfolio=[\"']add-row[\"'][^>]*type=[\"']button[\"']", literal), "add a weight row"
    assert re.search(r"<button\b[^>]*type=[\"']submit[\"']", literal)
    for tag in re.findall(r"<input\b[^>]*>", "\n".join(texts)):
        m = re.search(r"\bvalue=[\"']([^\"']*)[\"']", tag)
        assert m is None or m.group(1) == "\u00a7", f"prefilled input {tag}"
    assert not typed_digits(texts) and not PERSONA_RE.search(literal)
    rendered = _body(page, "render")
    assert "renderPortfolioForm(" in rendered or "renderOnboarding(" in rendered
    assert re.search(r"portfolio-form.{0,300}?addEventListener\(\s*[\"']submit[\"'].{0,300}?savePortfolio\(", rendered, re.S)
    assert re.search(r"data-portfolio.{0,300}?addEventListener\(\s*[\"']click[\"']", rendered, re.S)


def test_save_portfolio_puts_the_portfolio_route_through_api_post(page):
    """SPEC-AMBIGUITY (P5.USERS): `PUT /profiles/{id}/portfolio {as_of, value_cad, weights_pct}`. Body = exactly those keys,
    numbers as numbers, weights in percent keyed by ticker; the sum is checked (100 +- 0.01) before anything is sent; the verb
    goes through apiPost's third argument as the lowercase string "put" (see STALE-ORACLE); then the review is loaded."""
    body = _body(page, "savePortfolio")
    assert "apiBase(" in body
    calls = _api_posts(body, r"/profiles/\$\{[^}]*\}/portfolio")
    assert len(calls) == 1, "one request to /profiles/${id}/portfolio"
    args = calls[0]
    assert len(args) == 3 and re.fullmatch(r"""["']put["']""", args[2].strip()), args
    keys, expr = I.body_keys(body, args[1])
    assert "..." not in keys and sorted(keys) == ["as_of", "value_cad", "weights_pct"], keys
    assert re.search(r"Number\(|parseFloat\(", body), "numbers are sent as numbers"
    assert "weight_pct" in body and "ticker" in body and ("querySelectorAll" in body or "elements" in body), \
        "the weight rows are read from the form"
    assert "0.01" in body and "100" in body and body.index("0.01") < body.index("apiPost("), "the sum is checked before the request"
    assert re.search(r"state\.onboarding\s*=\s*null", body.split("catch")[0]), "the onboarding ends on success only"
    assert "loadLiveReview(" in body, "the review follows the portfolio"
    assert "catch" in body and "state.error" in body and "render(" in body
    for forbidden in ("limits", "llm"):
        assert forbidden not in body, f"SPEC inv. 2: the UI never sends `{forbidden}`"
    assert not re.search(r"\bPUT\b", page), "frozen test_ui_flow_b: no uppercase PUT in the page"


def test_api_post_stays_the_single_request_helper(page):
    """The one fetch with an explicit method stays apiPost (frozen): a third parameter selects the verb, default POST; still
    one `method:` and one quoted \"POST\" in the page."""
    m = re.search(r"function\s+apiPost\s*\(([^)]*)\)", page)
    assert m
    params = [p.strip() for p in m.group(1).split(",") if p.strip()]
    assert len(params) == 3, params
    body = _body(page, "apiPost")
    assert re.search(rf"\bmethod\s*:\s*{re.escape(params[2])}\s*(\|\||\?\?)\s*[\"']POST[\"']", body), "method: verb || \"POST\""
    assert len(re.findall(r"\bmethod\s*:", page)) == 1 and len(re.findall(r"[\"']POST[\"']", page)) == 1
    assert not re.search(r"XMLHttpRequest|\bPUT\b|\bDELETE\b|\bPATCH\b", page)


# ---------------------------------------------------------------- 3. what-if for any profile; no persona in the page

def test_what_if_files_come_from_the_report_site_block(page):
    """Task (3): the page reads each report's own what-if files (`site.whatif.universe` / `.scenarios`, names relative to
    reports/), not two fixed files; a report with no precomputed what-if gets the note."""
    uni, wif = _body(page, "loadUniverse"), _body(page, "loadWhatif")
    assert re.search(r"site\.whatif\.universe", uni), "loadUniverse reads the report's universe file"
    assert re.search(r"site\.whatif\.scenarios", wif), "loadWhatif reads the report's scenarios file"
    code = _code(page)
    assert "reports/universe.json" not in code and "reports/whatif.json" not in code
    for name in ("pageRecherche", "pageSimulation"):
        body = _body(page, name)
        assert "site.whatif" in body and "whatifNote(" in body, name


def test_what_if_note_names_no_persona(page):
    """Task (3): the note shown for a report without precomputed what-if says nothing about whose profile it is; it is plain
    wording (SPEC §9.3 blocklist, D10 vocabulary, no typed figure)."""
    body, texts = function_source(page, "whatifNote")
    assert not PERSONA_RE.search(body), PERSONA_RE.search(body)
    assert blocklist_hits(body) == [] and not typed_digits(texts)
    assert "Simulation" in body or "simulation" in body


def test_no_user_facing_string_names_a_persona(page):
    """Task (4): outside comments, the page never writes Robert, Amira or Mateo (picker labels, notes, headings, errors): names
    come from data (index labels, report `site.profile_card.name`, GET /profiles `name`). The flow B file path is a URL."""
    code = _code(page).replace("reports/robert-nvda.json", "reports/<flow-b-file>")
    hits = [code[max(0, m.start() - 40):m.end() + 40].replace("\n", " ") for m in PERSONA_RE.finditer(code)]
    assert hits == []


def test_new_strings_pass_the_blocklist_and_the_d10_vocabulary(page):
    """SPEC §9.3 item 3 / D10 items 1, 2, 4: the new functions print no advisory phrase and no word that proposes, ranks or
    labels a choice (HTML <option> tags are widgets); the templates of the new renderers carry no typed figure."""
    for name in NEW_HOOKS:
        body, texts = function_source(page, name)
        assert blocklist_hits(body) == [], name
        found = D10_WORDS.search(norm(re.sub(r"</?(option|optgroup)\b", " ", body)))
        assert found is None, (name, found and found.group(0))
        if name in RENDERERS:
            assert not typed_digits(texts), (name, typed_digits(texts)[:5])
    exempt = page.replace(H.DISCLAIMER_FR, " ").replace(H.REFUSAL_FR, " ").replace(H.SAMPLE_QUESTION, " ")
    assert blocklist_hits(exempt) == []


def test_static_demo_branch_is_the_one_of_today(page):
    """Task (2): without an API the page is unchanged: empty `coherence-api-base` meta, the picker from reports/index.json
    with the first entry opened, the flow B file loaded by loadFlowB() as its own file, the same load-error messages."""
    assert re.search(r"<meta\s+name=[\"']coherence-api-base[\"']\s+content=[\"']{2}\s*/?>", page)
    body = _body(page, "boot")
    assert re.search(r"\bload\(\s*\w+\[0\]\.file\s*\)", body), "opens the first index entry"
    assert "Impossible de charger la liste des rapports" in page
    assert "Impossible de charger le rapport de la transaction" in _body(page, "loadFlowB")
    assert 'id="pick"' in page and "reports/index.json" in page
    assert "index.json" not in _body(page, "loadFlowB")
    assert "apiBase(" in _body(page, "createProfile") and "apiBase(" in _body(page, "savePortfolio"), \
        "no profile creation or portfolio write without an API"


# ---------------------------------------------------------------- 4. deploy/whatif.py and deploy/build_site.py

def _load_deploy(name: str, filename: str):
    sys.path.insert(0, str(DEPLOY))                     # build_site does `import whatif`
    try:
        spec = importlib.util.spec_from_file_location(name, DEPLOY / filename)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(DEPLOY))
    return mod


@pytest.fixture(scope="module")
def whatif():
    return _load_deploy("deploy_whatif_any_user", "whatif.py")


def _portfolio(persona_dir: Path) -> dict:
    return json.loads((persona_dir / "portfolio.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("persona", ["mateo", "robert"])
def test_whatif_build_precomputes_for_any_profile_directory(whatif, tmp_path, monkeypatch, persona):
    """Task (3): `build(out, profile_dir)` runs on whatever profile directory it is given (here a copy under a neutral name,
    outside fixtures/): the base (value, date, weights), the held share of each asset, the funding modes (the user's
    largest bond holding, if any) and the scenarios all come from that directory's profile + portfolio.json. Two calls in a
    row (two profiles) share nothing."""
    from coherence_analyzer import data
    params = list(inspect.signature(whatif.build).parameters.values())
    assert len(params) >= 2 and params[1].kind in (inspect.Parameter.POSITIONAL_ONLY,
                                                    inspect.Parameter.POSITIONAL_OR_KEYWORD), "build(out, profile_dir)"
    pdir = tmp_path / "people" / "u-7f3a"
    shutil.copytree(FIXTURES / persona, pdir)
    pf = _portfolio(pdir)
    monkeypatch.setattr(whatif, "TARGETS", (5.0,))
    monkeypatch.setenv("COHERENCE_ANALYZER_STORE_DIR", str(tmp_path / "store"))
    out = tmp_path / "out"
    out.mkdir()
    base = whatif.build(out, pdir)
    uni = json.loads((out / "universe.json").read_text(encoding="utf-8"))
    wi = json.loads((out / "whatif.json").read_text(encoding="utf-8"))

    assert base["value_cad"] == pytest.approx(pf["value_cad"], abs=1e-9) and base["as_of"] == pf["as_of"]
    assert base["weights_pct"] == pf["weights_pct"] and uni["base"]["weights_pct"] == pf["weights_pct"]
    assets = data.load_assets()
    assert {a["ticker"] for a in uni["assets"]} == set(assets.index) == set(wi)
    for a in uni["assets"]:
        assert a["held_pct"] == pytest.approx(round(pf["weights_pct"].get(a["ticker"], 0.0), 2), abs=1e-9)
    w = pf["weights_pct"]
    bonds = sorted((t for t in w if assets.loc[t, "asset_class"] == "etf_bond"), key=lambda t: -w[t])
    funding_ids = ["pro_rata_all"] + ([f"specific:{bonds[0]}"] if bonds else [])
    assert [f["id"] for f in base["fundings"]] == funding_ids
    assert base["targets"] == [5.0]
    scen = wi["NVDA"]["scenarios"]                       # neither persona holds NVDA
    assert list(scen) == [f"5|{fid}" for fid in funding_ids]
    s = scen["5|pro_rata_all"]
    assert s["weights_pct"]["NVDA"] == pytest.approx(5.0, abs=1e-2)
    assert sum(s["weights_pct"].values()) == pytest.approx(100.0, abs=0.05)
    assert s["verdict"] in ("coherent", "coherent_with_warnings", "incoherent")
    assert [x["factor"] for x in s["sections"]] == ["objectif", "horizon", "risque", "contraintes", "preferences"]


def test_whatif_and_build_site_code_has_no_persona_in_its_paths(whatif):
    """Task (3): no persona name in whatif.py (constants, names, docstrings: the profile directory is a parameter) and no
    `WHATIF_PERSONA` / persona comparison in build_site.py (the persona list is data: REPORTS, labels and the flow B file)."""
    wi_tree = ast.parse((DEPLOY / "whatif.py").read_text(encoding="utf-8"))
    seen = [n.value for n in ast.walk(wi_tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    seen += [n.id for n in ast.walk(wi_tree) if isinstance(n, ast.Name)] + [n.attr for n in ast.walk(wi_tree) if isinstance(n, ast.Attribute)]
    assert [s[:60] for s in seen if PERSONA_RE.search(s)] == []
    assert not [n for n in vars(whatif) if PERSONA_RE.search(n)]
    bs = (DEPLOY / "build_site.py").read_text(encoding="utf-8")
    assert "WHATIF_PERSONA" not in bs
    for node in ast.walk(ast.parse(bs)):
        if isinstance(node, ast.Compare):
            consts = [c.value for c in ast.walk(node) if isinstance(c, ast.Constant) and isinstance(c.value, str)]
            assert not [c for c in consts if PERSONA_RE.search(c)], ast.unparse(node)[:80]


# ---------------------------------------------------------------- 5. the built static site

def _build_site(mod, out: Path) -> Path:
    saved = os.environ.get("COHERENCE_ANALYZER_STORE_DIR")
    sys.path.insert(0, str(DEPLOY))                     # build_site does `import whatif`
    try:
        built = mod.build(out)
    finally:
        sys.path.remove(str(DEPLOY))
        if saved is None:
            os.environ.pop("COHERENCE_ANALYZER_STORE_DIR", None)
        else:
            os.environ["COHERENCE_ANALYZER_STORE_DIR"] = saved
    return Path(built or out)


def _read_site(out: Path) -> dict:
    rdir = out / "reports"
    index = json.loads((rdir / "index.json").read_text(encoding="utf-8"))
    reports = {e["file"]: json.loads((rdir / e["file"]).read_text(encoding="utf-8")) for e in index}
    return {"out": out, "rdir": rdir, "index": index, "reports": reports}


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> dict:
    out = tmp_path_factory.mktemp("any_user_site") / "site"
    mod = _load_deploy("build_site_any_user", "build_site.py")
    return _read_site(_build_site(mod, out))


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_static_index_is_still_the_four_flow_c_reviews(built):
    """Task (2), frozen P2.D10C constraint: reports/index.json is exactly the 4 flow C reviews (same files, same order);
    the flow B demo report stays a separate file that is not listed."""
    files = [e["file"] for e in built["index"]]
    assert files == ["robert-review-nvda.json", "robert-review.json", "amira-review.json", "mateo-review.json"]
    assert (built["rdir"] / "robert-nvda.json").is_file() and "robert-nvda.json" not in files
    assert all(r["flow"] == "C" for r in built["reports"].values())


def test_every_built_review_has_the_what_if_of_its_own_profile(built):
    """Task (3): `site.whatif` is `{"universe": f, "scenarios": g}` (files inside reports/) for every review, not only
    Robert's: the base of each universe file is that report's profile directory (value, date, weights of its
    portfolio.json), the two reviews of one profile share their files, the three profiles have three different ones, and the
    legacy reports/universe.json + whatif.json are the first review's."""
    by_profile: dict[str, tuple[str, str]] = {}
    for entry in built["index"]:
        rep = built["reports"][entry["file"]]
        wf = rep["site"]["whatif"]
        assert isinstance(wf, dict) and {"universe", "scenarios"} <= set(wf), entry["file"]
        for key in ("universe", "scenarios"):
            assert wf[key] == Path(wf[key]).name and (built["rdir"] / wf[key]).is_file(), (entry["file"], key)
        who = rep["profile_id"].removeprefix("persona-")
        pf = _portfolio(FIXTURES / who)
        uni, scen = _json(built["rdir"] / wf["universe"]), _json(built["rdir"] / wf["scenarios"])
        assert uni["base"]["value_cad"] == pytest.approx(pf["value_cad"], abs=1e-9), entry["file"]
        assert uni["base"]["as_of"] == pf["as_of"] and uni["base"]["weights_pct"] == pf["weights_pct"], entry["file"]
        assert "NVDA" in scen and scen["NVDA"]["scenarios"], entry["file"]
        assert by_profile.setdefault(rep["profile_id"], (wf["universe"], wf["scenarios"])) == (wf["universe"], wf["scenarios"])
    assert len(by_profile) == 3 and len({u for u, _ in by_profile.values()}) == 3 == len({s for _, s in by_profile.values()})
    first = built["reports"][built["index"][0]["file"]]["site"]["whatif"]
    assert _json(built["rdir"] / "universe.json") == _json(built["rdir"] / first["universe"])
    assert _json(built["rdir"] / "whatif.json") == _json(built["rdir"] / first["scenarios"])


def test_the_reports_list_and_not_a_persona_constant_choose_the_what_if_profile(tmp_path):
    """Task (3): `build_site` precomputes the what-if of whatever profiles REPORTS names: a REPORTS list holding one profile
    directory with a neutral name (a copy of a seed, outside the repo) gives a one-entry index whose what-if is that
    directory's, and the legacy reports/universe.json + whatif.json are its own."""
    fx = tmp_path / "fx"
    shutil.copytree(FIXTURES / "robert", fx / "robert")                  # the flow B demo report still reads it
    shutil.copytree(FIXTURES / "mateo", fx / "someone-new")
    mod = _load_deploy("build_site_any_user_reports", "build_site.py")
    assert not hasattr(mod, "WHATIF_PERSONA")
    mod.FIXTURES = fx
    mod.REPORTS = [("someone-review", "someone-new", "portfolio.json", "Profil de test")]
    site = _read_site(_build_site(mod, tmp_path / "site"))
    assert [e["file"] for e in site["index"]] == ["someone-review.json"]
    rep = site["reports"]["someone-review.json"]
    wf = rep["site"]["whatif"]
    assert isinstance(wf, dict) and (site["rdir"] / wf["universe"]).is_file() and (site["rdir"] / wf["scenarios"]).is_file()
    pf = _portfolio(fx / "someone-new")
    for name in (wf["universe"], "universe.json"):
        base = _json(site["rdir"] / name)["base"]
        assert base["value_cad"] == pytest.approx(pf["value_cad"], abs=1e-9) and base["weights_pct"] == pf["weights_pct"], name
    assert "NVDA" in _json(site["rdir"] / "whatif.json")
    assert (site["rdir"] / "robert-nvda.json").is_file(), "the flow B demo report is its own file, whatever REPORTS says"

