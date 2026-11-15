"""P2.UI oracle: Flow B UI on the app (SPEC §12 UI requirements and route table, §4 Flow B B2, §3.6 intent, §13
report; docs/spec-decisions.md D7, D10; docs/spec-factors.md §20.7).

`deploy/site/index.html` (French, single-page) is being retired; this oracle now pins the equivalent behaviour
on the English multi-screen app: `deploy/site/app.html` + `deploy/site/js/screens/trade.js` (Flow B screen) +
`deploy/site/js/core/data_source.js` (the one API/data layer every screen calls through) +
`deploy/site/js/screens/pillars/preferences.js` (N3 approve/reject; read-only reference, not owned by this task).

What is pinned
  1. Live vs. static mode is a health probe (`api/health`) against a configured, never-hard-coded base
     (`apiBase()` reads `<meta name="coherence-api-base">` / `window.COHERENCE_API_BASE`), not a literal host.
     The only POST helper is `data_source.js`'s `post()`; every screen calls through `App.source.*`.
  2. N3 approve / reject (preferences.js, already built, read-only here): live, they call
     `POST /profiles/{id}/parsed-rules/{i}/decision` (body `{approved}`) via `App.source.decideRule` and
     rerun the analysis; without a live API the buttons are omitted outright (stronger than merely disabled).
  3. B2 intent confirmation: `trade.js`'s confirm card fields (ticker, target weight, funding-by-ticker) are
     editable and read only from the draft intent (D10: nothing the user did not state); submit ->
     `App.source.confirmIntent` -> `POST /intents/{intent_id}/confirm`, live only. With no live API, the card
     still shows Robert's `in_0001` (NVDA 10 %, funding specific ZST.TO), read from a static demo report file,
     confirming is disabled, and nothing is ever sent.
  4. Flow B report: the before/after cone overlays `metrics_current` ("Before") and `metrics_proposed`
     ("After"), labelled, with an aria-label; `trade.js` reads the report directly (no `site` block needed).
  5. Every string in the touched code passes the SPEC §9.3 blocklist and the D10 vocabulary.

Static demo report: `deploy/preview_report.py` `build_en_ca()` (new, additive; the existing FR `build()` /
`build_flow_b` are untouched) writes the English equivalent of the FR `robert-nvda.json` demo, with
`headroom.line` filled in via the real `coherence_analyzer.resizer.headroom_text` (SPEC §8 EN template), to
`<out>/robert-nvda.json` -- the file `trade.js` fetches at `reports/en-CA/robert-nvda.json`, matching
`data_source.js`'s own `en-CA` report directory convention. It is not listed in any reports index.

Offline and deterministic: COHERENCE_ANALYZER_LLM=mock, JS is read as text (no browser, no network, no clock).
"""
from __future__ import annotations

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
APP_HTML = DEPLOY / "site" / "app.html"
DATA_SOURCE = DEPLOY / "site" / "js" / "core" / "data_source.js"
TRADE = DEPLOY / "site" / "js" / "screens" / "trade.js"
PREFERENCES = DEPLOY / "site" / "js" / "screens" / "pillars" / "preferences.js"
PREVIEW = DEPLOY / "preview_report.py"
BUILD_SITE = DEPLOY / "build_site.py"

# SPEC §9.3 item 3, verbatim list (matched case- and accent-insensitively).
BLOCKLIST = (
    "tu devrais", "vous devriez", "tu pourrais", "vous pourriez", "envisage", "il serait judicieux",
    "il serait préférable", "je recommande", "nous recommandons", "je conseille", "nous conseillons",
    "je suggère", "rééquilibr", "you should", "you could", "you may want to", "you might want to", "consider",
    "it would be wise", "it would be better", "we recommend", "i recommend", "we suggest", "i suggest",
    "we advise", "rebalanc", "next step", "prochaine étape",
)
FUNDING_KINDS = ("specific", "pro_rata_all", "pro_rata_class", "cash_contribution")          # SPEC §3.6
# D10 item 1-2 vocabulary: nothing proposes, ranks or labels a choice.
D10_WORDS = re.compile(r"\b(meilleur(e|s)?|ideal|ideale|recommand\w*|suggere\w*|suggest\w*|alternatives?|options?|"
                       r"best|ranking|rank)\b")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.replace("\u2019", "'").replace("\xa0", " ").replace("\u202f", " "))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", s)


NORM_BLOCKLIST = tuple(norm(p) for p in BLOCKLIST)


def blocklist_hits(s: str) -> list[str]:
    low = norm(s)
    return [p for p in NORM_BLOCKLIST if p in low]


def strings(obj, skip_keys: frozenset[str] = frozenset({"disclaimer"})) -> list[str]:
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


# ---------------------------------------------------------------- tiny JS scanner
# trade.js / data_source.js / preferences.js build HTML/text by string concatenation (`'...' + x + '...'`),
# not template literals: no `${}` slot-drilling is needed, just comment/string-aware brace balancing.

def _token_end(s: str, i: int) -> int | None:
    """If `s[i]` starts a string or a comment, the index just past it; else None."""
    c = s[i]
    if c in "\"'`":
        j = i + 1
        while s[j] != c:
            j += 2 if s[j] == "\\" else 1
        return j + 1
    if s.startswith("//", i):
        nl = s.find("\n", i)
        return nl if nl != -1 else len(s)
    if s.startswith("/*", i):
        return s.index("*/", i) + 2
    return None


def _balanced(s: str, start: int, open_ch: str, close_ch: str) -> int:
    """`start` is the index of the opening char; returns the index just after its match."""
    depth, i, n = 0, start, len(s)
    while i < n:
        te = _token_end(s, i)
        if te is not None:
            i = te
            continue
        c = s[i]
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError(f"unbalanced {open_ch!r}")


def function_source(src: str, name: str) -> str:
    found = list(re.finditer(rf"function\s+{re.escape(name)}\s*\(", src))
    assert len(found) == 1, f"must define `function {name}(` exactly once (found {len(found)})"
    paren_open = found[0].end() - 1
    paren_close = _balanced(src, paren_open, "(", ")")
    brace_open = src.index("{", paren_close)
    brace_close = _balanced(src, brace_open, "{", "}")
    return src[brace_open + 1:brace_close - 1]


def string_literals(body: str) -> list[str]:
    """String-literal contents only (comments and code stripped), for wording/digit scans."""
    out, i, n = [], 0, len(body)
    while i < n:
        c = body[i]
        if c in "\"'`":
            j = i + 1
            while body[j] != c:
                j += 2 if body[j] == "\\" else 1
            out.append(body[i + 1:j])
            i = j + 1
            continue
        if body.startswith("//", i):
            nl = body.find("\n", i)
            i = nl if nl != -1 else n
            continue
        if body.startswith("/*", i):
            i = body.index("*/", i) + 2
            continue
        i += 1
    return out


def reconstructed_markup(body: str) -> str:
    """The function's own built markup as one string: string-literal text kept as is, every run of
    non-string code (the `+ esc(x) +` glue between concatenated literals) collapsed to one `§`
    placeholder -- so a tag split across concatenated literals (`'<input ... value="' + esc(v) + '">'`)
    rejoins into one tag for stripping, the same way the old template-literal `${}` slots did."""
    out, i, n, in_code = [], 0, len(body), False
    while i < n:
        c = body[i]
        if c in "\"'`":
            j = i + 1
            while body[j] != c:
                j += 2 if body[j] == "\\" else 1
            out.append(body[i + 1:j])
            i, in_code = j + 1, False
            continue
        if body.startswith("//", i):
            nl = body.find("\n", i)
            i = nl if nl != -1 else n
            continue
        if body.startswith("/*", i):
            i = body.index("*/", i) + 2
            continue
        if not c.isspace() and not in_code:
            out.append("§")
            in_code = True
        i += 1
    return "".join(out)


def typed_digits(text: str) -> list[str]:
    text = re.sub(r"<[^>]*>", " ", text)
    text = re.sub(r"&#?\w+;", " ", text)
    return re.findall(r"\S*\d\S*", text)


def uses(src: str, name: str) -> int:
    """Occurrences of the call/reference `name(` in the file (the definition counts once)."""
    return len(re.findall(rf"\b{re.escape(name)}\s*\(", src))


@pytest.fixture(scope="module")
def data_source_src() -> str:
    return DATA_SOURCE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def trade_src() -> str:
    return TRADE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def prefs_src() -> str:
    return PREFERENCES.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_html() -> str:
    return APP_HTML.read_text(encoding="utf-8")


# ---------------------------------------------------------------- 1. API wiring and the static demo

def test_api_base_is_configured_not_hard_coded(data_source_src, app_html, trade_src):
    """SPEC §12 / task: whether the page talks to a live API is a health probe against a configured base
    (`apiBase()`), never a literal host; the shipped page carries an empty `<meta coherence-api-base>`."""
    body = function_source(data_source_src, "apiBase")
    assert "coherence-api-base" in body or "COHERENCE_API_BASE" in body
    assert re.search(r"<meta\s+name=[\"']coherence-api-base[\"']\s+content=[\"']{2}\s*/?>", app_html), \
        "app.html must carry an empty <meta name=\"coherence-api-base\" content=\"\">"
    init_body = function_source(data_source_src, "init")
    assert "health" in init_body, "boot decides live vs. static from a health probe (api/health)"
    for src, label in ((data_source_src, "data_source.js"), (trade_src, "trade.js")):
        low = src.lower()
        assert "localhost" not in low and "127.0.0.1" not in low and "0.0.0.0" not in low, label
        assert not re.search(r"fetch\(\s*[\"'`]https?://", src), f"no absolute URL in a fetch call ({label})"
    assert uses(data_source_src, "apiBase") >= 3, "defined once, used by apiDir and siteApiDir at least"


def test_post_requests_go_through_one_helper(data_source_src, trade_src, prefs_src):
    """The only POST in the data layer is `post()` (delegates to `request()`, URL = apiDir()/siteApiDir() +
    path, JSON body); trade.js's one `fetch(` is a plain GET of the static demo file, never a POST; screens
    never call `fetch(` themselves to reach the API, only `App.source.*`."""
    post_body = function_source(data_source_src, "post")
    assert "method" in post_body and "POST" in post_body and "JSON.stringify" in post_body
    request_body = function_source(data_source_src, "request")
    assert "fetch(" in request_body
    assert data_source_src.count('"POST"') == 1, "exactly one POST verb literal"
    assert len(re.findall(r"\bmethod\s*:", data_source_src)) == 2, "POST (post()) and PUT (savePortfolio) only"
    assert not re.search(r"XMLHttpRequest|\bDELETE\b", data_source_src)
    assert trade_src.count("fetch(") == 1, "the one GET of the static demo report"
    demo_body = function_source(trade_src, "renderStaticDemo")
    assert "fetch(" in demo_body and "STATIC_DEMO_PATH" in demo_body
    assert '"POST"' not in demo_body and not re.search(r"\bmethod\s*:", demo_body), "a GET, not an API call"
    assert prefs_src.count("fetch(") == 0
    assert uses(data_source_src, "decideRule") >= 1 and "App.source.decideRule(" in prefs_src
    assert uses(data_source_src, "correction") >= 1 and "App.source.correction(" in prefs_src
    assert uses(data_source_src, "confirmIntent") >= 1 and "App.source.confirmIntent(" in trade_src
    assert uses(data_source_src, "requestDraft") >= 1 and "App.source.requestDraft(" in trade_src


def test_rule_buttons_stay_disabled_with_the_current_note_when_no_api(prefs_src):
    """Task (2), preferences.js (read-only reference, N3 already built): without a live API the approve/reject
    buttons are not merely disabled, they are not rendered at all -- a stronger version of the old page's
    "stay disabled with the current note" guarantee, since no element exists that could even attempt a
    decision offline. The gate is `App.source.mode === "live"`, not a literal boolean."""
    body = function_source(prefs_src, "parsedRuleHtml")
    m = re.search(r"const\s+actions\s*=\s*App\.source\.mode\s*===\s*[\"']live[\"']\s*\?(?P<live>.*?):\s*\"\"\s*;",
                  body, re.S)
    assert m, "the approve/reject buttons must be gated on `App.source.mode === \"live\"`, else omitted"
    live_branch = m.group("live")
    for action in ("approve", "reject"):
        assert re.search(rf'data-rule-action=[\"\']{action}[\"\']', live_branch), action
    outside = body[:m.start()] + body[m.end():]
    assert not re.search(r"data-rule-action=", outside), "no unconditional decision button elsewhere"
    assert not typed_digits(reconstructed_markup(body))


def test_rule_buttons_are_bound_to_decide_rule(prefs_src):
    """The `[data-rule-action]` buttons are bound (click) to `handleRuleDecision`, which calls
    `App.source.decideRule` -- only ever reachable through that one path."""
    bind_body = function_source(prefs_src, "bindOnce")
    assert re.search(r"closest\(\s*[\"'`]\[data-rule-action\]", bind_body)
    assert "handleRuleDecision(" in bind_body
    assert uses(prefs_src, "handleRuleDecision") >= 2
    decision_body = function_source(prefs_src, "handleRuleDecision")
    assert "App.source.decideRule(" in decision_body


def test_decide_rule_posts_the_decision_route_and_rerenders(data_source_src, prefs_src):
    """SPEC §12 + api.py: POST /profiles/{profile_id}/parsed-rules/{index}/decision, body `{approved}` (or
    `{approved, max_pct}`); approve sends true, reject false; the analysis is rerun after a successful
    decision; a failure is shown (window.alert), never swallowed; neither function ever sends a `limits` or
    `llm` field."""
    route_body = function_source(data_source_src, "decideRule")
    assert "profiles/" in route_body and "/parsed-rules/" in route_body and "/decision" in route_body
    assert route_body.index("profiles/") < route_body.index("/parsed-rules/") < route_body.index("/decision")
    assert "encodeURIComponent(personaId)" in route_body
    assert re.search(r"\bapproved\b", route_body)
    assert "requireLive(" in route_body
    decision_body = function_source(prefs_src, "handleRuleDecision")
    assert re.search(r"\bapprove\b", decision_body) and re.search(r"\breject\b", decision_body)
    assert "App.rerunAnalysis(" in decision_body, "the analysis is rerun after a recorded decision"
    assert "catch" in decision_body and "window.alert(" in decision_body, "a failure is shown, never swallowed"
    assert not re.search(r"catch\s*\([^)]*\)\s*\{\s*\}", decision_body), "the catch block is never empty"
    for forbidden in ("limits", "llm"):
        assert forbidden not in route_body, f"SPEC inv. 2: the data layer never sends `{forbidden}`"
        assert forbidden not in decision_body, f"SPEC inv. 2: the UI never sends `{forbidden}`"


def test_rejecting_never_sends_approval(prefs_src, data_source_src):
    """N3 safety: `approved` is a boolean computed from the clicked action (`action === "approve"`), and the
    request body is built from that variable, never a literal `true`."""
    decision_body = function_source(prefs_src, "handleRuleDecision")
    assert re.search(r"approved\s*=\s*action\s*===\s*[\"']approve[\"']", decision_body)
    assert not re.search(r"\bapproved\s*[:=]\s*true\b", decision_body)
    route_body = function_source(data_source_src, "decideRule")
    assert not re.search(r"\bapproved\s*[:=]\s*true\b", route_body)
    assert re.search(r"\{\s*approved\s*[,}]", route_body), "the body is `{approved}` (or with max_pct), not a literal"


# ---------------------------------------------------------------- 2. B2 intent confirmation (trade.js)

def test_intent_form_has_the_editable_draft_fields(app_html, trade_src):
    """SPEC §4 B2 / §3.6: ticker, target weight and the funding-by-ticker field are editable and each read
    only from the draft intent; the confirm form exists with a submit button."""
    assert re.search(r"<form\b[^>]*\bdata-trade-confirm-form\b", app_html)
    assert re.search(r"<button\b[^>]*\btype=[\"']submit[\"'][^>]*>Confirm", app_html)
    for name, field in (("tickerFieldHtml", "ticker"), ("weightFieldHtml", "target_weight_pct")):
        body = function_source(trade_src, name)
        assert re.search(rf'\bname=[\"\']{field}[\"\']', body), field
        assert "readonly" not in body
        assert not re.search(r"<(input|select)\b[^>]*\bdisabled\b", body)
    ticker_body = function_source(trade_src, "tickerFieldHtml")
    assert "draft.ticker" in ticker_body
    weight_body = function_source(trade_src, "weightFieldHtml")
    assert "draft.target_weight_pct" in weight_body
    funding_body = function_source(trade_src, "fundingFieldsHtml")
    assert re.search(r'\bname=[\"\']funding_ticker[\"\']', funding_body)
    assert "funding.ticker" in funding_body
    assert not typed_digits(reconstructed_markup(ticker_body))
    assert not typed_digits(reconstructed_markup(weight_body))


def test_intent_form_prefills_nothing_the_user_did_not_state(trade_src):
    """D10 item 1: every field value is read from the draft intent (`v(draft.x)` / `v(funding.ticker)`, where
    `v(null) === ""`), never a `||`/`??` fallback to a typed ticker, number or funding kind; the demo
    persona's own identifiers never appear as a literal in the functions that build or paint the confirm
    form and the static demo (comments mentioning the demo persona, e.g. this file's own header, are not
    "typed" into the UI and are exempt, matched by extracting only string-literal content, not comments)."""
    confirm_bodies = [function_source(trade_src, name) for name in
                       ("tickerFieldHtml", "weightFieldHtml", "fundingFieldsHtml", "renderConfirmFields",
                        "ensureConfirmPainted", "handleConfirmSubmit", "renderStaticDemo", "paintStaticDemo")]
    for body in confirm_bodies:
        assert not re.search(r"(\|\||\?\?)\s*[\"'`][A-Z]{2,6}(\.[A-Z]{1,3})?[\"'`]", body)
        assert not re.search(r"(\|\||\?\?)\s*\d", body)
    literal = " ".join(lit for body in confirm_bodies for lit in string_literals(body))
    # a `placeholder="e.g. ZST.TO"` format hint is not a prefilled value (shown only while the field is
    # empty, asserts nothing about the user's own data); strip hints before the typed-identifier scan.
    literal = re.sub(r'placeholder=\\?["\'][^"\']*\\?["\']', "", literal)
    for typed in ("NVDA", "ZST.TO", "in_0001", "ev_robert"):
        assert typed not in literal, f"`{typed}` must come from the fetched report, not be typed in trade.js"


def test_confirm_posts_the_intent_route_with_the_edited_values(data_source_src, trade_src, app_html):
    """SPEC §12: POST /intents/{intent_id}/confirm, body = the edited {ticker, target_weight_pct, funding}; no
    request is possible without a live API (`App.source.mode !== "live"` returns before anything is built or
    sent); a failure is shown (role=alert), never swallowed; the response report replaces the screen's report."""
    route_body = function_source(data_source_src, "confirmIntent")
    assert "intents/" in route_body and "/confirm" in route_body
    assert route_body.index("intents/") < route_body.index("/confirm")
    assert "requireLive(" in route_body
    submit_body = function_source(trade_src, "handleConfirmSubmit")
    assert re.search(r'App\.source\.mode\s*!==\s*[\"\']live[\"\']', submit_body), \
        "no request is built when the screen is not live"
    assert "App.source.confirmIntent(" in submit_body
    for field in ("ticker", "target_weight_pct", "funding"):
        assert field in submit_body, field
    assert re.search(r"Number\(", submit_body), "the weight is sent as a number"
    assert "catch" in submit_body and "showConfirmError(" in submit_body
    assert re.search(r'role=[\"\']alert[\"\']', app_html) or "role=\"alert\"" in trade_src
    for forbidden in ("limits", "llm"):
        assert forbidden not in submit_body, f"SPEC inv. 2: the UI never sends `{forbidden}`"
    assert uses(trade_src, "handleConfirmSubmit") >= 2
    bind_body = function_source(trade_src, "bindOnce")
    assert re.search(r"data-trade-confirm-form", bind_body) and "preventDefault" in bind_body


def test_intent_form_is_wired_into_the_page(trade_src):
    """The static-demo report is its own file (`reports/en-CA/robert-nvda.json`, `deploy/preview_report.py`
    `build_en_ca` output), fetched directly, never through a reports index (P2.D10C oracle, same rule as the
    FR demo); the confirm card is painted from it and submitting stays disabled (nothing is ever sent)."""
    assert 'const STATIC_DEMO_PATH = "reports/en-CA/robert-nvda.json";' in trade_src
    assert "index.json" not in trade_src
    demo_body = function_source(trade_src, "renderStaticDemo")
    assert "STATIC_DEMO_PATH" in demo_body
    paint_body = function_source(trade_src, "paintStaticDemo")
    assert "ensureConfirmPainted(" in paint_body
    assert re.search(r"confirmBtn\.disabled\s*=\s*true", paint_body)
    assert uses(trade_src, "ensureConfirmPainted") >= 2
    assert uses(trade_src, "renderConfirmFields") >= 1


def test_index_listing_stays_the_four_flow_c_reviews():
    """P2.D10C frozen oracle: build_site's FR report list is unchanged (4 flow C reviews); Flow B is not in
    it. build_site.py is untouched by this change."""
    spec = importlib.util.spec_from_file_location("build_site_flow_b_ui", BUILD_SITE)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(DEPLOY))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(DEPLOY))
    reports = getattr(mod, "REPORTS")
    names = [r[0] for r in reports]
    assert len(names) == 4
    assert all(n.endswith("-review") or n.endswith("-review-nvda") for n in names), names
    assert not [n for n in names if n == "robert-nvda"]


# ---------------------------------------------------------------- 3. before / after cone (trade.js)

def test_cone_overlays_current_and_proposed_when_the_report_has_both(trade_src):
    """SPEC §12 / §20.7: `coneMarkup(report)` draws `metrics_current` and overlays `metrics_proposed` when
    present (flow B), guarded so a report with no proposed side still draws the current cone alone; both feed
    the shared y-range; p5/p95 of both cones are drawn."""
    body = function_source(trade_src, "coneMarkup")
    assert "metrics_current" in body and "metrics_proposed" in body
    assert body.count(".cone") >= 2
    first = body.index("propCone")
    assert re.search(r"&&|\bif\s*\(|propCone\s*\?", body[first:first + 200]), \
        "the proposed cone is drawn only when present"
    assert body.count(".p5") >= 3 and body.count(".p95") >= 3
    assert "<polyline" in body and "<path" in body
    assert "return null" in body, "no current cone -> nothing to draw"


def test_cone_overlay_is_labelled_before_and_after(trade_src):
    """Task (4): the two cones are labelled "Before" and "After" (chart text and the aria-label), as facts
    about the user's own trade (D10 vocabulary); the chart is described for assistive tech, not by colour
    alone."""
    body = function_source(trade_src, "coneMarkup")
    assert "Before" in body and "After" in body
    assert "aria-label" in body
    literal = " ".join(string_literals(body))
    assert D10_WORDS.search(norm(literal)) is None
    assert blocklist_hits(literal) == []


def test_flow_b_report_gets_the_display_block_the_page_needs(trade_src):
    """Retarget, guarantee inverted on purpose: index.html's renderers needed a `rep.site` block built by
    `loadFlowB()` because `preview_report.py` carries `profile_card` / `limits` / `weights_current` but no
    `site`. trade.js's renderers (`drawChart`, `renderHeadroom`, `renderChecks`, `renderReport`) need no such
    transformation at all: `metrics_current`, `metrics_proposed`, `checks_current`, `checks_proposed` and
    `headroom` are already present on the report as the pipeline/preview shape; every renderer works on the
    Flow B report with zero `.site` access anywhere in trade.js."""
    assert ".site" not in trade_src
    assert "report" in function_source(trade_src, "drawChart") and "coneMarkup(" in function_source(trade_src, "drawChart")
    for name, field in (
        ("coneMarkup", "metrics_current"), ("coneMarkup", "metrics_proposed"),
        ("renderHeadroom", "headroom"), ("renderChecks", "checks_proposed"),
        ("renderReport", "checks_current"), ("renderReport", "checks_proposed"),
    ):
        assert field in function_source(trade_src, name), (name, field)


# ---------------------------------------------------------------- 4. the Flow B reports (preview_report.py)

def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def preview_mod():
    return _load("preview_report_flow_b_ui", PREVIEW)


@pytest.fixture(scope="module")
def flow_b(preview_mod, tmp_path_factory) -> dict:
    """`robert-nvda` from deploy/preview_report.py `build()` (real engine, fixed seed, no LLM), loaded as its
    own file (never through an index). Unaffected by the app port: `build()` itself is untouched."""
    out = tmp_path_factory.mktemp("flow_b_ui")
    paths = preview_mod.build(out)
    target = next(p for p in paths if p.stem == "robert-nvda")
    return json.loads(target.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def flow_b_en_ca(preview_mod, tmp_path_factory) -> dict:
    """The new additive EN-CA build: `build_en_ca()` writes the app's static demo report."""
    out = tmp_path_factory.mktemp("flow_b_en_ca")
    dest = preview_mod.build_en_ca(out)
    assert dest == out / "robert-nvda.json", "matches data_source.js's en-CA report directory convention"
    assert not (out / "index.json").exists(), "the demo report is never listed in an index"
    return json.loads(dest.read_text(encoding="utf-8"))


def test_flow_b_report_carries_robert_in_0001_for_the_static_demo(flow_b):
    """SPEC §3.6, §4: the static demo's confirm card shows Robert's draft `in_0001` (NVDA, target 10 %,
    funding specific ZST.TO), read from `report.intent`."""
    assert flow_b["flow"] == "B"
    intent = flow_b["intent"]
    assert intent["intent_id"] == "in_0001"
    assert intent["ticker"] == "NVDA"
    assert intent["target_weight_pct"] == 10
    assert intent["funding"] == {"kind": "specific", "ticker": "ZST.TO"}
    assert intent["funding"]["kind"] in FUNDING_KINDS


def test_flow_b_report_has_before_and_after_cones_on_one_grid(flow_b):
    """SPEC §13 / §12: `metrics_current` and `metrics_proposed` both carry an engine cone with the same
    percentile keys and length (so the overlay shares one time axis), ordered p5 <= p25 <= p50 <= p75 <= p95
    at every step, and the two cones differ (the trade changes the portfolio)."""
    cur, prop = flow_b["metrics_current"]["cone"], flow_b["metrics_proposed"]["cone"]
    keys = ("p5", "p25", "p50", "p75", "p95")
    assert set(keys) <= set(cur) and set(keys) <= set(prop)
    n = len(cur["p50"])
    assert n > 0
    for k in keys:
        assert len(cur[k]) == n and len(prop[k]) == n, k
    for cone in (cur, prop):
        for i in range(n):
            vals = [cone[k][i] for k in keys]
            assert vals == sorted(vals), (i, vals)
    assert cur["p50"] != prop["p50"]


def test_flow_b_report_before_after_sides_follow_decisions_d2_d7(flow_b):
    """D7 point 5 / D2: Robert today is coherent (his pessimistic value at horizon is not below his floor);
    the NVDA trade is incoherent with H9 breached, so the 'after' cone's P5 ends below the floor and the
    'before' one above."""
    floor = flow_b["limits"]["floor_at_horizon"]
    assert flow_b["metrics_current"]["p5_T"] >= floor
    assert flow_b["metrics_proposed"]["p5_T"] < floor
    assert flow_b["verdict"] == "incoherent"
    assert flow_b["metrics_current"]["p5_T"] > flow_b["metrics_proposed"]["p5_T"]


def test_build_en_ca_adds_the_spec_8_headroom_line_and_matches_the_naming_convention(flow_b, flow_b_en_ca):
    """`build_en_ca` is additive (reuses `build()`'s `robert-nvda` content unchanged) and fills in
    `headroom.line` with the real SPEC §8 EN template (`coherence_analyzer.resizer.headroom_text`), which
    `build()`'s own preview-only `headroom` lacks; everything else about the report is identical."""
    assert flow_b["headroom"].get("line") is None or "line" not in flow_b["headroom"]
    h = flow_b_en_ca["headroom"]
    assert h["binding_check"] == flow_b["headroom"]["binding_check"]
    assert h["max_weight_pct"] == flow_b["headroom"]["max_weight_pct"]
    assert isinstance(h.get("line"), str) and h["line"]
    assert "ZST.TO" in h["line"] and h["binding_check"] in h["line"]
    assert re.fullmatch(r"With the funding you gave \(ZST\.TO\), this trade crosses your limit H\d+ above "
                        r"\d+(?:\.\d+)? ?%\.", h["line"]), h["line"]
    for key in ("intent", "metrics_current", "metrics_proposed", "verdict"):
        assert flow_b_en_ca[key] == flow_b[key], key
    assert "site" not in flow_b_en_ca


def test_flow_b_report_has_no_site_block_so_the_loader_builds_it(flow_b, flow_b_en_ca):
    """Pins the contract trade.js relies on (see `test_flow_b_report_gets_the_display_block_the_page_needs`):
    the preview/app report offers `profile_card`, `limits`, `weights_current`, `assumptions`, `data_window`
    directly and has no `site` of its own -- for both the FR preview report and the EN-CA app demo."""
    for rep in (flow_b, flow_b_en_ca):
        assert "site" not in rep
        for key in ("profile_card", "limits", "weights_current", "assumptions", "data_window", "profile_id",
                    "portfolio_id", "sections", "explanation", "disclaimer", "headroom"):
            assert key in rep, key
        assert isinstance(rep["profile_card"].get("value_cad"), (int, float))
        assert rep["profile_card"].get("name") == "Robert"


# ---------------------------------------------------------------- 5. wording: blocklist and D10 vocabulary

def test_page_passes_the_blocklist_apart_from_the_fixed_disclaimer(trade_src):
    """SPEC §9.3 item 3 / D10 item 4: nothing in trade.js (markup, text, attributes, scripts) matches the
    blocklist; trade.js carries no fixed disclaimer or refusal sentence to exempt."""
    assert blocklist_hits(trade_src) == []


def test_new_ui_code_passes_the_blocklist_and_d10_vocabulary(trade_src):
    """Every string-building function's own literals: no advisory phrase, and no word that proposes, ranks or
    labels a choice (D10 items 1-2)."""
    for name in ("renderStaticDemo", "paintStaticDemo", "tickerFieldHtml", "weightFieldHtml", "fundingFieldsHtml",
                 "renderConfirmFields", "handleConfirmSubmit", "handleDraftSubmit", "coneMarkup", "renderHeadroom",
                 "renderChecks", "renderReport"):
        body = function_source(trade_src, name)
        literal = " ".join(string_literals(body))
        assert blocklist_hits(literal) == [], name
        words = re.sub(r"</?(option|optgroup)\b", " ", literal)   # HTML <option> tags are widgets, not wording
        found = D10_WORDS.search(norm(words))
        assert found is None, (name, found and found.group(0))


def test_flow_b_report_text_passes_the_blocklist(flow_b, flow_b_en_ca):
    """Every string of the Flow B report the screen shows (FR demo content and the EN-CA app demo) passes the
    blocklist, the fixed disclaimer excepted (FR and EN), and carries no option / alternative wording in the
    text blocks the screen prints (explanation, preview banner, sections)."""
    disclaimers = {flow_b["disclaimer"]["fr"], flow_b["disclaimer"]["en"]}
    for rep in (flow_b, flow_b_en_ca):
        texts = [s for s in strings(rep) if s not in disclaimers]
        bad = [(s[:80], blocklist_hits(s)) for s in texts if blocklist_hits(s)]
        assert bad == []
        shown = strings(rep["explanation"]) + strings(rep.get("preview", {})) + strings(rep.get("sections", []))
        assert not [s[:80] for s in shown if re.search(r"\b(options?|alternatives?)\b", s, re.I)]
