"""Flow A (rules + N3) and Flow C review + cli review/demo, end-to-end offline (P0.9).

Sources: docs/spec-decisions.md D7 (wins over SPEC §4/§12/§17), D1, D3, D5, D6; docs/spec-factors.md §20.4-20.5;
SPEC §4 Flow A / Flow C, §10 audit, §12 CLI, §13 report schema, §15 test_pipeline_offline.

Per D7 the pipeline wires no N1/N2 (and no N4: Flow B is P2). The only LLM nodes a run may call are N3
`values_parser` (Flow A, only when `values.notes` changed) and N5 `explainer` (one call per non-pass section, at most
two). Everything runs with COHERENCE_ANALYZER_LLM=mock: no network, no clock, fixed engine seed.

The report is read from the file `store.save_report` returned (the test wraps that function), and the audit with
`store.load_audit(run_id)`; both are read while the working directory is a temp dir, so nothing is written next to
the repo fixtures. Measured context (tests/test_factors.py, D2/D7):
  Robert + NVDA 10% (`engine.ROBERT_NVDA`, 48 months, 400 000 $): H3, H4, H7, H8, H9, H10 violation; H6, H10 unknown.
  Robert current (`engine.ROBERT`): H6, H10 unknown only; verdict `coherent`.
"""
import contextlib
import copy
import io
import json
import os
import re
import shutil
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

from coherence_analyzer import cli, config, data, engine, explain, factors, pipeline, store  # noqa: E402
from coherence_analyzer.coherence import hard, report as report_mod  # noqa: E402
from coherence_analyzer.llm import bedrock  # noqa: E402
from coherence_analyzer.rules import derive, limits as limits_mod  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "fixtures" / "robert"
AS_OF = "2026-11-12"
ROBERT_VALUE = 400_000
ROBERT_HORIZON_M = 48
META_NAMES = ("asset_meta", "etf_sector_weights", "activity_tags")

DISPLAY = ("objectif", "horizon", "risque", "contraintes", "preferences")
VERDICTS = ("coherent", "coherent_with_warnings", "incoherent")
MODES = ("llm", "llm_retry", "template")
MODE_RANK = {"llm": 0, "llm_retry": 1, "template": 2}
SECTION_KEYS = {"factor", "title", "question", "status", "checks", "unknowns", "not_covered", "facts", "narrative"}
CHECK_KEYS = {"id", "kind", "status", "subject", "value", "limit", "unit", "derived_from", "source", "confidence",
              "message_key"}
LLM_CALL_KEYS = {"node", "model_id", "prompt_version", "input_hash", "cache_hit", "latency_ms", "tokens_in",
                 "tokens_out"}
ALLOWED_NODES = {"explainer", "values_parser"}      # D7: no N1 / N2 / N4 in the P0 pipeline
FR_DISCLAIMER = ("Simulation éducative avec des profils fictifs. Ceci n'est pas un conseil financier personnalisé. "
                 "Aucune transaction n'est exécutée.")                                    # SPEC §9.4
ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")                                           # SPEC §4: run_id is a ULID
ISO_TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")

_REAL_SAVE_REPORT = store.save_report
_REAL_CALL_TOOL = bedrock.call_tool
_FIXTURE_SNAPSHOT = {p.name: p.read_bytes() for p in FIX.iterdir() if p.is_file()}      # taken before any run


# ---------------------------------------------------------------- helpers

def _fractions(w_pct: dict) -> dict:
    return {t: v / 100 for t, v in w_pct.items()}


def _read(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _run(argv: list[str], cwd: Path) -> dict:
    """Run `cli.main(argv)` in `cwd`, offline, and capture what it wrote.

    Returns {texts: report file texts, reports: parsed reports, audits: {run_id: audit}, calls: every
    `bedrock.call_tool` call (node, payload, call_log), stdout, audit_stdout: `cli audit <run_id>` of the first
    report}. The report is read inside the `save_report` wrapper and the audit before the cwd is restored.
    """
    mp = pytest.MonkeyPatch()
    texts: list[str] = []
    calls: list[dict] = []

    def save_report(rep):
        path = _REAL_SAVE_REPORT(rep)
        texts.append(Path(path).read_text(encoding="utf-8"))
        return path

    def call_tool(node, schema, system, user_payload, prompt_version):
        result = _REAL_CALL_TOOL(node, schema, system, user_payload, prompt_version)
        calls.append({"node": node, "payload": copy.deepcopy(user_payload), "call_log": copy.deepcopy(result["call_log"])})
        return result

    mp.setattr(store, "save_report", save_report)
    mp.setattr(bedrock, "call_tool", call_tool)
    mp.chdir(cwd)
    out, audit_out = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            cli.main(argv)
        reports = [json.loads(t) for t in texts]
        audits = {r["run_id"]: store.load_audit(r["run_id"]) for r in reports}
        if reports:
            with contextlib.redirect_stdout(audit_out):
                cli.main(["audit", reports[0]["run_id"]])
    finally:
        mp.undo()
    return {"texts": texts, "reports": reports, "audits": audits, "calls": calls, "stdout": out.getvalue(),
            "audit_stdout": audit_out.getvalue()}


def _only(run: dict) -> tuple[dict, dict]:
    assert len(run["reports"]) == 1, "one review run writes exactly one report"
    rep = run["reports"][0]
    return rep, run["audits"][rep["run_id"]]


def _normalise(text: str, run_id: str) -> str:
    """The text of a report with the run_id and every ISO timestamp masked (the only allowed differences)."""
    return ISO_TS.sub("<TS>", text.replace(run_id, "<RUN_ID>"))


def _section_modes(node):
    """Find the per-section narration modes in an audit record, whatever the key is called.

    SPEC-AMBIGUITY: SPEC §10 does not name the key. Accepted shapes: {factor: mode} or [{factor, mode}], anywhere
    in the record. Returns {factor: mode} or None.
    """
    if isinstance(node, dict):
        if set(node) >= set(DISPLAY) and all(isinstance(node[f], str) for f in DISPLAY):
            return {f: node[f] for f in DISPLAY}
        if set(node) >= set(DISPLAY) and all(isinstance(node[f], dict) and "mode" in node[f] for f in DISPLAY):
            return {f: node[f]["mode"] for f in DISPLAY}
        for v in node.values():
            found = _section_modes(v)
            if found:
                return found
    elif isinstance(node, list):
        if node and all(isinstance(x, dict) and "factor" in x and "mode" in x for x in node):
            return {x["factor"]: x["mode"] for x in node}
        for v in node:
            found = _section_modes(v)
            if found:
                return found
    return None


# ---------------------------------------------------------------- expected values (same code path as test_factors)

@pytest.fixture(scope="module")
def raw_profile() -> dict:
    return _read(FIX / "profile.json")


@pytest.fixture(scope="module")
def profile(raw_profile) -> dict:
    p = derive.derive_all(copy.deepcopy(raw_profile), portfolio_value_cad=ROBERT_VALUE, as_of=AS_OF)
    lim, trace = limits_mod.build_limits(p, ROBERT_VALUE)
    p["limits"], p["limits_trace"] = lim, trace
    return p


@pytest.fixture(scope="module")
def expected_nvda(profile, market) -> dict:
    metrics = engine.analyse(market, engine.ROBERT_NVDA, ROBERT_HORIZON_M, ROBERT_VALUE)
    checks = hard.run_all(_fractions(engine.ROBERT_NVDA), metrics, profile, data.load_assets(),
                          {n: data.load_meta(n) for n in META_NAMES})
    return {"metrics": metrics, "checks": checks}


@pytest.fixture(scope="module")
def expected_base(profile, market) -> dict:
    metrics = engine.analyse(market, engine.ROBERT, ROBERT_HORIZON_M, ROBERT_VALUE)
    checks = hard.run_all(_fractions(engine.ROBERT), metrics, profile, data.load_assets(),
                          {n: data.load_meta(n) for n in META_NAMES})
    return {"metrics": metrics, "checks": checks}


@pytest.fixture(scope="module")
def report_schema() -> dict:
    schema = json.loads((config.SCHEMAS_DIR / "report.json").read_text(encoding="utf-8"))   # SPEC §13
    Draft202012Validator.check_schema(schema)
    return schema


# ---------------------------------------------------------------- the runs (module scoped: each is one engine run)

@pytest.fixture(scope="module")
def review_nvda(tmp_path_factory) -> dict:
    """`cli review fixtures/robert --portfolio portfolio_review.json` (acceptance, D7 point 5)."""
    return _run(["review", str(FIX), "--portfolio", "portfolio_review.json"], tmp_path_factory.mktemp("nvda1"))


@pytest.fixture(scope="module")
def review_nvda_again(tmp_path_factory) -> dict:
    return _run(["review", str(FIX), "--portfolio", "portfolio_review.json"], tmp_path_factory.mktemp("nvda2"))


@pytest.fixture(scope="module")
def review_current(tmp_path_factory) -> dict:
    """`cli review fixtures/robert` (the current portfolio, fixtures/robert/portfolio.json)."""
    return _run(["review", str(FIX)], tmp_path_factory.mktemp("current"))


# ---------------------------------------------------------------- fixtures: portfolio_review.json (D7 point 5)

def test_portfolio_review_fixture_holds_the_robert_nvda_weights():
    """D7 §5: fixtures/robert/portfolio_review.json = ROBERT_NVDA weights ("already bought NVDA"); portfolio.json is untouched."""
    review = _read(FIX / "portfolio_review.json")
    assert review["weights_pct"] == engine.ROBERT_NVDA
    assert sum(review["weights_pct"].values()) == pytest.approx(100, abs=0.01)           # SPEC §3.5
    assert review["profile_id"] == "persona-robert"
    assert review["as_of"] == AS_OF
    assert review["value_cad"] == ROBERT_VALUE
    assert isinstance(review["portfolio_id"], str) and review["portfolio_id"]
    base = _read(FIX / "portfolio.json")
    assert base["weights_pct"] == engine.ROBERT                                          # frozen tests read it
    assert base["portfolio_id"] == "robert-main"
    assert review["portfolio_id"] != base["portfolio_id"]
    loaded = data.load_portfolio(FIX / "portfolio_review.json", data.load_assets())      # known tickers, sum 100
    assert loaded["weights"]["NVDA"] == pytest.approx(0.10, abs=1e-9)
    assert loaded["weights"]["ZST.TO"] == pytest.approx(0.03, abs=1e-9)


# ---------------------------------------------------------------- Flow C on the NVDA portfolio: report

def test_review_nvda_report_is_schema_valid_and_has_the_section_13_shape(review_nvda, report_schema):
    """§13 + §20.4: the saved report validates against schemas/report.json and carries every §13 key; Flow C has no intent."""
    rep, _ = _only(review_nvda)
    errors = sorted(Draft202012Validator(report_schema).iter_errors(rep), key=lambda e: list(e.absolute_path))
    assert errors == [], [e.message for e in errors]
    for key in ("run_id", "flow", "profile_id", "profile_version", "portfolio_id", "intent", "verdict", "checks_current",
                "checks_proposed", "diff", "metrics_current", "metrics_proposed", "alternatives", "unknowns",
                "assumptions", "data_window", "explanation", "disclaimer", "sections"):
        assert key in rep, key
    assert ULID.match(rep["run_id"])                                                     # SPEC §4: run_id is a ULID
    assert rep["flow"] == "C"                                                            # SPEC §4 flow labels
    assert rep["profile_id"] == "persona-robert"
    assert rep["portfolio_id"] == _read(FIX / "portfolio_review.json")["portfolio_id"]
    assert isinstance(rep["profile_version"], int) and rep["profile_version"] >= 1
    # Flow C is "B5-B9 without an intent"; the resizer and the trade diff are P2 (D7 point 3)
    assert rep["intent"] is None and rep["checks_proposed"] is None
    assert rep["metrics_proposed"] is None and rep["diff"] is None
    assert rep["alternatives"] == []
    assert rep["data_window"] == ["2022-01-31", "2026-10-31"]                            # D4: 58 months
    assert all(isinstance(a, str) and a for a in rep["assumptions"]) and rep["assumptions"]
    assert any("survivorship" in a.lower() for a in rep["assumptions"])                  # §3.1 / §5 limitation shown


def test_review_nvda_verdict_and_sections_in_display_order(review_nvda):
    """D7 acceptance + §20.4: verdict incoherent; five sections in display order; statuses horizon/risque/contraintes
    violation, objectif/preferences pass; the verdict equals the one derived from the section statuses."""
    rep, _ = _only(review_nvda)
    assert rep["verdict"] == "incoherent"
    assert [s["factor"] for s in rep["sections"]] == list(DISPLAY)
    status = {s["factor"]: s["status"] for s in rep["sections"]}
    assert status == {"objectif": "pass", "horizon": "violation", "risque": "violation",
                      "contraintes": "violation", "preferences": "pass"}
    assert report_mod.sections_verdict(rep["sections"]) == rep["verdict"]                # §20.4 consistency
    for s in rep["sections"]:
        assert SECTION_KEYS <= set(s), s["factor"]
        assert s["facts"]["factor"] == s["factor"] and s["facts"]["status"] == s["status"]
        assert s["facts"]["verdict"] == "incoherent" and s["facts"]["intent"] is None
        assert s["facts"]["locale"] == "fr-CA"                                           # profile meta.locale
        assert all(factors.CHECK_FACTOR[cid] == s["factor"] for cid in s["checks"])      # ids owned by the section
    by = {s["factor"]: s for s in rep["sections"]}
    assert by["horizon"]["checks"] == ["H9"]
    assert {"H4", "H7", "H8"} <= set(by["risque"]["checks"])
    assert {"H3", "H10"} <= set(by["contraintes"]["checks"])
    for fid, ids in (("horizon", {"H9"}), ("risque", {"H4", "H7", "H8"}), ("contraintes", {"H3", "H10"})):
        viol = {c["id"] for c in by[fid]["facts"]["checks"] if c["status"] == "violation"}
        assert viol == ids, fid                                                          # §20.4: facts list non-pass checks


def test_review_nvda_checks_match_the_hard_checks_and_carry_a_factor_key(review_nvda, expected_nvda):
    """D7 §5 + §20.4: checks_current = hard checks of engine.ROBERT_NVDA (+ soft S2/S4 only); every check has `factor`."""
    rep, _ = _only(review_nvda)
    checks = rep["checks_current"]
    assert all(c["factor"] == factors.CHECK_FACTOR[c["id"]] for c in checks)             # every check has a factor key
    hard_rep = [c for c in checks if c["kind"] == "hard"]
    assert all(CHECK_KEYS <= set(c) for c in hard_rep)                                   # §7.3 keys
    key = lambda c: (c["id"], c.get("subject") or "", c["status"])  # noqa: E731
    assert sorted(map(key, hard_rep)) == sorted(map(key, expected_nvda["checks"]))
    exp = {key(c): c for c in expected_nvda["checks"]}
    for c in hard_rep:
        e = exp[key(c)]
        for f in ("value", "limit"):
            if isinstance(e.get(f), (int, float)):
                assert c[f] == pytest.approx(e[f], abs=1e-9), (c["id"], f)
        assert c["derived_from"] == e["derived_from"]
    viol = {c["id"] for c in checks if c["status"] == "violation"}
    assert viol == {"H3", "H4", "H7", "H8", "H9", "H10"}                                 # D7 point 5
    h3 = next(c for c in checks if c["id"] == "H3")
    assert h3["subject"] == "NVDA"
    assert h3["value"] == pytest.approx(0.10, abs=1e-9) and h3["limit"] == pytest.approx(0.08, abs=1e-9)
    h4 = next(c for c in checks if c["id"] == "H4")
    assert h4["value"] == pytest.approx(0.25, abs=1e-9) and h4["limit"] == pytest.approx(0.30, abs=1e-9)
    # inv. 3: only hard checks produce `violation`; D7: S1/S3 need N1, which is not in the pipeline
    for c in checks:
        if c["kind"] == "soft":
            assert c["status"] in ("pass", "warning", "info", "unknown"), c["id"]
            assert c["id"] in ("S2", "S4"), c["id"]
    assert [u["id"] for u in rep["unknowns"]] == [c["id"] for c in checks if c["status"] == "unknown"]
    assert all(u["status"] == "unknown" for u in rep["unknowns"])                        # D3: always listed


def test_review_nvda_metrics_are_the_engine_values(review_nvda, expected_nvda):
    """§5/§13: metrics_current is engine.analyse(ROBERT_NVDA, 48 months, 400 000 $) with the fixed seed (1e-9)."""
    rep, _ = _only(review_nvda)
    got, exp = rep["metrics_current"], expected_nvda["metrics"]
    for k in ("vol", "vol_local", "mdd", "loss_prob", "width", "p5_T", "p50_T", "p95_T", "corr_eq_bond"):
        assert got[k] == pytest.approx(exp[k], abs=1e-9), k
    assert got["mdd_date"] == exp["mdd_date"]
    for p in ("p5", "p25", "p50", "p75", "p95"):
        assert got["cone"][p] == pytest.approx(exp["cone"][p], abs=1e-9), p
    assert got["p5_T"] < 360_000                                                         # H9 floor (D2 context)
    assert set(exp["assumptions"]) <= set(rep["assumptions"])


# ---------------------------------------------------------------- narration (§20.5) and the fixed text

def test_review_nvda_narration_modes_are_recorded_per_section_and_verified(review_nvda):
    """§20.5: each section records its narrative mode; pass sections never use the writer; nothing ships unverified."""
    rep, _ = _only(review_nvda)
    for s in rep["sections"]:
        nar = s["narrative"]
        assert nar["mode"] in MODES, s["factor"]
        assert 1 <= len(nar["segments"]) <= explain.MAX_SEGMENTS                         # V5
        assert nar["verify"] == {"passed": True, "failures": []}, s["factor"]
        assert explain.verify_section({"segments": nar["segments"]}, s["facts"]) == [], s["factor"]
        if s["status"] == "pass":
            assert nar["mode"] == "template", s["factor"]                                # pass sections: no LLM
        if nar["mode"] == "template":                                                    # deterministic fallback text
            assert nar["segments"] == explain.template_section(s["facts"])["segments"]
        for seg in nar["segments"]:
            assert seg["kind"] in ("fact", "assumption", "inference", "unknown")         # inv. 9: labelled
            assert seg["refs"]
    # D5 / Flow C: the three failing sections cite the checks that fail in them (V3)
    by = {s["factor"]: s for s in rep["sections"]}
    for fid, ids in (("horizon", {"H9"}), ("risque", {"H4", "H7", "H8"}), ("contraintes", {"H3", "H10"})):
        refs = {r for seg in by[fid]["narrative"]["segments"] for r in seg["refs"]}
        assert ids <= refs, fid


def test_review_nvda_explanation_is_template_headline_section_segments_and_fixed_disclaimer(review_nvda):
    """§20.5 report-level text: headline = template; segments = section segments in display order with a `factor`
    key; mode = worst non-pass section mode; disclaimer = fixed §9.4 text (inv. 7), also at the report level."""
    rep, _ = _only(review_nvda)
    ex = rep["explanation"]
    assert ex["headline"] == explain.headline(rep["verdict"], rep["sections"], "fr-CA")
    assert len(ex["headline"]) <= explain.MAX_HEADLINE_CHARS
    expected = [{"factor": s["factor"], **seg} for s in rep["sections"] for seg in s["narrative"]["segments"]]
    assert ex["segments"] == expected
    non_pass = [s["narrative"]["mode"] for s in rep["sections"] if s["status"] != "pass"]
    assert ex["mode"] == max(non_pass, key=MODE_RANK.__getitem__)
    assert ex["disclaimer"] == rep["disclaimer"] == config.DISCLAIMER["fr-CA"] == FR_DISCLAIMER
    for seg in ex["segments"]:                                                           # inv. 6: numbers in the facts
        facts = next(s["facts"] for s in rep["sections"] if s["factor"] == seg["factor"])
        assert explain.numcheck(seg["text"], facts) == []
        low = seg["text"].casefold()
        assert not any(p in low for p in explain.ADVISORY_BLOCKLIST)                     # inv. 7: no advice


# ---------------------------------------------------------------- LLM calls and audit (§10, D6, D7)

def test_review_nvda_llm_calls_are_explainer_only_for_the_three_failing_sections(review_nvda):
    """D7: no N1/N2/N4 in the pipeline. Robert's notes are unchanged, so only N5 runs: one writer call per non-pass
    section, at most two (§20.5), never for a pass section; the cache is hit (mock)."""
    nodes = {c["node"] for c in review_nvda["calls"]}
    assert nodes and nodes <= ALLOWED_NODES, nodes
    assert not nodes & {"behaviour_classifier", "question_rubric", "intent_parser"}
    explainer = [c for c in review_nvda["calls"] if c["node"] == "explainer"]
    per_factor: dict[str, int] = {}
    for c in explainer:
        assert c["payload"]["factor"] in ("horizon", "risque", "contraintes")
        assert c["payload"]["status"] != "pass"
        per_factor[c["payload"]["factor"]] = per_factor.get(c["payload"]["factor"], 0) + 1
    assert set(per_factor) <= {"horizon", "risque", "contraintes"}
    assert all(1 <= n <= 2 for n in per_factor.values())                                 # one retry at most
    assert len(explainer) <= 6                                                           # 2 x 3 non-pass sections
    assert all(c["call_log"]["cache_hit"] is True for c in review_nvda["calls"])         # mock: recorded responses only


def test_review_nvda_audit_record(review_nvda):
    """§10 + D1 + D6 + D7 acceptance: the audit record has run_id/flow/profile ids, engine params, the as_of date, the
    model id, every LLM call, the verdict and explanation mode, and the narration mode of each section."""
    rep, audit = _only(review_nvda)
    assert audit["run_id"] == rep["run_id"]
    assert audit["flow"] == "C" and audit["profile_id"] == "persona-robert"
    for k in ("started_at", "ended_at", "profile_version_in", "profile_version_out", "events_considered",
              "llm_calls", "profile_diff", "engine", "verdict", "explanation_mode"):
        assert k in audit, k
    assert audit["verdict"] == rep["verdict"] == "incoherent"
    assert audit["explanation_mode"] == rep["explanation"]["mode"]
    assert audit["engine"]["seed"] == config.ENGINE_SEED == 7
    assert audit["engine"]["n"] == config.ENGINE_N == 5000
    assert audit["engine"]["block"] == config.ENGINE_BLOCK == 12
    assert audit["engine"]["drift"] == "assumed"
    assert audit["engine"]["data_window"] == rep["data_window"]
    assert isinstance(audit["model_id"], str) and audit["model_id"]                      # D6: model id in every record
    assert AS_OF in json.dumps(audit)                                                    # D1: the reference date used
    # SPEC-AMBIGUITY: "audit has model_id" is read as a top-level key AND on every llm_calls entry (stricter).
    calls = audit["llm_calls"]
    assert [c["node"] for c in calls] == [c["node"] for c in review_nvda["calls"]]      # every call, in order
    for c, spy in zip(calls, review_nvda["calls"]):
        assert LLM_CALL_KEYS <= set(c)
        assert c["model_id"] and c["cache_hit"] is True
        assert c["input_hash"] == bedrock.input_hash(spy["payload"])
        assert c["prompt_version"] == spy["call_log"]["prompt_version"]
    modes = _section_modes({k: v for k, v in audit.items() if k != "llm_calls"})
    assert modes is not None, "the audit record must list the narration mode of each section"
    assert modes == {s["factor"]: s["narrative"]["mode"] for s in rep["sections"]}


def test_cli_audit_prints_the_record(review_nvda):
    """§12: `cli audit <run_id>` shows the record of that run (the "show your work" screen)."""
    rep, _ = _only(review_nvda)
    assert rep["run_id"] in review_nvda["audit_stdout"]
    assert "model_id" in review_nvda["audit_stdout"]


def test_review_nvda_run_leaves_the_fixtures_unchanged(review_nvda):
    """Flow C must not rewrite or add files in fixtures/robert (frozen tests read them); versions go to the store (§10)."""
    now = {p.name: p.read_bytes() for p in FIX.iterdir() if p.is_file()}
    assert now == _FIXTURE_SNAPSHOT
    assert _read(FIX / "portfolio_review.json")["weights_pct"] == engine.ROBERT_NVDA


# ---------------------------------------------------------------- reproducibility (inv. 8)

def test_running_the_review_twice_gives_byte_identical_reports_except_run_id_and_timestamps(review_nvda, review_nvda_again):
    """D7 acceptance: two runs differ only by run_id and timestamps (mock cache + fixed seed, inv. 8)."""
    a, _ = _only(review_nvda)
    b, _ = _only(review_nvda_again)
    assert a["run_id"] != b["run_id"] and ULID.match(a["run_id"]) and ULID.match(b["run_id"])
    ta = _normalise(review_nvda["texts"][0], a["run_id"])
    tb = _normalise(review_nvda_again["texts"][0], b["run_id"])
    assert ta == tb
    assert json.loads(ta) == json.loads(tb)                                              # and as parsed documents


# ---------------------------------------------------------------- Flow C on the current portfolio

def test_review_current_portfolio_is_coherent_with_h6_h10_unknown(review_current, expected_base, report_schema):
    """D7 acceptance + D3: `cli review fixtures/robert` -> verdict coherent, H6 and H10 listed in unknowns, no violation;
    sections: contraintes unknown, every other section pass (§20.4 ranking); metrics are the engine's for ROBERT."""
    rep, audit = _only(review_current)
    assert Draft202012Validator(report_schema).is_valid(rep)
    assert rep["verdict"] == audit["verdict"] == "coherent"
    assert rep["portfolio_id"] == "robert-main" and rep["flow"] == "C"
    unknown_ids = {u["id"] for u in rep["unknowns"]}
    assert {"H6", "H10"} <= unknown_ids
    assert unknown_ids == {c["id"] for c in expected_base["checks"] if c["status"] == "unknown"}
    assert all(c["status"] not in ("violation", "warning") for c in rep["checks_current"])
    assert all(c["factor"] == factors.CHECK_FACTOR[c["id"]] for c in rep["checks_current"])
    assert [s["factor"] for s in rep["sections"]] == list(DISPLAY)
    assert {s["factor"]: s["status"] for s in rep["sections"]} == {
        "objectif": "pass", "horizon": "pass", "risque": "pass", "contraintes": "unknown", "preferences": "pass"}
    assert report_mod.sections_verdict(rep["sections"]) == "coherent"
    contraintes = next(s for s in rep["sections"] if s["factor"] == "contraintes")
    assert {"H6", "H10"} <= {u["id"] for u in contraintes["unknowns"]}
    for k in ("vol", "mdd", "loss_prob", "width", "p5_T", "p50_T", "p95_T"):
        assert rep["metrics_current"][k] == pytest.approx(expected_base["metrics"][k], abs=1e-9), k
    for s in rep["sections"]:
        assert s["narrative"]["mode"] in MODES
        assert s["narrative"]["verify"]["passed"] is True
        if s["status"] == "pass":
            assert s["narrative"]["mode"] == "template"
    assert rep["explanation"]["disclaimer"] == rep["disclaimer"] == FR_DISCLAIMER
    nodes = {c["node"] for c in review_current["calls"]}
    assert nodes <= {"explainer"}                                                        # unknown section may be narrated
    assert all(c["payload"]["factor"] == "contraintes" for c in review_current["calls"])
    modes = _section_modes({k: v for k, v in audit.items() if k != "llm_calls"})
    assert modes == {s["factor"]: s["narrative"]["mode"] for s in rep["sections"]}


def test_pipeline_review_returns_the_report(tmp_path, monkeypatch, report_schema):
    """§4 Flow C via the API: `pipeline.review(fixture_dir)` runs on fixtures/robert/portfolio.json and returns the report."""
    monkeypatch.chdir(tmp_path)
    rep = pipeline.review(FIX)
    assert isinstance(rep, dict)
    assert Draft202012Validator(report_schema).is_valid(rep)
    assert rep["verdict"] == "coherent" and rep["portfolio_id"] == "robert-main"
    assert ULID.match(rep["run_id"])


# ---------------------------------------------------------------- Flow A: rules + N3 only

def _spy(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    def call_tool(node, schema, system, user_payload, prompt_version):
        result = _REAL_CALL_TOOL(node, schema, system, user_payload, prompt_version)
        calls.append({"node": node, "payload": copy.deepcopy(user_payload)})
        return result

    monkeypatch.setattr(bedrock, "call_tool", call_tool)
    return calls


def test_refresh_derives_the_rule_fields_and_limits_without_n1_n2(tmp_path, monkeypatch, profile):
    """§4 Flow A + D7: refresh = rules (derive -> limits + trace); the result matches the rules module; the only LLM
    node it may call is N3 (never N1/N2/N4). Robert: effective horizon 3.6, capacity and effective `low_moderate`."""
    monkeypatch.chdir(tmp_path)
    calls = _spy(monkeypatch)
    out = pipeline.refresh(FIX)
    p = out.get("profile", out)                 # SPEC-AMBIGUITY: return shape unspecified; accept the profile or {profile: ...}
    assert p["objectives"]["effective_horizon_years"]["value"] == pytest.approx(3.6, abs=1e-9)
    assert p["risk"]["capacity"]["value"] == "low_moderate" and p["risk"]["effective"]["value"] == "low_moderate"
    assert p["identity"]["life_stage"]["value"] == "pre_retirement"
    assert p["situation"]["emergency_fund_months"]["value"] == pytest.approx(4.6, abs=1e-9)
    assert p["limits"] == profile["limits"]
    assert p["limits_trace"] == profile["limits_trace"]
    assert p["limits"]["floor_at_horizon"] == pytest.approx(360_000, abs=1e-9)
    assert {c["node"] for c in calls} <= {"values_parser"}


def test_refresh_new_notes_go_through_n3_and_the_gate_and_start_unapproved(tmp_path, monkeypatch, profile):
    """D7 point 1 + SPEC §6.2 N3/§11: changed `values.notes` -> N3 once (payload = the notes only) -> gate: quotes are exact
    substrings, `by` llm:values_parser, every parsed rule `approved_by_user: false`, and nothing reaches `limits`
    (inv. 2: a rule with no number, Amira's \"pas tout en tech\", is not a limit either). Meta version bumps."""
    notes = "I love AI and new tech but I don't want everything in tech."          # recorded in fixtures/llm_cache
    work = tmp_path / "robert"
    shutil.copytree(FIX, work)
    prof = _read(work / "profile.json")
    prof["values"]["notes"] = notes
    (work / "profile.json").write_text(json.dumps(prof, ensure_ascii=False, indent=2), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    calls = _spy(monkeypatch)
    out = pipeline.refresh(work)
    p = out.get("profile", out)
    assert [c["node"] for c in calls] == ["values_parser"]                              # N3 once; no N1/N2
    assert calls[0]["payload"] == {"notes": notes}                                      # the notes only, no profile field
    parsed = p["values"]["parsed_notes"]
    assert parsed["by"].startswith("llm:values_parser")
    rules = parsed["value"]
    assert {r["rule_kind"] for r in rules} == {"prefer_attribute", "max_theme_weight"}
    assert all(r["approved_by_user"] is False for r in rules)                           # start unapproved
    assert all(r["quote"] in notes for r in rules)                                      # exact substrings
    assert not p["limits"].get("max_theme_weight")
    assert p["limits"] == profile["limits"]                                             # unapproved rules change no limit
    assert p["meta"]["version"] > prof["meta"]["version"]                               # A6: bump meta.version


# ---------------------------------------------------------------- demo and the unwired Flow B

def test_demo_reviews_the_personas_with_complete_fixtures_and_notices_the_rest(tmp_path_factory):
    """D7 acceptance: `demo` writes a valid report for every persona whose fixtures are complete (profile, events,
    portfolio), prints each verdict, and prints a notice naming each persona that is not complete."""
    run = _run(["demo"], tmp_path_factory.mktemp("demo"))
    complete, incomplete = {}, []
    for name in ("robert", "amira", "mateo"):
        d = ROOT / "fixtures" / name
        if all((d / f).exists() for f in ("profile.json", "events.jsonl", "portfolio.json")):
            complete[name] = _read(d / "profile.json")["meta"]["profile_id"]
        else:
            incomplete.append(name)
    assert "robert" in complete
    schema = json.loads((config.SCHEMAS_DIR / "report.json").read_text(encoding="utf-8"))
    reports = run["reports"]
    assert reports
    for rep in reports:
        assert Draft202012Validator(schema).is_valid(rep)
        assert rep["verdict"] in VERDICTS and rep["flow"] == "C"                         # Flow B is P2: not wired
        assert rep["intent"] is None and rep["checks_proposed"] is None
        assert rep["verdict"] in run["stdout"]                                           # §12: prints verdicts
    assert {r["profile_id"] for r in reports} == set(complete.values())                 # no report for incomplete personas
    out = run["stdout"].lower()
    for name in incomplete:                                                              # a notice, not a crash
        assert name in out
    assert {c["node"] for c in run["calls"]} <= ALLOWED_NODES


def test_flow_b_is_not_wired_in_p0():
    """D7 point 3: Flow B (N4 intent_parser, B2 confirm, trade diff) is P2; `pipeline.check` is still the stub."""
    with pytest.raises(NotImplementedError):
        pipeline.check(FIX, "in_0001", auto_confirm=True)
