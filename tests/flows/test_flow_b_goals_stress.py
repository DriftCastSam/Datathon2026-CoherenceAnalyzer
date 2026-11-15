"""P3.FLOWBG1: the Flow B report carries the G1 `goals` block and the P2.16 `stress` block, like Flow C.

Sources: SPEC §13 (report schema: `goals`, `stress` are top-level, optional blocks of `schemas/report.json`), §4 Flow B
B9 (`report.save` + audit), §7.3 G1 (always `info`, assumption labels, no limit), §7.5 (the Flow B verdict is computed on
the PROPOSED checks), docs/spec-factors.md §20.3 / §20.4 / §20.8 (factor `facts`; Flow B sections are built on the
proposed side; G1 rows are shown in the objectif facts, outside the section status and the verdict), README P2.14 and
P2.16 notes (Flow C wiring: `pipeline._goals_block`, `stress.stress_block`; info only, `feeds_limits` false, never a
check, never in the verdict or the limits), CLAUDE.md invariants 2 and 3, D10 (describe, never prescribe).

Scenario: Robert in_0001 = NVDA 10 % funded from ZST.TO (`engine.ROBERT` -> `engine.ROBERT_NVDA`, 48 months, 400 000 CAD,
as_of 2026-11-12, seed 7). Declared by Robert: `situation.monthly_contribution` 1 500 CAD, goals "Retire at 63" (2030-06,
450 000, month 43: inside the 48-month horizon) and "Help children with a down payment" (2032-01: outside the horizon,
no probability).

Reading chosen where the spec is silent (stricter one, SPEC-AMBIGUITY): the report has ONE `goals` block and ONE `stress`
block (no current / proposed pair in the schema). The verdict, the sections and the explanation of a Flow B report are all
on the PROPOSED side, so both blocks describe the proposed portfolio (the one the user is about to hold). The blocks are
built from user-declared fields only (`objectives.goals`, `situation.monthly_contribution`) and the portfolio, never
from `limits` or an `llm:` field.

Offline: COHERENCE_ANALYZER_LLM=mock, a tmp store, fixed seeds, no clock value asserted. Mock-cache misses on the changed
explainer payload fall back to the template text; no test here depends on the narration mode.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

from coherence_analyzer import config, data, engine, pipeline, store, stress  # noqa: E402
from coherence_analyzer.coherence import report as report_mod  # noqa: E402
from coherence_analyzer.factors import fmt  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "fixtures" / "robert"
TAGS = ROOT / "data" / "meta" / "activity_tags.csv"
TAGS_BYTES = TAGS.read_bytes() if TAGS.exists() else None
INTENT_ID = "in_0001"
AS_OF = "2026-11-12"
VALUE = 400_000
HORIZON_M = 48
CONTRIBUTION = 1500
META_NAMES = ("asset_meta", "etf_sector_weights", "activity_tags")
SCENARIO_IDS = ["parallel_-100bp", "parallel_+100bp", "parallel_+200bp", "historical_max_12m_rise"]
PRESCRIPTIVE = re.compile(
    r"\b(you should|should you|we recommend|recommended|buy|sell|devriez|vous recommandons|achetez|vendez|"
    r"pourriez|you could)\b", re.IGNORECASE)                                       # D10, SPEC §1 inv. 7


# ---------------------------------------------------------------- helpers

def _read(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _fractions(w_pct: dict) -> dict:
    return {t: v / 100 for t, v in w_pct.items()}


def _same(a, b, path: str = "$") -> None:
    """Deep equality: floats within 1e-9 (engine values), everything else exact."""
    if isinstance(a, dict) and isinstance(b, dict):
        assert sorted(a) == sorted(b), f"{path}: keys {sorted(a)} != {sorted(b)}"
        for k in a:
            _same(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        assert len(a) == len(b), f"{path}: length {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            _same(x, y, f"{path}[{i}]")
    elif isinstance(a, float) or isinstance(b, float):
        assert a == pytest.approx(b, abs=1e-9), f"{path}: {a} != {b}"
    else:
        assert a == b, f"{path}: {a!r} != {b!r}"


def _strings(node) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in _strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return []


def _section(rep: dict, factor: str) -> dict:
    return next(s for s in rep["sections"] if s["factor"] == factor)


def _hard_view(checks: list[dict]) -> list[tuple]:
    return sorted((c["id"], c.get("subject") or "", c["status"]) for c in checks if c["kind"] == "hard")


def _locale(profile: dict) -> str:
    return profile["meta"].get("locale", pipeline.DEFAULT_LOCALE)


def _fixture_copy(tmp: Path, edit) -> Path:
    """A copy of fixtures/robert whose profile.json went through `edit(profile)` (fixtures/ is never written)."""
    dst = tmp / "robert_edit"
    shutil.copytree(FIX, dst)
    prof = _read(dst / "profile.json")
    edit(prof)
    (dst / "profile.json").write_text(json.dumps(prof, ensure_ascii=False, indent=2), encoding="utf-8")
    return dst


def _check(fixture: Path = FIX) -> dict:
    return pipeline.check(fixture, INTENT_ID, auto_confirm=True)


def _expected_stress(weights_pct: dict, value: float, profile: dict) -> dict:
    meta = {n: data.load_meta(n) for n in META_NAMES}
    return store.plain(stress.stress_block(
        _fractions(weights_pct), value, data.load_assets(), data.load_macro(), as_of=AS_OF,
        asset_meta=meta["asset_meta"], locale=_locale(profile)))


# ---------------------------------------------------------------- shared run and expected values

@pytest.fixture(scope="module")
def report_schema() -> dict:
    schema = json.loads((config.SCHEMAS_DIR / "report.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict:
    """`pipeline.check(robert, in_0001, auto_confirm=True)` once, in a tmp store."""
    base = tmp_path_factory.mktemp("flowbg1")
    mp = pytest.MonkeyPatch()
    mp.setenv("COHERENCE_ANALYZER_LLM", "mock")
    mp.setenv("COHERENCE_ANALYZER_STORE_DIR", str(base / "store"))
    mp.chdir(base)
    try:
        rep = _check()
        saved = store.load_report(rep["run_id"])
        audit = store.load_audit(rep["run_id"])
    finally:
        mp.undo()
    return {"rep": rep, "saved": saved, "audit": audit}


@pytest.fixture(scope="module")
def rep(run) -> dict:
    return run["rep"]


@pytest.fixture(scope="module")
def profile() -> dict:
    return _read(FIX / "profile.json")


@pytest.fixture(scope="module")
def declared_goals(profile) -> list[dict]:
    return list(profile["objectives"]["goals"])


@pytest.fixture(scope="module")
def expected_goals(market, declared_goals) -> dict:
    """engine.analyse on the PROPOSED portfolio with the declared contribution and goals (same seed, same horizon)."""
    out = {}
    for side, w in (("current", engine.ROBERT), ("proposed", engine.ROBERT_NVDA)):
        out[side] = engine.analyse(market, w, HORIZON_M, VALUE, monthly_contribution=CONTRIBUTION,
                                   goals=declared_goals, as_of=AS_OF)
    return out


# ---------------------------------------------------------------- the report carries both blocks

def test_flow_b_report_carries_goals_and_stress_and_is_schema_valid(rep, report_schema):
    """SPEC §13 + P2.14 / P2.16: the Flow B report has the optional `goals` and `stress` blocks, and the whole
    report validates against schemas/report.json (G1 checks are `info` only, stress `status` const `info`)."""
    assert rep["flow"] == "B"
    assert isinstance(rep.get("goals"), dict), "Flow B report has no `goals` block"
    assert isinstance(rep.get("stress"), dict), "Flow B report has no `stress` block"
    errors = sorted(Draft202012Validator(report_schema).iter_errors(rep), key=lambda e: list(e.path))
    assert not errors, [f"{list(e.path)}: {e.message}" for e in errors[:5]]


def test_saved_report_carries_the_same_blocks(run, rep):
    """SPEC §4 B9: `report.save` writes the report the caller gets: the stored copy has the same goals and stress."""
    saved = run["saved"]
    assert "goals" in saved and "stress" in saved
    _same(saved["goals"], store.plain(rep["goals"]))
    _same(saved["stress"], store.plain(rep["stress"]))


# ---------------------------------------------------------------- goals (G1)

def test_goals_block_header_is_the_declared_contribution_and_the_horizon(rep):
    """SPEC §7.3 G1 + README P2.14: the block names the user-declared contribution and the simulated horizon."""
    g = rep["goals"]
    assert g["monthly_contribution"] == pytest.approx(CONTRIBUTION, abs=1e-9)
    assert g["horizon_months"] == HORIZON_M
    assert set(g) >= {"monthly_contribution", "horizon_months", "goal_probs", "checks", "assumptions"}


def test_goal_probs_come_from_the_proposed_portfolio_with_the_declared_contribution(rep, expected_goals):
    """SPEC §13 + §7.5 + spec-factors §20.4: a Flow B report is on the proposed side, so the single `goals` block
    is the engine's `goal_probs` for the proposed weights, with the declared contribution and goals (same seed)."""
    # SPEC-AMBIGUITY: the schema has one `goals` block; the stricter reading is the proposed side (verdict, sections
    # and explanation are proposed). The guard below makes sure this assertion can tell the two sides apart.
    cur, prop = expected_goals["current"]["goal_probs"], expected_goals["proposed"]["goal_probs"]
    assert [p["prob"] for p in cur] != [p["prob"] for p in prop], "test guard: the two sides must differ"
    got = rep["goals"]["goal_probs"]
    assert [(p["name"], p["date"], p["amount"], p["month"]) for p in got] == \
        [(p["name"], p["date"], p["amount"], p["month"]) for p in prop]
    for g, e in zip(got, prop):
        assert g["prob"] == pytest.approx(e["prob"], abs=1e-9)
    # inside the horizon only: "Retire at 63" (2030-06) is month 43, the 2032-01 goal is out of the 48 months
    assert [p["name"] for p in got] == ["Retire at 63"]
    assert got[0]["month"] == 43


def test_g1_checks_one_info_check_per_declared_goal(rep, declared_goals, expected_goals):
    """SPEC §7.3 G1: always `info`, one per declared goal in the profile's order, rule-sourced, assumption labels;
    a goal outside the horizon has no number (`value` None, `goal_outside_horizon`)."""
    checks = rep["goals"]["checks"]
    assert [c["subject"] for c in checks] == [g["name"] for g in declared_goals]
    assert {c["id"] for c in checks} == {"G1"}
    assert {c["status"] for c in checks} == {"info"}
    assert {c["source"] for c in checks} == {"rule:goal_probability"}
    assert all(c["unit"] == "probability" and c["limit"] is None for c in checks)
    retire, later = checks
    prop = expected_goals["proposed"]["goal_probs"][0]
    assert retire["value"] == pytest.approx(prop["prob"], abs=1e-9)
    assert retire["message_key"] == "goal_probability"
    assert later["value"] is None and later["message_key"] == "goal_outside_horizon"
    for c in checks:
        assert c["assumptions"], "every G1 row carries its assumption labels"
        assert all(isinstance(a, str) and a for a in c["assumptions"])
    assert rep["goals"]["assumptions"], "the block lists the assumption labels"


def test_g1_derives_from_declared_fields_never_from_limits_or_llm(rep):
    """CLAUDE.md invariant 2: the goals block is built from `objectives.goals` and `situation.monthly_contribution`,
    never from `limits.*` or an `llm:` field."""
    for c in rep["goals"]["checks"]:
        assert "objectives.goals" in c["derived_from"]
        assert not [d for d in c["derived_from"] if d.startswith("limits") or "llm" in d], c["derived_from"]
        assert not str(c["source"]).startswith("llm")


def test_g1_stays_out_of_the_checks_the_diff_the_unknowns_and_the_verdict(rep):
    """SPEC §7.3 + spec-factors §20.8 + README P2.14: G1 is shown in the `goals` block and the objectif facts, never
    among `checks_current` / `checks_proposed`, never a flipped check, never an unknown, never in the verdict."""
    for key in ("checks_current", "checks_proposed", "unknowns"):
        assert "G1" not in {c["id"] for c in rep[key]}, key
    assert "G1" not in {f["id"] for f in rep["diff"]["flipped"]}
    # the verdict is the one derived from the PROPOSED hard + soft checks alone (and from the section statuses)
    assert rep["verdict"] == report_mod.verdict(rep["checks_proposed"]) == "incoherent"
    assert report_mod.sections_verdict(rep["sections"]) == rep["verdict"]


def test_objectif_section_shows_g1_like_flow_c(rep, expected_goals, declared_goals):
    """spec-factors §20.3 + README P2.14: the objectif facts carry `goals` / `goal_assumptions` (pre-formatted
    strings, same shape as Flow C); no other section does, and the section status is untouched by G1."""
    facts = _section(rep, "objectif")["facts"]
    assert "goals" in facts and "goal_assumptions" in facts, sorted(facts)
    loc = fmt.check_locale("fr-CA")
    rows = facts["goals"]
    assert [r["name"] for r in rows] == [g["name"] for g in declared_goals]
    assert {r["check"] for r in rows} == {"G1"} and {r["status"] for r in rows} == {"info"}
    prob = expected_goals["proposed"]["goal_probs"][0]["prob"]
    assert rows[0]["probability"] == fmt.percent(prob, loc)
    assert rows[0]["amount"] == fmt.amount(declared_goals[0]["amount"], loc)
    assert rows[0]["date"] == declared_goals[0]["date"]
    assert rows[1]["probability"] is None
    assert facts["goal_assumptions"] == list(dict.fromkeys(a for c in rep["goals"]["checks"] for a in c["assumptions"]))
    for sec in rep["sections"]:
        if sec["factor"] != "objectif":
            assert "goals" not in sec["facts"] and "goal_assumptions" not in sec["facts"], sec["factor"]
    assert _section(rep, "objectif")["status"] == "pass"      # spec-factors §20.8: an `info` row never moves it


# ---------------------------------------------------------------- stress (P2.16)

def test_stress_block_is_the_stress_module_on_the_proposed_portfolio(rep, profile):
    """README P2.16 + SPEC §13: the block is `stress.stress_block` (same helper as Flow C) on the proposed weights and
    value, the portfolio as_of, the macro series and the asset_meta; no logic of its own."""
    # SPEC-AMBIGUITY: one `stress` block; stricter reading = the proposed side, as for `goals`.
    expected = _expected_stress(engine.ROBERT_NVDA, VALUE, profile)
    current = _expected_stress(engine.ROBERT, VALUE, profile)
    assert expected["scenarios"] != current["scenarios"], "test guard: the two sides must differ"
    _same(store.plain(rep["stress"]), expected)


def test_stress_is_information_only(rep):
    """README P2.16 + schemas/report.json: `status` info, `kind` rate_scenario, `feeds_limits` false, the four
    scenarios (-100 / +100 / +200 bp and the historical one), labelled assumptions, `sourced` false (ASSUMED durations)."""
    s = rep["stress"]
    assert s["status"] == "info" and s["kind"] == "rate_scenario"
    assert s["feeds_limits"] is False
    assert [x["id"] for x in s["scenarios"]] == SCENARIO_IDS
    assert [x["shock_bp"] for x in s["scenarios"]][:3] == [-100, 100, 200]
    assert s["sourced"] is False
    assert s["assumptions"] and all(isinstance(a, str) and a for a in s["assumptions"])
    assert s["as_of"] == AS_OF
    assert s["value_cad"] == pytest.approx(VALUE, abs=1e-9)
    assert any(h["ticker"] == "XBB.TO" for h in s["bond_holdings"])


def test_stress_is_never_a_check_an_unknown_or_part_of_the_verdict(rep):
    """CLAUDE.md invariants 2 and 3 + README P2.16: stress is no check (no `stress` / `rate_scenario` id, kind or
    source in any check list, no flipped check), no unknown, and moves neither the verdict nor a section status."""
    for key in ("checks_current", "checks_proposed", "unknowns"):
        for c in rep[key]:
            blob = json.dumps(c, default=str).lower()
            assert "stress" not in blob and "rate_scenario" not in blob, (key, c["id"])
    assert not [f for f in rep["diff"]["flipped"] if "stress" in json.dumps(f).lower()]
    assert rep["verdict"] == "incoherent"
    assert "stress" not in {s["factor"] for s in rep["sections"]}
    assert "stress" not in rep["metrics_current"] and "stress" not in rep["metrics_proposed"]


# ---------------------------------------------------------------- verdicts, headroom, metrics unchanged

def test_verdict_checks_headroom_and_metrics_are_unchanged(rep, run, market, profile):
    """Task: adding the blocks changes no verdict, check, headroom or metric of Flow B (tests/flows/test_flow_b.py
    numbers): H3 H4 H7 H8 H9 H10 violate on the proposed side, headroom 1.5 % from ZST.TO bound by H9, H7-H10 / H14
    still on the contribution-free simulation."""
    assert rep["verdict"] == "incoherent" and run["audit"]["verdict"] == "incoherent"
    prop = {(c["id"]) for c in rep["checks_proposed"] if c["status"] == "violation"}
    assert prop == {"H3", "H4", "H7", "H8", "H9", "H10"}
    assert {c["id"] for c in rep["checks_current"] if c["status"] == "violation"} == set()
    hr = rep["headroom"]
    assert hr["binding_check"] == "H9" and hr["max_weight_pct"] == pytest.approx(1.5, abs=1e-9)
    base = engine.analyse(market, engine.ROBERT_NVDA, HORIZON_M, VALUE)
    for k in ("vol", "mdd", "p5_T", "width", "loss_prob"):
        assert rep["metrics_proposed"][k] == pytest.approx(base[k], abs=1e-9), k
    assert rep["metrics_proposed"].get("goal_probs", []) == []
    cur = engine.analyse(market, engine.ROBERT, HORIZON_M, VALUE)
    assert rep["metrics_current"]["p5_T"] == pytest.approx(cur["p5_T"], abs=1e-9)
    assert rep["metrics_current"].get("goal_probs", []) == []
    statuses = {s["factor"]: s["status"] for s in rep["sections"]}
    assert statuses == {"objectif": "pass", "horizon": "violation", "risque": "violation",
                        "contraintes": "violation", "preferences": "pass"}          # spec-factors §20.8


def test_goal_and_contribution_edits_move_the_goals_block_only(tmp_path, rep):
    """CLAUDE.md invariant 2: a different declared goal amount changes the G1 numbers and nothing that feeds
    limits: same hard checks, verdict, headroom, section statuses and stress block."""
    def edit(p):
        p["objectives"]["goals"][0]["amount"] = 900_000

    alt = _check(_fixture_copy(tmp_path, edit))
    assert alt["goals"]["goal_probs"][0]["prob"] < rep["goals"]["goal_probs"][0]["prob"]
    assert alt["goals"]["checks"][0]["value"] == pytest.approx(alt["goals"]["goal_probs"][0]["prob"], abs=1e-9)
    assert alt["verdict"] == rep["verdict"]
    assert _hard_view(alt["checks_current"]) == _hard_view(rep["checks_current"])
    assert _hard_view(alt["checks_proposed"]) == _hard_view(rep["checks_proposed"])
    _same(store.plain(alt["headroom"]), store.plain(rep["headroom"]))
    assert {s["factor"]: s["status"] for s in alt["sections"]} == {s["factor"]: s["status"] for s in rep["sections"]}
    _same(store.plain(alt["stress"]), store.plain(rep["stress"]))
    _same(store.plain(alt["metrics_proposed"]), store.plain(rep["metrics_proposed"]))


def test_without_declared_goals_the_blocks_stay_present_and_empty(tmp_path, rep):
    """Flow C shape (`pipeline._goals_block`): no goals declared -> `goals` block with empty `goal_probs` / `checks`
    (the contribution and horizon still stated), no `goals` row in the objectif facts; stress unchanged."""
    def edit(p):
        p["objectives"]["goals"] = []

    alt = _check(_fixture_copy(tmp_path, edit))
    g = alt["goals"]
    assert g["goal_probs"] == [] and g["checks"] == []
    assert g["monthly_contribution"] == pytest.approx(CONTRIBUTION, abs=1e-9) and g["horizon_months"] == HORIZON_M
    assert not _section(alt, "objectif")["facts"].get("goals")
    _same(store.plain(alt["stress"]), store.plain(rep["stress"]))
    assert alt["verdict"] == rep["verdict"]
    assert _hard_view(alt["checks_proposed"]) == _hard_view(rep["checks_proposed"])


# ---------------------------------------------------------------- describe, never prescribe (D10)

def test_blocks_describe_and_never_prescribe(rep):
    """D10 + SPEC §1 inv. 7: every user-facing string of both blocks (and of the objectif goal rows) describes; none
    tells the user to buy, sell or do anything."""
    texts = (_strings(rep["goals"]) + _strings(rep["stress"]["assumptions"]) + _strings(rep["stress"]["facts"])
             + _strings(_section(rep, "objectif")["facts"].get("goals")))
    assert texts
    bad = [t for t in texts if PRESCRIPTIVE.search(t)]
    assert not bad, bad[:3]
    assert not [k for k in ("recommendation", "suggested", "alternatives") if k in json.dumps(rep["stress"]).lower()]


def test_audit_record_still_has_no_blocks_beyond_the_report(run):
    """SPEC §10: the audit keeps its own fields (verdict, engine info); the new blocks live in the report."""
    audit = run["audit"]
    assert audit["verdict"] == run["rep"]["verdict"]
    assert "stress" not in audit and "goals" not in audit


# ---------------------------------------------------------------- Step Functions path and API route

def _run_steps_through_handler() -> dict:
    """The Step Functions order (B1, B3, B4, B5 current + proposed merged, B6, B7 if violations, B8, B9) through
    `lambda_handler.handler`, each state going through JSON like an execution payload."""
    from coherence_analyzer import lambda_handler as lh

    def call(step, **kw):
        return json.loads(json.dumps(lh.handler({"step": step, **kw}, None)))

    state = call("b1_parse_intent", fixture_dir=str(FIX), intent_id=INTENT_ID, auto_confirm=True)
    state = call("b3_refresh", state=state)
    state = call("b4_apply", state=state)
    cur, prop = call("b5_analyse_current", state=state), call("b5_analyse_proposed", state=state)
    state = call("b6_checks_and_diff", state={**state, **cur, **prop})
    if state["has_violations"]:
        state = call("b7_resize", state=state)
    return call("b9_save", state=call("b8_explain", state=state))["report"]


def test_step_functions_path_carries_the_same_blocks(rep, report_schema, tmp_path, monkeypatch):
    """P2.15 stays green: the Lambda steps (state through JSON) give a report whose `goals` and `stress` equal
    those of `pipeline.check`, and that validates against the schema."""
    monkeypatch.setenv("COHERENCE_ANALYZER_STORE_DIR", str(tmp_path / "sfn_store"))
    out = _run_steps_through_handler()
    assert not list(Draft202012Validator(report_schema).iter_errors(out))
    _same(store.plain(out["goals"]), store.plain(rep["goals"]))
    _same(store.plain(out["stress"]), store.plain(rep["stress"]))
    assert out["verdict"] == rep["verdict"]


def test_api_confirm_route_returns_the_report_with_both_blocks(rep, report_schema, tmp_path, monkeypatch):
    """SPEC §12: `POST /intents/{id}/confirm` runs the Flow B steps, so its report carries the same `goals` and
    `stress` as `pipeline.check` for the same trade (NVDA 10 % funded from ZST.TO)."""
    from fastapi.testclient import TestClient
    from coherence_analyzer import api

    monkeypatch.setenv("COHERENCE_ANALYZER_STORE_DIR", str(tmp_path / "api_store"))
    client = TestClient(api.app)
    assert client.get("/profiles/persona-robert").status_code == 200
    r = client.post("/profiles/persona-robert/events", json={
        "type": "order_intent", "text": "I want to put 10% in NVDA, everyone's talking about it",
        "ts": "2026-11-15T10:00:00-05:00", "source": "ui_chat"})
    assert r.status_code in (200, 201), r.text
    body = r.json()
    draft = body.get("intent") if isinstance(body.get("intent"), dict) else body.get("draft")
    confirm = client.post(f"/intents/{draft['intent_id']}/confirm", json={
        "ticker": "NVDA", "target_weight_pct": 10, "funding": {"kind": "specific", "ticker": "ZST.TO"}})
    assert confirm.status_code == 200, confirm.text
    out = confirm.json()
    assert not list(Draft202012Validator(report_schema).iter_errors(out))
    _same(store.plain(out["goals"]), store.plain(rep["goals"]))
    _same(store.plain(out["stress"]), store.plain(rep["stress"]))
    assert out["verdict"] == rep["verdict"]


def test_activity_tags_csv_is_never_written():
    """CLAUDE.md: `data/meta/activity_tags.csv` is human-only; the Flow B run above did not touch it."""
    assert (TAGS.read_bytes() if TAGS.exists() else None) == TAGS_BYTES
