"""P5.DASH oracle: the dashboard works for any user, not only the three personas (SPEC §12 UI + route table, §13;
docs/spec-factors.md §20.6 intake, §20.7 UI; docs/spec-decisions.md D7, D10; owner request 2026-11-14).

RETARGETED (2026-11-15, index.html -> app.html): index.html is being retired; the newer multi-screen English
`deploy/site/app.html` + its modular JS under `deploy/site/js/` now carries this behaviour. Same guarantees as
the old single-file oracle, different real architecture:

  1. The persona picker (`<select data-persona>` in `app.html`, built by `summary.js`'s `pickerOptions`) lists
     every persona of `App.state.personas` -- live, that list comes from `GET /profiles`
     (`data_source.js`'s `init()` / `listProfiles()`); static, from the prebuilt `reports/en-CA/index.json`. The
     page renders the live review through one dedicated route, `POST /site-api/profiles/{id}/review`.
  2. New profile: `summary.js`'s `createNewProfile` posts `POST /profiles {locale, display_name?}` (locale fixed
     to `en-CA` -- this surface is English-only by design, unlike the old bilingual index.html picker; the
     optional name is asked through a prompt, never prefilled), then routes to `conversation.js`'s intake Q&A,
     which (task (1)) also handles the "no portfolio yet" case with its own portfolio-entry form
     (`PUT /profiles/{id}/portfolio {as_of, value_cad, weights_pct}`).
  3. `deploy/whatif.py` `build(out, profile_dir)` precomputes for any profile directory (untouched: no persona is
     hard-coded there, before or after this task); `deploy/build_site.py` builds the EN what-if files once per
     persona in `REPORTS` and embeds them by naming convention (`data_source.js`'s `personaFile`), not a
     `site.whatif` dict; the FR pipeline backing `index.html` is unchanged (its own `site.whatif` convention stays).
  4. No user-facing string names Robert, Amira or Mateo (labels come from data); every new string passes the
     SPEC §9.3 blocklist and the D10 vocabulary.

Offline and deterministic: COHERENCE_ANALYZER_LLM=mock, JS read as text (no browser, no network, no clock), the
build runs the real pipeline in mock mode into temp dirs. `deploy/site/index.html` is untouched here (it is
retired separately); `deploy/site/js/core/data_source.js` is a read-only reference, not owned by this task: this
file points assertions at its real function and route names, it does not reimplement them. The JS scanner and the
SPEC §9.3 blocklist / D10 vocabulary of the (also retargeted) `tests/dashboard/test_ui_flow_b.py`, and the generic
JS-expression helpers of `tests/dashboard/test_ui_intake_confirm.py`, are reused here, not copied.

Real hooks pinned here (each `function <name>(`, defined once in its file, used elsewhere):
  shell.js            loadData, loadPersona, setPersona
  data_source.js      listProfiles, createProfile, savePortfolio
  summary.js          pickerOptions, createNewProfile, switchPersona
  conversation.js     portfolioHtml, submitPortfolio, staticMissingHtml, staticMissingPaths
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
SITE = DEPLOY / "site"
JS = SITE / "js"
FIXTURES = ROOT / "fixtures"
FROZEN_FLOW_B = ROOT / "tests" / "dashboard" / "test_ui_flow_b.py"
FROZEN_INTAKE = ROOT / "tests" / "dashboard" / "test_ui_intake_confirm.py"

SITE_FILES = {
    "app.html": SITE / "app.html",
    "shell.js": JS / "core" / "shell.js",
    "data_source.js": JS / "core" / "data_source.js",
    "summary.js": JS / "screens" / "summary" / "summary.js",
    "conversation.js": JS / "screens" / "conversation.js",
    "whatif.js": JS / "screens" / "whatif" / "whatif.js",
}


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# The JS scanner, the SPEC §9.3 blocklist and the D10 vocabulary of the (also retargeted) flow-B oracle are
# reused, not copied; the generic JS-expression helpers (call-argument parsing) of the intake oracle likewise.
H = _load_module("_ui_flow_b_helpers_for_any_user", FROZEN_FLOW_B)
U = _load_module("_ui_intake_helpers_for_any_user", FROZEN_INTAKE)
function_source, typed_digits, uses, norm = H.function_source, H.typed_digits, H.uses, H.norm
string_literals, blocklist_hits, D10_WORDS = H.string_literals, H.blocklist_hits, H.D10_WORDS
calls_to, body_keys, _assigned_function = U.calls_to, U.body_keys, U._assigned_function

PERSONAS = ("Robert", "Amira", "Mateo")
PERSONA_RE = re.compile(r"\b(robert|amira|mateo)\b", re.I)
HOOKS = (
    ("shell.js", "loadData"),
    ("shell.js", "loadPersona"),
    ("shell.js", "setPersona"),
    ("data_source.js", "listProfiles"),
    ("data_source.js", "createProfile"),
    ("data_source.js", "savePortfolio"),
    ("summary.js", "pickerOptions"),
    ("summary.js", "createNewProfile"),
    ("summary.js", "switchPersona"),
    ("conversation.js", "portfolioHtml"),
    ("conversation.js", "submitPortfolio"),
    ("conversation.js", "staticMissingHtml"),
    ("conversation.js", "staticMissingPaths"),
)


@pytest.fixture(scope="module")
def site() -> dict[str, str]:
    return {name: path.read_text(encoding="utf-8") for name, path in SITE_FILES.items()}


def _body(site: dict, filename: str, name: str) -> str:
    return function_source(site[filename], name)


def _uses_any(site: dict, name: str) -> int:
    """Cross-file call count: these screens call each other's exports only through `App.*`, not directly, so a
    hook defined in one file is normally used from another."""
    return sum(uses(text, name) for text in site.values())


# ---------------------------------------------------------------- 0. hooks

def test_new_hooks_are_defined_once_and_used(site):
    """The picker, live-review, new-profile and portfolio hooks exist as real `function`s, each defined exactly
    once in its own file and called from somewhere in the app."""
    for filename, name in HOOKS:
        function_source(site[filename], name)
        assert _uses_any(site, name) >= 2, f"{filename}:{name} is defined but never called"


# ---------------------------------------------------------------- 1. picker: GET /profiles when an API is set

def test_boot_reads_the_api_only_when_one_is_configured(site):
    """Task (1): `shell.js`'s `loadData()` is the entry point: it awaits `App.source.init()` before reading
    `App.source.personas()`. `data_source.js`'s `init()` always loads the prebuilt index first (the static
    fallback, read regardless), then probes `api/health`; only on success does it `GET /profiles` and switch to
    live mode -- so the live branch is reached only when a real API answers, never a hard-coded assumption."""
    load_body = _body(site, "shell.js", "loadData")
    assert "App.source.init()" in load_body and "App.source.personas()" in load_body
    assert load_body.index("App.source.init()") < load_body.index("App.source.personas()")
    init_body = _body(site, "data_source.js", "init")
    assert "loadStaticIndex()" in init_body
    assert re.search(r"request\(\s*apiDir\(\)\s*\+\s*[\"']health[\"']", init_body)
    assert re.search(r"request\(\s*apiDir\(\)\s*\+\s*[\"']profiles[\"']\s*\)", init_body)
    assert init_body.index("loadStaticIndex()") < init_body.index('"health"')
    assert init_body.index('"health"') < init_body.index('"profiles"')
    assert "mode = MODE_STATIC" in init_body, "a failed health probe falls back to static, never throws"


def test_every_fetch_is_through_the_one_data_layer(site):
    """Every screen (shell.js, summary.js, conversation.js) calls only `App.source.*` / `App.*`; `data_source.js`
    is the one file issuing network requests, and it does so through exactly one `fetch(`, inside `request()`."""
    for filename in ("shell.js", "summary.js", "conversation.js"):
        assert "fetch(" not in site[filename], filename
    assert site["data_source.js"].count("fetch(") == 1
    assert "fetch(" in _body(site, "data_source.js", "request")
    assert not re.search(r"fetch\(\s*[\"'`]https?://", site["data_source.js"]), "no absolute URL in a fetch call"


def test_list_profiles_is_a_plain_get_of_the_profiles_route(site):
    """SPEC §12 / task (1): `listProfiles()` is a plain `GET /profiles` (no `method:`), used to refresh the
    picker's list after a new profile is created; `init()`'s own `GET /profiles` is likewise a plain read."""
    body = _body(site, "data_source.js", "listProfiles")
    assert re.search(r"""request\(\s*apiDir\(\)\s*\+\s*["']profiles["']\s*\)""", body)
    assert not re.search(r"\bmethod\s*:", body)
    assert "requireLive(" in body, "no profile list without a live API"
    init_body = _body(site, "data_source.js", "init")
    assert not re.search(r"\bmethod\s*:", init_body.split('"profiles"')[0].split('"health"')[1])


def test_persona_picker_lists_the_profiles_and_offers_a_new_profile_only_when_live(site):
    """Task (1): `pickerOptions()` builds one `<option>` per persona of `App.state.personas` (id as the value,
    name escaped as the label) -- the same list whether it came from `GET /profiles` (live) or the prebuilt index
    (static); the renderer appends the `+ New profile` entry only in live mode (static: no profile creation), and
    no persona name is typed into either function."""
    options_body = _body(site, "summary.js", "pickerOptions")
    assert re.search(r"\bperson\.id\b", options_body) and re.search(r"\bperson\.name\b", options_body)
    render_body = _assigned_function(site, "summary.js", r"App\.renderers\.summary\s*=\s*function\s*\(")
    assert "pickerOptions(" in render_body
    assert "App.esc(" in render_body, "the picker's values and labels are escaped when painted"
    assert re.search(r"""App\.source\.mode\s*===\s*["']live["']\s*\?\s*options\.concat""", render_body)
    assert "NEW_PROFILE_LABEL" in render_body and "NEW_PROFILE_VALUE" in render_body
    assert not PERSONA_RE.search(options_body) and not PERSONA_RE.search(render_body)


def test_picker_change_routes_new_profile_or_persona_switch(site):
    """`select.onchange` (bound in `App.renderers.summary`) routes `NEW_PROFILE_VALUE` to `createNewProfile` and
    anything else to `switchPersona`; both show an inline error on failure and restore the select, never a
    silently swallowed failure."""
    render_body = _assigned_function(site, "summary.js", r"App\.renderers\.summary\s*=\s*function\s*\(")
    assert re.search(r"value\s*===\s*NEW_PROFILE_VALUE", render_body)
    assert "createNewProfile(" in render_body and "switchPersona(" in render_body
    switch_body = _body(site, "summary.js", "switchPersona")
    assert "App.setPersona(" in switch_body and "showError(" in switch_body and "catch" in switch_body
    new_body = _body(site, "summary.js", "createNewProfile")
    assert "showError(" in new_body and "catch" in new_body


# ---------------------------------------------------------------- 1b. live review

def test_load_persona_posts_the_review_route_with_no_ai_or_limit_body(site):
    """SPEC §12: `shell.js`'s `loadPersona()` calls `App.source.report` / `App.source.rerun`, which (live) POST
    `/site-api/profiles/{id}/review` -- data_source.js's `runLiveReview` calls `post(...)` with no second
    argument at all, so nothing is sent that could be a limit or an AI field (stronger than an empty `{}` body);
    universe and what-if are fetched in the same `Promise.all`, and a failed load falls back to the prebuilt
    report (`liveOrFallback`) rather than ever being silently empty."""
    review_body = _body(site, "data_source.js", "runLiveReview")
    calls = calls_to(review_body, "post")
    assert len(calls) == 1 and "review" in calls[0][0]
    assert len(calls[0]) == 1, "no body argument: nothing is sent"
    load_body = _body(site, "shell.js", "loadPersona")
    assert "Promise.all(" in load_body and "App.source.universe(" in load_body and "App.source.whatif(" in load_body
    assert "applyState(" in load_body
    fallback_body = _body(site, "data_source.js", "liveOrFallback")
    assert "catch" in fallback_body and "staticReport(" in fallback_body
    for forbidden in ("limits", "llm:"):
        assert forbidden not in review_body


def test_the_live_site_block_is_built_server_side_not_by_the_page(site):
    """Retarget, guarantee inverted on purpose (same as test_ui_flow_b.py's equivalent note for trade.js):
    index.html's `loadLiveReview` had to build a `site` block client-side from a separate profile GET, because
    the old API had no dedicated route for it. The app's `/site-api/profiles/{id}/review` (`deploy/serve_local.py`
    `live_review`) already attaches `report["site"]` server-side, via the same rules code
    (`build_site.site_extras`) the static build uses -- so the client never computes or overrides `site` or
    `limits` at all; the live review response is used as-is."""
    for filename in ("data_source.js", "shell.js"):
        assert not re.search(r"\.site\s*=|\bsite\s*:", site[filename]), filename
        assert "limits" not in site[filename], filename


def test_universe_and_whatif_are_always_fresh_for_the_current_persona(site):
    """What-if files are per-profile now, named by persona id: `loadPersona()` always re-fetches both and
    overwrites `App.state.universe` / `App.state.whatif` via `applyState` on every persona switch, so there is no
    separate cache to reset and no way for one persona's what-if to leak into another's render."""
    body = _body(site, "shell.js", "loadPersona")
    assert re.search(r"universe,\s*whatif\s*\]\s*=\s*await\s*Promise\.all", body)
    assert re.search(r"applyState\(\{[^}]*\buniverse\b[^}]*\bwhatif\b", body, re.S) or \
        (re.search(r"\buniverse\b", body) and re.search(r"\bwhatif\b", body) and "applyState(" in body)
    data_source = site["data_source.js"]
    assert "universeCache" not in data_source and "whatifCache" not in data_source, \
        "no separate universe/whatif cache variable to go stale: personaFile() always re-fetches by persona id"


# ---------------------------------------------------------------- 2. new profile -> intake -> portfolio

def test_create_new_profile_sends_a_fixed_locale_and_an_optional_typed_name(site):
    """Task (2) / SPEC-AMBIGUITY: this surface is English-only (unlike the old bilingual index.html picker), so
    there is no locale choice to offer; `createNewProfile` asks (via `window.prompt`, never prefilled) for an
    optional display name and sends `{locale: "en-CA"}` or `{locale, display_name}` -- a blank/cancelled name is
    never sent -- then starts the onboarding by routing to `#/conversation`."""
    body = _body(site, "summary.js", "createNewProfile")
    assert "window.prompt(" in body
    assert "typed === null" in body, "a cancelled prompt creates no profile"
    assert 'display_name ? {locale: NEW_PROFILE_LOCALE, display_name} : {locale: NEW_PROFILE_LOCALE}' in body
    assert ".trim()" in body, "a blank name is never sent"
    assert 'App.go("#/conversation")' in body
    for forbidden in ("limits", "llm"):
        assert forbidden not in body


def test_create_profile_route_sends_exactly_the_given_body(site):
    """`data_source.js`'s `createProfile(body)` is a thin `POST /profiles` wrapper: whatever body
    `createNewProfile` built is sent unchanged (no merge, no extra field), and the API answers 422 for unknown
    keys (api.ProfileIn, `extra="forbid"`)."""
    from coherence_analyzer import api
    route_body = _body(site, "data_source.js", "createProfile")
    calls = calls_to(route_body, "post", r"profiles")
    assert len(calls) == 1 and calls[0][1] == "body"
    assert "requireLive(" in route_body
    api.ProfileIn.model_validate({"locale": "en-CA"})
    api.ProfileIn.model_validate({"locale": "en-CA", "display_name": "My profile"})
    new_body = _body(site, "summary.js", "createNewProfile")
    assert "App.source.createProfile(" in new_body and "App.source.listProfiles(" in new_body
    assert re.search(r"App\.state\.persona\s*=\s*profileId", new_body), "the new id becomes the current persona"


def test_portfolio_entry_appears_only_when_the_review_route_says_no_portfolio_yet(site):
    """Task (2): `conversation.js`'s `runAnalysis` catches the server's 409 for a portfolio-less profile
    (`NO_PORTFOLIO_PATTERN`, matching api.py's `post_review` "no portfolio yet" message) and sets
    `screen.needsPortfolio`; `renderCurrent` then shows `portfolioHtml()` instead of the intake Q&A; any other
    failure is shown as a run error instead."""
    body = _body(site, "conversation.js", "runAnalysis")
    assert "NO_PORTFOLIO_PATTERN.test(" in body
    assert "screen.needsPortfolio = true" in body
    render_body = _body(site, "conversation.js", "renderCurrent")
    assert re.search(r"screen\.needsPortfolio\)\s*\{\s*container\.innerHTML\s*=\s*portfolioHtml\(\)", render_body)


def test_portfolio_form_has_a_value_and_weight_rows_nothing_prefilled(site):
    """PUT body fields: `value_cad` (text/decimal input) and weights as rows of `ticker` + `weight` the user can
    add to (`data-portfolio-add`); every field starts from `screen.portfolioValue` / `screen.portfolioRows`,
    which are empty strings until typed -- no typed figure, no typed ticker. `as_of` is stamped from today's date
    at submit time (`new Date().toISOString()`), the same clock-stamping already used for a trade's event `ts`
    (`App.source.requestDraft` in trade.js): a submission timestamp, not a value the user had to state."""
    body = _body(site, "conversation.js", "portfolioHtml")
    assert "data-pf-value" in body and "data-portfolio-form" in body
    assert "data-portfolio-add" in body
    row_body = _body(site, "conversation.js", "portfolioRowHtml")
    assert "data-pf-ticker" in row_body and "data-pf-weight" in row_body
    assert 'value="' + "'" + '"' not in body  # no obviously hard-coded literal value
    assert "screen.portfolioValue" in body and "screen.portfolioRows" in body
    empty_body = _body(site, "conversation.js", "emptyPortfolioRows")
    assert 'ticker: ""' in empty_body and 'weight: ""' in empty_body
    submit_body = _body(site, "conversation.js", "submitPortfolio")
    assert "new Date().toISOString()" in submit_body


def test_save_portfolio_sums_the_weights_before_sending_exactly_those_three_keys(site):
    """SPEC-AMBIGUITY (P5.USERS): `PUT /profiles/{id}/portfolio {as_of, value_cad, weights_pct}`; weights are
    summed to 100 +- 0.01 before anything is sent (numbers via `Number(...)`); on success the onboarding ends and
    the analysis is retried automatically; a failure is shown and the form stays editable."""
    body = _body(site, "conversation.js", "submitPortfolio")
    assert "App.source.savePortfolio(" in body
    calls = calls_to(body, "savePortfolio")
    assert len(calls) == 1
    keys, _ = body_keys(body, calls[0][1])
    assert sorted(keys) == ["as_of", "value_cad", "weights_pct"], keys
    assert "Number(" in body
    assert "Math.abs(sum - 100) > 0.01" in body
    assert body.index("Math.abs(sum - 100) > 0.01") < body.index("App.source.savePortfolio(")
    assert "screen.needsPortfolio = false" in body
    assert "runAnalysis()" in body
    assert "catch" in body and "screen.portfolioError" in body
    for forbidden in ("limits", "llm"):
        assert forbidden not in body


def test_save_portfolio_route_puts_through_a_plain_request_not_post(site):
    """`data_source.js`'s `savePortfolio` is the one place besides `post()` that sets an explicit `method:`
    (`"PUT"`) -- exactly two `method:` occurrences in the whole data layer, one POST default and this one PUT;
    no PATCH/DELETE/XHR anywhere."""
    body = _body(site, "data_source.js", "savePortfolio")
    assert re.search(r"""method:\s*["']PUT["']""", body)
    assert "requireLive(" in body
    data_source = site["data_source.js"]
    assert len(re.findall(r"\bmethod\s*:", data_source)) == 2
    assert data_source.count('"POST"') == 1
    assert not re.search(r"XMLHttpRequest|\bDELETE\b|\bPATCH\b", data_source)


# ---------------------------------------------------------------- 3. what-if for any profile; no persona in the page

def test_what_if_files_are_named_from_the_persona_id_not_two_fixed_files(site):
    """Task (3): `data_source.js`'s `universe(personaId)` / `whatif(personaId)` read
    `reports/en-CA/universe-<personaId>.json` / `whatif-<personaId>.json` -- derived from the persona id itself,
    never two shared files, and a persona the prebuilt index does not list throws (`requireKnownPersona`) instead
    of silently serving another one's files."""
    body = _body(site, "data_source.js", "personaFile")
    assert "requireKnownPersona(" in body
    assert re.search(r"""REPORT_DIR\s*\+\s*prefix\s*\+\s*["']-["']\s*\+\s*personaId""", body)
    assert "universe.json" not in body and "whatif.json" not in body


def test_what_if_note_names_no_persona(site):
    """Task (3): `whatif.js`'s empty/unavailable notes (shown when a profile has no precomputed what-if, or the
    requested asset isn't in it) say nothing about whose profile it is; plain wording, no typed figure, no D10
    word."""
    body = site["whatif.js"]
    for const in ("EMPTY_NO_DATA", "EMPTY_UNAVAILABLE", "BASE_PROFILE_NOTE"):
        assert const in body, const
    notes_block = body[body.index("EMPTY_NONE"):body.index("LIVE_LOADING")]
    assert not PERSONA_RE.search(notes_block)
    assert blocklist_hits(notes_block) == []
    assert D10_WORDS.search(norm(notes_block)) is None
    assert not typed_digits(notes_block)


def test_no_user_facing_string_names_a_persona(site):
    """Task (4): outside this file's own docstring/comments, none of app.html or its JS writes Robert, Amira or
    Mateo: picker labels, notes, headings and errors all come from data (the prebuilt index's own labels, a
    report's `site.profile_card.name`, or `GET /profiles`' own `name`)."""
    for filename, text in site.items():
        code = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
        code = re.sub(r"(?m)(^|\s)//[^\n]*", " ", code)
        hits = [code[max(0, m.start() - 40):m.end() + 40].replace("\n", " ") for m in PERSONA_RE.finditer(code)]
        assert hits == [], (filename, hits)


def test_new_strings_pass_the_blocklist_and_the_d10_vocabulary(site):
    """SPEC §9.3 item 3 / D10 items 1, 2, 4: the new functions print no advisory phrase and no word that
    proposes, ranks or labels a choice (HTML <option> tags are widgets); no typed figure in their built markup."""
    for filename, name in HOOKS:
        body = function_source(site[filename], name)
        literal = " ".join(string_literals(body))
        assert blocklist_hits(literal) == [], (filename, name)
        found = D10_WORDS.search(norm(re.sub(r"</?(option|optgroup)\b", " ", literal)))
        assert found is None, (filename, name, found and found.group(0))
    for filename in site:
        assert blocklist_hits(site[filename]) == [], filename


def test_static_demo_branch_needs_no_api_and_offers_no_profile_creation(site):
    """Task (2): without an API the page is unchanged in spirit: an empty `coherence-api-base` meta, the picker
    from the prebuilt index, and no profile creation or portfolio write possible -- both gated behind
    `App.source.mode === "live"` (createNewProfile is only ever reachable through the live-only `+ New profile`
    picker entry; savePortfolio refuses outside live mode)."""
    app_html = site["app.html"]
    assert re.search(r"<meta\s+name=[\"']coherence-api-base[\"']\s+content=[\"']{2}\s*/?>", app_html)
    assert 'data-persona' in app_html
    assert "requireLive(" in _body(site, "data_source.js", "createProfile")
    assert "requireLive(" in _body(site, "data_source.js", "savePortfolio")
    options_body = _body(site, "summary.js", "pickerOptions")
    assert "new-profile" not in options_body.lower() or "NEW_PROFILE_VALUE" not in options_body


# ---------------------------------------------------------------- 4. deploy/whatif.py and deploy/build_site.py

def _load_deploy(name: str, filename: str):
    sys.path.insert(0, str(DEPLOY))                     # build_site does `import whatif` / `import preview_report`
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
    """Task (3): `build(out, profile_dir)` runs on whatever profile directory it is given (here a copy under a
    neutral name, outside fixtures/): the base (value, date, weights), the held share of each asset, the funding
    modes (the user's largest bond holding, if any) and the scenarios all come from that directory's profile +
    portfolio.json. Two calls in a row (two profiles) share nothing. Unaffected by the app port: `whatif.py` never
    names a persona, before or after this task."""
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
    """Task (3): no persona name in whatif.py (constants, names, docstrings: the profile directory is a
    parameter) and no `WHATIF_PERSONA` / persona comparison in build_site.py (the persona list is data: REPORTS,
    labels and the flow B file)."""
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


# ---------------------------------------------------------------- 5. the built static site (FR pipeline, index.html)

def _build_site(mod, out: Path) -> Path:
    saved = os.environ.get("COHERENCE_ANALYZER_STORE_DIR")
    sys.path.insert(0, str(DEPLOY))                     # build_site does `import whatif` / `import preview_report`
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
    """Task (2), frozen P2.D10C constraint: the FR `reports/index.json` (backing `index.html`, being retired
    separately) is exactly the 4 flow C reviews (same files, same order); the flow B demo report stays a separate
    file that is not listed. Untouched by the app.html port: build_site.py's FR pipeline is unchanged."""
    files = [e["file"] for e in built["index"]]
    assert files == ["robert-review-nvda.json", "robert-review.json", "amira-review.json", "mateo-review.json"]
    assert (built["rdir"] / "robert-nvda.json").is_file() and "robert-nvda.json" not in files
    assert all(r["flow"] == "C" for r in built["reports"].values())


def test_every_built_fr_review_has_the_what_if_of_its_own_profile(built):
    """Task (3): `site.whatif` is `{"universe": f, "scenarios": g}` (files inside reports/) for every FR review,
    not only Robert's: the base of each universe file is that report's profile directory (value, date, weights of
    its portfolio.json), the two reviews of one profile share their files, the three profiles have three
    different ones, and the legacy reports/universe.json + whatif.json are the first review's. (The EN pipeline
    for app.html uses a naming convention instead of a `site.whatif` dict -- see
    `test_what_if_files_are_named_from_the_persona_id_not_two_fixed_files` above.)"""
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


def test_the_reports_list_and_not_a_persona_constant_choose_the_fr_what_if_profile(tmp_path):
    """Task (3): `build_site` precomputes the FR what-if of whatever profiles `REPORTS` names: a `REPORTS` list
    holding one profile directory with a neutral name (a copy of a seed, outside the repo) gives a one-entry
    index whose what-if is that directory's, and the legacy reports/universe.json + whatif.json are its own."""
    fx = tmp_path / "fx"
    shutil.copytree(FIXTURES / "robert", fx / "robert")                  # the flow B demo report still reads it
    shutil.copytree(FIXTURES / "mateo", fx / "someone-new")
    mod = _load_deploy("build_site_any_user_reports", "build_site.py")
    assert not hasattr(mod, "WHATIF_PERSONA")
    mod.FIXTURES = fx
    mod.REPORTS = [("someone-review", "someone-new", "portfolio.json", "Profil de test")]
    site_out = _read_site(_build_site(mod, tmp_path / "site"))
    assert [e["file"] for e in site_out["index"]] == ["someone-review.json"]
    rep = site_out["reports"]["someone-review.json"]
    wf = rep["site"]["whatif"]
    assert isinstance(wf, dict) and (site_out["rdir"] / wf["universe"]).is_file() and (site_out["rdir"] / wf["scenarios"]).is_file()
    pf = _portfolio(fx / "someone-new")
    for name in (wf["universe"], "universe.json"):
        base = _json(site_out["rdir"] / name)["base"]
        assert base["value_cad"] == pytest.approx(pf["value_cad"], abs=1e-9) and base["weights_pct"] == pf["weights_pct"], name
    assert "NVDA" in _json(site_out["rdir"] / "whatif.json")
    assert (site_out["rdir"] / "robert-nvda.json").is_file(), "the flow B demo report is its own file, whatever REPORTS says"
