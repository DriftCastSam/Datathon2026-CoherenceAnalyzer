"""P3.O1: check O1, capital_preservation objective vs the portfolio's historical drawdown (info only).

Sources: docs/spec-factors.md §20.2 (O1: `objectives.primary = capital_preservation` and
`engine.mdd < risk.max_acceptable_drawdown_pct` -> `info` "your stated goal is capital preservation; the historical
drawdown is X"; `objective_vs_portfolio` stays in `not_covered` only "until O1 lands"), §20.3 / §20.4 (section checks,
facts payload, the verdict equals the verdict derived from the section statuses), SPEC §7.3 (check shape; the
`engine.mdd` value is a fraction, `risk.max_acceptable_drawdown_pct` is a percent), §7.5 (verdict), CLAUDE.md invariants
(`info` is never `violation`/`warning`; every number in an explanation exists in the facts; no LLM field feeds a rule),
docs/spec-decisions.md D3 (unknown never changes the verdict), D10 (describe, never prescribe), and the P3.SKIP
convention (a skipped answer is `{value: null, by: user:skipped}` and the related check is `unknown`).

O1 is emitted by `hard.run_all` (the one entry point both Flow B, `pipeline.b6_checks_and_diff`, and Flow C,
`pipeline.review`, call). Offline and deterministic: COHERENCE_ANALYZER_LLM=mock, fixed engine seed, reference date =
portfolio.as_of (D1).
"""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from coherence_analyzer import data, engine, explain, factors, intake, numfacts, pipeline
from coherence_analyzer.coherence import hard, report as report_mod
from coherence_analyzer.factors import objectif
from coherence_analyzer.llm import bedrock
from coherence_analyzer.rules import derive, limits as limits_mod

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "fixtures" / "robert"
AS_OF = "2026-11-12"          # portfolio.as_of (docs/spec-decisions.md D1); no clock reads
ROBERT_VALUE = 400_000
ROBERT_HORIZON_M = 48
META_NAMES = ("asset_meta", "etf_sector_weights", "activity_tags")
CAPITAL = "capital_preservation"
ROBERT_MDD = -0.077           # engine golden (Robert, 48 months): historical monthly drawdown, 2022-09 trough
INTENT_ID = "in_0001"
FORBIDDEN = ("consider", "you could", "rebalance", "next step", "vous pourriez", "envisagez")   # D10
_ROBERT_RAW = json.loads((FIX / "profile.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _mock_llm(monkeypatch):
    monkeypatch.setattr(bedrock, "COHERENCE_ANALYZER_LLM", "mock")


# ---------------------------------------------------------------- helpers

def _fractions(weights_pct: dict[str, float]) -> dict[str, float]:
    return {t: v / 100 for t, v in weights_pct.items()}


def _raw(primary=CAPITAL, dd=-12) -> dict:
    """Robert's raw profile with the declared objective and the declared maximum drawdown (percent)."""
    p = copy.deepcopy(_ROBERT_RAW)
    p["objectives"]["primary"] = primary
    p["risk"]["max_acceptable_drawdown_pct"] = dd
    return p


def _prepare(raw: dict) -> dict:
    """derive_all + build_limits like Flow A does."""
    p = derive.derive_all(raw, portfolio_value_cad=ROBERT_VALUE, as_of=AS_OF)
    lim, trace = limits_mod.build_limits(p, ROBERT_VALUE)
    p["limits"], p["limits_trace"] = lim, trace
    return p


def _o1(checks: list) -> list:
    return [c for c in checks if c["id"] == "O1"]


@pytest.fixture(scope="module")
def assets():
    return data.load_assets()


@pytest.fixture(scope="module")
def meta() -> dict:
    return {n: data.load_meta(n) for n in META_NAMES}


@pytest.fixture(scope="module")
def metrics(market) -> dict:
    return engine.analyse(market, engine.ROBERT, ROBERT_HORIZON_M, ROBERT_VALUE)


def _run(profile: dict, m: dict, assets, meta) -> list:
    return hard.run_all(_fractions(engine.ROBERT), m, profile, assets, meta)


def _with_mdd(m: dict, mdd) -> dict:
    out = dict(m)
    out["mdd"] = mdd
    return out


# ---------------------------------------------------------------- the check itself (§20.2, §7.3)

def test_robert_mdd_is_the_golden_value(metrics):
    """Control: the engine value the rest of this file builds on (golden, unchanged)."""
    assert metrics["mdd"] == pytest.approx(ROBERT_MDD, abs=1e-9)


def test_o1_is_info_when_the_historical_drawdown_is_worse_than_the_accepted_one(metrics, assets, meta):
    """§20.2: primary = capital_preservation and mdd < max_acceptable_drawdown_pct -> exactly one `info` O1."""
    p = _prepare(_raw(dd=-5))                      # accepts -5 %; history is -7.7 %
    res = _o1(_run(p, metrics, assets, meta))
    assert len(res) == 1
    c = res[0]
    assert c["status"] == "info"
    assert c["value"] == pytest.approx(ROBERT_MDD, abs=1e-9)          # the engine's mdd, as a fraction
    assert c["limit"] == pytest.approx(-0.05, abs=1e-9)               # the declared -5 %, converted to a fraction once
    assert c["unit"] == "drawdown"


def test_o1_check_has_the_spec_shape(metrics, assets, meta):
    """§7.3 check keys; rule-based (never llm:), full confidence; derived_from names both declared inputs."""
    c = _o1(_run(_prepare(_raw(dd=-5)), metrics, assets, meta))[0]
    assert {"id", "kind", "status", "subject", "value", "limit", "unit", "derived_from", "source", "confidence",
            "message_key"} <= set(c)
    # SPEC-AMBIGUITY: §20.2 calls O1 "rule-based" without naming its kind; the stricter reading is `hard` (like H14),
    # since a soft check is the LLM-derived family (§7.4).
    assert c["kind"] == "hard"
    assert c["source"].startswith("rule:") and "llm" not in c["source"]
    assert c["confidence"] == 1.0
    assert isinstance(c["message_key"], str) and c["message_key"]
    assert {"objectives.primary", "risk.max_acceptable_drawdown_pct"} <= set(c["derived_from"])


def test_o1_is_pass_when_the_drawdown_is_within_the_accepted_one(metrics, assets, meta):
    """§20.2: the condition is false (-7.7 % is not worse than the declared -12 %) -> pass, never info."""
    res = _o1(_run(_prepare(_raw(dd=-12)), metrics, assets, meta))
    assert [c["status"] for c in res] == ["pass"]
    assert res[0]["value"] == pytest.approx(ROBERT_MDD, abs=1e-9)
    assert res[0]["limit"] == pytest.approx(-0.12, abs=1e-9)


@pytest.mark.parametrize("mdd, status", [(-0.1201, "info"), (-0.12, "pass"), (-0.1199, "pass"), (-0.30, "info")])
def test_o1_boundary_is_strict_worse_than(mdd, status, metrics, assets, meta):
    """§20.2 `engine.mdd < risk.max_acceptable_drawdown_pct`: strict, so a drawdown exactly at the declared figure
    passes; -0.30 is far past it."""
    p = _prepare(_raw(dd=-12))
    res = _o1(_run(p, _with_mdd(metrics, mdd), assets, meta))
    assert [c["status"] for c in res] == [status]
    assert res[0]["value"] == pytest.approx(mdd, abs=1e-9)


def test_o1_compares_with_the_declared_figure_not_with_the_derived_limit(metrics, assets, meta):
    """§20.2 names `risk.max_acceptable_drawdown_pct`. Changing `limits.max_drawdown_pct` (derived) changes nothing."""
    p = _prepare(_raw(dd=-5))
    base = _o1(_run(p, metrics, assets, meta))
    other = copy.deepcopy(p)
    other["limits"]["max_drawdown_pct"] = -50
    assert _o1(_run(other, metrics, assets, meta)) == base
    gone = copy.deepcopy(p)
    gone["limits"]["max_drawdown_pct"] = None
    assert _o1(_run(gone, metrics, assets, meta)) == base


def test_o1_ignores_every_llm_field(metrics, assets, meta):
    """Invariant: LLM-derived fields never feed a rule check. Changing risk.behavioural / question_quality / the
    parsed notes leaves O1 untouched."""
    p = _prepare(_raw(dd=-5))
    base = _o1(_run(p, metrics, assets, meta))
    q = copy.deepcopy(p)
    q["risk"]["behavioural"]["value"] = "low"
    q["risk"]["behavioural"]["confidence"] = 0.99
    q["knowledge"]["question_quality"]["value"] = 0.0
    assert _o1(_run(q, metrics, assets, meta)) == base


@pytest.mark.parametrize("primary", ["growth", "income", "capital_preservation_with_growth", "balanced",
                                     "aggressive_growth"])
def test_o1_does_not_fire_for_another_objective(primary, metrics, assets, meta):
    """§20.2 fires for `capital_preservation` only. SPEC-AMBIGUITY: whether a non-applicable profile gets a `pass` row
    or no row is not fixed; the test accepts both but never `info`, `warning`, `violation` or `unknown`
    (the objective is known and is not capital preservation)."""
    p = _prepare(_raw(primary=primary, dd=-5))     # the drawdown would be worse than accepted, objective differs
    res = _o1(_run(p, metrics, assets, meta))
    assert all(c["status"] == "pass" for c in res)
    assert len(res) <= 1


def test_o1_is_never_a_violation_or_a_warning(metrics, assets, meta):
    """Invariant: only hard checks give `violation`; O1 is info only. Whatever the drawdown, no O1 outcome is
    `violation` or `warning`."""
    p = _prepare(_raw(dd=-5))
    for mdd in (-0.0, -0.05, -0.0501, -0.12, -0.5, -0.99):
        for c in _o1(_run(p, _with_mdd(metrics, mdd), assets, meta)):
            assert c["status"] in {"pass", "info"}, (mdd, c)


def test_o1_does_not_mutate_its_inputs(metrics, assets, meta):
    p = _prepare(_raw(dd=-5))
    before_p, before_m = copy.deepcopy(p), copy.deepcopy(metrics)
    _run(p, metrics, assets, meta)
    assert p == before_p and metrics == before_m


def test_adding_o1_leaves_the_other_hard_checks_as_they_were(metrics, assets, meta):
    """O1 is an addition: H1-H14 results are what they were (same ids, subjects, statuses, values, limits)."""
    p = _prepare(_raw(dd=-5))
    q = _prepare(_raw(primary="growth", dd=-5))
    key = lambda cs: [(c["id"], c["subject"], c["status"], c["value"], c["limit"])  # noqa: E731
                      for c in cs if c["id"] != "O1"]
    assert key(_run(p, metrics, assets, meta)) == key(_run(q, metrics, assets, meta))


# ---------------------------------------------------------------- unknown, never a guess (P3.SKIP)

def _unknown_ok(res: list, path: str) -> None:
    assert res, "O1 must be reported as unknown, not left out"
    for c in res:
        assert c["status"] == "unknown"
        assert c["kind"] == "hard"
        assert path in c["derived_from"]
        assert c["id"] == "O1"


def test_o1_is_unknown_when_the_objective_is_skipped(metrics, assets, meta):
    """P3.SKIP: objectives.primary = {value: null, by: user:skipped} -> unknown naming the path; no info, no pass."""
    raw = intake.skip(_raw(dd=-5), "objectives.primary")
    _unknown_ok(_o1(_run(_prepare(raw), metrics, assets, meta)), "objectives.primary")


def test_o1_is_unknown_when_the_accepted_drawdown_is_skipped(metrics, assets, meta):
    """P3.SKIP: capital_preservation + a skipped risk.max_acceptable_drawdown_pct -> unknown (never a default)."""
    raw = intake.skip(_raw(), "risk.max_acceptable_drawdown_pct")
    res = _o1(_run(_prepare(raw), metrics, assets, meta))
    _unknown_ok(res, "risk.max_acceptable_drawdown_pct")
    assert all(c["limit"] is None for c in res)               # no invented number


def test_a_skip_never_turns_o1_into_a_violation_or_changes_the_verdict(metrics, assets, meta):
    """D3 / §7.5: the unknown O1 is listed with the unknowns and the verdict is the one of the other checks."""
    raw = intake.skip(_raw(), "objectives.primary")
    checks = _run(_prepare(raw), metrics, assets, meta)
    o1 = _o1(checks)
    assert o1 and all(c["status"] == "unknown" for c in o1)
    others = [c for c in checks if c["id"] != "O1"]
    assert report_mod.verdict(checks) == report_mod.verdict(others)
    assert {c["id"] for c in report_mod.unknowns(checks)} >= {"O1"}


def test_o1_with_a_skipped_unrelated_answer_is_unchanged(metrics, assets, meta):
    """Only objectives.primary / risk.max_acceptable_drawdown_pct / the engine value matter to O1."""
    p = _prepare(_raw(dd=-5))
    base = _o1(_run(p, metrics, assets, meta))
    q = _prepare(intake.skip(_raw(dd=-5), "preferences.home_bias_ok"))
    assert _o1(_run(q, metrics, assets, meta)) == base


# ---------------------------------------------------------------- verdict (§7.5, D3)

def test_o1_info_never_changes_the_verdict(metrics, assets, meta):
    """§20.2 + §7.5: info only. The verdict with O1 equals the verdict computed without it."""
    p = _prepare(_raw(dd=-5))
    checks = _run(p, metrics, assets, meta)
    assert [c["status"] for c in _o1(checks)] == ["info"]
    others = [c for c in checks if c["id"] != "O1"]
    assert report_mod.verdict(checks) == report_mod.verdict(others)


def test_an_o1_info_alone_leaves_the_verdict_coherent(metrics, assets, meta):
    """§7.5: with O1 `info` as the only non-pass result, the verdict is `coherent` (info is neither violation nor
    warning); the unknown version of O1 does not change that either (D3)."""
    info = _o1(_run(_prepare(_raw(dd=-5)), metrics, assets, meta))
    unknown = _o1(_run(_prepare(intake.skip(_raw(), "objectives.primary")), metrics, assets, meta))
    assert [c["status"] for c in info] == ["info"] and [c["status"] for c in unknown] == ["unknown"]
    assert report_mod.verdict(info) == "coherent"
    assert report_mod.verdict(unknown) == "coherent"


# ---------------------------------------------------------------- factor view (§20.2, §20.3)

def test_o1_belongs_to_objectif():
    """§20.2: O1 is owned by the objectif factor (the mapping the audit found already in place)."""
    assert factors.CHECK_FACTOR["O1"] == "objectif"
    assert "O1" in factors.CHECK_FACTOR and factors.factor_of("O1") == "objectif"


def test_objective_vs_portfolio_is_no_longer_not_covered():
    """§20.2: `objective_vs_portfolio` is listed as not covered only until O1 lands."""
    assert "objective_vs_portfolio" not in objectif.NOT_COVERED
    assert "fiscalite" not in objectif.NOT_COVERED


def test_tagging_gives_o1_the_objectif_factor(metrics, assets, meta):
    checks = report_mod.tag_checks(_run(_prepare(_raw(dd=-5)), metrics, assets, meta))
    assert [c["factor"] for c in _o1(checks)] == ["objectif"]


# ---------------------------------------------------------------- Flow C: pipeline.review

def _fixture_copy(tmp: Path, raw: dict) -> Path:
    """A copy of fixtures/robert with `raw` as profile.json (fixtures/ is never written)."""
    dst = tmp / "robert_o1"
    shutil.copytree(FIX, dst)
    (dst / "profile.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return dst


def _section(rep: dict, factor: str) -> dict:
    return next(s for s in rep["sections"] if s["factor"] == factor)


def _narrative_text(section: dict) -> str:
    return " ".join(str(seg.get("text", "")) for seg in section["narrative"]["segments"])


@pytest.fixture(scope="module")
def review_info(tmp_path_factory):
    """Flow C on Robert with objective capital_preservation and an accepted drawdown of -5 % (O1 = info)."""
    work = tmp_path_factory.mktemp("o1_review")
    mp = pytest.MonkeyPatch()
    mp.setattr(bedrock, "COHERENCE_ANALYZER_LLM", "mock")
    mp.chdir(work)
    try:
        return pipeline.review(_fixture_copy(work, _raw(dd=-5)))
    finally:
        mp.undo()


@pytest.fixture(scope="module")
def review_skip(tmp_path_factory):
    """Flow C with a skipped objective."""
    work = tmp_path_factory.mktemp("o1_review_skip")
    mp = pytest.MonkeyPatch()
    mp.setattr(bedrock, "COHERENCE_ANALYZER_LLM", "mock")
    mp.chdir(work)
    try:
        return pipeline.review(_fixture_copy(work, intake.skip(_raw(dd=-12), "objectives.primary")))
    finally:
        mp.undo()


def test_flow_c_reports_o1_as_info_in_checks_current(review_info):
    """Flow C: O1 is in `checks_current`, `info`, tagged `objectif`, value = the report's own mdd."""
    res = _o1(review_info["checks_current"])
    assert len(res) == 1 and res[0]["status"] == "info" and res[0]["factor"] == "objectif"
    assert res[0]["value"] == pytest.approx(review_info["metrics"]["mdd"], abs=1e-9)


def test_flow_c_verdict_ignores_o1(review_info):
    """§20.4: the report verdict equals the one from the section statuses, and O1 does not move it."""
    others = [c for c in review_info["checks_current"] if c["id"] != "O1"]
    assert review_info["verdict"] == report_mod.verdict(others) == report_mod.sections_verdict(review_info["sections"])


def test_flow_c_objectif_section_lists_o1_and_no_longer_not_covered(review_info):
    s = _section(review_info, "objectif")
    assert "O1" in s["checks"]
    assert s["not_covered"] == [] and s["status"] in {"info", "pass"}
    assert s["facts"]["status"] == s["status"]


def test_flow_c_o1_row_is_in_the_objectif_facts_with_formatted_numbers(review_info):
    """§20.4: the writer sees pre-formatted strings; the O1 row carries the drawdown and the declared figure."""
    facts = _section(review_info, "objectif")["facts"]
    flat = json.dumps(facts, ensure_ascii=False)
    assert '"O1"' in flat, "the O1 row must be in the objectif section facts"
    assert "7,7" in flat or "7.7" in flat                      # the measured drawdown, formatted in the locale
    assert "-5" in flat or "−5" in flat or "5 %" in flat or "5%" in flat


def test_flow_c_narrative_numbers_exist_in_the_facts_and_stay_descriptive(review_info):
    """Invariant (numcheck) + D10: every number of the narration is in the facts; no advisory wording."""
    s = _section(review_info, "objectif")
    assert s["narrative"]["verify"]["passed"] is True
    text = _narrative_text(s)
    assert text
    assert numfacts.numcheck(text, s["facts"]) == []
    assert explain.numcheck(text, s["facts"]) == []
    low = text.lower()
    assert not any(w in low for w in FORBIDDEN)
    assert explain.advisory_hits(text) == []


def test_flow_c_with_a_skipped_objective_lists_o1_in_the_unknowns(review_skip):
    """P3.SKIP + D3: unknown O1 appears in `unknowns`, names the skipped path, and the verdict stays `coherent`."""
    res = _o1(review_skip["checks_current"])
    assert res and all(c["status"] == "unknown" for c in res)
    assert all("objectives.primary" in c["derived_from"] for c in res)
    assert "O1" in {u["id"] for u in review_skip["unknowns"]}
    assert review_skip["verdict"] == "coherent"
    assert not [c for c in review_skip["checks_current"] if c["status"] == "violation"]


# ---------------------------------------------------------------- Flow B: pipeline.check

@pytest.fixture(scope="module")
def flow_b(tmp_path_factory):
    """Flow B (NVDA from ZST) on the same profile: O1 is run on both sides."""
    work = tmp_path_factory.mktemp("o1_flow_b")
    mp = pytest.MonkeyPatch()
    mp.setattr(bedrock, "COHERENCE_ANALYZER_LLM", "mock")
    mp.chdir(work)
    try:
        return pipeline.check(_fixture_copy(work, _raw(dd=-5)), INTENT_ID, auto_confirm=True)
    finally:
        mp.undo()


def test_flow_b_runs_o1_on_the_current_and_the_proposed_portfolio(flow_b):
    """§4 Flow B: both check lists carry O1 (info), each with its own drawdown from its own metrics."""
    for side, mkey in (("checks_current", "metrics_current"), ("checks_proposed", "metrics_proposed")):
        res = _o1(flow_b[side])
        assert len(res) == 1, side
        assert res[0]["factor"] == "objectif"
        assert res[0]["status"] in {"info", "pass"}
        assert res[0]["status"] != "violation"
        assert res[0]["value"] == pytest.approx(flow_b[mkey]["mdd"], abs=1e-9), side


def test_flow_b_o1_never_changes_the_verdict(flow_b):
    """D3 / §7.5 / §20.4: the verdict is computed as if O1 were not there, and equals the sections' verdict."""
    others = [c for c in flow_b["checks_proposed"] if c["id"] != "O1"]
    assert flow_b["verdict"] == report_mod.verdict(others)
    assert flow_b["verdict"] == report_mod.sections_verdict(flow_b["sections"])
    assert "O1" in _section(flow_b, "objectif")["checks"]
    assert _section(flow_b, "objectif")["not_covered"] == []
