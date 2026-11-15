"""P3.SKIP: a skipped intake answer ({value: null, by: user:skipped}) gives `unknown` checks, never a crash.

Sources: docs/spec-factors.md §20.6 (a skipped field is `{value: null, by: user:skipped}` and the related checks
report `unknown`), docs/spec-decisions.md D3 (unknowns never change the verdict) and D7, SPEC §3.1, §7.1 (derived
fields), §7.2 (limits + limits_trace), §7.3 (hard checks), §7.5 (verdict), §1 inv. 1 (limits come only from `rule:`
fields and user-declared values) and inv. 3 (only hard checks give `violation`; never a guessed default).

For every path in `intake.REQUIRED` (15 paths), on a deep copy of Robert:
  intake.skip -> derive_all -> build_limits -> hard.run_all / run_cheap -> pipeline.review, with no exception.
A derived field or limit whose input is skipped is null / absent (never a default), `limits_trace` says which input was
skipped, every check that needs the missing value is `unknown` (never `pass`, never `violation`) and names the skipped
path in `derived_from`; everything that does not depend on the skipped path is exactly what the full profile gives.

Offline and deterministic: COHERENCE_ANALYZER_LLM=mock, fixed engine seed, reference date = portfolio.as_of (D1).
"""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from coherence_analyzer import config, data, engine, intake, pipeline, store
from coherence_analyzer.coherence import hard, report as report_mod
from coherence_analyzer.llm import bedrock
from coherence_analyzer.rules import derive, limits as limits_mod

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "fixtures" / "robert"
AS_OF = "2026-11-12"          # portfolio.as_of (D1)
ROBERT_VALUE = 400_000
ROBERT_HORIZON_M = 48
META_NAMES = ("asset_meta", "etf_sector_weights", "activity_tags")
STATUSES = {"pass", "violation", "info", "unknown"}

REQUIRED_PATHS = [p for factor in intake.FACTORS for p in intake.REQUIRED[factor]]

# ---------------------------------------------------------------- what each skipped path touches (SPEC §7.1-§7.3)
# Inputs of risk.effective (and so of every LIMITS_TABLE limit): horizon, withdrawals, emergency fund, expenses, declared.
TABLE_PATHS = {"objectives.horizon_years", "situation.planned_withdrawals", "situation.emergency_fund",
               "situation.monthly_expenses", "risk.declared"}
TABLE_LIMITS = {"vol_band_annual": "H7", "floor_at_horizon": "H9", "max_drawdown_pct": "H8",
                "min_fixed_income_weight": "H4", "max_single_stock_weight": "H3", "max_sector_risk_share": "H10",
                "max_crypto_weight": "H2", "max_foreign_currency_weight": "H5", "excluded_asset_types": "H1"}

# skipped path -> {limit name: check id} that can no longer be computed
AFFECTED_LIMITS: dict[str, dict[str, str]] = {p: dict(TABLE_LIMITS) for p in TABLE_PATHS}
AFFECTED_LIMITS["risk.max_acceptable_drawdown_pct"] = {"max_drawdown_pct": "H8"}
AFFECTED_LIMITS["preferences.asset_types_refused"] = {"excluded_asset_types": "H1"}

# skipped path -> check ids that must be `unknown` (limit missing, or the declared value the check reads is missing)
AFFECTED_CHECKS: dict[str, set[str]] = {p: set(v.values()) for p, v in AFFECTED_LIMITS.items()}
AFFECTED_CHECKS["identity.employer_sector"] = {"H6"}
AFFECTED_CHECKS["risk.reaction_to_20pct_drop"] = {"H14"}

# derived field -> skipped paths that make it impossible to compute (transitively)
_EF = {"situation.emergency_fund", "situation.monthly_expenses"}
_EH = {"objectives.horizon_years", "situation.planned_withdrawals"}
_CAP = _EH | _EF
DERIVED_DEPENDS: dict[str, set[str]] = {
    "identity.life_stage": {"identity.retirement_target_age"},
    "situation.income_stability": set(),
    "situation.emergency_fund_months": _EF,
    "objectives.effective_horizon_years": _EH,
    "risk.capacity": _CAP,
    "risk.effective": _CAP | {"risk.declared"},
}


# ---------------------------------------------------------------- helpers

@pytest.fixture(autouse=True)
def _mock_llm(monkeypatch):
    monkeypatch.setattr(bedrock, "COHERENCE_ANALYZER_LLM", "mock")


@pytest.fixture()
def robert() -> dict:
    """A deep copy of the Robert fixture profile (the fixture file is never written)."""
    return copy.deepcopy(_ROBERT_RAW)


_ROBERT_RAW = json.loads((FIX / "profile.json").read_text(encoding="utf-8"))


def _node_value(profile: dict, path: str):
    """Value at a dotted path; None when the node is absent, null, or `{value: null, ...}`."""
    node = profile
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node["value"] if isinstance(node, dict) and "value" in node else node


def _is_skip_marker(node) -> bool:
    return isinstance(node, dict) and node.get("by") == intake.SKIPPED_BY


def _contains_skip_marker(node) -> bool:
    if _is_skip_marker(node):
        return True
    if isinstance(node, dict):
        return any(_contains_skip_marker(v) for v in node.values())
    if isinstance(node, (list, tuple)):
        return any(_contains_skip_marker(v) for v in node)
    return False


def _prepare(profile: dict) -> tuple[dict, dict, dict]:
    """derive_all + build_limits like Flow A does. Returns (profile with limits + trace, limits, trace)."""
    p = derive.derive_all(profile, portfolio_value_cad=ROBERT_VALUE, as_of=AS_OF)
    lim, trace = limits_mod.build_limits(p, ROBERT_VALUE)
    p["limits"], p["limits_trace"] = lim, trace
    return p, lim, trace


def _fractions(weights_pct: dict[str, float]) -> dict[str, float]:
    return {t: v / 100 for t, v in weights_pct.items()}


def _by_id(checks: list, cid: str) -> list:
    return [c for c in checks if c["id"] == cid]


def _skip_entry(entries: list[str], path: str) -> bool:
    return any(isinstance(e, str) and path in e and "skip" in e.lower() for e in entries)


@pytest.fixture(scope="module")
def baseline() -> dict:
    return _prepare(copy.deepcopy(_ROBERT_RAW))[0]


@pytest.fixture(scope="module")
def assets():
    return data.load_assets()


@pytest.fixture(scope="module")
def meta() -> dict:
    return {n: data.load_meta(n) for n in META_NAMES}


@pytest.fixture(scope="module")
def metrics(market) -> dict:
    return engine.analyse(market, engine.ROBERT, ROBERT_HORIZON_M, ROBERT_VALUE)


@pytest.fixture(scope="module")
def baseline_checks(baseline, metrics, assets, meta) -> list:
    return hard.run_all(_fractions(engine.ROBERT), metrics, baseline, assets, meta)


# ---------------------------------------------------------------- the checklist itself

def test_the_checklist_has_the_15_required_paths_and_every_path_is_covered_by_the_tables_below():
    """§20.6: REQUIRED = 2 + 3 + 3 + 4 + 3 = 15 paths; this file parametrizes over all of them."""
    assert len(REQUIRED_PATHS) == 15 and len(set(REQUIRED_PATHS)) == 15
    assert set(AFFECTED_CHECKS) <= set(REQUIRED_PATHS)


# ---------------------------------------------------------------- derive (§7.1)

@pytest.mark.parametrize("path", REQUIRED_PATHS)
def test_derive_all_skipped_path_does_not_raise_and_does_not_mutate_the_input(robert, path):
    """A skipped required answer never crashes derive_all (§20.6) and the input profile is not mutated."""
    skipped = intake.skip(robert, path)
    before = copy.deepcopy(skipped)
    out = derive.derive_all(skipped, portfolio_value_cad=ROBERT_VALUE, as_of=AS_OF)
    assert isinstance(out, dict)
    assert skipped == before
    # the skip marker stays where the user put it: derive never overwrites a declared answer
    node = out
    for part in path.split("."):
        node = node[part]
    assert node == {"value": None, "by": intake.SKIPPED_BY}


@pytest.mark.parametrize("path", REQUIRED_PATHS)
def test_derived_fields_that_need_the_skipped_input_are_null_never_a_guess(robert, baseline, path):
    """§7.1 + task rule: a derived field whose input is skipped has value null (or is absent); the others keep the
    exact value they have on the full profile (no guessed default, no collateral change)."""
    out = derive.derive_all(intake.skip(robert, path), portfolio_value_cad=ROBERT_VALUE, as_of=AS_OF)
    for field, needs in DERIVED_DEPENDS.items():
        if path in needs:
            # SPEC-AMBIGUITY: strictest reading of "never a guessed default": the capacity / effective risk are null as
            # soon as one of their inputs is skipped, even when the missing notch could only lower the level.
            assert _node_value(out, field) is None, (path, field, _node_value(out, field))
        else:
            assert _node_value(out, field) == _node_value(baseline, field), (path, field)


@pytest.mark.parametrize("path", sorted(DERIVED_DEPENDS["risk.effective"]))
def test_skipping_a_risk_input_leaves_no_level_for_the_limits_table(robert, path):
    """§7.2: LIMITS_TABLE is indexed by risk.effective; with no effective level there is no row to read."""
    out = derive.derive_all(intake.skip(robert, path), portfolio_value_cad=ROBERT_VALUE, as_of=AS_OF)
    assert _node_value(out, "risk.effective") is None


def test_derived_fields_of_the_full_profile_are_unchanged_by_the_fix(baseline):
    """Golden anchor (§7.1, Robert): effective horizon 3.6, ef months 4.6, capacity and effective low_moderate."""
    assert _node_value(baseline, "objectives.effective_horizon_years") == pytest.approx(3.6, abs=1e-9)
    assert _node_value(baseline, "situation.emergency_fund_months") == pytest.approx(4.6, abs=1e-9)
    assert _node_value(baseline, "risk.capacity") == "low_moderate"
    assert _node_value(baseline, "risk.effective") == "low_moderate"


@pytest.mark.parametrize("path", REQUIRED_PATHS)
def test_answering_after_a_skip_restores_the_full_profile_derivation(robert, baseline, path):
    """Skip then answer (same value as the fixture) -> derive_all gives the same derived fields as the full profile:
    null nodes left by the skip do not stick."""
    original = copy.deepcopy(robert)
    node = original
    for part in path.split("."):
        node = node[part]
    skipped = intake.skip(robert, path)
    first = derive.derive_all(skipped, portfolio_value_cad=ROBERT_VALUE, as_of=AS_OF)
    answered = intake.answer(first, path, node)
    again = derive.derive_all(answered, portfolio_value_cad=ROBERT_VALUE, as_of=AS_OF)
    for field in DERIVED_DEPENDS:
        assert _node_value(again, field) == _node_value(baseline, field), (path, field)


# ---------------------------------------------------------------- limits + limits_trace (§7.2, inv. 1)

@pytest.mark.parametrize("path", REQUIRED_PATHS)
def test_build_limits_skipped_path_does_not_raise_and_drops_only_the_dependent_limits(robert, baseline, path):
    """A limit whose input is skipped is absent / null (never a default); every other limit equals the full profile's
    value; the profile is not mutated; no skip marker leaks into `limits`."""
    skipped_p = derive.derive_all(intake.skip(robert, path), portfolio_value_cad=ROBERT_VALUE, as_of=AS_OF)
    before = copy.deepcopy(skipped_p)
    lim, trace = limits_mod.build_limits(skipped_p, ROBERT_VALUE)
    assert skipped_p == before
    base_lim = baseline["limits"]
    assert set(lim) <= set(base_lim)                       # inv. 1: nothing new appears, only rule:/declared inputs
    assert not _contains_skip_marker(lim)
    affected = AFFECTED_LIMITS.get(path, {})
    for name, base_value in base_lim.items():
        if name in affected:
            assert lim.get(name) is None, (path, name, lim.get(name))
        else:
            assert lim.get(name) == base_value, (path, name)


@pytest.mark.parametrize("path", REQUIRED_PATHS)
def test_limits_trace_names_the_skipped_input_of_each_missing_limit(robert, baseline, path):
    """§7.2/§3.3: `limits_trace` keeps one entry per limit (same keys as the full profile). Each limit that could not
    be computed has an entry that names the skipped path and says it was skipped; limits that did not depend on it
    carry exactly the full profile's trace (no 'skipped' noise)."""
    skipped_p = derive.derive_all(intake.skip(robert, path), portfolio_value_cad=ROBERT_VALUE, as_of=AS_OF)
    _, trace = limits_mod.build_limits(skipped_p, ROBERT_VALUE)
    assert set(trace) == set(baseline["limits_trace"])
    assert all(isinstance(v, list) and all(isinstance(e, str) for e in v) for v in trace.values())
    affected = AFFECTED_LIMITS.get(path, {})
    for name, entries in trace.items():
        if name in affected:
            # SPEC-AMBIGUITY: the trace format for a skipped input is not fixed; the stricter reading is one entry that
            # contains both the skipped path and the word "skip" (e.g. "objectives.horizon_years (skipped)").
            assert _skip_entry(entries, path), (path, name, entries)
        else:
            assert entries == baseline["limits_trace"][name], (path, name)
            assert not any("skip" in e.lower() for e in entries)


def test_limits_of_the_full_profile_have_no_skip_entry_in_the_trace(baseline):
    """Control: with nothing skipped, no trace entry talks about a skip, and the §7.2 Robert limits are unchanged."""
    assert not any("skip" in e.lower() for entries in baseline["limits_trace"].values() for e in entries)
    assert baseline["limits"]["max_single_stock_weight"] == 0.08
    assert baseline["limits"]["min_fixed_income_weight"] == 0.30
    assert baseline["limits"]["floor_at_horizon"] == pytest.approx(360_000, abs=1e-9)
    assert baseline["limits"]["max_drawdown_pct"] == -12


@pytest.mark.parametrize("path", REQUIRED_PATHS)
def test_the_refreshed_profile_with_a_skip_still_matches_the_profile_schema(robert, path):
    """§3.3: the profile is validated on every save. The derived + limits profile of a skipped answer must validate,
    otherwise Flow A could not store the version and POST /review would fail."""
    p, _, _ = _prepare(intake.skip(robert, path))
    pipeline.validate_profile(store.plain(p), f"skipped {path}")


# ---------------------------------------------------------------- hard checks (§7.3, inv. 3, D3)

def _skipped_profile(robert: dict, path: str) -> dict:
    return _prepare(intake.skip(robert, path))[0]


def _assert_affected_are_unknown(checks: list, path: str, ids: set[str]) -> None:
    for cid in sorted(ids):
        found = _by_id(checks, cid)
        assert found, (path, cid, "no result at all")
        for c in found:
            # never pass, never violation, never info: the input is missing, so the check cannot say anything
            assert c["status"] == "unknown", (path, cid, c)
            assert c["kind"] == "hard"
            assert c["limit"] is None or not isinstance(c["limit"], dict)
            # SPEC-AMBIGUITY: "naming the skipped path" = the check's `derived_from` lists it (§7.3 key).
            assert path in c["derived_from"], (path, cid, c["derived_from"])


@pytest.mark.parametrize("path", REQUIRED_PATHS)
def test_run_all_skipped_path_gives_unknown_for_every_check_that_needs_it(robert, path, metrics, assets, meta):
    """§7.3 + §20.6: hard.run_all runs without exception; each check that needs the skipped input is `unknown`
    (all its results), with the skipped path in derived_from."""
    p = _skipped_profile(robert, path)
    checks = hard.run_all(_fractions(engine.ROBERT), metrics, p, assets, meta)
    assert checks and all(c["status"] in STATUSES for c in checks)
    _assert_affected_are_unknown(checks, path, AFFECTED_CHECKS.get(path, set()))


@pytest.mark.parametrize("path", REQUIRED_PATHS)
def test_run_all_skipped_path_leaves_every_other_check_as_on_the_full_profile(
        robert, path, metrics, assets, meta, baseline_checks):
    """Checks that do not depend on the skipped path give the same results as the full profile (status, subject,
    value, limit): a skip never invents a violation and never hides an unrelated one (Robert current: none)."""
    p = _skipped_profile(robert, path)
    checks = hard.run_all(_fractions(engine.ROBERT), metrics, p, assets, meta)
    affected = AFFECTED_CHECKS.get(path, set())
    keep = lambda cs: [(c["id"], c["status"], c["subject"], c["value"], c["limit"])  # noqa: E731
                       for c in cs if c["id"] not in affected]
    assert keep(checks) == keep(baseline_checks)
    assert not [c for c in checks if c["status"] == "violation"]       # inv. 3: unknown is not a violation


@pytest.mark.parametrize("path", REQUIRED_PATHS)
def test_run_cheap_skipped_path_does_not_raise_and_marks_the_cheap_checks_unknown(robert, path, assets, meta):
    """The resizer scan (`run_cheap`, no engine) is also safe on a skipped profile (§8, §20.6)."""
    p = _skipped_profile(robert, path)
    checks = hard.run_cheap(_fractions(engine.ROBERT), p, assets, meta)
    assert checks and all(c["status"] in STATUSES for c in checks)
    cheap_ids = set(hard.CHEAP)
    _assert_affected_are_unknown(checks, path, AFFECTED_CHECKS.get(path, set()) & cheap_ids)
    assert not [c for c in checks if c["status"] == "violation"]


@pytest.mark.parametrize("path", REQUIRED_PATHS)
def test_verdict_ignores_the_unknowns_that_a_skip_creates(robert, path, metrics, assets, meta):
    """D3 / §7.5: unknown never changes the verdict. Robert's current portfolio is `coherent`; skipping a question
    keeps it `coherent`, with the new unknowns listed (report_mod.unknowns)."""
    p = _skipped_profile(robert, path)
    checks = hard.run_all(_fractions(engine.ROBERT), metrics, p, assets, meta)
    assert report_mod.verdict(checks) == "coherent"
    known = [c for c in checks if c["status"] != "unknown"]
    assert report_mod.verdict(known) == report_mod.verdict(checks)
    listed = {c["id"] for c in report_mod.unknowns(checks)}
    assert AFFECTED_CHECKS.get(path, set()) <= listed


# ---------------------------------------------------------------- full pipeline (Flow C) on a skipped profile

FORBIDDEN_WORDS = ("consider", "you could", "rebalance", "next step", "vous pourriez", "envisagez")   # D10


@pytest.fixture(scope="module")
def report_schema() -> dict:
    return json.loads((config.SCHEMAS_DIR / "report.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module", params=REQUIRED_PATHS)
def review_skipped(request, tmp_path_factory):
    """pipeline.review on a copy of fixtures/robert whose profile.json has `request.param` skipped. Offline (mock LLM,
    fixed seed); the store writes into a temp dir; the fixtures are never written."""
    path = request.param
    work = tmp_path_factory.mktemp("skip_" + path.replace(".", "_"))
    fixture_dir = work / "robert"
    shutil.copytree(FIX, fixture_dir)
    skipped = intake.skip(copy.deepcopy(_ROBERT_RAW), path)
    (fixture_dir / "profile.json").write_text(json.dumps(skipped, indent=2), encoding="utf-8")
    mp = pytest.MonkeyPatch()
    mp.setattr(bedrock, "COHERENCE_ANALYZER_LLM", "mock")
    mp.chdir(work)
    try:
        rep = pipeline.review(fixture_dir)
    finally:
        mp.undo()
    return path, rep


def test_review_with_a_skipped_answer_returns_a_schema_valid_report(review_skipped, report_schema):
    """§4 Flow C + §13: POST /review after intake/skip must not fail; the report keeps its schema."""
    path, rep = review_skipped
    assert isinstance(rep, dict) and rep["flow"] == "C" and rep["portfolio_id"] == "robert-main"
    errors = [e.message for e in Draft202012Validator(report_schema).iter_errors(rep)]
    assert not errors, (path, errors[:3])
    assert [s["factor"] for s in rep["sections"]] == list(intake.FACTORS)


def test_review_with_a_skipped_answer_lists_the_unknowns_and_keeps_the_verdict(review_skipped):
    """D3 + §7.5: Robert's current portfolio is `coherent`; a skipped answer adds unknowns (listed in
    `unknowns`, status `unknown`, naming the skipped path), never a violation, never a verdict change."""
    path, rep = review_skipped
    assert rep["verdict"] == "coherent"
    assert not [c for c in rep["checks_current"] if c["status"] == "violation"]
    listed = {u["id"] for u in rep["unknowns"]}
    for cid in sorted(AFFECTED_CHECKS.get(path, set())):
        found = _by_id(rep["checks_current"], cid)
        assert found, (path, cid)
        for c in found:
            assert c["status"] == "unknown", (path, cid, c)
            assert path in c["derived_from"], (path, cid, c["derived_from"])
        assert cid in listed
    # every `unknowns` entry is a check with status unknown (what we can't verify), whatever the verdict
    assert all(u["status"] == "unknown" for u in rep["unknowns"])


def test_review_with_a_skipped_answer_states_no_guessed_limit_and_stays_descriptive(review_skipped):
    """Inv. 1 + D10: the review does not turn a skipped input into a number (limit of an affected check is null) and
    the explanation text contains no suggestion wording."""
    path, rep = review_skipped
    for cid in set(AFFECTED_LIMITS.get(path, {}).values()):
        for c in _by_id(rep["checks_current"], cid):
            assert c["status"] == "unknown" and c["limit"] is None, (path, cid, c)
    text = " ".join(seg["text"] for seg in rep["explanation"]["segments"]).lower()
    assert not any(w in text for w in FORBIDDEN_WORDS), (path, text)


def test_the_robert_fixture_files_are_untouched_by_the_skip_runs():
    """The skip tests run on copies: fixtures/robert/profile.json still has no skip marker."""
    assert not _contains_skip_marker(json.loads((FIX / "profile.json").read_text(encoding="utf-8")))
