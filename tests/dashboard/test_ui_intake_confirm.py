"""P3.UIINTAKE oracle: dashboard B2 draft + confirm body, intake form, theme `max_pct` input
(SPEC §12 UI + route table, §4 Flow B B2, §16 Amira; docs/spec-factors.md §20.6, §20.7, §20.9;
docs/spec-decisions.md D7 point 1, D8 point 1, D10).

Audit gaps pinned here (the page is `deploy/site/index.html`, vanilla JS, scanned as text: no browser, no network, no
clock, no JS runtime; the backend contracts are the pydantic models of `coherence_analyzer/api.py`, imported, not edited)

  1. B2. With an API base the page must CREATE the draft (`POST /profiles/{id}/events`, an `order_intent` event whose
     body validates with `api.EventIn`), show the returned `intent` (D10: nothing pre-filled that the user did not
     state), and confirm with a body of EXACTLY the keys `api.ConfirmIn` accepts (`ticker`, `target_weight_pct`,
     `funding`; `extra="forbid"`). The old body `Object.assign({}, state.rep.intent, {...})` also carried `intent_id`,
     `event_id`, `action`, `status`: a 422 every time. The stored Flow B report's intent is already confirmed (a 409), so
     it is never a submittable form.
  2. Intake. With an API base the page shows `intake.next_question` of `GET /profiles/{id}/intake`, answers / skips it
     with `POST .../intake/answer` `{path, value}` and `POST .../intake/skip` `{path}` (AnswerIn / SkipIn), then reloads
     and re-renders. The static demo shows the missing list read-only, from data embedded by `deploy/build_site.py`.
  3. N3. For a `max_theme_weight` rule with no `max_pct` the page asks for the number (Amira, §16) and sends it with the
     decision (`DecisionIn.max_pct`, 0 < x <= 100); a rule that has one is decided with `{approved}` only (the API
     answers 409 otherwise).

New hooks (each `function <name>(`, defined once, used elsewhere):
  requestDraft(text)       POST the order_intent event, keep `response.intent` in `state.draft`, re-render.
  renderDraftRequest()     the `<form id="draft-form">` (textarea `text`), only with an API.
  intentCard(rep)          the B2 card: with an API it shows `state.draft`; without, the static report's `rep.intent`.
  renderIntake(rep)        inside the « Points à clarifier » card: live = question + answer form + skip; static = the
                           missing list, read-only (no form, no input, no button).
  loadIntake()             GET `/profiles/${id}/intake` (a plain `fetch`, no `method`), stores it in `state.intake`.
  answerIntake(path, value) / skipIntake(path)   the two POSTs through `apiPost`, then `loadIntake()` and `render()`.
Skip uses `data-intake="skip"`, never `data-action` (that attribute is bound to `decideRule`).
Static embed contract (build_site): each built review report carries `intake` (top level or in `site`), a dict
{factor: [path, ...]} or {factor: {"missing": [path, ...]}} equal to `intake.missing(profile, factor)` for the persona.
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
INDEX = DEPLOY / "site" / "index.html"
FROZEN = ROOT / "tests" / "dashboard" / "test_ui_flow_b.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# The JS scanner, SPEC §9.3 blocklist and D10 vocabulary of the frozen Flow B UI oracle are reused, not copied.
H = _load_module("_ui_flow_b_helpers_for_intake", FROZEN)
function_source, typed_digits, blocklist_hits, uses, norm = H.function_source, H.typed_digits, H.blocklist_hits, H.uses, H.norm
FUNDING_KINDS = H.FUNDING_KINDS
D10_WORDS = H.D10_WORDS

KIND_PARAM = {"specific": "ticker", "pro_rata_class": "asset_class", "cash_contribution": "amount_cad", "pro_rata_all": None}
NEW_HOOKS = ("requestDraft", "renderDraftRequest", "intentCard", "renderIntake", "loadIntake", "answerIntake",
             "skipIntake")


def _api():
    return importlib.import_module("coherence_analyzer.api")


@pytest.fixture(scope="module")
def page() -> str:
    return INDEX.read_text(encoding="utf-8")


def _body(page: str, name: str) -> str:
    return function_source(page, name)[0]


# ---------------------------------------------------------------- small JS expression helpers (text, no runtime)

def _skip_literal(s: str, i: int) -> int:
    if s[i] == "`":
        return H._scan_template(s, i, [])
    return H._skip_string(s, i)


def split_top(s: str) -> list[str]:
    """Split on top-level commas (quotes, templates, (), [], {} respected)."""
    parts, depth, cur, i = [], 0, 0, 0
    while i < len(s):
        c = s[i]
        if c in "\"'`":
            i = _skip_literal(s, i)
            continue
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
    depth = 0
    while True:
        c = s[i]
        if c in "\"'`":
            i = _skip_literal(s, i)
            continue
        depth += c in "([{"
        depth -= c in ")]}"
        i += 1
        if depth == 0:
            return i


def read_expr(s: str, i: int) -> str:
    """The expression starting at s[i], up to a top-level `;` or newline."""
    j, depth = i, 0
    while j < len(s):
        c = s[j]
        if c in "\"'`":
            j = _skip_literal(s, j)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c in ";\n" and depth <= 0:
            break
        j += 1
    return s[i:j].strip()


def api_post_calls(body: str, path_re: str) -> list[list[str]]:
    """Argument lists of every `apiPost(<path>, <body>)` call whose first argument matches `path_re`."""
    out = []
    for m in re.finditer(r"\bapiPost\s*\(", body):
        end = _matching(body, m.end() - 1)
        args = split_top(body[m.end():end - 1])
        if args and re.search(path_re, args[0]):
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
    """The expression an `apiPost` body argument stands for: the literal itself, or the `const x = <expr>` it names."""
    arg = arg.strip()
    if re.fullmatch(r"[A-Za-z_$][\w$]*", arg):
        m = re.search(rf"\b(?:const|let|var)\s+{re.escape(arg)}\s*=\s*", fn_body)
        assert m, f"no definition of the body variable `{arg}`"
        return read_expr(fn_body, m.end())
    return arg


def assigned_keys(fn_body: str, var: str) -> list[str]:
    return re.findall(rf"\b{re.escape(var)}\.([A-Za-z_]\w*)\s*=(?!=)", fn_body)


def body_keys(fn_body: str, arg: str) -> tuple[list[str], str]:
    """(top-level keys of the body, its expression text). `...` marks a spread or Object.assign (not a plain literal)."""
    expr = body_expression(fn_body, arg)
    var = arg.strip() if re.fullmatch(r"[A-Za-z_$][\w$]*", arg.strip()) else None
    if expr.startswith("Object.assign"):
        return ["..."], expr
    keys = object_keys(expr)
    if var:
        keys += assigned_keys(fn_body, var)
    return keys, expr


# ---------------------------------------------------------------- 0. hooks exist and are used

def test_new_hooks_are_defined_once_and_used(page):
    """The B2 draft, intake and N3 number hooks exist as `function`s, each defined exactly once and used by the page."""
    for name in NEW_HOOKS:
        function_source(page, name)                       # fails unless defined exactly once
        assert uses(page, name) >= 2, f"{name} is defined but never called"


# ---------------------------------------------------------------- 1. B2: the draft comes from POST /events

def test_request_draft_posts_an_order_intent_event_and_keeps_the_returned_intent(page):
    """SPEC §12 + §4 B1/B2: `POST /profiles/{id}/events` with an `order_intent` event; the response `{event, intent}`
    gives the draft, kept in `state.draft` and rendered; failures are shown (role=alert), never swallowed."""
    body = _body(page, "requestDraft")
    assert "apiBase(" in body, "no request when no API is configured"
    calls = api_post_calls(body, r"/profiles/\$\{[^}]*\}/events")
    assert len(calls) == 1, "one POST to /profiles/${profile_id}/events"
    assert "profile_id" in calls[0][0]
    keys, expr = body_keys(body, calls[0][1])
    assert "..." not in keys, "the event body is a plain literal: nothing is copied from a report"
    assert re.search(r"\border_intent\b", expr + body), "the event type is order_intent"
    # SPEC-AMBIGUITY: `payload` is allowed by EventIn, but a payload would carry fields the user did not type (D10);
    # stricter reading: the page sends only type, text, ts (and optionally source).
    assert set(keys) <= {"type", "text", "ts", "source"} and {"type", "text", "ts"} <= set(keys), keys
    assert "toISOString(" in body, "ts is an ISO date-time"
    assert "trim(" in body, "a blank text is never sent (EventIn.text is not blank)"
    assert re.search(r"state\.draft\s*=\s*[\w.\[\]]*\bintent\b", body), "the draft is the response's `intent`"
    assert "render(" in body and "catch" in body
    assert "state.error" in body
    for forbidden in ("limits", "llm"):
        assert forbidden not in body, f"SPEC inv. 2: the UI never sends `{forbidden}`"
    assert "in_0001" not in body and "NVDA" not in body, "no typed intent"


def test_event_body_built_like_the_page_validates_with_event_in(page):
    """The keys the page sends build an `api.EventIn` the API accepts (extra keys are a 422); the ts format the page
    produces (`Date.toISOString()`, fractional seconds + Z) is accepted."""
    api = _api()
    body = _body(page, "requestDraft")
    keys, _ = body_keys(body, api_post_calls(body, r"/events")[0][1])
    sample = {"type": "order_intent", "text": "Je veux mettre une partie de mon portefeuille dans un titre.",
              "ts": "2026-11-15T14:30:00.123Z", "source": "ui_chat"}
    event = api.EventIn.model_validate({k: sample[k] for k in keys})
    assert event.type == "order_intent"


def test_draft_request_form_is_free_text_and_prefills_nothing(page):
    """D10 item 1: the user types the request (SPEC §4 B1 reads the order_intent text); the form has an empty
    textarea named `text`, a submit button, no typed ticker / size / funding, and exists only with an API."""
    body, texts = function_source(page, "renderDraftRequest")
    literal = "\n".join(texts)
    assert re.search(r"<form\b[^>]*\bid=[\"']draft-form[\"']", literal)
    assert re.search(r"<textarea\b[^>]*\bname=[\"']text[\"']", literal)
    assert re.search(r"<textarea\b[^>]*\brequired\b", literal)
    assert re.search(r"<button\b[^>]*type=[\"']submit[\"']", literal)
    assert not re.search(r"<textarea\b[^>]*>[^<]+</textarea>", literal), "the textarea starts empty"
    assert not re.search(r"\bvalue=[\"'][^\"'\u00a7]", literal) and not re.search(r"\bplaceholder=[\"'][^\"']*\d", literal)
    assert "apiBase(" in body, "shown only with an API"
    for typed in ("NVDA", "ZST", "pro_rata", "in_0001"):
        assert typed not in body
    assert not typed_digits(texts), typed_digits(texts)[:5]
    # the form is bound: submit -> requestDraft with the typed text
    bind = re.search(r"draft-form.{0,300}?addEventListener\(\s*[\"']submit[\"'].{0,300}?requestDraft\(", page, re.S)
    assert bind, "render() binds a submit listener on #draft-form that calls requestDraft("


def test_intent_card_feeds_the_form_from_the_draft_when_an_api_is_configured(page):
    """The report's own intent (`in_0001`) is already confirmed: with an API the editable form shows `state.draft`
    (from POST /events), and `rep.intent` is only the static demo's read-out."""
    body = _body(page, "intentCard")
    assert "apiBase(" in body
    assert "state.draft" in body
    assert re.search(r"renderIntentForm\(\s*state\.draft", body), "live: the form is fed from the draft"
    assert "rep.intent" in body, "static demo keeps Robert's report intent"
    assert "renderDraftRequest(" in body, "live: the card also offers the free-text request"
    assert "in_0001" not in body


def test_a_confirmed_intent_is_never_a_submittable_form(page):
    """api.confirm_intent answers 409 for an intent that is not `draft`. The form reads `intent.status` and keeps its
    submit button disabled unless the intent is a draft, so the stored report's confirmed intent cannot be re-sent."""
    body, texts = function_source(page, "renderIntentForm")
    assert "intent.status" in body
    assert re.search(r"\bdraft\b", body)
    submit = re.search(r"<button\b[^>]*type=[\"']submit[\"'][^>]*>", "\n".join(texts))
    assert submit, "a submit button"
    assert "disabled" not in submit.group(0) and "\u00a7" in submit.group(0), \
        "`disabled` comes from a `${...}` slot, not a literal"
    assert "disabled" in body
    # a draft after confirmation is gone: confirmIntent drops it
    confirm = _body(page, "confirmIntent")
    assert re.search(r"state\.draft\s*=\s*null", confirm), "the confirmed draft is cleared"


# ---------------------------------------------------------------- 1b. B2 confirm: exactly the keys ConfirmIn accepts

def _confirm_call(page: str):
    body = _body(page, "confirmIntent")
    calls = api_post_calls(body, r"/intents/\$\{[^}]*\}/confirm")
    assert len(calls) == 1, "one POST to /intents/${intent_id}/confirm"
    return body, calls[0]


def test_confirm_body_is_exactly_ticker_weight_funding(page):
    """api.ConfirmIn is `extra="forbid"` with 3 fields. The body is a plain literal of exactly those keys: no
    `Object.assign`, no spread, nothing copied from `state.rep.intent` (intent_id, event_id, action, status ... are a 422)."""
    body, (path, arg) = _confirm_call(page)
    keys, expr = body_keys(body, arg)
    assert "..." not in keys, f"the confirm body must not merge another object: {expr[:80]}"
    assert sorted(keys) == ["funding", "target_weight_pct", "ticker"], keys
    assert not re.search(r"Object\.assign\([^)]*intent", body), "no merge of the draft / report intent"
    assert "state.rep.intent" not in body and "rep.intent" not in body
    for banned in ("intent_id", "event_id", "action", "status", "confirmed_at"):
        assert banned not in keys, f"`{banned}` is not a ConfirmIn field"


def _funding_params_from_page(body: str) -> dict[str, set[str]]:
    """For each funding kind, the parameter names the page adds to `funding` on that branch (rest of its statement)."""
    out: dict[str, set[str]] = {}
    for kind in FUNDING_KINDS:
        found: set[str] = set()
        for m in re.finditer(rf"[\"']{kind}[\"']", body):
            stmt = read_expr(body, m.end())
            found |= set(re.findall(r"\b(ticker|asset_class|amount_cad)\b", stmt))
        out[kind] = found
    return out


def test_confirm_funding_carries_only_the_parameter_its_kind_takes(page):
    """SPEC §3.6 / api.FundingIn: specific -> ticker; pro_rata_class -> asset_class; cash_contribution -> amount_cad;
    pro_rata_all -> nothing else. The page builds that, with numbers as numbers."""
    body, _ = _confirm_call(page)
    params = _funding_params_from_page(body)
    for kind, wanted in KIND_PARAM.items():
        assert params[kind] == ({wanted} if wanted else set()), (kind, params[kind])
    lines = body.splitlines()
    amount_stmt = " ".join(ln for ln in lines if "amount_cad" in ln)
    assert re.search(r"Number\(|parseFloat\(|\+\s*\w", amount_stmt), "amount_cad is sent as a number"
    weight_stmt = " ".join(ln for ln in lines if "target_weight_pct" in ln)
    assert re.search(r"Number\(|parseFloat\(|\+\s*\w", weight_stmt), "the weight is sent as a number"
    assert "funding_kind" in body, "the kind comes from the form's select"


@pytest.mark.parametrize("kind", FUNDING_KINDS)
def test_confirm_body_built_like_the_page_validates_with_confirm_in(page, kind):
    """The body the page builds from the form values, with the draft's extra fields present in the page state, is
    accepted by `api.ConfirmIn` (the 422 of the audit is gone); the old merged body is rejected (control)."""
    api = _api()
    from pydantic import ValidationError
    body, (_, arg) = _confirm_call(page)
    keys, _ = body_keys(body, arg)
    param = _funding_params_from_page(body)[kind]
    funding = {"kind": kind}
    sample = {"ticker": "ZST.TO", "asset_class": "Fixed Income", "amount_cad": 5000}
    funding.update({k: sample[k] for k in param})
    built = {"ticker": "NVDA", "target_weight_pct": 10, "funding": funding}
    posted = json.loads(json.dumps({k: built[k] for k in keys}))      # what JSON.stringify would send
    confirmed = api.ConfirmIn.model_validate(posted)
    assert confirmed.funding.kind == kind and confirmed.ticker == "NVDA"
    # control: the page state's draft carries these keys; merging it (the old code) is exactly what the API refuses
    draft = {"intent_id": "in_0002", "event_id": "ev_robert_0008", "action": "buy", "status": "draft",
             "ticker": None, "target_weight_pct": None, "funding": None, "unstated_fields": ["ticker"]}
    with pytest.raises(ValidationError):
        api.ConfirmIn.model_validate({**draft, **posted})


def test_confirm_failure_and_success_paths(page):
    """The confirm response (the Flow B report) replaces the page's report; a failure (409 / 422) is shown and the draft
    stays editable (it is cleared only after success); no request without an API."""
    body, _ = _confirm_call(page)
    assert "apiBase(" in body
    assert re.search(r"state\.rep\s*=", body) and "render(" in body
    assert "state.error" in body and "catch" in body
    success = body.split("catch")[0]
    assert "state.draft = null" in success, "cleared on success, not in the catch branch"
    assert "state.draft = null" not in body.split("catch", 1)[1].split("render(")[0]
    for forbidden in ("limits", "llm"):
        assert forbidden not in body


# ---------------------------------------------------------------- 2. intake form (§20.6): routes, form, static demo

def test_load_intake_reads_the_intake_route_with_a_plain_get(page):
    """`GET /profiles/{id}/intake` (api.get_intake) -> `state.intake`; a plain `fetch` with no `method` (the page keeps
    exactly one fetch with an explicit method: apiPost); needs an API; failures are shown."""
    body = _body(page, "loadIntake")
    assert "apiBase(" in body
    assert re.search(r"fetch\(\s*apiBase\(\)\s*\+\s*[`\"']/profiles/|fetch\(\s*`\$\{apiBase\(\)\}/profiles/", body)
    assert re.search(r"/profiles/\$\{[^}]*\}/intake\b(?!/)", body) or re.search(r"/profiles/[\"']\s*\+[^;]*intake", body)
    assert "profile_id" in body
    assert not re.search(r"\bmethod\s*:", body)
    assert re.search(r"state\.intake\s*=", body)
    assert "catch" in body and "render(" in body
    assert len(re.findall(r"\bmethod\s*:", page)) == 1 and len(re.findall(r"[\"']POST[\"']", page)) == 1


@pytest.mark.parametrize("fn, args, route, model, sample", [
    ("answerIntake", "path, value", r"/profiles/\$\{[^}]*\}/intake/answer", "AnswerIn",
     {"path": "risk.declared", "value": "moderate"}),
    ("skipIntake", "path", r"/profiles/\$\{[^}]*\}/intake/skip", "SkipIn", {"path": "risk.declared"}),
])
def test_intake_answer_and_skip_post_the_route_bodies(page, fn, args, route, model, sample):
    """SPEC §12 / §20.6: answer -> `{path, value}`, skip -> `{path}` (api.AnswerIn / SkipIn, extra keys forbidden), through
    apiPost, then the intake is reloaded and the page re-rendered; failures are shown; nothing else is sent."""
    api = _api()
    body = _body(page, fn)
    assert "apiBase(" in body
    calls = api_post_calls(body, route)
    assert len(calls) == 1, route
    keys, _ = body_keys(body, calls[0][1])
    assert "..." not in keys and sorted(keys) == sorted(sample), keys
    getattr(api, model).model_validate({k: sample[k] for k in keys})
    assert "loadIntake(" in body and "render(" in body, "reload the checklist, then re-render"
    assert "catch" in body and "state.error" in body
    for forbidden in ("limits", "llm"):
        assert forbidden not in body, f"SPEC inv. 2: the UI never sends `{forbidden}`"


def test_a_theme_share_answer_is_sent_as_a_number(page):
    """api.post_intake_answer: the theme share (`values.parsed_notes.value[i].params.max_pct`) must be a JSON number in
    (0, 100], never the string typed in the box; the page converts it before the POST."""
    form = _body(page, "answerIntake") + _body(page, "renderIntake") + _body(page, "render")
    assert re.search(r"max_pct", form), "the page knows the theme-share path"
    assert re.search(r"Number\(|parseFloat\(", form), "typed numbers are converted"
    assert "isFinite" in form or "Number.isFinite" in form or "isNaN" in form, "a non-number is not sent"


def test_render_intake_live_shows_the_next_question_with_answer_and_skip(page):
    """SPEC §20.6 / D7 point 1: with an API, `intake.next_question` (fixed template text from the server) is shown in a
    form: an answer field, a submit button and a skip button. The question is escaped text, the form carries the
    question's path, and skip does not use `data-action` (that attribute belongs to the N3 buttons)."""
    body, texts = function_source(page, "renderIntake")
    literal = "\n".join(texts)
    assert "apiBase(" in body
    assert "next_question" in body and re.search(r"\.text\b", body) and re.search(r"\.path\b", body)
    assert re.search(r"esc\(\s*[\w.]*\.text\s*\)", body), "the question text is escaped"
    assert re.search(r"<form\b[^>]*\bid=[\"']intake-form[\"']", literal)
    assert re.search(r"<(input|textarea)\b[^>]*\bname=[\"']answer[\"']", literal)
    assert re.search(r"<button\b[^>]*type=[\"']submit[\"']", literal)
    assert re.search(r"<button\b[^>]*data-intake=[\"']skip[\"']", literal)
    assert re.search(r"data-path=", literal), "the path of the question travels with the form"
    assert "data-action" not in literal, "data-action is bound to decideRule"
    assert not re.search(r"\bvalue=[\"'][^\"'\u00a7]", literal), "the answer field starts empty"
    assert "fetch(" not in body and "apiPost(" not in body, "rendering makes no request"
    assert not typed_digits(texts), typed_digits(texts)[:5]


def test_render_intake_static_demo_is_a_read_only_missing_list(page):
    """Task (2): with no API the card lists what is still missing, read from the report's embedded `intake`
    (`rep.intake` or `rep.site.intake`), and offers no input; it never claims an answer was saved."""
    body, texts = function_source(page, "renderIntake")
    assert re.search(r"\brep\.(site\.)?intake\b", body), "the static list is read from the report"
    assert "missing" in body
    assert body.count("apiBase(") >= 1
    live_form = re.search(r"<form\b[^>]*intake-form", body)
    assert live_form, "the live form markup exists"
    # the form markup sits behind an apiBase() condition: the text before it must test the API base
    assert "apiBase(" in body[:live_form.start()] or "live" in body[:live_form.start()]


def test_intake_is_wired_into_the_clarification_card_and_the_page_events(page):
    """The « Points à clarifier » card hosts the intake; `render()` binds the intake form (submit -> answerIntake) and the
    skip button (click -> skipIntake); the checklist is fetched when the API is set."""
    profil = _body(page, "pageProfil")
    anchor = profil.index("Points à clarifier")
    assert "renderIntake(" in profil[anchor:], "inside / after the card title, not elsewhere"
    assert profil.index("renderIntake(", anchor) < profil.index("renderValuesRules(rep)")
    rendered = _body(page, "render")
    assert re.search(r"intake-form.{0,300}?addEventListener\(\s*[\"']submit[\"'].{0,300}?answerIntake\(", rendered, re.S)
    assert re.search(r"data-intake.{0,300}?addEventListener\(\s*[\"']click[\"'].{0,300}?skipIntake\(", rendered, re.S)
    assert "preventDefault" in rendered
    assert uses(page, "loadIntake") >= 3, "defined once; called when the page loads a report and after each answer"


# ---------------------------------------------------------------- 2b. static demo: the missing list is embedded

@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("intake_site") / "site"
    saved = os.environ.get("COHERENCE_ANALYZER_STORE_DIR")
    sys.path.insert(0, str(DEPLOY))                       # build_site does `import whatif`
    try:
        mod = _load_module("_build_site_for_intake", DEPLOY / "build_site.py")
        built_dir = mod.build(out)
    finally:
        sys.path.remove(str(DEPLOY))
        if saved is None:
            os.environ.pop("COHERENCE_ANALYZER_STORE_DIR", None)
        else:
            os.environ["COHERENCE_ANALYZER_STORE_DIR"] = saved
    return Path(built_dir or out)


def _missing_of(block, factor: str) -> list[str]:
    entry = block[factor]
    return list(entry["missing"] if isinstance(entry, dict) else entry)


def test_static_reports_embed_the_missing_list_per_factor(built):
    """The static demo has no server: every built review report carries `intake` ({factor: missing paths}) computed by
    `intake.missing` on the persona's profile, one entry per factor; the P2.D10C index stays the 4 flow C reviews."""
    from coherence_analyzer import intake
    index = json.loads((built / "reports" / "index.json").read_text(encoding="utf-8"))
    assert len(index) == 4, "frozen P2.D10C constraint: reports/index.json is exactly the 4 flow C reviews"
    assert not [r for r in index if "robert-nvda" in r["file"]]
    personas = {"robert-review-nvda": "robert", "robert-review": "robert", "amira-review": "amira", "mateo-review": "mateo"}
    for entry in index:
        name = entry["file"].removesuffix(".json")
        rep = json.loads((built / "reports" / entry["file"]).read_text(encoding="utf-8"))
        block = rep.get("intake") or (rep.get("site") or {}).get("intake")
        assert isinstance(block, dict), f"{name}: no embedded intake"
        assert set(block) == set(intake.FACTORS), f"{name}: one entry per factor"
        profile = json.loads((ROOT / "fixtures" / personas[name] / "profile.json").read_text(encoding="utf-8"))
        for factor in intake.FACTORS:
            assert _missing_of(block, factor) == intake.missing(profile, factor), (name, factor)
        blob = json.dumps(block)
        assert "llm:" not in blob and "limits" not in blob, "paths only: no AI output, no limit"


# ---------------------------------------------------------------- 3. N3: ask for the theme number (§16 Amira)

def test_decide_rule_asks_for_the_missing_theme_number_and_sends_it(page):
    """§16 Amira / D7 point 1: a `max_theme_weight` rule whose `params.max_pct` is not a number is not approved
    blindly: the page asks for the number (prompt or number field), keeps it in (0, 100] and sends it as `max_pct`
    with the decision; no request when the user gives none."""
    body = _body(page, "decideRule")
    values = _body(page, "renderValuesRules")
    both = body + values
    assert "max_theme_weight" in both and "max_pct" in body
    assert re.search(r"typeof\b[^;]*?[!=]==?\s*[\"']number[\"']|Number\.isFinite\(|isFinite\(|==\s*null|===\s*undefined",
                     both), "the page tests whether the rule already has a number"
    assert re.search(r"\bprompt\(|name=[\"']max_pct[\"']|data-max-pct|type=[\"']number[\"']", both), "the page asks"
    assert re.search(r"Number\(|parseFloat\(", body), "the number is sent as a JSON number"
    assert re.search(r"\b100\b", both) and re.search(r">\s*0\b|<=?\s*0\b|\b0\s*<", both), "0 < max_pct <= 100 is checked"
    assert re.search(r"return", body), "no request without a valid number"
    assert "apiPost(" in body and re.search(r"/parsed-rules/\$\{[^}]*\}/decision", body)


def test_decision_body_built_like_the_page_validates_with_decision_in(page):
    """The decision body is `{approved}` for a rule that has its number and `{approved, max_pct}` only when the page
    just asked for it (api.post_decision answers 409 for `max_pct` on a rule that has one); both validate with
    `api.DecisionIn` and nothing else is sent."""
    api = _api()
    body = _body(page, "decideRule")
    calls = api_post_calls(body, r"/parsed-rules/")
    assert len(calls) == 1
    keys, expr = body_keys(body, calls[0][1])
    explicit = [k for k in keys if k != "..."]
    assert set(explicit) <= {"approved", "max_pct"}, explicit
    assert "approved" in explicit or "approved" in expr
    assert re.search(r"\bmax_pct\b", expr + body)
    # max_pct is conditional: not an unconditional key of the literal
    assert "max_pct" not in explicit or re.search(r"\?\s*\{[^}]*max_pct|if\s*\([^)]*\)[^;]*max_pct|&&[^;]*max_pct", body), \
        "max_pct must only be sent when the rule had no number"
    assert api.DecisionIn.model_validate({"approved": True}).max_pct is None
    assert api.DecisionIn.model_validate({"approved": True, "max_pct": 40}).max_pct == 40
    with pytest.raises(Exception):
        api.DecisionIn.model_validate({"approved": True, "max_pct": 0})
    for forbidden in ("limits", "llm"):
        assert forbidden not in body


def test_rejecting_a_rule_still_never_sends_approval_nor_needs_a_number(page):
    """SPEC-AMBIGUITY: the spec says the page asks for the number of a rule with none; a rejection records no limit, so
    the stricter reading is that `reject` is not blocked behind the number prompt (approved stays `action === "approve"`)."""
    body = _body(page, "decideRule")
    assert "max_pct" in body, "the number the user gives is part of the decision"
    assert not re.search(r"approved\s*:\s*true\b", body)
    assert re.search(r"approved\s*=\s*action\s*===?\s*[\"']approve[\"']", body)
    assert re.search(r"\bapproved\s*&&|if\s*\(\s*approved\b|[\"']approve[\"']\s*&&|if\s*\(\s*action\s*===?\s*[\"']approve", body), \
        "the number is requested on the approve path only"


# ---------------------------------------------------------------- 4. wording: blocklist and D10 vocabulary

def test_new_ui_strings_pass_the_blocklist_and_d10_vocabulary(page):
    """SPEC §9.3 item 3 / D10 items 1, 2, 4: the new functions print no advisory phrase and no word that proposes, ranks
    or labels a choice; HTML <option> tags are widgets, not wording. No typed number in their template text."""
    names = NEW_HOOKS + ("confirmIntent", "renderIntentForm", "decideRule", "renderValuesRules", "pageProfil")
    for name in names:
        body, texts = function_source(page, name)
        assert blocklist_hits(body) == [], name
        found = D10_WORDS.search(norm(re.sub(r"</?(option|optgroup)\b", " ", body)))
        assert found is None, (name, found and found.group(0))
        if name in NEW_HOOKS:
            assert not typed_digits(texts), (name, typed_digits(texts)[:5])


def test_page_passes_the_blocklist_apart_from_the_fixed_disclaimer(page):
    """The whole page (markup, text, attributes, scripts) matches no blocklist phrase, the disclaimer and the Recherche
    refusal sentence excepted (SPEC §9.3 item 3, D10 item 4)."""
    exempt = page.replace(H.DISCLAIMER_FR, " ").replace(H.REFUSAL_FR, " ").replace(H.SAMPLE_QUESTION, " ")
    assert blocklist_hits(exempt) == []
