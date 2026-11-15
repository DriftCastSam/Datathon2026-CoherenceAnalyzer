"""P3.UIINTAKE oracle: dashboard B2 draft + confirm body, intake form, theme `max_pct` input
(SPEC §12 UI + route table, §4 Flow B B2, §16 Amira; docs/spec-factors.md §20.6, §20.7, §20.9;
docs/spec-decisions.md D7 point 1, D8 point 1, D10).

RETARGETED (2026-11-15, index.html -> app.html): index.html is being retired; the newer multi-screen English
`deploy/site/app.html` + its modular JS under `deploy/site/js/` now carries this behaviour. The guarantees below
are the same ones the old single-file oracle pinned; only what is scanned, and how, changed. (The sibling B2/N3
oracle `tests/dashboard/test_ui_flow_b.py` was retargeted the same way, ahead of this file, by a parallel lane;
its JS scanner (`function_source`, `string_literals`, `reconstructed_markup`, `typed_digits`, `uses`,
`blocklist_hits`, `norm`, `D10_WORDS`, `FUNDING_KINDS`) is reused here, not copied.)

  1. B2. `deploy/site/js/screens/trade.js` creates the draft (`App.source.requestDraft` -> `POST
     /profiles/{id}/events`, an `order_intent` event whose body validates with `api.EventIn`), shows the returned
     `intent` in the confirm card, and confirms with a body of EXACTLY the keys `api.ConfirmIn` accepts (`ticker`,
     `target_weight_pct`, `funding`; `extra="forbid"`) through `App.source.confirmIntent`. The confirm button stays
     disabled after a successful confirm, and the whole confirm card is read-only in the offline/static demo
     (trade.js's own worked example, `reports/en-CA/robert-nvda.json`, built by `deploy/preview_report.py`'s
     `build_en_ca`): neither path can resend a confirmed intent.
  2. Intake. `deploy/site/js/screens/conversation.js` shows every required question (`App.source.intake`, which
     reads `GET /profiles/{id}` plus the build's static question manifest rather than the backend's one-question
     `/intake` route) and answers / skips through `App.source.answer` / `App.source.skip`
     (`POST .../intake/answer {path, value}` / `POST .../intake/skip {path}`, AnswerIn / SkipIn). The offline/static
     demo (no API) shows the still-missing paths read-only, from `site.intake` / `intake` embedded by
     `deploy/build_site.py`'s `missing_by_factor`.
  3. N3. For a `max_theme_weight` rule with no `max_pct`, `deploy/site/js/screens/pillars/preferences.js`'s
     `handleRuleDecision` asks for the number (a `window.prompt`, Amira §16) and sends it with the decision
     (`DecisionIn.max_pct`, 0 < x <= 100) through `App.source.decideRule`; a rule that already has one is decided
     with `{approved}` only (the API answers 409 otherwise).

Still offline: no browser, no network, no clock, no JS runtime; the pages/scripts are scanned as text; the backend
contracts are the pydantic models of `coherence_analyzer/api.py`, imported, not edited. `deploy/site/index.html`
is untouched here (it is retired separately) and `deploy/site/js/screens/pillars/preferences.js`,
`deploy/site/js/screens/trade.js` and `deploy/site/js/core/data_source.js` are read-only references: this file
points assertions at their real function and route names, it does not reimplement them.

Real hooks pinned here (each `function <name>(`, defined once in its file, used elsewhere):
  trade.js            handleDraftSubmit, handleConfirmSubmit, renderConfirmFields, ensureConfirmPainted
  conversation.js     loadQuestions, saveAnswer, skipQuestion, staticMissingHtml
  data_source.js      requestDraft, confirmIntent, intake, answer, skip, decideRule
  preferences.js      handleRuleDecision
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
SITE = DEPLOY / "site"
JS = SITE / "js"
FROZEN = ROOT / "tests" / "dashboard" / "test_ui_flow_b.py"

SITE_FILES = {
    "app.html": SITE / "app.html",
    "data_source.js": JS / "core" / "data_source.js",
    "intake_questions.js": JS / "core" / "intake_questions.js",
    "conversation.js": JS / "screens" / "conversation.js",
    "preferences.js": JS / "screens" / "pillars" / "preferences.js",
    "trade.js": JS / "screens" / "trade.js",
}


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# The JS scanner, SPEC §9.3 blocklist and D10 vocabulary of the (also retargeted) Flow B UI oracle are reused,
# not copied: they are generic text-scanning utilities parameterised by the string handed to them.
H = _load_module("_ui_flow_b_helpers_for_intake", FROZEN)
function_source, typed_digits, uses, norm = H.function_source, H.typed_digits, H.uses, H.norm
string_literals, blocklist_hits = H.string_literals, H.blocklist_hits
FUNDING_KINDS = H.FUNDING_KINDS
D10_WORDS = H.D10_WORDS

KIND_PARAM = {"specific": "ticker", "pro_rata_class": "asset_class", "cash_contribution": "amount_cad", "pro_rata_all": None}
HOOKS = (
    ("trade.js", "handleDraftSubmit"),
    ("trade.js", "handleConfirmSubmit"),
    ("trade.js", "renderConfirmFields"),
    ("trade.js", "ensureConfirmPainted"),
    ("conversation.js", "loadQuestions"),
    ("conversation.js", "saveAnswer"),
    ("conversation.js", "skipQuestion"),
    ("conversation.js", "staticMissingHtml"),
)


def _api():
    return importlib.import_module("coherence_analyzer.api")


@pytest.fixture(scope="module")
def site() -> dict[str, str]:
    return {name: path.read_text(encoding="utf-8") for name, path in SITE_FILES.items()}


def _body(site: dict, filename: str, name: str) -> str:
    return function_source(site[filename], name)


def _assigned_function(site: dict, filename: str, pattern: str) -> str:
    """Body of a function assigned to a property (`Foo.bar = function (x) {...}`), not a named `function`
    statement: `App.renderers.trade = function (section) {...}` cannot be found by `function_source`."""
    text = site[filename]
    m = re.search(pattern, text)
    assert m, f"{pattern!r} not found in {filename}"
    brace_open = text.index("{", m.end())
    brace_close = H._balanced(text, brace_open, "{", "}")
    return text[brace_open + 1:brace_close - 1]


# ---------------------------------------------------------------- small JS expression helpers (text, no runtime)

def split_top(s: str) -> list[str]:
    """Split on top-level commas (strings, template literals, comments, (), [], {} respected)."""
    parts, depth, cur, i = [], 0, 0, 0
    while i < len(s):
        te = H._token_end(s, i)
        if te is not None:
            i = te
            continue
        c = s[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            parts.append(s[cur:i].strip())
            cur = i + 1
        i += 1
    tail = s[cur:].strip()
    if tail:
        parts.append(tail)
    return parts


def _matching(s: str, i: int) -> int:
    """Index just after the bracket group that opens at s[i]."""
    close = {"(": ")", "[": "]", "{": "}"}[s[i]]
    return H._balanced(s, i, s[i], close)


def read_expr(s: str, i: int) -> str:
    """The expression starting at s[i], up to a top-level `;` or newline."""
    j, depth = i, 0
    while j < len(s):
        te = H._token_end(s, j)
        if te is not None:
            j = te
            continue
        c = s[j]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c in ";\n" and depth <= 0:
            break
        j += 1
    return s[i:j].strip()


def calls_to(body: str, fn_name: str, path_re: str | None = None) -> list[list[str]]:
    """Argument lists of every `fn_name(<args>)` call, optionally filtered by a regex on the first argument."""
    out = []
    for m in re.finditer(rf"\b{re.escape(fn_name)}\s*\(", body):
        end = _matching(body, m.end() - 1)
        args = split_top(body[m.end():end - 1])
        if path_re is None or (args and re.search(path_re, args[0])):
            out.append(args)
    return out


def object_keys(obj: str) -> list[str]:
    """Keys of a `{...}` literal (shorthand and `key: value`); `...x` spreads are reported as `...`."""
    obj = obj.strip()
    assert obj.startswith("{") and obj.endswith("}"), obj
    keys = []
    for part in split_top(obj[1:-1]):
        if part.startswith("..."):
            keys.append("...")
            continue
        m = re.match(r"""([A-Za-z_$][\w$]*|"[^"]+"|'[^']+')\s*(?::|$)""", part)
        assert m, f"cannot read the key of {part!r}"
        keys.append(m.group(1).strip("\"'"))
    return keys


def body_expression(fn_body: str, arg: str) -> str:
    """The expression a call argument stands for: the literal itself, or the `const x = <expr>` it names."""
    arg = arg.strip()
    if re.fullmatch(r"[A-Za-z_$][\w$]*", arg):
        m = re.search(rf"\b(?:const|let|var)\s+{re.escape(arg)}\s*=\s*", fn_body)
        assert m, f"no definition of the body variable `{arg}`"
        return read_expr(fn_body, m.end())
    return arg


def body_keys(fn_body: str, arg: str) -> tuple[list[str], str]:
    """(top-level keys of a call argument, its expression text). `...` marks a spread / Object.assign."""
    expr = body_expression(fn_body, arg)
    if expr.startswith("Object.assign"):
        return ["..."], expr
    return object_keys(expr), expr


# ---------------------------------------------------------------- 0. hooks exist and are used

def test_new_hooks_are_defined_once_and_used(site):
    """The B2 draft/confirm, intake and static-missing-list hooks exist as `function`s, each defined exactly once
    in its own file and called from elsewhere in that file."""
    for filename, name in HOOKS:
        function_source(site[filename], name)
        assert uses(site[filename], name) >= 2, f"{filename}:{name} is defined but never called"


# ---------------------------------------------------------------- 1. B2: the draft comes from POST /events

def test_request_draft_posts_an_order_intent_event_and_keeps_the_returned_intent(site):
    """SPEC §12 + §4 B1/B2: trade.js's `handleDraftSubmit` calls `App.source.requestDraft`, whose body
    (data_source.js) is a plain `order_intent` event; the response's `intent` becomes `screen.draft` and is painted;
    failures are shown through `showDraftError`, never swallowed."""
    draft_body = _body(site, "data_source.js", "requestDraft")
    calls = calls_to(draft_body, "post", r"profiles/")
    assert len(calls) == 1, "one POST through post(...)"
    assert "/events" in calls[0][0]
    keys, expr = body_keys(draft_body, calls[0][1])
    assert "..." not in keys, f"the event body is a plain literal: nothing is copied from a report: {expr[:80]}"
    assert set(keys) == {"type", "text", "ts"}, keys
    assert re.search(r"""type:\s*["']order_intent["']""", expr), "the event type is order_intent"
    assert "toISOString(" in expr, "ts is an ISO date-time"

    submit_body = _body(site, "trade.js", "handleDraftSubmit")
    assert "requestDraft(" in submit_body and "personaId" in submit_body
    assert ".trim()" in submit_body and "if (!text)" in submit_body, "a blank text is never sent"
    assert re.search(r"screen\.draft\s*=\s*intent\b", submit_body), "the draft is the response's `intent`"
    assert "showDraftError(" in submit_body and "catch" in submit_body
    for forbidden in ("limits", "llm"):
        assert forbidden not in submit_body and forbidden not in draft_body, f"SPEC inv. 2: never send `{forbidden}`"
    assert "in_0001" not in submit_body and "NVDA" not in submit_body, "no typed intent in the submit handler"


def test_event_body_built_like_the_page_validates_with_event_in(site):
    """The keys data_source.js sends build an `api.EventIn` the API accepts (extra keys are a 422); the ts format
    (`Date.toISOString()`, fractional seconds + Z) is accepted."""
    api = _api()
    draft_body = _body(site, "data_source.js", "requestDraft")
    calls = calls_to(draft_body, "post", r"profiles/")
    keys, _ = body_keys(draft_body, calls[0][1])
    sample = {"type": "order_intent", "text": "I want to move part of my portfolio into one stock.",
              "ts": "2026-11-15T14:30:00.123Z"}
    event = api.EventIn.model_validate({k: sample[k] for k in keys})
    assert event.type == "order_intent"


def test_draft_request_form_is_free_text_and_prefills_nothing(site):
    """D10 item 1: the user types the request (SPEC §4 B1 reads the order_intent text). `app.html` ships the
    `trade-draft__form` with an empty, un-prefilled `text` input and a submit button (always present; trade.js
    is live-only, so nothing is sent without an API); trade.js binds its submit to `handleDraftSubmit` and never
    hardcodes a ticker, size or funding when building the request."""
    app_html = site["app.html"]
    m = re.search(r'<form\b[^>]*\bdata-trade-draft-form\b[^>]*>(.*?)</form>', app_html, re.S)
    assert m, "the trade-draft__form markup"
    form = m.group(1)
    tag = re.search(r'<input\b[^>]*\bname="text"[^>]*>', form)
    assert tag, "a text input named text"
    assert 'data-trade-draft-input' in tag.group(0)
    assert not re.search(r'\bvalue="[^"]', tag.group(0)), "nothing prefilled"
    assert re.search(r'<button\b[^>]*type="submit"', form)

    submit_body = _body(site, "trade.js", "handleDraftSubmit")
    for typed in ("NVDA", "ZST.TO", "pro_rata", "in_0001"):
        assert typed not in submit_body, f"`{typed}` is typed in the submit handler: it must come from the input"
    bind = re.search(r'data-trade-draft-form.{0,200}?handleDraftSubmit\(', site["trade.js"], re.S)
    assert bind, "bindOnce wires the draft form's submit to handleDraftSubmit"


def test_confirm_card_is_fed_from_the_module_level_draft(site):
    """The confirm card's fields (`renderConfirmFields` / `ensureConfirmPainted`) are always built from
    `screen.draft`, the `intent` of the last `requestDraft` response in live mode, or of the fetched
    `reports/en-CA/robert-nvda.json` worked example in the offline/static demo (`renderStaticDemo`); the live path
    never reads a report's own `intent` field directly."""
    trade_js = site["trade.js"]
    ensure_body = _body(site, "trade.js", "ensureConfirmPainted")
    assert "screen.draft" in ensure_body and "renderConfirmFields(" in ensure_body
    live_renderer = _assigned_function(site, "trade.js", r"App\.renderers\.trade\s*=\s*function\s*\(")
    assert "ensureConfirmPainted(" in live_renderer
    assert "report.intent" not in live_renderer, "the live path reads the draft, not a report's own intent"
    static_body = _body(site, "trade.js", "paintStaticDemo")
    assert "report.intent" not in static_body
    assert re.search(r"screen\.draft\s*=\s*report\.intent", trade_js), \
        "the static demo's worked example feeds the same `screen.draft`, from its own fetched report"


def test_a_confirmed_intent_is_never_a_submittable_form(site):
    """api.confirm_intent answers 409 for an intent that is not `draft`. `handleConfirmSubmit` keeps the submit
    button disabled after a successful confirm (comment: "a second confirmation ... is a 409"); the offline/static
    demo additionally disables it unconditionally and `handleConfirmSubmit` returns immediately when there is no
    live API, so neither path can resend a confirmed intent."""
    confirm_body = _body(site, "trade.js", "handleConfirmSubmit")
    assert re.search(r'App\.source\.mode\s*!==\s*"live"\)\s*return', confirm_body), \
        "no confirm request at all without a live API"
    assert re.search(r"submitBtn\.disabled\s*=\s*true", confirm_body)
    after_then = confirm_body.split(".then(", 1)[1]
    success_branch = after_then.split(".catch(", 1)[0]
    assert "submitBtn.disabled = false" not in success_branch, \
        "the button is re-enabled only in the catch branch (a failed confirm), never after success"
    static_body = _body(site, "trade.js", "paintStaticDemo")
    assert re.search(r"confirmBtn\.disabled\s*=\s*true", static_body)


# ---------------------------------------------------------------- 1b. B2 confirm: exactly the keys ConfirmIn accepts

def _confirm_body(site):
    return _body(site, "trade.js", "handleConfirmSubmit")


def test_confirm_body_is_exactly_ticker_weight_funding(site):
    """api.ConfirmIn is `extra="forbid"` with 3 fields. `App.source.confirmIntent` is called with a plain literal of
    exactly those keys: no `Object.assign` of the whole draft, no spread, nothing copied from a report's own intent."""
    body = _confirm_body(site)
    calls = calls_to(body, "confirmIntent")
    assert len(calls) == 1, "one call to App.source.confirmIntent"
    assert len(calls[0]) == 2
    keys, expr = body_keys(body, calls[0][1])
    assert "..." not in keys, f"the confirm body must not merge the draft: {expr[:80]}"
    assert sorted(keys) == ["funding", "target_weight_pct", "ticker"], keys
    for banned in ("intent_id", "event_id", "action", "status", "confirmed_at"):
        assert banned not in keys, f"`{banned}` is not a ConfirmIn field"


def test_confirm_funding_carries_only_the_parameter_its_kind_takes(site):
    """SPEC §3.6 / api.FundingIn: a `specific` funding is user-edited (ticker only); any other kind (or none
    stated) has no editable field in this UI and is carried through unedited from the draft via `Object.assign`,
    so it keeps exactly the parameter it already had."""
    body = _confirm_body(site)
    assert re.search(r"""kind:\s*["']specific["']""", body) and "fundingTicker" in body
    assert re.search(r"Object\.assign\(\{\},\s*screen\.draft\.funding\)", body), \
        "non-specific funding is passed through unedited"
    assert "amount_cad" not in body and "asset_class" not in body, \
        "no editable field for pro_rata_class / cash_contribution: those pass through from the draft"


@pytest.mark.parametrize("kind", FUNDING_KINDS)
def test_confirm_body_built_like_the_page_validates_with_confirm_in(site, kind):
    """The body `handleConfirmSubmit` builds (ticker typed, weight typed, funding either a fresh `specific` object
    or the draft's own funding passed through unedited) is accepted by `api.ConfirmIn`."""
    api = _api()
    sample_funding = {
        "specific": {"kind": "specific", "ticker": "ZST.TO"},
        "pro_rata_all": {"kind": "pro_rata_all"},
        "pro_rata_class": {"kind": "pro_rata_class", "asset_class": "Fixed Income"},
        "cash_contribution": {"kind": "cash_contribution", "amount_cad": 5000},
    }[kind]
    body = {"ticker": "NVDA", "target_weight_pct": 10, "funding": sample_funding}
    confirmed = api.ConfirmIn.model_validate(body)
    assert confirmed.funding.kind == kind and confirmed.ticker == "NVDA"
    from pydantic import ValidationError
    draft_shaped = {"intent_id": "in_0002", "event_id": "ev_0008", "action": "buy", "status": "draft",
                    "ticker": None, "target_weight_pct": None, "funding": None, "unstated_fields": ["ticker"]}
    with pytest.raises(ValidationError):
        api.ConfirmIn.model_validate({**draft_shaped, **body})


def test_confirm_failure_and_success_paths(site):
    """A successful confirm replaces `screen.report` and paints the before/after report; a failure (409 / 422) is
    shown through `showConfirmError` and the button is re-enabled so the user can retry; no request without the
    live gate above."""
    body = _confirm_body(site)
    assert re.search(r"screen\.report\s*=\s*report\b", body)
    assert "renderReport(" in body
    assert "showConfirmError(" in body and "catch" in body
    for forbidden in ("limits", "llm"):
        assert forbidden not in body


# ---------------------------------------------------------------- 1c. the offline/static worked example

@pytest.fixture(scope="module")
def built_site(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("trade_demo_site") / "site"
    saved = os.environ.get("COHERENCE_ANALYZER_STORE_DIR")
    sys.path.insert(0, str(DEPLOY))  # build_site does `import whatif` / `import preview_report`
    try:
        mod = _load_module("_build_site_for_trade_demo", DEPLOY / "build_site.py")
        built_dir = mod.build(out)
    finally:
        sys.path.remove(str(DEPLOY))
        if saved is None:
            os.environ.pop("COHERENCE_ANALYZER_STORE_DIR", None)
        else:
            os.environ["COHERENCE_ANALYZER_STORE_DIR"] = saved
    return Path(built_dir or out)


def test_build_site_publishes_the_flow_b_demo_under_reports_en_ca_too(site, built_site):
    """trade.js's offline/static demo fetches `reports/en-CA/robert-nvda.json` directly (never through
    index.json); `deploy/build_site.py` must publish it there via `deploy/preview_report.py`'s `build_en_ca`
    (Robert's NVDA 10% intent, with `headroom.line` filled in -- see `tests/dashboard/test_ui_flow_b.py`'s
    `test_build_en_ca_...`, which checks `build_en_ca`'s own output against `preview_report.build()` directly)."""
    assert site["trade.js"].count("reports/en-CA/robert-nvda.json") >= 1
    en = json.loads((built_site / "reports" / "en-CA" / "robert-nvda.json").read_text(encoding="utf-8"))
    assert en["intent"]["ticker"] == "NVDA" and en["intent"]["target_weight_pct"] == 10
    assert en["verdict"] in ("coherent", "coherent_with_warnings", "incoherent")
    assert "metrics_current" in en and "metrics_proposed" in en
    assert isinstance(en["headroom"].get("line"), str) and en["headroom"]["line"]
    index_en = json.loads((built_site / "reports" / "en-CA" / "index.json").read_text(encoding="utf-8"))
    assert "robert-nvda.json" not in [e["file"] for e in index_en]


# ---------------------------------------------------------------- 2. intake checklist (§20.6): routes, form, static demo

def test_load_intake_reads_the_profile_and_the_build_manifest(site):
    """conversation.js's `loadQuestions` calls `App.source.intake(personaId, report)`. data_source.js answers it
    from the build's static question manifest (`reports/en-CA/intake.json`, `deploy/build_site.py`
    `intake_payload`) plus, live, a plain `GET /profiles/{id}` to read each path's current value and whether it was
    skipped -- a checklist read, not the backend's one-question-at-a-time `/intake` route; a plain GET, no
    `method:`. Failures are shown; the question list is never swallowed."""
    intake_body = _body(site, "data_source.js", "intake")
    assert "loadIntakeManifest(" in intake_body
    assert "profiles/" in intake_body and "encodeURIComponent(personaId)" in intake_body
    assert not re.search(r"/intake\b", intake_body), "no dedicated /intake route: the checklist is built client-side"
    manifest_body = _body(site, "data_source.js", "loadIntakeManifest")
    assert "REPORT_DIR" in manifest_body and "INTAKE_MANIFEST" in manifest_body
    assert not re.search(r"\bmethod\s*:", intake_body) and not re.search(r"\bmethod\s*:", manifest_body)

    conv_body = _body(site, "conversation.js", "loadQuestions")
    assert "App.source.intake(" in conv_body and "personaId" in conv_body
    assert "screen.questions" in conv_body and "catch" in conv_body and "screen.error" in conv_body


def test_intake_answer_and_skip_post_the_route_bodies(site):
    """SPEC §12 / §20.6: `App.source.answer` -> `{path, value}`, `App.source.skip` -> `{path}` (api.AnswerIn /
    SkipIn, extra keys forbidden), each going through `post()`; data_source.js refuses both outside live mode."""
    api = _api()
    answer_body = _body(site, "data_source.js", "answer")
    skip_body = _body(site, "data_source.js", "skip")
    answer_calls = calls_to(answer_body, "post", r"profiles/")
    skip_calls = calls_to(skip_body, "post", r"profiles/")
    assert len(answer_calls) == 1 and "/intake/answer" in answer_calls[0][0]
    assert len(skip_calls) == 1 and "/intake/skip" in skip_calls[0][0]
    akeys, _ = body_keys(answer_body, answer_calls[0][1])
    skeys, _ = body_keys(skip_body, skip_calls[0][1])
    assert sorted(akeys) == ["path", "value"], akeys
    assert sorted(skeys) == ["path"], skeys
    api.AnswerIn.model_validate({"path": "risk.declared", "value": "moderate"})
    api.SkipIn.model_validate({"path": "risk.declared"})
    assert "requireLive(" in answer_body and "requireLive(" in skip_body


def test_saving_or_skipping_an_answer_updates_the_question_in_place(site):
    """conversation.js's `saveAnswer` / `skipQuestion` go through `commit()`: on success the question's own
    `current` / `skipped` fields are updated directly (no second round-trip to re-fetch the checklist), the screen
    moves to the next open question, and the screen is re-rendered; a failure is shown and the index does not move."""
    save_body = _body(site, "conversation.js", "saveAnswer")
    skip_body = _body(site, "conversation.js", "skipQuestion")
    commit_body = _body(site, "conversation.js", "commit")
    assert "App.source.answer(" in save_body and re.search(r"question\.current\s*=\s*value", save_body)
    assert "App.source.skip(" in skip_body and re.search(r"question\.skipped\s*=\s*true", skip_body)
    assert "firstOpenAfter(" in commit_body and "render(" in commit_body
    assert "screen.fieldError" in commit_body, "a failure is recorded, not swallowed"


def test_a_theme_share_answer_is_sent_as_a_number(site):
    """intake_questions.js's `THEME_SHARE_SPEC` (any path ending `.params.max_pct`) is a NUMBER kind, exclusive
    minimum 0, maximum 100 -- matching `values.parsed_notes[...].params.max_pct` in (0, 100]. conversation.js's
    `parseNumber` converts the typed text to a JS number and rejects anything that is not finite before it is ever
    handed to `saveAnswer` / `App.source.answer`."""
    spec_body = site["intake_questions.js"]
    assert "THEME_SHARE_PATH" in spec_body and r"params\.max_pct$" in spec_body
    assert "THEME_SHARE_SPEC" in spec_body and "kind: KIND.NUMBER" in spec_body
    assert "exclusiveMin: 0" in spec_body and "max: MAX_PERCENT" in spec_body
    parse_body = _body(site, "conversation.js", "parseNumber")
    assert "Number(" in parse_body and "Number.isFinite(" in parse_body


def test_render_intake_live_shows_the_next_question_with_answer_and_skip(site):
    """SPEC §20.6 / D7 point 1: with an API, `currentHtml()` shows the current question's text (escaped), a
    suggestion/answer field (`data-answer-input`, starting empty), a submit button and a skip button
    (`data-skip-question`, never `data-rule-action` -- that attribute belongs to preferences.js's N3 decisions).
    Rendering itself makes no request."""
    body = _body(site, "conversation.js", "currentHtml")
    assert "question.text" in body and re.search(r"esc\(\s*question\.text\s*\)", body)
    input_html = _body(site, "conversation.js", "inputHtml")
    assert re.search(r'<button\b[^>]*type="submit"', input_html)
    assert "data-answer-input" in input_html
    assert "data-skip-question" in body
    assert "data-rule-action" not in body, "data-rule-action is bound to preferences.js's handleRuleDecision"
    assert "fetch(" not in body and "App.source" not in body, "rendering makes no request"


def test_render_intake_static_demo_is_a_read_only_missing_list(site):
    """Task (2): without an API, `renderCurrent` calls `staticMissingHtml(App.state.report)`: a read-only list of
    the still-missing paths from `site.intake` / `intake` (deploy/build_site.py's `missing_by_factor`), with no
    `<form>`, `<input>` or `<button>`, and no claim that an answer was saved."""
    render_body = _body(site, "conversation.js", "renderCurrent")
    assert "!isLive()" in render_body and "staticMissingHtml(" in render_body
    assert re.search(r"!isLive\(\)[^;{}]*\{\s*container\.innerHTML\s*=\s*staticMissingHtml\(", render_body)
    missing_body = _body(site, "conversation.js", "staticMissingHtml")
    paths_body = _body(site, "conversation.js", "staticMissingPaths")
    assert "report.site" in paths_body and "report.intake" in paths_body
    assert "<form" not in missing_body and "<input" not in missing_body and "<button" not in missing_body
    assert "specFor(" in missing_body, "paths are shown with their human label, not the raw path"


def test_intake_is_wired_into_the_conversation_screen_and_its_events(site):
    """`App.renderers.conversation` loads the checklist for the current persona and renders the Q&A card
    (`data-conversation-current`) and the profile-so-far panel (`data-profile-rows`, `data-profile-count`,
    `data-profile-progress`); `bindOnce` wires the answer form's submit to `submitInput` and the skip button's
    click to `skipQuestion`."""
    app_html = site["app.html"]
    for selector in ("data-conversation-current", "data-profile-rows", "data-profile-count", "data-profile-progress"):
        assert selector in app_html, selector
    renderer_body = _assigned_function(site, "conversation.js", r"App\.renderers\.conversation\s*=\s*function\s*\(")
    assert "loadQuestions(" in renderer_body and "render()" in renderer_body
    render_body = _body(site, "conversation.js", "render")
    assert "renderCurrent(" in render_body and "renderPanel(" in render_body
    bind_body = _body(site, "conversation.js", "bindOnce")
    assert "submitInput(section)" in bind_body and "addEventListener" in bind_body
    click_body = _body(site, "conversation.js", "onClick")
    assert "data-skip-question" in click_body and "skipQuestion()" in click_body
    assert "preventDefault" in bind_body


# ---------------------------------------------------------------- 2b. static demo: the missing list is embedded

def test_static_en_reviews_embed_the_missing_list_per_factor(built_site):
    """Every EN review built for app.html (`reports/en-CA/*.json`) carries `site.intake` ({factor: missing paths}),
    computed by `intake.missing` on that persona's profile, one entry per factor -- the same contract the FR
    pipeline (index.html) embeds, read by conversation.js's `staticMissingHtml`."""
    from coherence_analyzer import intake
    rdir = built_site / "reports" / "en-CA"
    index = json.loads((rdir / "index.json").read_text(encoding="utf-8"))
    assert len(index) == 4, "one current-portfolio review per persona, plus Robert's extra NVDA scenario"
    for entry in index:
        rep = json.loads((rdir / entry["file"]).read_text(encoding="utf-8"))
        block = (rep.get("site") or {}).get("intake")
        assert isinstance(block, dict), f"{entry['file']}: no embedded intake"
        assert set(block) == set(intake.FACTORS), f"{entry['file']}: one entry per factor"
        who = entry["persona_id"].removeprefix("persona-")
        profile = json.loads((ROOT / "fixtures" / who / "profile.json").read_text(encoding="utf-8"))
        for factor in intake.FACTORS:
            entry_block = block[factor]
            missing = list(entry_block["missing"] if isinstance(entry_block, dict) else entry_block)
            assert missing == intake.missing(profile, factor), (entry["file"], factor)
        blob = json.dumps(block)
        assert "llm:" not in blob and "limits" not in blob, "paths only: no AI output, no limit"


# ---------------------------------------------------------------- 3. N3: ask for the theme number (§16 Amira)

def test_decide_rule_asks_for_the_missing_theme_number_and_sends_it(site):
    """§16 Amira / D7 point 1: `handleRuleDecision` does not approve a `max_theme_weight` rule with no number
    blindly: it asks for the number (`window.prompt`), keeps it in (0, 100] (rejecting out-of-range or
    non-numeric input with a `window.alert`, no request sent) and passes it to `App.source.decideRule` as
    `maxPct`; a cancelled prompt (`null`) also sends nothing."""
    body = _body(site, "preferences.js", "handleRuleDecision")
    assert "max_theme_weight" in body and "max_pct" in body
    assert "!isNumber((rule.params || {}).max_pct)" in body, \
        "the page tests whether the rule already has a number"
    assert "window.prompt(" in body
    assert "typed === null) return" in body, "a cancelled prompt sends nothing"
    assert "Number(String(typed)" in body, "the typed number is converted to a JSON number"
    assert "maxPct > 0 && maxPct <= 100" in body, "0 < max_pct <= 100 is checked"
    assert "window.alert(" in body, "an invalid number is reported, not silently ignored"
    decide_calls = calls_to(body, "decideRule")
    assert decide_calls and "maxPct" in decide_calls[0]


def test_decision_is_approved_only_or_approved_plus_max_pct(site):
    """The decision sent is `{approved}` for a rule that already has its number, and `{approved, max_pct}` only on
    the branch that just asked for one (api.post_decision answers 409 for `max_pct` on a rule that has one); both
    validate with `api.DecisionIn`, and data_source.js's `decideRule` builds exactly that shape."""
    api = _api()
    decide_body = _body(site, "data_source.js", "decideRule")
    assert "maxPct === undefined || maxPct === null ? {approved} : {approved, max_pct: maxPct}" in decide_body
    assert api.DecisionIn.model_validate({"approved": True}).max_pct is None
    assert api.DecisionIn.model_validate({"approved": True, "max_pct": 40}).max_pct == 40
    with pytest.raises(Exception):
        api.DecisionIn.model_validate({"approved": True, "max_pct": 0})
    handler_body = _body(site, "preferences.js", "handleRuleDecision")
    for forbidden in ("limits", "llm"):
        assert forbidden not in handler_body


def test_rejecting_a_rule_never_asks_for_a_number_nor_sends_approval(site):
    """SPEC-AMBIGUITY (kept from the index.html oracle): rejecting records no limit, so the number prompt is gated
    to the approve path only; `approved` is computed from the clicked action, never hard-coded `true`."""
    body = _body(site, "preferences.js", "handleRuleDecision")
    assert "const approved = action === \"approve\";" in body
    assert not re.search(r"approved\s*:\s*true\b", body)
    assert "if (approved && rule && rule.rule_kind" in body, "the number is asked for on the approve path only"


# ---------------------------------------------------------------- 4. wording: blocklist and D10 vocabulary

def test_new_ui_strings_pass_the_blocklist_and_d10_vocabulary(site):
    """SPEC §9.3 item 3 / D10 items 1, 2, 4: the new functions print no advisory phrase and no word that proposes,
    ranks or labels a choice; HTML <option> tags are widgets, not wording."""
    checked = HOOKS + (("preferences.js", "handleRuleDecision"), ("trade.js", "coneMarkup"))
    for filename, name in checked:
        body = function_source(site[filename], name)
        literal = " ".join(string_literals(body))
        assert blocklist_hits(literal) == [], (filename, name)
        words = re.sub(r"</?(option|optgroup)\b", " ", literal)
        found = D10_WORDS.search(norm(words))
        assert found is None, (filename, name, found and found.group(0))


def test_pages_pass_the_blocklist_apart_from_the_fixed_disclaimer(site):
    """The relevant pages/scripts (markup, text, attributes, scripts) match no blocklist phrase (SPEC §9.3 item 3,
    D10 item 4)."""
    for filename in ("app.html", "conversation.js", "trade.js", "preferences.js", "data_source.js"):
        hits = blocklist_hits(site[filename])
        assert hits == [], (filename, hits)
