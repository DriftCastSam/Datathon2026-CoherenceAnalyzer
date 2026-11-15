"""P6.PROVISIONAL (phase A oracle): D11, numeric note rules become provisional limits at once, capped at warning.

Sources: docs/spec-decisions.md D11 (1. a gated N3 rule with a number is promoted into `limits` at once as a provisional
limit: `limits.max_theme_weight[theme]` carries `provisional: true`, `limits_trace` names
`values.parsed_notes[provisional]`; 2. a check against it is capped at `warning`, never `violation`; 3. approval makes it
full strength, rejection removes it; 4. rules without a number are unchanged, S4 keeps reminding about unapproved
rules), CLAUDE.md invariants 1-3 (as amended), SPEC §6.2 N3, §7.2, §7.3 H13, §7.4 S4, §11.

# STALE-ORACLE: these frozen tests pin "unapproved parsed rules never reach limits" (pre-D11) with an unapproved numeric
# theme rule; D11.1 and D11.5 replace that and the owner approved updating them (listed in .claude/loop/BLOCKED.md):
#   tests/profile/test_limits_no_llm_inputs.py::{test_llm_fields_do_not_change_limits,
#     test_llm_fields_do_not_change_limits_without_rederive, test_unapproved_parsed_rules_do_not_appear_in_limits},
#   tests/profile/test_intake.py::{test_unapproved_parsed_rules_are_ignored_by_build_limits,
#     test_only_the_approved_rule_of_a_mixed_batch_is_promoted},
#   tests/profile/test_rules.py::test_unapproved_parsed_rules_are_not_promoted.

Fixture: Amira (fixtures/amira). Her tech_heavy rule (max_pct 50) is already approved, so her own report is unchanged;
every provisional case below works on a COPY with the rule unapproved and, where a breach is needed, max_pct 30
(her tech_heavy weight is 0.38 on the committed portfolio, so 30 % is breached and 50 % is not).

Offline and deterministic: COHERENCE_ANALYZER_LLM=mock, as_of = portfolio.as_of (D1/D12 pin), tmp store, fixtures/ untouched.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

from coherence_analyzer import api, data, pipeline  # noqa: E402
from coherence_analyzer.coherence import hard, report as report_mod, soft  # noqa: E402
from coherence_analyzer.rules import derive, limits as limits_mod  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
AMIRA_DIR = FIXTURES / "amira"
AMIRA = "persona-amira"
AS_OF = "2026-11-12"                      # portfolio.as_of
VALUE = 60_000
THEME = "tech_heavy"
TECH_RULE = 1                             # Amira: max_theme_weight tech_heavy, 50, approved
QUOTE = "I don't want everything in tech."        # exact substring of Amira's notes
PROVISIONAL_PATH = "values.parsed_notes[provisional]"
APPROVED_PATH = "values.parsed_notes[approved_by_user]"
TS = "2026-11-15T09:30:00-05:00"
TOL = 1e-9


# ---------------------------------------------------------------- helpers

@pytest.fixture(autouse=True)
def _mock_llm(monkeypatch):
    monkeypatch.setenv("COHERENCE_ANALYZER_LLM", "mock")


def _amira() -> dict:
    return json.loads((AMIRA_DIR / "profile.json").read_text(encoding="utf-8"))


def _rules(p: dict) -> list[dict]:
    return p["values"]["parsed_notes"]["value"]


def _variant(*, approved: bool, max_pct="keep", rule_kind: str | None = None) -> dict:
    """A copy of Amira's profile whose tech_heavy rule is (un)approved, with another number (or none)."""
    p = _amira()
    rule = _rules(p)[TECH_RULE]
    rule["approved_by_user"] = approved
    if max_pct is None:
        rule["params"].pop("max_pct")
    elif max_pct != "keep":
        rule["params"]["max_pct"] = max_pct
    if rule_kind is not None:
        rule["rule_kind"] = rule_kind
    return p


def _ruled(p: dict) -> dict:
    """The profile after Flow A's rules (derive -> limits + trace), exactly as the pipeline builds it."""
    p = derive.derive_all(copy.deepcopy(p), portfolio_value_cad=VALUE, as_of=AS_OF)
    lim, trace = limits_mod.build_limits(p, VALUE)
    p["limits"], p["limits_trace"] = lim, trace
    return p


def _frac(entry) -> float:
    """The fraction of a max_theme_weight entry: a bare number (full strength) or the one number of a provisional dict.
    # SPEC-AMBIGUITY: D11.1 says the entry "carries provisional: true" but not the key of its number; any single numeric
    # field of the dict is accepted, so the implementation is free to call it value / max / weight."""
    if isinstance(entry, dict):
        nums = [v for v in entry.values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
        assert len(nums) == 1, entry
        return float(nums[0])
    assert isinstance(entry, (int, float)) and not isinstance(entry, bool), entry
    return float(entry)


def _theme_limits(p: dict) -> dict:
    return p["limits"].get("max_theme_weight") or {}


def _is_provisional(entry) -> bool:
    return isinstance(entry, dict) and entry.get("provisional") is True


def _h13(p: dict) -> list[dict]:
    assets = data.load_assets()
    pf = data.load_portfolio(AMIRA_DIR / "portfolio.json", assets)
    meta = {n: data.load_meta(n) for n in ("asset_meta", "activity_tags", "etf_sector_weights")}
    return [c for c in hard.run_cheap(pf["weights"], p, assets, meta) if c["id"] == "H13"]


def _tech_checks(checks: list[dict]) -> list[dict]:
    return [c for c in checks if c["id"] == "H13" and THEME in (c.get("subject") or "")]


# ---------------------------------------------------------------- limits: promotion (D11.1)

def test_unapproved_numeric_theme_rule_is_promoted_as_a_provisional_limit():
    # D11.1: a gated, unapproved max_theme_weight rule with max_pct 30 is in limits at once, flagged provisional,
    # percent -> fraction once (0.30).
    p = _ruled(_variant(approved=False, max_pct=30))
    entry = _theme_limits(p)[THEME]
    assert _is_provisional(entry)
    assert _frac(entry) == pytest.approx(0.30, abs=TOL)
    assert set(_theme_limits(p)) == {THEME}


def test_provisional_limit_trace_names_provisional_not_approved():
    # D11.1: limits_trace.max_theme_weight names values.parsed_notes[provisional], distinct from [approved_by_user].
    p = _ruled(_variant(approved=False, max_pct=30))
    trace = p["limits_trace"]["max_theme_weight"]
    assert PROVISIONAL_PATH in trace
    assert APPROVED_PATH not in trace


def test_approved_numeric_theme_rule_is_full_strength_with_the_approved_trace():
    # D11.3: approval = full-strength limit (a plain fraction, `provisional` gone), trace [approved_by_user] only.
    p = _ruled(_variant(approved=True, max_pct=30))
    entry = _theme_limits(p)[THEME]
    assert not _is_provisional(entry)
    assert not (isinstance(entry, dict) and "provisional" in entry)
    assert _frac(entry) == pytest.approx(0.30, abs=TOL)
    trace = p["limits_trace"]["max_theme_weight"]
    assert APPROVED_PATH in trace and PROVISIONAL_PATH not in trace


def test_amiras_committed_approved_rule_is_unchanged():
    # D11 "Amira's fixture": her tech_heavy rule is approved, so limits and trace keep the pre-D11 shape.
    p = _ruled(_amira())
    assert p["limits"]["max_theme_weight"] == {THEME: pytest.approx(0.5, abs=TOL)}
    assert not _is_provisional(p["limits"]["max_theme_weight"][THEME])
    assert p["limits_trace"]["max_theme_weight"] == [APPROVED_PATH]
    committed = _amira()
    assert committed["limits"]["max_theme_weight"] == {THEME: pytest.approx(0.5, abs=TOL)}
    assert committed["limits_trace"]["max_theme_weight"] == [APPROVED_PATH]


@pytest.mark.parametrize("approved", [False, True])
@pytest.mark.parametrize("max_pct", [None, "30", True], ids=["no_number", "string", "bool"])
def test_theme_rule_without_a_real_number_promotes_nothing(approved, max_pct):
    # D11.4: a theme rule with no number (or a non-number) is unchanged: nothing in limits, no provisional trace.
    p = _ruled(_variant(approved=approved, max_pct=max_pct))
    assert not _theme_limits(p)
    assert PROVISIONAL_PATH not in p["limits_trace"].get("max_theme_weight", [])


def test_unapproved_exclude_activity_rule_is_not_promoted():
    # D11.4: exclude_activity is not promoted before approval (the intake / an approval click stays the way in).
    base = _amira()
    p = copy.deepcopy(base)
    _rules(p).append({"rule_kind": "exclude_activity", "params": {"tag": "fossil_fuel_producer"},
                      "applies_to": "all", "quote": QUOTE, "approved_by_user": False})
    lim = _ruled(p)["limits"]
    assert "fossil_fuel_producer" not in lim["excluded_activities"]
    assert lim["excluded_activities"] == _ruled(base)["limits"]["excluded_activities"]
    assert PROVISIONAL_PATH not in _ruled(p)["limits_trace"]["excluded_activities"]


def test_provisional_rule_changes_no_other_limit_and_no_other_trace():
    # D11.1 + inv. 2: only max_theme_weight (and its trace) differ between the unapproved and the approved reading.
    prov, full = _ruled(_variant(approved=False, max_pct=30)), _ruled(_variant(approved=True, max_pct=30))
    strip = lambda lim: {k: v for k, v in lim.items() if k != "max_theme_weight"}
    assert strip(prov["limits"]) == strip(full["limits"])
    other = lambda t: {k: v for k, v in t.items() if k != "max_theme_weight"}
    assert other(prov["limits_trace"]) == other(full["limits_trace"])
    assert prov["limits"]["by"].startswith("rule:")


def test_no_other_llm_field_feeds_limits_with_a_provisional_rule():
    # Inv. 1 (as amended): the only llm: path in any trace is the flagged provisional promotion; the old test's
    # NOT_LIMIT_INPUTS (risk.behavioural, knowledge.*) stay out.
    p = _ruled(_variant(approved=False, max_pct=30))
    forbidden = {"risk.behavioural", "knowledge.question_quality", "knowledge.score", "values.parsed_notes"}
    for name, paths in p["limits_trace"].items():
        assert not (set(paths) & forbidden), name
        if name != "max_theme_weight":
            assert PROVISIONAL_PATH not in paths, name


# ---------------------------------------------------------------- H13 against a provisional limit (D11.2)

def test_h13_breach_of_a_provisional_limit_is_a_warning_never_a_violation():
    # D11.2 + inv. 3: tech_heavy weighs 0.38 > provisional 0.30 -> `warning`, never `violation`.
    checks = _h13(_ruled(_variant(approved=False, max_pct=30)))
    assert all(x["status"] != "violation" for x in checks)
    (c,) = _tech_checks(checks)
    assert c["status"] == "warning"
    assert c["value"] == pytest.approx(0.38, abs=TOL) and c["limit"] == pytest.approx(0.30, abs=TOL)
    assert c["kind"] == "hard" and c["source"] == "rule:max_theme_weight"


def test_h13_within_a_provisional_limit_passes():
    # D11.2: the cap only lowers a breach to warning; below the limit it is a pass (0.38 <= 0.50).
    (c,) = _tech_checks(_h13(_ruled(_variant(approved=False, max_pct=50))))
    assert c["status"] == "pass"
    assert c["value"] == pytest.approx(0.38, abs=TOL) and c["limit"] == pytest.approx(0.50, abs=TOL)


def test_h13_provisional_check_says_it_comes_from_the_users_notes():
    # D11.2 / task notes: the check carries the provisional trace path and the user's quote (subject or derived_from).
    # SPEC-AMBIGUITY: "quote in subject / derived_from" -> accept the quote in either field; the provisional path is required.
    (c,) = _tech_checks(_h13(_ruled(_variant(approved=False, max_pct=30))))
    assert "limits.max_theme_weight" in c["derived_from"]
    assert PROVISIONAL_PATH in c["derived_from"] and APPROVED_PATH not in c["derived_from"]
    assert any(QUOTE in s for s in [c["subject"] or "", *c["derived_from"]])


def test_h13_same_breach_is_a_violation_once_the_rule_is_approved():
    # D11.3: approval = full strength; the same 0.38 > 0.30 is now a `violation`, trace [approved_by_user].
    (c,) = _tech_checks(_h13(_ruled(_variant(approved=True, max_pct=30))))
    assert c["status"] == "violation"
    assert c["value"] == pytest.approx(0.38, abs=TOL) and c["limit"] == pytest.approx(0.30, abs=TOL)
    assert APPROVED_PATH in c["derived_from"] and PROVISIONAL_PATH not in c["derived_from"]
    assert c["subject"] == THEME      # the approved check keeps the bare theme subject (existing H13 contract)


def test_h13_on_amiras_committed_profile_is_unchanged():
    # D11 "Amira's report is unchanged": pass, 0.38 vs 0.5, derived_from exactly as before.
    (c,) = _h13(_ruled(_amira()))
    assert c["status"] == "pass" and c["subject"] == THEME
    assert c["value"] == pytest.approx(0.38, abs=TOL) and c["limit"] == pytest.approx(0.50, abs=TOL)
    assert c["derived_from"] == ["limits.max_theme_weight", APPROVED_PATH]


# ---------------------------------------------------------------- S4 keeps reminding (D11.4)

def test_s4_still_reminds_about_the_unapproved_rule_that_is_now_provisional():
    # D11.4: S4 keeps reminding about unapproved rules (info, quote in subject); once approved the reminder is gone.
    reminders = [c for c in soft.s4(_variant(approved=False, max_pct=30)) if c["id"] == "S4"]
    assert QUOTE in [c["subject"] for c in reminders]
    assert all(c["status"] == "info" and c["kind"] == "soft" for c in reminders)
    approved = [c for c in soft.s4(_variant(approved=True, max_pct=30)) if c["id"] == "S4"]
    assert QUOTE not in [c["subject"] for c in approved]


# ---------------------------------------------------------------- end to end: report (inv. 3) and the decision route

@pytest.fixture()
def amira_copy(tmp_path) -> Path:
    """A tmp copy of fixtures/amira (the committed files stay untouched) with the tech rule unapproved, 30 %."""
    d = tmp_path / "amira"
    shutil.copytree(AMIRA_DIR, d)
    p = json.loads((d / "profile.json").read_text(encoding="utf-8"))
    rule = _rules(p)[TECH_RULE]
    rule["approved_by_user"], rule["params"]["max_pct"] = False, 30
    (d / "profile.json").write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    return d


def test_review_with_a_provisional_breach_has_a_warning_and_no_h13_violation(amira_copy, tmp_path, monkeypatch):
    # Inv. 3 + §7.5: the provisional breach is a `warning` in the report; it alone never makes the verdict `incoherent`.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("COHERENCE_ANALYZER_STORE_DIR", str(tmp_path))
    rep = pipeline.review(amira_copy)
    h13 = _tech_checks(rep["checks_current"])
    assert [c["status"] for c in h13] == ["warning"]
    assert PROVISIONAL_PATH in h13[0]["derived_from"]
    assert any(QUOTE in s for s in [h13[0]["subject"] or "", *h13[0]["derived_from"]])
    others = [c["status"] for c in rep["checks_current"] if c["id"] != "H13"]
    assert rep["verdict"] == report_mod.verdict_from_statuses([*others, "warning"])
    assert "violation" in others or rep["verdict"] == "coherent_with_warnings"
    assert any(c["id"] == "S4" and c["subject"] == QUOTE for c in rep["checks_current"])


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    monkeypatch.setenv("COHERENCE_ANALYZER_STORE_DIR", str(store_dir))
    return TestClient(api.app)


def _api_profile(client: TestClient) -> dict:
    r = client.get(f"/profiles/{AMIRA}")
    assert r.status_code == 200, r.text
    return r.json()


def _refresh(client: TestClient) -> dict:
    r = client.post(f"/profiles/{AMIRA}/events", json={"type": "note", "text": "Starting a session.", "ts": TS,
                                                      "source": "ui_chat", "payload": None})
    assert r.status_code in (200, 201), r.text
    return _api_profile(client)


def _seed_unapproved(client: TestClient, tmp_path: Path, max_pct: int = 30) -> dict:
    """Copy the fixture via the API, rewrite the working profile with the tech rule unapproved, then run one Flow A
    refresh (a note event) so the stored limits are rebuilt; returns the refreshed profile."""
    _api_profile(client)
    path = tmp_path / "store" / "profiles" / AMIRA / "profile.json"
    p = json.loads(path.read_text(encoding="utf-8"))
    rule = _rules(p)[TECH_RULE]
    rule["approved_by_user"], rule["params"]["max_pct"] = False, max_pct
    path.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    return _refresh(client)


def _decide(client: TestClient, approved: bool) -> dict:
    r = client.post(f"/profiles/{AMIRA}/parsed-rules/{TECH_RULE}/decision", json={"approved": approved})
    assert r.status_code == 200, r.text
    return r.json()


def test_flow_a_refresh_stores_the_provisional_limit_and_trace(client, tmp_path):
    # D11.1 through the API: the refresh promotes the unapproved numeric rule; GET shows it flagged, with the trace.
    p = _seed_unapproved(client, tmp_path)
    entry = p["limits"]["max_theme_weight"][THEME]
    assert _is_provisional(entry) and _frac(entry) == pytest.approx(0.30, abs=TOL)
    assert PROVISIONAL_PATH in p["limits_trace"]["max_theme_weight"]
    assert _rules(p)[TECH_RULE]["approved_by_user"] is False         # the rule itself is still unapproved


def test_approving_the_provisional_rule_makes_it_full_strength(client, tmp_path):
    # D11.3 via the existing decision route + refresh: `provisional` gone, trace [approved_by_user].
    before = _seed_unapproved(client, tmp_path)
    body = _decide(client, True)
    assert _rules(body)[TECH_RULE]["approved_by_user"] is True
    assert body["limits"]["max_theme_weight"] == {THEME: pytest.approx(0.30, abs=TOL)}
    assert not _is_provisional(body["limits"]["max_theme_weight"][THEME])
    assert APPROVED_PATH in body["limits_trace"]["max_theme_weight"]
    assert PROVISIONAL_PATH not in body["limits_trace"]["max_theme_weight"]
    assert _api_profile(client) == body
    assert body["meta"]["version"] == before["meta"]["version"] + 1


def test_rejecting_the_provisional_rule_removes_the_limit_and_it_stays_removed(client, tmp_path):
    # D11.3: rejection removes the provisional limit (response and GET), and a later refresh does not bring it back.
    # SPEC-AMBIGUITY: D11 does not say how a rejected rule is marked; only the observable limits / trace are pinned.
    _seed_unapproved(client, tmp_path)
    body = _decide(client, False)
    assert not body["limits"].get("max_theme_weight")
    assert PROVISIONAL_PATH not in body["limits_trace"].get("max_theme_weight", [])
    assert not _api_profile(client)["limits"].get("max_theme_weight")
    assert not _refresh(client)["limits"].get("max_theme_weight")


def test_amiras_approved_rule_through_the_api_stays_a_plain_full_strength_limit(client):
    # D11 "Amira's fixture": the committed profile, once refreshed, still has {tech_heavy: 0.5} and no provisional flag.
    _api_profile(client)
    p = _refresh(client)
    assert p["limits"]["max_theme_weight"] == {THEME: pytest.approx(0.5, abs=TOL)}
    assert PROVISIONAL_PATH not in p["limits_trace"]["max_theme_weight"]
