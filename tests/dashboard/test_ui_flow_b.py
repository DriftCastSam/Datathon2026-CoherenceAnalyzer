"""P2.UI oracle: Dashboard Flow B UI in `deploy/site/index.html` (SPEC §12 UI requirements and route table, §4 Flow B
B2, §3.6 intent, §13 report; docs/spec-decisions.md D7, D10; docs/spec-factors.md §20.7).

What is pinned
  1. N3 approve / reject: with an API base URL configured they call `POST /profiles/{id}/parsed-rules/{i}/decision`
     (body `{approved: bool}`, the route of coherence_analyzer/api.py) and re-render; with no API (static demo) they
     stay disabled with the current « Affichage seulement » note.
  2. B2 intent confirmation form: ticker, target weight and funding, editable, filled only from the draft intent
     (D10: nothing the user did not state); submit -> `POST /intents/{intent_id}/confirm`; the static demo shows
     Robert's `in_0001` (NVDA 10 %, funding specific ZST.TO) read from the flow B report.
  3. Flow B report: the cone overlays `metrics_current` (before) and `metrics_proposed` (after), labelled.
  4. Every string passes the SPEC §9.3 blocklist (apart from the fixed disclaimer) and the D10 vocabulary.

Hooks the page must define (each `function <name>(`, exactly once, and used at least once elsewhere):
  apiBase()      configured API base URL ("" when none). Read from `<meta name="coherence-api-base" content="">`
                 (empty in the shipped static page; `window.COHERENCE_API_BASE` may override it). Never hard-coded.
  apiPost(path, body)   the ONLY place that calls `fetch(` with `method: "POST"`; URL = apiBase() + path; JSON body.
  decideRule(...)       N3 decision: apiPost to `/profiles/${profile_id}/parsed-rules/${index}/decision` with
                        `approved`, then re-render. `renderValuesRules` itself makes no request (tests/dashboard/
                        test_site.py) and binds `[data-action]` buttons to decideRule.
  renderIntentForm(intent)  the B2 form (`<form id="intent-form">`), reading every field from `intent`.
  confirmIntent(...)    submit handler: apiPost to `/intents/${intent_id}/confirm`; no request without an API.
  loadFlowB()           fetches `reports/robert-nvda.json` (preview_report.py output) as a separate file, NOT via
                        `reports/index.json` (the P2.D10C oracle keeps that index at the 4 flow C reviews).
  cone(rep)             draws `metrics_current` and, when not null, `metrics_proposed`, labelled « Avant » / « Après ».
Function bodies are scanned as text (no browser, no network, no clock): the page is vanilla JS and no JS runtime is
assumed. Reports come from `deploy/preview_report.py` `build()` in a temp dir (real engine, fixed seed, no LLM).
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
INDEX = DEPLOY / "site" / "index.html"
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
DISCLAIMER_FR = ("Simulation éducative avec des profils fictifs. Ceci n'est pas un conseil financier personnalisé. "
                 "Aucune transaction n'est exécutée.")
DISCLAIMER_EN = ("Educational simulation with fictional profiles. This is not personalised financial advice. "
                 "No transaction is executed.")
REFUSAL_FR = "Je ne peux pas classer ni recommander d'actifs."          # Recherche refusal: exempt (P2.D10C oracle)
SAMPLE_QUESTION = "Lequel est le meilleur pour moi?"                      # user's own sample question, exempt
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


# ---------------------------------------------------------------- tiny JS scanner (strings, templates, braces)

def _skip_string(s: str, i: int) -> int:
    q = s[i]
    i += 1
    while s[i] != q:
        i += 2 if s[i] == "\\" else 1
    return i + 1


def _scan_template(s: str, i: int, out: list[str]) -> int:
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
    """(body source, literal template texts with `${}` slots as '§') of `function <name>(...) {...}`."""
    found = list(re.finditer(rf"function\s+{re.escape(name)}\s*\(", html))
    assert len(found) == 1, f"index.html must define `function {name}(` exactly once (found {len(found)})"
    i = found[0].end()
    depth = 1
    while depth:
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
    bad = []
    for t in texts:
        t = re.sub(r"<[^>]*>", " ", t)
        t = re.sub(r"&#?\w+;", " ", t)
        bad += re.findall(r"\S*\d\S*", t)
    return bad


def uses(html: str, name: str) -> int:
    """Occurrences of the call/reference `name(` in the page (the definition counts once)."""
    return len(re.findall(rf"\b{re.escape(name)}\s*\(", html))


@pytest.fixture(scope="module")
def page() -> str:
    return INDEX.read_text(encoding="utf-8")


def _body(page: str, name: str) -> str:
    return function_source(page, name)[0]


def _exempt(page: str) -> str:
    return page.replace(DISCLAIMER_FR, " ").replace(REFUSAL_FR, " ").replace(SAMPLE_QUESTION, " ")


# ---------------------------------------------------------------- 1. API wiring and the static demo

def test_api_base_is_configured_not_hard_coded(page):
    """SPEC §12 / task: the API base URL is configuration. Empty in the shipped static page; no host in the code."""
    body = _body(page, "apiBase")
    assert "coherence-api-base" in body or "COHERENCE_API_BASE" in body
    assert re.search(r"<meta\s+name=[\"']coherence-api-base[\"']\s+content=[\"']{2}\s*/?>", page), \
        "the shipped page must carry an empty <meta name=\"coherence-api-base\" content=\"\">"
    low = page.lower()
    assert "localhost" not in low and "127.0.0.1" not in low and "0.0.0.0" not in low
    assert not re.search(r"fetch\(\s*[\"'`]https?://", page), "no absolute URL in a fetch call"
    assert uses(page, "apiBase") >= 3, "defined once, used by apiPost, renderValuesRules and the intent form"


def test_post_requests_go_through_one_helper(page):
    """The only POST in the page is `apiPost` (URL = apiBase() + path, JSON body); every other fetch is a GET of a
    report file."""
    body = _body(page, "apiPost")
    assert "fetch(" in body and "POST" in body and "apiBase(" in body
    assert "JSON.stringify" in body and re.search(r"application/json", body)
    assert len(re.findall(r"\bmethod\s*:", page)) == 1, "exactly one fetch with an explicit method"
    assert len(re.findall(r"[\"']POST[\"']", page)) == 1
    assert not re.search(r"XMLHttpRequest|\bPUT\b|\bDELETE\b|\bPATCH\b", page)
    assert uses(page, "apiPost") >= 3, "definition + decision + confirm"


def test_rule_buttons_stay_disabled_with_the_current_note_when_no_api(page):
    """Task (1): with no API base the N3 buttons keep `disabled` and the « Affichage seulement » note; the note and the
    `disabled` attribute are conditional on apiBase() (a `${}` slot), not unconditional literal text."""
    body, texts = function_source(page, "renderValuesRules")
    literal = " ".join(texts)
    assert "apiBase(" in body
    assert "Affichage seulement" in body
    assert "disabled" in body
    # SPEC-AMBIGUITY: "stay disabled" does not say how the code branches; stricter reading: the literal markup has no
    # unconditional `disabled`, and the old static sentence is not printed when an API is configured.
    assert not re.search(r"\bdisabled\b", literal), "`disabled` must come from a conditional slot"
    assert "affichage seulement" not in literal.lower()
    for action in ("approve", "reject"):
        assert re.search(rf"data-action=\\?[\"']{action}", body), action
    assert "fetch(" not in body and "apiPost(" not in body, "renderValuesRules makes no request (test_site.py)"
    assert not typed_digits(texts)


def test_rule_buttons_are_bound_to_decide_rule(page):
    """The `[data-action]` buttons are bound to decideRule(...) so a click calls the API; only when API is set."""
    assert re.search(r"querySelectorAll\(\s*[\"'`]\[data-action", page)
    assert uses(page, "decideRule") >= 2
    bind = re.search(r"querySelectorAll\(\s*[\"'`]\[data-action[^;]*?decideRule\(", page, re.S)
    assert bind, "a [data-action] listener must call decideRule("
    assert "apiBase(" in page[max(0, bind.start() - 600):bind.end() + 200] or "apiBase(" in _body(page, "decideRule")


def test_decide_rule_posts_the_decision_route_and_rerenders(page):
    """SPEC §12 + api.py: POST /profiles/{profile_id}/parsed-rules/{index}/decision, body `{approved}`; approve sends
    true, reject false; then the page re-renders; a failure is shown (role=alert), never swallowed."""
    body = _body(page, "decideRule")
    assert "apiBase(" in body, "no request when no API is configured"
    assert "apiPost(" in body
    assert re.search(r"/profiles/\$\{[^}]*\}/parsed-rules/\$\{[^}]*\}/decision", body), \
        "path `/profiles/${profile_id}/parsed-rules/${index}/decision`"
    assert "profile_id" in body
    assert re.search(r"\bapproved\b", body)
    assert re.search(r"approve", body) and re.search(r"reject", body)
    assert "render(" in body, "re-render after the decision"
    assert "approved_by_user" in body or "/review" in body, "the report is refreshed (patched locally or re-reviewed)"
    assert "catch" in body
    assert "role=\"alert\"" in page or "role='alert'" in page
    for forbidden in ("limits", "llm"):
        assert forbidden not in body, f"SPEC inv. 2: the UI never sends `{forbidden}`"


def test_rejecting_never_sends_approval(page):
    """N3 safety: reject maps to `approved: false` (a boolean computed from the action), never a hard-coded true."""
    body = _body(page, "decideRule")
    assert not re.search(r"approved\s*:\s*true\b", body), "approval must depend on the clicked action"
    assert re.search(r"approved\s*[:=]|\{\s*approved\s*[,}]|=== ?[\"']approve[\"']", body)


# ---------------------------------------------------------------- 2. B2 intent confirmation form

def test_intent_form_has_the_editable_draft_fields(page):
    """SPEC §4 B2 / §3.6: ticker, target weight, funding (kind + ticker / asset class / amount), editable, each read
    from the draft intent; the intent id is shown and carried."""
    body, texts = function_source(page, "renderIntentForm")
    literal = "\n".join(texts)
    assert re.search(r"<form\b[^>]*\bid=[\"']intent-form[\"']", literal)
    for name in ("ticker", "target_weight_pct", "funding_kind", "funding_ticker", "funding_asset_class",
                 "funding_amount_cad"):
        assert re.search(rf"\bname=[\"']{name}[\"']", literal), name
    for kind in FUNDING_KINDS:
        assert kind in page, kind
    for field in ("intent.ticker", "intent.target_weight_pct", "intent.funding", "intent.intent_id"):
        assert field in body, field
    # editable: no readonly, no disabled input or select (only the submit button may be disabled)
    assert not re.search(r"\breadonly\b", body)
    assert not re.search(r"<(input|select|textarea)\b[^>]*\bdisabled\b", literal)
    assert re.search(r"<button\b[^>]*type=[\"']submit[\"']", literal)
    assert not typed_digits(texts), typed_digits(texts)[:5]


def test_intent_form_prefills_nothing_the_user_did_not_state(page):
    """D10 item 1 (wins over the SPEC §3.6 'default pro_rata_all' display): every `<input value>` is a slot read from
    the draft intent; no `selected` / `checked` literal; no `|| default` or `?? default` on ticker, weight or funding;
    the funding kind select starts on an empty choice and is `required`; no typed asset, size or funding source."""
    body, texts = function_source(page, "renderIntentForm")
    literal = "\n".join(texts)
    for tag in re.findall(r"<input\b[^>]*>", literal):
        m = re.search(r"\bvalue=[\"']([^\"']*)[\"']", tag)
        assert m is None or m.group(1) == "\u00a7", f"literal value in {tag}"
    assert not re.search(r"\b(selected|checked)\b", literal), "preselection must be a conditional slot"
    assert not re.search(r"(\|\||\?\?)\s*[\"'`]?(pro_rata_all|specific|pro_rata_class|cash_contribution)", body)
    assert not re.search(r"(\|\||\?\?)\s*[\"'`]?\d", body), "no numeric default"
    assert re.search(r"<option\b[^>]*value=[\"']{2}", body) or re.search(r"<option\b[^>]*value=[\"']{2}", literal), \
        "an empty first choice for the funding kind"
    assert re.search(r"<select\b[^>]*name=[\"']funding_kind[\"'][^>]*\brequired\b|"
                     r"<select\b[^>]*\brequired\b[^>]*name=[\"']funding_kind[\"']", literal)
    for typed in ("NVDA", "ZST", "in_0001", "ev_robert"):
        assert typed not in body, f"`{typed}` is typed in the form: it must come from the intent"


def test_confirm_posts_the_intent_route_with_the_edited_values(page):
    """SPEC §12: POST /intents/{intent_id}/confirm, body = the edited intent; no request when no API; the response
    report replaces the page's report (Flow B3-B9); failures are shown."""
    body = _body(page, "confirmIntent")
    assert "apiBase(" in body, "static demo: submit makes no request"
    assert "apiPost(" in body
    assert re.search(r"/intents/\$\{[^}]*\}/confirm", body), "path `/intents/${intent_id}/confirm`"
    for field in ("ticker", "target_weight_pct", "funding"):
        assert field in body, field
    assert re.search(r"Number\(|parseFloat\(|\+\s*\w+\.value", body), "the weight is sent as a number"
    assert "render(" in body and "catch" in body
    assert re.search(r"state\.rep\s*=|loadReport\(|setReport\(", body), "the confirm response is the new report"
    for forbidden in ("limits", "llm"):
        assert forbidden not in body, f"SPEC inv. 2: the UI never sends `{forbidden}`"
    assert uses(page, "confirmIntent") >= 2
    assert re.search(r"addEventListener\(\s*[\"']submit[\"']", page)
    assert "preventDefault" in page


def test_intent_form_is_wired_into_the_page(page):
    """The form and the Flow B loader are used (not dead code) and the static demo reads Robert's report file."""
    assert uses(page, "renderIntentForm") >= 2
    assert uses(page, "loadFlowB") >= 2
    body = _body(page, "loadFlowB")
    assert "reports/robert-nvda.json" in body
    assert "index.json" not in body, "P2.D10C oracle: flow B is a separate file, never listed in reports/index.json"
    assert "fetch(" in body
    # the form reads `rep.intent` of that report (in_0001 in the static demo): no typed intent anywhere in the page
    assert re.search(r"(?<!function )renderIntentForm\(\s*[\w.]*\bintent\b", page), "a call that passes rep.intent"
    assert "in_0001" not in page


def test_index_listing_stays_the_four_flow_c_reviews():
    """P2.D10C frozen oracle: build_site's report list is unchanged (4 flow C reviews); Flow B is not in it."""
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


# ---------------------------------------------------------------- 3. before / after cone from a Flow B report

def test_cone_overlays_current_and_proposed_when_the_report_has_both(page):
    """SPEC §12 / §20.7: `cone(rep)` draws `metrics_current` and overlays `metrics_proposed` (flow B), guarded so a
    flow C report (`metrics_proposed` null) still draws the current cone alone; both cones feed the y-range."""
    body = _body(page, "cone")
    assert "metrics_current" in body and "metrics_proposed" in body
    first = body.index("metrics_proposed")
    assert re.search(r"\?|&&|\bif\s*\(|!==?\s*null|!=\s*null", body[max(0, first - 200):first + 300]), \
        "the proposed cone is drawn only when present"
    assert len(re.findall(r"\.cone\b", body)) >= 2, "the current and the proposed `cone` series"
    assert len(re.findall(r"\bp5\b", body)) >= 3 and len(re.findall(r"\bp95\b", body)) >= 3, \
        "p5 / p95 of both cones are drawn and enter the axis range"
    assert re.search(r"<polyline|<path", body)


def test_cone_overlay_is_labelled_before_and_after(page):
    """Task (3): the two cones are labelled « Avant » and « Après » (chart text and legend), as facts about the
    user's own trade (D10 vocabulary)."""
    text = _body(page, "cone") + _body(page, "projection")
    assert "Avant" in text and "Après" in text
    assert not D10_WORDS.search(norm(re.sub(r"</?option\b", " ", text)))
    # the chart is described for assistive tech with both states (aria-label), not by colour alone
    assert re.search(r"aria-label", _body(page, "cone"))


def test_flow_b_report_gets_the_display_block_the_page_needs(page):
    """`preview_report.py` reports carry `profile_card` / `limits` / `weights_current` but no `site` block (the page
    reads `rep.site`): loadFlowB builds it, so every renderer works on the Flow B report."""
    body = _body(page, "loadFlowB")
    assert re.search(r"\.site\s*=|\bsite\s*:", body)
    assert "profile_card" in body and "limits" in body
    assert "render(" in body or "route(" in body or "state.rep" in body


# ---------------------------------------------------------------- the Flow B report the page reads (preview_report.py)

def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def flow_b(tmp_path_factory) -> dict:
    """`robert-nvda` from deploy/preview_report.py `build()` (real engine, fixed seed, no LLM), loaded as its own
    file (never through index.json)."""
    out = tmp_path_factory.mktemp("flow_b_ui")
    mod = _load("preview_report_flow_b_ui", PREVIEW)
    paths = mod.build(out)
    target = next(p for p in paths if p.stem == "robert-nvda")
    return json.loads(target.read_text(encoding="utf-8"))


def test_flow_b_report_carries_robert_in_0001_for_the_static_demo(flow_b):
    """SPEC §3.6, §4: the static demo's B2 form shows Robert's draft `in_0001` (NVDA, target 10 %, funding specific
    ZST.TO), read from `report.intent`."""
    assert flow_b["flow"] == "B"
    intent = flow_b["intent"]
    assert intent["intent_id"] == "in_0001"
    assert intent["ticker"] == "NVDA"
    assert intent["target_weight_pct"] == 10
    assert intent["funding"] == {"kind": "specific", "ticker": "ZST.TO"}
    assert intent["funding"]["kind"] in FUNDING_KINDS


def test_flow_b_report_has_before_and_after_cones_on_one_grid(flow_b):
    """SPEC §13 / §12: `metrics_current` and `metrics_proposed` both carry an engine cone with the same percentile
    keys and length (so the overlay shares one time axis), ordered p5 <= p25 <= p50 <= p75 <= p95 at every step, and
    the two cones differ (the trade changes the portfolio)."""
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
    """D7 point 5 / D2: Robert today is coherent (his pessimistic value at horizon is not below his floor); the NVDA
    trade is incoherent with H9 breached, so the 'after' cone's P5 ends below the floor and the 'before' one above."""
    floor = flow_b["limits"]["floor_at_horizon"]
    assert flow_b["metrics_current"]["p5_T"] >= floor
    assert flow_b["metrics_proposed"]["p5_T"] < floor
    assert flow_b["verdict"] == "incoherent"
    assert flow_b["metrics_current"]["p5_T"] > flow_b["metrics_proposed"]["p5_T"]


def test_flow_b_report_has_no_site_block_so_the_loader_builds_it(flow_b):
    """Pins the contract the loader relies on: the preview report offers `profile_card`, `limits`, `weights_current`,
    `assumptions`, `data_window` (the fields `rep.site` is built from) and has no `site` of its own."""
    assert "site" not in flow_b
    for key in ("profile_card", "limits", "weights_current", "assumptions", "data_window", "profile_id",
                "portfolio_id", "sections", "explanation", "disclaimer", "headroom"):
        assert key in flow_b, key
    assert isinstance(flow_b["profile_card"].get("value_cad"), (int, float))
    assert flow_b["profile_card"].get("name") == "Robert"


# ---------------------------------------------------------------- 4. wording: blocklist and D10 vocabulary

def test_page_passes_the_blocklist_apart_from_the_fixed_disclaimer(page):
    """SPEC §9.3 item 3 / D10 item 4: nothing in the page (markup, text, attributes, scripts) matches the blocklist,
    the disclaimer and the Recherche refusal sentence excepted."""
    assert blocklist_hits(_exempt(page)) == []


def test_new_ui_code_passes_the_blocklist_and_d10_vocabulary(page):
    """The new functions' own strings (labels, notes, errors): no advisory phrase, and no word that proposes, ranks or
    labels a choice (D10 items 1-2). HTML <option> tags are widgets, not wording."""
    for name in ("apiBase", "apiPost", "decideRule", "renderValuesRules", "renderIntentForm", "confirmIntent",
                 "loadFlowB", "cone"):
        body = _body(page, name)
        assert blocklist_hits(body) == [], name
        words = norm(re.sub(r"</?(option|optgroup)\b", " ", body))
        found = D10_WORDS.search(words)
        assert found is None, (name, found and found.group(0))


def test_flow_b_report_text_passes_the_blocklist(flow_b):
    """Every string of the Flow B report the page shows passes the blocklist, the fixed disclaimer excepted
    (FR and EN), and carries no option / alternative wording."""
    texts = [s for s in strings(flow_b) if s not in (DISCLAIMER_FR, DISCLAIMER_EN)]
    bad = [(s[:80], blocklist_hits(s)) for s in texts if blocklist_hits(s)]
    assert bad == []
    # SPEC-AMBIGUITY: `limits.excluded_asset_types` holds the profile's asset-type code `options` (data, not wording);
    # the option/alternative wording rule is checked on the text blocks the page prints, as test_d10_dashboard does.
    shown = strings(flow_b["explanation"]) + strings(flow_b.get("preview", {})) + strings(flow_b.get("sections", []))
    assert not [s[:80] for s in shown if re.search(r"\b(options?|alternatives?)\b", s, re.I)]
