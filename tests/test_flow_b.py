"""Flow B: trade check wired end-to-end offline (P2.FB).

Sources: SPEC §4 Flow B (B1-B9), §6.2 N1/N4, §7.4 S1/S3, §7.5 verdict + diff, §8 resizer, §9.4 disclaimer, §10 audit,
§12 CLI, §13 report schema, §15; docs/spec-decisions.md D1 (as_of), D2 (resizer golden), D3, D5 (sections), D6
(model id in the audit), D7 point 3 (Flow B is P2: N4, B2, diff, resizer, N1, S1/S3 come back here), D8;
docs/spec-factors.md §20.4-20.5 (Flow B reports on the proposed checks).

Scenario: Robert in_0001 = NVDA 10% funded from ZST.TO on the starter data (`engine.ROBERT` -> `engine.ROBERT_NVDA`,
48 months, 400 000 CAD, seed 7). Measured context (tests/test_resizer.py, tests/test_pipeline_offline.py):
  current  : vol 7.27 %, mdd -7.7 %, p5_T 366 719, no violation (H6, H10 unknown only).
  proposed : vol 9.83 %, mdd -13.4 %, p5_T 316 971; H3, H4, H7, H8, H9, H10 violation.
  resizer  : max 1.5 % from ZST.TO (binding H9), 2.0 % pro rata all; every option binds H9 (D2).

Everything runs with COHERENCE_ANALYZER_LLM=mock in a temp working directory: no network, no clock value is asserted,
fixed seeds. The pipeline's step functions are not named by the spec, so the tests drive `pipeline.check` and the CLI
and look at what they write (report, audit) and at the LLM calls they make (spy on `bedrock.call_tool`).
"""
import contextlib
import copy
import io
import json
import os
import re
import shutil
from datetime import date
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

os.environ["COHERENCE_ANALYZER_LLM"] = "mock"

from coherence_analyzer import cli, config, data, engine, explain, factors, gate, pipeline, store  # noqa: E402
from coherence_analyzer.coherence import hard, report as report_mod  # noqa: E402
from coherence_analyzer.llm import bedrock, extractors  # noqa: E402
from coherence_analyzer.rules import derive, limits as limits_mod  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "fixtures" / "robert"
CACHE = ROOT / "fixtures" / "llm_cache"
AS_OF = "2026-11-12"
VALUE = 400_000
HORIZON_M = 48
META_NAMES = ("asset_meta", "etf_sector_weights", "activity_tags")

INTENT_ID = "in_0001"
EVENT_ID = "ev_robert_0007"
EVENTS_UPTO_INTENT = ["ev_robert_0001", "ev_robert_0004", "ev_robert_0005", "ev_robert_0007"]
DISPLAY = ("objectif", "horizon", "risque", "contraintes", "preferences")
MODES = ("llm", "llm_retry", "template")
MODE_RANK = {"llm": 0, "llm_retry": 1, "template": 2}
GATE_STATUSES = ("accepted", "clamped", "kept:abstain", "rejected:evidence", "rejected:schema", "rejected:verifier")
ALLOWED_NODES = {"intent_parser", "behaviour_classifier", "explainer", "values_parser", "profile_consistency"}
LLM_CALL_KEYS = {"node", "model_id", "prompt_version", "input_hash", "cache_hit", "latency_ms", "tokens_in",
                 "tokens_out", "gate"}
FR_DISCLAIMER = ("Simulation éducative avec des profils fictifs. Ceci n'est pas un conseil financier personnalisé. "
                 "Aucune transaction n'est exécutée.")                                    # SPEC §9.4
ISO_TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")
ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
OPTION_KEYS = {"funding", "max_weight_pct", "binding_check", "metrics"}                  # SPEC §8 step 2
SPECIFIC_ZST = {"kind": "specific", "ticker": "ZST.TO"}

_REAL_SAVE_REPORT = store.save_report
_REAL_CALL_TOOL = bedrock.call_tool
_FIXTURE_SNAPSHOT = {p.name: p.read_bytes() for p in FIX.iterdir() if p.is_file()}
_TAGS = ROOT / "data" / "meta" / "activity_tags.csv"
_TAGS_BYTES = _TAGS.read_bytes() if _TAGS.exists() else None


# ---------------------------------------------------------------- helpers

def _fractions(w_pct: dict) -> dict:
    return {t: v / 100 for t, v in w_pct.items()}


def _read(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _run(argv: list[str], cwd: Path, *, patch_output=None) -> dict:
    """Run `cli.main(argv)` offline in `cwd`. `patch_output(node, output) -> output` may rewrite what a node returns
    (to simulate a bad or weak LLM answer); the real mock cache is still read. Returns {texts, reports, audits,
    calls (node, payload, output, call_log, in call order), stdout}. The report is read inside the save_report wrapper."""
    mp = pytest.MonkeyPatch()
    texts: list[str] = []
    calls: list[dict] = []

    def save_report(rep):
        path = _REAL_SAVE_REPORT(rep)
        texts.append(Path(path).read_text(encoding="utf-8"))
        return path

    def call_tool(node, schema, system, user_payload, prompt_version):
        result = _REAL_CALL_TOOL(node, schema, system, user_payload, prompt_version)
        if patch_output is not None:
            result = dict(result, output=patch_output(node, copy.deepcopy(result["output"])))
        calls.append({"node": node, "payload": copy.deepcopy(user_payload), "output": copy.deepcopy(result["output"]),
                      "call_log": copy.deepcopy(result["call_log"])})
        return result

    mp.setattr(store, "save_report", save_report)
    mp.setattr(bedrock, "call_tool", call_tool)
    mp.chdir(cwd)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            cli.main(argv)
        reports = [json.loads(t) for t in texts]
        audits = {r["run_id"]: store.load_audit(r["run_id"]) for r in reports}
    finally:
        mp.undo()
    return {"texts": texts, "reports": reports, "audits": audits, "calls": calls, "stdout": out.getvalue(),
            "cwd": cwd}


def _check_argv(fixture: Path = FIX) -> list[str]:
    return ["check", str(fixture), "--intent", INTENT_ID, "--auto-confirm"]


def _only(run: dict) -> tuple[dict, dict]:
    assert len(run["reports"]) == 1, "one Flow B run writes exactly one report"
    rep = run["reports"][0]
    return rep, run["audits"][rep["run_id"]]


def _normalise(text: str, run_id: str) -> str:
    return ISO_TS.sub("<TS>", text.replace(run_id, "<RUN_ID>"))


def _by_id(checks: list[dict], cid: str) -> list[dict]:
    return [c for c in checks if c["id"] == cid]


def _num(x):
    """A metric delta may be a bare number or an object carrying it under `delta`."""
    return x["delta"] if isinstance(x, dict) else x


def _tickers(drivers) -> list[str]:
    return [d["ticker"] if isinstance(d, dict) else d for d in drivers]


def _n1_patch(change):
    return lambda node, out: change(out) if node == "behaviour_classifier" else out


# ---------------------------------------------------------------- expected values (same code path as the other suites)

@pytest.fixture(scope="module")
def profile() -> dict:
    p = derive.derive_all(_read(FIX / "profile.json"), portfolio_value_cad=VALUE, as_of=AS_OF)
    lim, trace = limits_mod.build_limits(p, VALUE)
    p["limits"], p["limits_trace"] = lim, trace
    return p


@pytest.fixture(scope="module")
def expected(profile, market) -> dict:
    assets = data.load_assets()
    meta = {n: data.load_meta(n) for n in META_NAMES}
    out = {}
    for name, w in (("current", engine.ROBERT), ("proposed", engine.ROBERT_NVDA)):
        m = engine.analyse(market, w, HORIZON_M, VALUE)
        out[name] = {"metrics": m, "checks": hard.run_all(_fractions(w), m, profile, assets, meta)}
    return out


@pytest.fixture(scope="module")
def report_schema() -> dict:
    schema = json.loads((config.SCHEMAS_DIR / "report.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


@pytest.fixture(scope="module")
def run_b(tmp_path_factory) -> dict:
    """`cli check fixtures/robert --intent in_0001 --auto-confirm` (acceptance)."""
    return _run(_check_argv(), tmp_path_factory.mktemp("b1"))


@pytest.fixture(scope="module")
def run_b_again(tmp_path_factory) -> dict:
    return _run(_check_argv(), tmp_path_factory.mktemp("b2"))


@pytest.fixture(scope="module")
def rep(run_b) -> dict:
    return _only(run_b)[0]


@pytest.fixture(scope="module")
def audit(run_b) -> dict:
    return _only(run_b)[1]


# ---------------------------------------------------------------- B2: the CLI stops at the confirmation step

def test_check_without_auto_confirm_stops_at_b2_after_n4_only(tmp_path):
    """SPEC §4 B2 + §12: without --auto-confirm the run stops at the draft: N4 ran (B1), the draft is shown, but no
    N1 / N5 call, no engine report and nothing is saved (the user has not confirmed; invariant: never auto-executed)."""
    run = _run(["check", str(FIX), "--intent", INTENT_ID], tmp_path)
    assert [c["node"] for c in run["calls"]] == ["intent_parser"]
    assert run["reports"] == []
    assert not (tmp_path / "reports").exists()
    assert "NVDA" in run["stdout"]                                       # the draft intent is shown to the user


def test_check_with_an_unknown_intent_id_raises_a_clear_error_not_the_stub(tmp_path):
    """SPEC §12: `--intent` names an intent of the fixture; an id that does not exist is an error, and the P0
    NotImplementedError stub is gone."""
    # SPEC-AMBIGUITY: the exception type is not specified; any lookup/value error is accepted, the stub is not.
    with pytest.raises((ValueError, LookupError, FileNotFoundError)):
        _run(["check", str(FIX), "--intent", "in_9999", "--auto-confirm"], tmp_path)


# ---------------------------------------------------------------- B9 report: shape, intent, verdict, sections

def test_check_report_is_schema_valid_flow_b_with_both_sides(rep, run_b, report_schema):
    """§13 + §4: the saved report validates against schemas/report.json, flow B, and unlike Flow C it carries the
    proposed side, the diff and the alternatives. Rich types are plain JSON (the later Step Functions wrap them)."""
    errors = sorted(Draft202012Validator(report_schema).iter_errors(rep), key=lambda e: list(e.absolute_path))
    assert errors == [], [e.message for e in errors]
    assert ULID.match(rep["run_id"])
    assert rep["flow"] == "B"
    assert rep["profile_id"] == "persona-robert" and rep["portfolio_id"] == "robert-main"
    assert isinstance(rep["profile_version"], int) and rep["profile_version"] >= 1
    assert rep["checks_proposed"] and rep["metrics_proposed"] and rep["diff"]
    assert isinstance(rep["alternatives"], list) and rep["alternatives"]
    assert rep["data_window"] == ["2022-01-31", "2026-10-31"]                           # D4
    assert any("survivorship" in a.lower() for a in rep["assumptions"])                  # §3.1 limitation shown
    assert json.loads(json.dumps(rep)) == rep                                            # JSON round trip, no extras
    assert rep["disclaimer"] == config.DISCLAIMER["fr-CA"] == FR_DISCLAIMER              # §9.4, fixed text


def test_report_intent_is_the_confirmed_in_0001_nvda_10_from_zst(rep):
    """§3.6 + §4 B2: the report carries the confirmed (user-declared) intent in_0001: NVDA 10 % funded from ZST.TO,
    linked to the order_intent event ev_robert_0007."""
    # SPEC-AMBIGUITY: with --intent in_0001 --auto-confirm the confirmed intent is the fixture intent (the N4 draft
    # is only what B2 shows); stricter reading: the report must hold specific/ZST.TO, not the draft's default funding.
    it = rep["intent"]
    assert it["intent_id"] == INTENT_ID and it["event_id"] == EVENT_ID
    assert it["action"] == "buy" and it["ticker"] == "NVDA"
    assert it["target_weight_pct"] == 10
    assert it["funding"] == SPECIFIC_ZST
    assert it["status"] == "confirmed"


def test_verdict_is_incoherent_and_sections_follow_the_proposed_checks(rep):
    """§7.5 + §20.4: verdict incoherent; five sections in display order whose status is computed on the PROPOSED checks
    (horizon H9, risque H4/H7/H8, contraintes H3/H10 violation; objectif, preferences pass); facts name the intent."""
    assert rep["verdict"] == "incoherent"
    assert [s["factor"] for s in rep["sections"]] == list(DISPLAY)
    assert {s["factor"]: s["status"] for s in rep["sections"]} == {
        "objectif": "pass", "horizon": "violation", "risque": "violation", "contraintes": "violation",
        "preferences": "pass"}
    assert report_mod.sections_verdict(rep["sections"]) == rep["verdict"]               # §20.4 consistency
    by = {s["factor"]: s for s in rep["sections"]}
    assert by["horizon"]["checks"] == ["H9"]
    assert {"H4", "H7", "H8", "S1", "S3"} <= set(by["risque"]["checks"])
    assert {"H3", "H10"} <= set(by["contraintes"]["checks"])
    for s in rep["sections"]:
        assert s["facts"]["verdict"] == "incoherent"
        assert s["facts"]["intent"] is not None
        assert s["facts"]["intent"]["ticker"] == "NVDA" and s["facts"]["intent"]["funding"] == "ZST.TO"
        assert "10" in s["facts"]["intent"]["target_weight"]
        assert all(factors.CHECK_FACTOR[cid] == s["factor"] for cid in s["checks"])
    for fid, ids in (("horizon", {"H9"}), ("risque", {"H4", "H7", "H8"}), ("contraintes", {"H3", "H10"})):
        viol = {c["id"] for c in by[fid]["facts"]["checks"] if c["status"] == "violation"}
        assert viol == ids, fid
    # the behaviour evidence (S1, S3) is quoted from real events only (invariant 5)
    ev = {e["event_id"] for e in by["risque"]["facts"]["evidence_events"]}
    assert EVENT_ID in ev and ev <= set(EVENTS_UPTO_INTENT)


# ---------------------------------------------------------------- B5/B6: checks and metrics on both sides

def test_checks_are_the_hard_checks_of_each_portfolio_and_carry_a_factor(rep, expected):
    """§4 B5/B6: checks_current = hard checks of ROBERT, checks_proposed = hard checks of ROBERT_NVDA (same profile,
    same engine call), each with a `factor` key; proposed violations H3 NVDA 0.10 > 0.08, H4 0.25 < 0.30 (§15)."""
    key = lambda c: (c["id"], c.get("subject") or "", c["status"])  # noqa: E731
    for side, exp in (("checks_current", expected["current"]), ("checks_proposed", expected["proposed"])):
        checks = rep[side]
        assert all(c["factor"] == factors.CHECK_FACTOR[c["id"]] for c in checks), side
        got = [c for c in checks if c["kind"] == "hard"]
        assert sorted(map(key, got)) == sorted(map(key, exp["checks"])), side
        by_key = {key(c): c for c in exp["checks"]}
        for c in got:
            e = by_key[key(c)]
            for f in ("value", "limit"):
                if isinstance(e.get(f), (int, float)):
                    assert c[f] == pytest.approx(e[f], abs=1e-9), (side, c["id"], f)
            assert c["derived_from"] == e["derived_from"]
    cur = {c["id"] for c in rep["checks_current"] if c["status"] == "violation"}
    new = {c["id"] for c in rep["checks_proposed"] if c["status"] == "violation"}
    assert cur == set()
    assert new == {"H3", "H4", "H7", "H8", "H9", "H10"}
    h3 = next(c for c in rep["checks_proposed"] if c["id"] == "H3" and c["status"] == "violation")
    assert h3["subject"] == "NVDA"
    assert h3["value"] == pytest.approx(0.10, abs=1e-9) and h3["limit"] == pytest.approx(0.08, abs=1e-9)
    h4 = next(c for c in rep["checks_proposed"] if c["id"] == "H4")
    assert h4["value"] == pytest.approx(0.25, abs=1e-9) and h4["limit"] == pytest.approx(0.30, abs=1e-9)
    h5 = next(c for c in rep["checks_proposed"] if c["id"] == "H5")
    assert h5["status"] == "pass" and h5["value"] == pytest.approx(0.37, abs=1e-9)       # §15: 0.37 <= 0.40


def test_metrics_current_and_proposed_are_the_engine_values_with_the_same_seed(rep, audit, expected):
    """§4 B4/B5: the proposed portfolio is intent.apply(ROBERT, in_0001) = ROBERT_NVDA; both sides come from
    engine.analyse with the same seed and n (1e-9); the audit records the engine parameters."""
    for side, key in (("metrics_current", "current"), ("metrics_proposed", "proposed")):
        got, exp = rep[side], expected[key]["metrics"]
        for k in ("vol", "vol_local", "mdd", "loss_prob", "width", "p5_T", "p50_T", "p95_T", "corr_eq_bond"):
            assert got[k] == pytest.approx(exp[k], abs=1e-9), (side, k)
        assert got["mdd_date"] == exp["mdd_date"]
        for p in ("p5", "p25", "p50", "p75", "p95"):
            assert got["cone"][p] == pytest.approx(exp["cone"][p], abs=1e-9), (side, p)
    assert rep["metrics_proposed"]["p5_T"] == pytest.approx(316_971.0, abs=1e-9)         # D2 context
    assert rep["metrics_proposed"]["mdd"] == pytest.approx(-0.134, abs=1e-9)
    assert audit["engine"]["seed"] == config.ENGINE_SEED == 7
    assert audit["engine"]["n"] == config.ENGINE_N == 5000
    assert audit["engine"]["block"] == config.ENGINE_BLOCK == 12
    assert audit["engine"]["drift"] == "assumed"
    assert audit["engine"]["data_window"] == rep["data_window"]


def test_unknowns_are_those_of_the_proposed_checks_and_listed_apart_from_the_verdict(rep):
    """§7.5 + D3: `unknowns` = the proposed checks with status unknown (H6, H10 ...), always listed, never a verdict."""
    unknown_ids = [c["id"] for c in rep["checks_proposed"] if c["status"] == "unknown"]
    assert {"H6", "H10"} <= set(unknown_ids)
    assert [u["id"] for u in rep["unknowns"]] == unknown_ids
    assert all(u["status"] == "unknown" for u in rep["unknowns"])


# ---------------------------------------------------------------- B3/B6: S1 and S3 only from gated N1, never violation

def test_s1_and_s3_come_from_the_gated_n1_and_are_never_violations(rep, audit):
    """§7.4 + inv. 3: Robert's behavioural level (moderate_high, conf 0.55 >= WARN_MIN_CONF) vs effective low_moderate
    is a gap of 2 -> S1 `warning`; N1 cites ev_robert_0007 with hype_chasing -> S3 `warning` attached to the trade.
    Both are soft checks, they cite the real event ids, and no soft check is ever a violation."""
    for side in ("checks_current", "checks_proposed"):
        s1 = _by_id(rep[side], "S1")
        assert len(s1) == 1 and s1[0]["status"] == "warning" and s1[0]["kind"] == "soft", side
        assert s1[0]["factor"] == "risque"
        # SPEC-AMBIGUITY: "message cites the evidence event_ids" does not name a key; accept them anywhere in the check.
        assert EVENT_ID in json.dumps(s1[0]) and "ev_robert_0004" in json.dumps(s1[0]), side
        assert config.WARN_MIN_CONF <= s1[0]["confidence"] <= config.BEHAVIOUR_MAX_CONF, side
    s3 = _by_id(rep["checks_proposed"], "S3")
    assert len(s3) == 1 and s3[0]["status"] == "warning" and s3[0]["kind"] == "soft"
    assert s3[0]["factor"] == "risque" and EVENT_ID in json.dumps(s3[0])
    # SPEC-AMBIGUITY: S3 is "attached to the trade": stricter reading, it exists on the proposed side only.
    assert _by_id(rep["checks_current"], "S3") == []
    for side in ("checks_current", "checks_proposed"):
        for c in rep[side]:
            if c["kind"] == "soft":
                assert c["status"] in ("pass", "warning", "info", "unknown"), (side, c["id"])
    assert all(c["kind"] == "hard" for c in rep["checks_proposed"] if c["status"] == "violation")
    # the N1 call went through the gate and was accepted (moderate_high -> moderate_high: no clamp)
    n1 = [c for c in audit["llm_calls"] if c["node"] == "behaviour_classifier"]
    assert len(n1) == 1 and n1[0]["gate"] == "accepted"


def test_low_confidence_n1_gives_info_not_warning(tmp_path):
    """§7.4: soft checks only warn when the source field passed the gate with confidence >= 0.5; at 0.4 the answer is
    stored as-is but S1 / S3 are `info` ("insufficient evidence"). The hard checks still decide the verdict."""
    def weak(out):
        out["confidence"] = 0.4
        return out
    run = _run(_check_argv(), tmp_path, patch_output=_n1_patch(weak))
    rep, audit = _only(run)
    assert rep["verdict"] == "incoherent"
    for side in ("checks_current", "checks_proposed"):
        assert _by_id(rep[side], "S1")[0]["status"] == "info", side
        assert all(c["status"] != "warning" for c in rep[side] if c["id"] in ("S1", "S3")), side
    n1 = [c for c in audit["llm_calls"] if c["node"] == "behaviour_classifier"]
    assert [c["gate"] for c in n1] == ["accepted"]


def test_invented_n1_evidence_is_rejected_by_the_gate_and_never_reaches_the_report(tmp_path):
    """Invariants 4 + 5 + §11: an N1 answer citing an event_id that does not exist is `rejected:evidence`; the previous
    behavioural value stays (S1 still warns on it), nothing invented appears in the report, the run completes."""
    def invent(out):
        out["evidence"] = [{"event_id": "ev_invented_9999", "signal": "hype_chasing", "note": "made up"}]
        return out
    run = _run(_check_argv(), tmp_path, patch_output=_n1_patch(invent))
    rep, audit = _only(run)
    assert "ev_invented_9999" not in run["texts"][0]
    assert "ev_invented_9999" not in json.dumps(audit)
    n1 = [c for c in audit["llm_calls"] if c["node"] == "behaviour_classifier"]
    assert [c["gate"] for c in n1] == ["rejected:evidence"]
    assert rep["verdict"] == "incoherent"
    assert _by_id(rep["checks_proposed"], "S1")[0]["status"] == "warning"                # previous value kept
    assert all(c["kind"] == "hard" for c in rep["checks_proposed"] if c["status"] == "violation")


# ---------------------------------------------------------------- B6 diff

def test_report_diff_lists_flipped_checks_and_metric_deltas(rep):
    """§7.5 + B6: diff.flipped = checks whose status changed current -> proposed ({id, from, to}); H3, H4, H7, H8, H9
    went pass -> violation, H10 became a violation; unchanged checks (H1, H2, H5, S1) are not listed."""
    flipped = rep["diff"]["flipped"]
    assert all({"id", "from", "to"} <= set(f) for f in flipped)
    assert all(f["from"] != f["to"] for f in flipped)
    by_id = {}
    for f in flipped:
        by_id.setdefault(f["id"], []).append(f)
    for cid in ("H3", "H4", "H7", "H8", "H9"):
        assert any(f["from"] == "pass" and f["to"] == "violation" for f in by_id[cid]), cid
    # SPEC-AMBIGUITY: H10 has several results (a per-sector pass/violation and a Broad-market unknown); only the
    # final status is pinned.
    assert any(f["to"] == "violation" for f in by_id["H10"])
    for cid in ("H1", "H2", "H5", "S1"):
        assert cid not in by_id, cid
    md = rep["diff"]["metrics_delta"]
    # SPEC-AMBIGUITY: delta = proposed - current (a bare number or an object with `delta`).
    for k in ("vol", "mdd", "p5_T", "width", "loss_prob"):
        exp = rep["metrics_proposed"][k] - rep["metrics_current"][k]
        assert _num(md[k]) == pytest.approx(exp, abs=1e-9), k
    assert _num(md["p5_T"]) == pytest.approx(316_971.0 - 366_719.0, abs=1e-9)
    assert _tickers(md["drivers"]) == ["NVDA", "XIU.TO", "SPY"]                           # proposed top 3 by risk
    assert len(md["drivers"]) == 3


def test_diff_function_flipped_checks_and_deltas_are_pure(expected):
    """§7.5: `report.diff(current, proposed, m_cur, m_new)` gives the flipped checks and the metric deltas (vol, mdd,
    p5_T, width, loss_prob, top-3 drivers), JSON-serialisable, without mutating its inputs; identical sides flip nothing."""
    cur, new = expected["current"], expected["proposed"]
    a, b = copy.deepcopy(cur["checks"]), copy.deepcopy(new["checks"])
    ma, mb = copy.deepcopy(cur["metrics"]), copy.deepcopy(new["metrics"])
    d = report_mod.diff(a, b, ma, mb)
    json.dumps(d)
    assert a == cur["checks"] and b == new["checks"]
    assert [m for m in (ma, mb)] == [cur["metrics"], new["metrics"]]
    flipped_ids = {f["id"] for f in d["flipped"]}
    assert {"H3", "H4", "H7", "H8", "H9", "H10"} <= flipped_ids
    assert not flipped_ids & {"H1", "H2", "H5", "H14"}
    md = d["metrics_delta"]
    for k in ("vol", "mdd", "p5_T", "width", "loss_prob"):
        assert _num(md[k]) == pytest.approx(new["metrics"][k] - cur["metrics"][k], abs=1e-9), k
    assert len(md["drivers"]) == 3 and _tickers(md["drivers"])[0] == "NVDA"
    same = report_mod.diff(a, copy.deepcopy(a), ma, copy.deepcopy(ma))
    assert same["flipped"] == []
    for k in ("vol", "mdd", "p5_T", "width", "loss_prob"):
        assert _num(same["metrics_delta"][k]) == pytest.approx(0.0, abs=1e-9), k


# ---------------------------------------------------------------- B7 resizer

def test_alternatives_are_the_resizer_options_for_robert_nvda_from_zst(rep):
    """§8 + D2: the proposed portfolio has violations, so the resizer runs; the report's `alternatives` are its
    options (<= 3, de-duplicated, the user's own funding first-class): ZST.TO 1.5 % (golden, below SPEC §8's 5 %),
    binding H9; every option's max is below the requested 10 % and below 5 %, binding H9."""
    opts = rep["alternatives"]
    assert 1 <= len(opts) <= 3
    assert all(OPTION_KEYS <= set(o) for o in opts)
    assert len({json.dumps(o["funding"], sort_keys=True) for o in opts}) == len(opts)
    zst = next(o for o in opts if o["funding"] == SPECIFIC_ZST)
    assert zst["max_weight_pct"] < 5.0                                                   # D2: below 5 %, H9 binds
    assert zst["max_weight_pct"] == pytest.approx(1.5, abs=1e-9)
    assert zst["binding_check"] == "H9"
    assert zst["metrics"]["p5_T"] >= 360_000                                             # fits the floor at n=5000
    for o in opts:
        assert o["max_weight_pct"] < 5.0 and o["binding_check"] == "H9"
    pro_rata = [o for o in opts if o["funding"] == {"kind": "pro_rata_all"}]
    if pro_rata:
        assert pro_rata[0]["max_weight_pct"] == pytest.approx(2.0, abs=1e-9)


def test_a_trade_that_fits_has_no_violation_no_resizer_and_keeps_the_soft_warnings(tmp_path):
    """§4 B7: "if proposed has violations -> resizer": NVDA 1 % (every check passes at n=5000, tests/test_resizer.py)
    gives no hard violation, `alternatives == []`, and verdict coherent_with_warnings (S1 and S3 warn, §7.5)."""
    work = tmp_path / "robert"
    shutil.copytree(FIX, work)
    intents = _read(work / "intents.json")
    intents[0]["target_weight_pct"] = 1
    (work / "intents.json").write_text(json.dumps(intents), encoding="utf-8")
    run = _run(_check_argv(work), tmp_path)
    rep, audit = _only(run)
    assert rep["intent"]["target_weight_pct"] == 1
    assert not [c for c in rep["checks_proposed"] if c["status"] == "violation"]
    assert rep["alternatives"] == []
    assert rep["verdict"] == audit["verdict"] == "coherent_with_warnings"
    assert _by_id(rep["checks_proposed"], "S3")[0]["status"] == "warning"


# ---------------------------------------------------------------- B8 explanation

def test_explanation_is_verified_template_framed_and_the_disclaimer_is_fixed(rep):
    """§9 + §20.5 + inv. 6/7/9: every section narrative passes V1-V5 (no unverified text ships); the headline is the
    template one (<= 140 chars); segments = section segments in display order with a `factor`; mode = worst non-pass
    section mode; numbers exist in the facts; no advisory phrase; the disclaimer is the fixed §9.4 text."""
    for s in rep["sections"]:
        nar = s["narrative"]
        assert nar["mode"] in MODES, s["factor"]
        assert nar["verify"] == {"passed": True, "failures": []}, s["factor"]
        assert explain.verify_section({"segments": nar["segments"]}, s["facts"]) == [], s["factor"]
        if s["status"] == "pass":
            assert nar["mode"] == "template", s["factor"]                                # pass sections: no LLM
        for seg in nar["segments"]:
            assert seg["kind"] in ("fact", "assumption", "inference", "unknown") and seg["refs"]
    by = {s["factor"]: s for s in rep["sections"]}
    for fid, ids in (("horizon", {"H9"}), ("risque", {"H4", "H7", "H8", "S1", "S3"}), ("contraintes", {"H3", "H10"})):
        refs = {r for seg in by[fid]["narrative"]["segments"] for r in seg["refs"]}
        assert ids <= refs, fid                                                          # V3: every violation/warning cited
    ex = rep["explanation"]
    assert ex["headline"] == explain.headline(rep["verdict"], rep["sections"], "fr-CA")
    assert 0 < len(ex["headline"]) <= explain.MAX_HEADLINE_CHARS
    assert ex["segments"] == [{"factor": s["factor"], **seg} for s in rep["sections"]
                              for seg in s["narrative"]["segments"]]
    non_pass = [s["narrative"]["mode"] for s in rep["sections"] if s["status"] != "pass"]
    assert ex["mode"] == max(non_pass, key=MODE_RANK.__getitem__)
    assert ex["disclaimer"] == rep["disclaimer"] == FR_DISCLAIMER
    for seg in ex["segments"]:
        facts = by[seg["factor"]]["facts"]
        assert explain.numcheck(seg["text"], facts) == []
        assert not any(p in seg["text"].casefold() for p in explain.ADVISORY_BLOCKLIST)


# ---------------------------------------------------------------- B1/B3: the LLM calls, in order, with the right inputs

def test_llm_calls_are_n4_then_n1_then_the_explainer_with_exact_payloads(run_b):
    """§4 B1 -> B3 -> B8, §6.2: N4 first (payload = the order_intent text + universe + current weights), then N1 (payload =
    the events only: no profile field, window per D1, events up to and including the order_intent), then N5 once or
    twice per non-pass section, never for a pass section. All answers come from the recorded cache (mock)."""
    calls = run_b["calls"]
    nodes = [c["node"] for c in calls]
    assert set(nodes) <= ALLOWED_NODES, nodes
    assert nodes[0] == "intent_parser" and nodes.count("intent_parser") == 1
    assert nodes.count("behaviour_classifier") == 1
    assert nodes.index("behaviour_classifier") < nodes.index("explainer")
    assert "question_rubric" not in nodes
    n4 = calls[0]
    events = _read_events()
    text = next(e["text"] for e in events if e["event_id"] == EVENT_ID)
    assets = data.load_assets()
    universe = [{"ticker": t, "name": r["name"]} for t, r in assets.iterrows()]
    assert n4["payload"] == extractors.n4_payload(text, universe, engine.ROBERT)
    n1 = next(c for c in calls if c["node"] == "behaviour_classifier")
    assert set(n1["payload"]) == {"events"}                                              # no profile field (anchoring)
    assert n1["payload"] == {"events": extractors.n1_events(events, date.fromisoformat(AS_OF))}
    assert [e["event_id"] for e in n1["payload"]["events"]] == ["ev_robert_0004", "ev_robert_0005", EVENT_ID]
    assert all(set(e) == {"event_id", "ts", "type", "text"} for e in n1["payload"]["events"])
    explainer = [c for c in calls if c["node"] == "explainer"]
    per_factor: dict[str, int] = {}
    for c in explainer:
        assert c["payload"]["status"] != "pass"
        assert c["payload"]["factor"] in ("horizon", "risque", "contraintes")
        per_factor[c["payload"]["factor"]] = per_factor.get(c["payload"]["factor"], 0) + 1
    assert all(1 <= n <= 2 for n in per_factor.values())
    assert all(c["call_log"]["cache_hit"] is True for c in calls)                        # mock: recorded responses only


def _read_events() -> list[dict]:
    return [json.loads(line) for line in (FIX / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------- B9 audit

def test_audit_record_has_model_id_as_of_engine_and_every_gate_decision(rep, audit, run_b):
    """§10 + D1 + D6 + B9: the audit record of a Flow B run: flow B, events considered up to and including the
    order_intent, the as_of date used, the model id (top level and on every call), every LLM call with its gate
    decision (N4 and N1 accepted), engine parameters, verdict and explanation mode, narration mode per section."""
    assert audit["run_id"] == rep["run_id"] and audit["flow"] == "B" and audit["profile_id"] == "persona-robert"
    for k in ("started_at", "ended_at", "profile_version_in", "profile_version_out", "events_considered", "llm_calls",
              "profile_diff", "engine", "verdict", "explanation_mode", "model_id", "as_of"):
        assert k in audit, k
    assert audit["as_of"] == AS_OF                                                       # D1: reference date, not the clock
    assert audit["events_considered"] == EVENTS_UPTO_INTENT
    assert audit["verdict"] == rep["verdict"] == "incoherent"
    assert audit["explanation_mode"] == rep["explanation"]["mode"]
    assert isinstance(audit["model_id"], str) and audit["model_id"]
    calls = audit["llm_calls"]
    assert sorted(c["node"] for c in calls) == sorted(c["node"] for c in run_b["calls"])  # every call is audited
    for c in calls:
        assert LLM_CALL_KEYS <= set(c), c["node"]
        assert c["model_id"] and c["cache_hit"] is True
        assert c["gate"] in GATE_STATUSES, (c["node"], c["gate"])
    by_node = {c["node"]: c for c in calls if c["node"] in ("intent_parser", "behaviour_classifier")}
    assert by_node["intent_parser"]["gate"] == "accepted"
    assert by_node["behaviour_classifier"]["gate"] == "accepted"
    n1 = next(s for s in run_b["calls"] if s["node"] == "behaviour_classifier")
    assert by_node["behaviour_classifier"]["input_hash"] == bedrock.input_hash(n1["payload"])
    n4 = next(s for s in run_b["calls"] if s["node"] == "intent_parser")
    assert by_node["intent_parser"]["input_hash"] == bedrock.input_hash(n4["payload"])
    modes = audit.get("section_modes")
    assert modes == {s["factor"]: s["narrative"]["mode"] for s in rep["sections"]}


def test_cli_check_prints_the_verdict_and_the_audit_is_readable(rep, run_b):
    """§12: `cli check ... --auto-confirm` prints the verdict; `cli audit <run_id>` shows the record of that run."""
    assert rep["verdict"] in run_b["stdout"]
    buf = io.StringIO()
    mp = pytest.MonkeyPatch()
    mp.chdir(run_b["cwd"])
    try:
        with contextlib.redirect_stdout(buf):
            cli.main(["audit", rep["run_id"]])
    finally:
        mp.undo()
    assert rep["run_id"] in buf.getvalue() and "model_id" in buf.getvalue()


# ---------------------------------------------------------------- invariants

def test_check_run_leaves_fixtures_and_activity_tags_unchanged(run_b):
    """Invariants: the run never rewrites fixtures/robert (versions go to the store, §10) and never writes
    data/meta/activity_tags.csv (humans only, with a source)."""
    assert {p.name: p.read_bytes() for p in FIX.iterdir() if p.is_file()} == _FIXTURE_SNAPSHOT
    now = _TAGS.read_bytes() if _TAGS.exists() else None
    assert now == _TAGS_BYTES


def test_running_the_check_twice_gives_identical_reports_except_run_id_and_timestamps(run_b, run_b_again):
    """Invariant 8: mock cache + fixed seed -> two runs differ only by run_id and timestamps."""
    a, _ = _only(run_b)
    b, _ = _only(run_b_again)
    assert a["run_id"] != b["run_id"]
    ta, tb = _normalise(run_b["texts"][0], a["run_id"]), _normalise(run_b_again["texts"][0], b["run_id"])
    assert ta == tb


def test_pipeline_check_returns_the_report_and_is_no_longer_the_stub(tmp_path, monkeypatch, report_schema):
    """§4 + P2.FB: `pipeline.check(fixture_dir, intent_id, auto_confirm=True)` runs B1-B9 and returns the report,
    like `pipeline.review` does; the report is the one saved in the store."""
    monkeypatch.chdir(tmp_path)
    res = pipeline.check(FIX, INTENT_ID, auto_confirm=True)
    assert isinstance(res, dict) and Draft202012Validator(report_schema).is_valid(res)
    assert res["flow"] == "B" and res["verdict"] == "incoherent"
    assert store.load_report(res["run_id"]) == res


def test_hand_written_cache_entries_for_n1_and_n4_pass_schema_and_gate():
    """§6.2 + §11 + task note: the recorded N1 / N4 entries of the Robert scenario validate against their schemas
    (N4: with the runtime ticker enum) and pass the gate / the universe check."""
    assets = data.load_assets()
    universe = [{"ticker": t, "name": r["name"]} for t, r in assets.iterrows()]
    tickers = {u["ticker"] for u in universe}
    events = _read_events()
    event_ids = {e["event_id"] for e in events}
    texts = {e["text"] for e in events if e["type"] == "order_intent"}
    seen = {"behaviour_classifier": 0, "intent_parser": 0}
    for path in sorted(CACHE.glob("*.json")):
        entry = json.loads(path.read_text(encoding="utf-8"))
        node, out, payload = entry["node"], entry["output"], entry["user_payload"]
        if node == "behaviour_classifier" and {e["event_id"] for e in payload["events"]} <= event_ids:
            Draft202012Validator(extractors.load_schema(node)).validate(out)
            ctx = {"event_ids": event_ids, "notes": "", "model_id": entry["model_id"],
                   "prompt_version": entry["prompt_version"], "input_hash": entry["input_hash"]}
            field, status = gate.gate(node, None, out, ctx)
            assert status in ("accepted", "clamped"), path.name
            assert field["by"] == "llm:behaviour_classifier" and 0.0 <= field["confidence"] <= config.BEHAVIOUR_MAX_CONF
            seen[node] += 1
        elif node == "intent_parser" and payload["text"] in texts:
            Draft202012Validator(extractors.n4_schema(universe)).validate(out)
            assert out["ticker"] in tickers
            if out["funding"]["kind"] == "specific":
                assert out["funding"]["ticker"] in tickers
            assert out["action"] == "buy" and 0 < out["target_weight_pct"] <= 100
            seen[node] += 1
    assert seen["behaviour_classifier"] >= 1 and seen["intent_parser"] >= 1
