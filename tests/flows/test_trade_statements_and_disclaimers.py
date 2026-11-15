"""P6.TRADEMSG: D13 (Flow B `trade_statements`) + D16 (two fixed disclaimers by profile origin).

Sources: docs/spec-decisions.md D13 (a breach is one plain statement with facts and the user's own words), D14 (the
set of crossed limits = the headroom's: violations added or worsened), D16 (two fixed disclaimers), D10 (describe,
never prescribe), D11 (provisional note rules), SPEC §4 B9, §8, §9.4, §13, §18.

# STALE-ORACLE: tests/flows/test_any_user.py:503-504 (`rep["disclaimer"] == config.DISCLAIMER[locale]` for a profile
# made by POST /profiles) and tests/flows/test_new_user_no_portfolio.py:484 pin the seed text for user-made profiles.
# D16 says a profile with meta.origin == "user" carries the text WITHOUT the fictional-profiles clause. Mine win; the
# orchestrator updates those two asserts (see .claude/loop/BLOCKED.md).

Contract pinned here:
  * config holds, per locale, the seed text (unchanged, byte for byte) and the user text (D16 wording); the seed text
    stays what `config.DISCLAIMER[locale]` returns (frozen tests/flows/test_cli_disclaimer.py:74-75 pin that);
  * a report carries the user text when the profile's meta.origin == "user" (POST /profiles sets it), the seed text
    otherwise (persona fixtures, absent or any other origin); `explanation.disclaimer` equals `disclaimer`;
  * a Flow B report has `trade_statements`: one {check_id, text, refs} per hard limit the trade adds or worsens, FR/EN
    templates of the D13 form, the quote part only when the limit traces to a parsed note rule or an event, numbers
    from the facts (numcheck), no question / button / suggestion (D10), status and verdict unchanged.

Offline: COHERENCE_ANALYZER_LLM=mock, tmp working directories, fixed seeds, no clock value asserted.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

from coherence_analyzer import api, cli, config, explain, numfacts, resizer  # noqa: E402
from coherence_analyzer.llm import bedrock  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "fixtures" / "robert"
INTENT_ID = "in_0001"

# SPEC §9.4 (seed, current text) and D16 (user text, without the fictional-profiles clause), verbatim.
SEED = {
    "fr-CA": ("Simulation éducative avec des profils fictifs. Ceci n'est pas un conseil financier personnalisé. "
              "Aucune transaction n'est exécutée."),
    "en-CA": ("Educational simulation with fictional profiles. This is not personalised financial advice. "
              "No transaction is executed."),
}
USER = {
    "fr-CA": ("Simulation éducative. Ceci n'est pas un conseil financier personnalisé. "
              "Aucune transaction n'est exécutée."),
    "en-CA": "Educational simulation. This is not personalised financial advice. No transaction is executed.",
}
EN_PREFIX = "Making this trade takes you outside your profile: "
QUOTE_FROM = "From what you told us: "
NOTE_QUOTE = "No hype stocks unless I can explain the business."   # an exact substring of Robert's values.notes
SIX_VIOLATIONS = {"H3", "H4", "H7", "H8", "H9", "H10"}            # tests/flows/test_flow_b.py: proposed side for Robert
STATEMENT_KEYS = {"check_id", "text", "refs"}


# ---------------------------------------------------------------- helpers

def _config_texts() -> set[str]:
    """Every string reachable from a `config.DISCLAIMER*` attribute, whatever structure the implementation picks."""
    found: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, str):
            found.add(node)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, (list, tuple, set, frozenset)):
            for v in node:
                walk(v)

    for name in dir(config):
        if name.upper().startswith("DISCLAIMER"):
            walk(getattr(config, name))
    return found


def _prepare(tmp: Path, *, locale: str | None = None, origin: str | None = None, note_rule: bool = False,
             weights_pct: dict | None = None) -> Path:
    """A private copy of the Robert fixture (never touch fixtures/), optionally edited."""
    dst = tmp / "robert"
    shutil.copytree(FIX, dst)
    prof = json.loads((dst / "profile.json").read_text(encoding="utf-8"))
    if locale:
        prof["meta"]["locale"] = locale
    if origin is not None:
        prof["meta"]["origin"] = origin
    if note_rule:
        # an approved numeric theme rule quoting the user's own notes verbatim (SPEC §6.2 N3, §7.2; D11 keeps it a
        # limit whether approved or provisional). NVDA carries the `tech_heavy` theme (data/meta/asset_meta.csv).
        assert NOTE_QUOTE in prof["values"]["notes"]
        prof["values"]["parsed_notes"]["value"].append(
            {"rule_kind": "max_theme_weight", "params": {"theme": "tech_heavy", "max_pct": 5},
             "applies_to": "all", "quote": NOTE_QUOTE, "approved_by_user": True})
    (dst / "profile.json").write_text(json.dumps(prof, ensure_ascii=False, indent=2), encoding="utf-8")
    if weights_pct is not None:
        pf = json.loads((dst / "portfolio.json").read_text(encoding="utf-8"))
        pf["weights_pct"] = weights_pct
        (dst / "portfolio.json").write_text(json.dumps(pf, indent=2), encoding="utf-8")
    return dst


def _run_check(tmp: Path, fixture: Path) -> dict:
    """`cli check <fixture> --intent in_0001 --auto-confirm` in `tmp`; returns the one saved report."""
    mp = pytest.MonkeyPatch()
    mp.chdir(tmp)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["check", str(fixture), "--intent", INTENT_ID, "--auto-confirm"])
    finally:
        mp.undo()
    files = sorted((tmp / "reports").glob("*.json"))
    assert len(files) == 1, files
    return json.loads(files[0].read_text(encoding="utf-8"))


def _facts_payload(rep: dict) -> dict:
    """The facts a statement may draw numbers from: the report's checks, metrics, sections' facts, intent, headroom."""
    return {k: rep.get(k) for k in ("checks_current", "checks_proposed", "metrics_current", "metrics_proposed",
                                    "intent", "headroom", "diff")} | {"sections": [s["facts"] for s in rep["sections"]]}


def _pcts(text: str) -> list[float]:
    return [float(t.value) for t in numfacts.tokenize(text) if t.kind == "pct"]


def _has_pct(text: str, expected: float) -> bool:
    return any(abs(p - expected) <= 0.05 + 1e-9 for p in _pcts(text))


def _violation(rep: dict, cid: str, side: str = "checks_proposed") -> dict:
    return next(c for c in rep[side] if c["id"] == cid and c["status"] == "violation")


def _by_id(rep: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for s in rep["trade_statements"]:
        assert s["check_id"] not in out, f"two statements for {s['check_id']}"   # SPEC-AMBIGUITY: one per limit id
        out[s["check_id"]] = s
    return out


def _assert_statement_shape(rep: dict) -> None:
    sts = rep["trade_statements"]
    assert isinstance(sts, list) and sts
    payload = _facts_payload(rep)
    for s in sts:
        assert set(s) == STATEMENT_KEYS, s
        assert isinstance(s["check_id"], str) and isinstance(s["text"], str) and s["text"].strip()
        assert isinstance(s["refs"], list)
        assert "?" not in s["text"] and "¿" not in s["text"]                          # D13: asks nothing
        assert explain.advisory_hits(s["text"]) == [], s["text"]                       # D10: no suggestion
        assert explain.numcheck(s["text"], payload) == [], s["text"]                   # invariant 6: numbers in facts
        assert "\n" not in s["text"]


# ---------------------------------------------------------------- the runs (module scope, one CLI run each)

@pytest.fixture(scope="module")
def run_fr(tmp_path_factory) -> dict:
    """Robert as shipped: fr-CA, no meta.origin (a seed), NVDA 10 % from ZST.TO."""
    tmp = tmp_path_factory.mktemp("fr")
    return _run_check(tmp, _prepare(tmp))


@pytest.fixture(scope="module")
def run_user_en(tmp_path_factory) -> dict:
    """en-CA, meta.origin == "user", plus an approved numeric note rule (tech_heavy <= 5 %) quoting the notes."""
    tmp = tmp_path_factory.mktemp("user_en")
    return _run_check(tmp, _prepare(tmp, locale="en-CA", origin="user", note_rule=True))


@pytest.fixture(scope="module")
def run_worse_en(tmp_path_factory) -> dict:
    """en-CA, meta.origin == "demo" (anything else = seed), NVDA already held at 9 % (H3 limit 8 %): the trade to
    10 % worsens an existing breach (D14). A 3 % BTC-USD holding is a breach (H1, H2) the trade leaves as is.
    ZST.TO 13 -> 4 keeps the weights at 100 and the funding available."""
    tmp = tmp_path_factory.mktemp("worse_en")
    w = {"XIU.TO": 15, "RY.TO": 7, "ENB.TO": 6, "FTS.TO": 7, "JNJ": 5, "PG": 5, "SPY": 12, "XBB.TO": 22,
         "ZST.TO": 4, "GLD": 5, "NVDA": 9, "BTC-USD": 3}
    assert sum(w.values()) == 100
    return _run_check(tmp, _prepare(tmp, locale="en-CA", origin="demo", weights_pct=w))


# ---------------------------------------------------------------- D16: the two fixed texts

def test_config_holds_both_fixed_texts_for_each_locale():
    """D16 / SPEC §9.4: config.py carries the seed text and the user text (exact wording), per locale, never LLM-made."""
    texts = _config_texts()
    for loc in ("fr-CA", "en-CA"):
        assert SEED[loc] in texts, loc
        assert USER[loc] in texts, loc
        assert "fictif" not in USER[loc] and "fictional" not in USER[loc]
        assert SEED[loc] != USER[loc]


def test_seed_text_is_unchanged_in_config():
    """D16: seed personas keep the current text byte for byte. SPEC-AMBIGUITY: `DISCLAIMER[locale]` stays the seed
    string (frozen tests/flows/test_cli_disclaimer.py:74-75 pin it), the user text lives next to it."""
    assert config.DISCLAIMER["fr-CA"] == SEED["fr-CA"]
    assert config.DISCLAIMER["en-CA"] == SEED["en-CA"]


def test_persona_flow_b_report_keeps_the_seed_text_byte_for_byte(run_fr):
    """D16: Robert (a seed, no meta.origin) -> the §9.4 text with the fictional-profiles clause, in both places."""
    assert run_fr["disclaimer"] == SEED["fr-CA"]
    assert run_fr["explanation"]["disclaimer"] == SEED["fr-CA"]


def test_user_origin_flow_b_report_carries_the_user_text(run_user_en):
    """D16: meta.origin == "user" -> the text without the fictional-profiles clause (EN), report and explanation."""
    assert run_user_en["disclaimer"] == USER["en-CA"]
    assert run_user_en["explanation"]["disclaimer"] == USER["en-CA"]
    assert "fictional" not in run_user_en["disclaimer"]


def test_any_other_origin_is_a_seed(run_worse_en):
    """D16: 'user' is the only origin that switches the text; anything else (here "demo") = seed, EN."""
    assert run_worse_en["disclaimer"] == SEED["en-CA"]
    assert run_worse_en["explanation"]["disclaimer"] == SEED["en-CA"]


@pytest.fixture()
def store_dir(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "store"
    d.mkdir()
    monkeypatch.setenv("COHERENCE_ANALYZER_STORE_DIR", str(d))
    monkeypatch.setenv("COHERENCE_ANALYZER_LLM", "mock")
    monkeypatch.setattr(bedrock, "COHERENCE_ANALYZER_LLM", "mock")
    return d


@pytest.mark.parametrize("locale", ["fr-CA", "en-CA"])
def test_post_profiles_sets_origin_user_and_its_review_uses_the_user_text(store_dir, locale):
    """D16: POST /profiles sets meta.origin = "user"; the Flow C review of that profile carries the user text."""
    client = TestClient(api.app)
    r = client.post("/profiles", json={"locale": locale})
    assert r.status_code in (200, 201), r.text
    body = r.json()
    pid = body["meta"]["profile_id"] if "meta" in body else body["profile_id"]
    assert client.get(f"/profiles/{pid}").json()["meta"]["origin"] == "user"
    pf = {"as_of": "2026-11-12", "value_cad": 120000, "weights_pct": {"XIU.TO": 30, "XBB.TO": 30, "SPY": 25, "GLD": 15}}
    assert client.put(f"/profiles/{pid}/portfolio", json=pf).status_code == 200
    r = client.post(f"/profiles/{pid}/review")
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["disclaimer"] == USER[locale]
    assert rep["explanation"]["disclaimer"] == USER[locale]
    assert "trade_statements" not in rep or not rep["trade_statements"]            # Flow C: no trade, no statement


def test_persona_review_through_the_api_keeps_the_seed_text(store_dir):
    """D16: a persona (seed) Flow C review through the API keeps the current text, FR (Robert)."""
    client = TestClient(api.app)
    r = client.post("/profiles/persona-robert/review")
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["disclaimer"] == SEED["fr-CA"]
    assert rep["explanation"]["disclaimer"] == SEED["fr-CA"]
    assert "trade_statements" not in rep or not rep["trade_statements"]


# ---------------------------------------------------------------- D13: the statements

def test_flow_b_report_has_trade_statements_for_each_crossed_limit(run_fr):
    """D13 + D14 + SPEC §8: one statement per hard limit the trade adds or worsens = the set the headroom uses.
    Robert in_0001: nothing breached now, H3 H4 H7 H8 H9 H10 breached after (tests/flows/test_flow_b.py)."""
    rep = run_fr
    crossed = set(resizer.violations_added(rep["checks_current"], rep["checks_proposed"]))
    assert crossed == SIX_VIOLATIONS
    assert [c for c in rep["checks_current"] if c["status"] == "violation"] == []
    assert set(_by_id(rep)) == crossed
    assert len(rep["trade_statements"]) == len(crossed)
    assert rep["headroom"]["binding_check"] in crossed                              # the headroom's own limit is among them


def test_statement_shape_and_schema(run_fr):
    """D13: each entry is exactly {check_id, text, refs}; the saved report still validates against the schema."""
    _assert_statement_shape(run_fr)
    schema = json.loads((config.SCHEMAS_DIR / "report.json").read_text(encoding="utf-8"))
    errors = [e.message for e in Draft202012Validator(schema).iter_errors(run_fr)]
    assert errors == [], errors[:3]


def test_status_and_verdict_are_unchanged_by_the_statements(run_fr):
    """D13 point 1: a crossed hard limit is still a `violation`, the verdict is `incoherent`; the five sections keep
    the statuses of tests/flows/test_flow_b.py."""
    assert run_fr["verdict"] == "incoherent"
    assert {c["id"] for c in run_fr["checks_proposed"] if c["status"] == "violation"} == SIX_VIOLATIONS
    assert {s["factor"]: s["status"] for s in run_fr["sections"]} == {
        "objectif": "pass", "horizon": "violation", "risque": "violation", "contraintes": "violation",
        "preferences": "pass"}


def test_fr_statements_carry_their_facts_and_no_quote_for_rule_limits(run_fr):
    """D13 point 2, FR: the H3 and H4 statements (limits derived from rule: fields) state the value after the trade
    and the limit; no quote part, because the limit does not trace to the user's own words."""
    by = _by_id(run_fr)
    h3, h4 = _violation(run_fr, "H3"), _violation(run_fr, "H4")
    assert _has_pct(by["H3"]["text"], h3["value"] * 100) and _has_pct(by["H3"]["text"], h3["limit"] * 100)   # 10 %, 8 %
    assert _has_pct(by["H4"]["text"], h4["value"] * 100) and _has_pct(by["H4"]["text"], h4["limit"] * 100)   # 25 %, 30 %
    for s in run_fr["trade_statements"]:
        assert not s["text"].startswith(EN_PREFIX), "persona Robert is fr-CA"
        assert "From what you told us" not in s["text"]


def test_en_statement_follows_the_d13_template_with_a_quote_when_the_limit_is_the_users_words(run_user_en):
    """D13 point 2, EN: 'Making this trade takes you outside your profile: <limit> (<limit value>). Facts: <value
    after>[, <value before>]. From what you told us: '<quote>' (<source>).' The quote is verbatim and only on the
    limit that traces to a parsed note rule (H13 tech_heavy <= 5 %); the rule-derived limits have no quote part."""
    rep = run_user_en
    _assert_statement_shape(rep)
    by = _by_id(rep)
    assert "H13" in by, sorted(by)
    h13 = _violation(rep, "H13")
    assert h13["limit"] == pytest.approx(0.05, abs=1e-9) and h13["value"] == pytest.approx(0.10, abs=1e-9)
    assert any("values.parsed_notes" in p for p in h13["derived_from"])
    t = by["H13"]["text"]
    assert t.startswith(EN_PREFIX), t
    assert " Facts: " in t and t.endswith(".")
    assert _has_pct(t.split(" Facts: ", 1)[1].split(QUOTE_FROM)[0], 10)               # the value after the trade
    assert _has_pct(t, 5)                                                              # the limit value
    quote_part = f"{QUOTE_FROM}'{NOTE_QUOTE}' ("
    assert quote_part in t, t                                                          # verbatim, then (<source>)
    assert t.index(" Facts: ") < t.index(quote_part)
    assert t[t.index(quote_part) + len(quote_part):].rstrip(".").endswith(")")
    assert NOTE_QUOTE in json.loads(  # the quote is an exact substring of the user's notes (invariant 5)
        (FIX / "profile.json").read_text(encoding="utf-8"))["values"]["notes"]
    for cid, s in by.items():
        if cid != "H13":
            assert QUOTE_FROM not in s["text"], cid                                    # rule-derived limits: no quote
            assert s["text"].startswith(EN_PREFIX), cid
    h3 = _violation(rep, "H3")
    assert _has_pct(by["H3"]["text"], h3["value"] * 100) and _has_pct(by["H3"]["text"], h3["limit"] * 100)


def test_user_origin_run_keeps_status_and_adds_a_statement_for_the_note_limit(run_user_en):
    """D13 point 1 + D11: the note-rule limit is a hard-check violation here (approved, full strength); verdict stays
    `incoherent`; the statement set still equals the headroom's (violations added or worsened)."""
    rep = run_user_en
    assert rep["verdict"] == "incoherent"
    crossed = set(resizer.violations_added(rep["checks_current"], rep["checks_proposed"]))
    assert {"H13"} <= crossed
    assert set(_by_id(rep)) == crossed


def test_a_worsened_breach_gets_a_statement_with_the_value_before(run_worse_en):
    """D14 + D13: NVDA already 9 % > 8 % (H3), the trade makes it 10 %: the breach is worsened, so H3 has a statement,
    and its facts give the value after and the value before; EN template."""
    rep = run_worse_en
    _assert_statement_shape(rep)
    h3_cur, h3_new = _violation(rep, "H3", "checks_current"), _violation(rep, "H3")
    assert h3_cur["value"] == pytest.approx(0.09, abs=1e-9) and h3_new["value"] == pytest.approx(0.10, abs=1e-9)
    crossed = set(resizer.violations_added(rep["checks_current"], rep["checks_proposed"]))
    assert "H3" in crossed
    by = _by_id(rep)
    assert set(by) == crossed
    t = by["H3"]["text"]
    assert t.startswith(EN_PREFIX), t
    assert _has_pct(t, 10) and _has_pct(t, 8)                                          # after, limit
    # SPEC-AMBIGUITY: D13 shows the value before in brackets; stricter reading: present when the breach existed before.
    assert _has_pct(t, 9), t
    assert QUOTE_FROM not in t                                                         # a rule: limit, no quote
    assert rep["verdict"] == "incoherent"


def test_an_unchanged_breach_gets_no_statement(run_worse_en):
    """D14: a limit already breached that the trade leaves as is or improves does not count as crossed, so it has no
    statement (the same filter as the headroom): every statement id is in `violations_added`."""
    rep = run_worse_en
    crossed = set(resizer.violations_added(rep["checks_current"], rep["checks_proposed"]))
    still = {c["id"] for c in rep["checks_proposed"] if c["status"] == "violation"}
    untouched = still - crossed
    assert untouched == {"H1", "H2"}                                                   # BTC-USD 3 %, before and after
    assert set(_by_id(rep)).isdisjoint(untouched)
