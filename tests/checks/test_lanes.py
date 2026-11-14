"""Parallel factor lanes + the `profile_coherence` block of the Flow C report (P0.12).

Sources: docs/spec-factors.md §20.10 (parallel lanes), §20.9 "Status and effect" (the separate block
`profile_coherence = {status, findings}`; it never changes the portfolio verdict, invariants 2-3), docs/spec-decisions.md
D8 (point 2: output identical to sequential), D3 (three verdicts only), D1 (`as_of` = `portfolio.as_of`), SPEC §10
(audit record), §12 (CLI), §13 (report schema), §1 inv. 8 (reports deterministic apart from the run_id).

Part 1 (explain): `explain.explain` narrates the non-pass sections on a `concurrent.futures.ThreadPoolExecutor` with
`max_workers = 5`; the result is identical to a sequential run; an exception in one lane does not lose the others.
Part 2 (pipeline / schema / cli): Flow C adds `report["profile_coherence"] = {status, findings}` from
`internal.check_profile(profile, as_of)`; the audit gets the N6 call and its gate status; `cli review` / `cli demo`
print `profile: warning (C1, N6.1)` for Robert.

Offline: COHERENCE_ANALYZER_LLM=mock, the store goes to a temporary directory, every fake is injected by monkeypatch.
"""
import concurrent.futures
import contextlib
import copy
import io
import json
import threading
import time
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from coherence_analyzer import cli, config, data, engine, explain, factors, pipeline, store
from coherence_analyzer.coherence import hard, internal, report as report_mod
from coherence_analyzer.llm import bedrock, extractors
from coherence_analyzer.rules import derive, limits as limits_mod

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "fixtures" / "robert"
AS_OF = "2026-11-12"
ROBERT_VALUE = 400_000
ROBERT_HORIZON_M = 48
META_NAMES = ("asset_meta", "etf_sector_weights", "activity_tags")
DISPLAY = ("objectif", "horizon", "risque", "contraintes", "preferences")
NON_PASS = ("horizon", "risque", "contraintes")       # Robert + NVDA 10%: the sections that call the writer
PASS_FACTORS = ("objectif", "preferences")
MODE_RANK = {"llm": 0, "llm_retry": 1, "template": 2}
N6_NODE = "profile_consistency"
PROFILE_LINE = "profile: warning (C1, N6.1)"
LLM_CALL_KEYS = {"node", "model_id", "prompt_version", "input_hash", "cache_hit", "latency_ms", "tokens_in",
                 "tokens_out"}

_REAL_EXECUTOR = concurrent.futures.ThreadPoolExecutor
_REAL_CHECK_PROFILE = internal.check_profile


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    monkeypatch.setattr(bedrock, "COHERENCE_ANALYZER_LLM", "mock")
    monkeypatch.setenv("COHERENCE_ANALYZER_LLM", "mock")
    monkeypatch.setenv("COHERENCE_ANALYZER_STORE_DIR", str(tmp_path / "store"))


# ---------------------------------------------------------------- part 1: explain lanes (§20.10)

def _build_payload(root, market, locale: str = "fr-CA") -> dict:
    """Robert + NVDA 10% (same construction as tests/test_explain.py): horizon, risque, contraintes are non-pass."""
    raw = json.loads((root / "fixtures" / "robert" / "profile.json").read_text(encoding="utf-8"))
    profile = derive.derive_all(copy.deepcopy(raw), portfolio_value_cad=ROBERT_VALUE, as_of=AS_OF)
    lim, trace = limits_mod.build_limits(profile, ROBERT_VALUE)
    profile["limits"], profile["limits_trace"] = lim, trace
    metrics = engine.analyse(market, engine.ROBERT_NVDA, ROBERT_HORIZON_M, ROBERT_VALUE)
    checks = hard.run_all({t: v / 100 for t, v in engine.ROBERT_NVDA.items()}, metrics, profile,
                          data.load_assets(), {n: data.load_meta(n) for n in META_NAMES})
    ctx = factors.SectionContext(profile=profile, limits=lim, limits_trace=trace, checks_current=checks,
                                 metrics_current=metrics, locale=locale)
    sections = factors.build_sections(ctx)
    verdict = report_mod.verdict_from_statuses(c["status"] for c in checks)
    return {"verdict": verdict, "locale": locale, "sections": sections}


@pytest.fixture(scope="module")
def payload(root, market) -> dict:
    p = _build_payload(root, market)
    assert [s["factor"] for s in p["sections"]] == list(DISPLAY)
    assert [s["factor"] for s in p["sections"] if s["status"] != "pass"] == list(NON_PASS)
    return p


def _fake_narrative(facts: dict, mode: str) -> dict:
    """A deterministic narrative for `facts` (template text, so it is a valid N5 result), labelled with `mode`."""
    return {"mode": mode, "segments": copy.deepcopy(explain.template_section(facts)["segments"]),
            "verify": {"passed": True, "failures": []}, "attempts": []}


class Lanes:
    """Recorder standing in for `explain.narrate_section`: records the calls, the threads, optionally sleeps, waits
    on a barrier shared by the non-pass lanes, or raises for one factor."""

    def __init__(self, modes: dict[str, str], delays: dict[str, float] | None = None, barrier_parties: int = 0,
                 raise_for: str | None = None):
        self.modes, self.delays, self.raise_for = modes, delays or {}, raise_for
        self.barrier = threading.Barrier(barrier_parties) if barrier_parties else None
        self.calls: list[str] = []
        self.threads: dict[str, int] = {}
        self.overlapped: dict[str, bool] = {}
        self.lock = threading.Lock()

    def __call__(self, facts: dict) -> dict:
        factor = facts["factor"]
        with self.lock:
            self.calls.append(factor)
            self.threads[factor] = threading.get_ident()
        if facts["status"] != "pass":
            if self.barrier is not None:
                try:
                    self.barrier.wait(timeout=2)
                    self.overlapped[factor] = True
                except threading.BrokenBarrierError:
                    self.overlapped[factor] = False
            time.sleep(self.delays.get(factor, 0))
            if factor == self.raise_for:
                raise RuntimeError(f"boom in {factor}")
        return _fake_narrative(facts, self.modes.get(factor, "template"))


def _reference(payload: dict, lanes: Lanes) -> dict:
    """What a plain sequential loop over the sections produces (headline, disclaimer and mode are code, §20.5)."""
    out_sections = [{"factor": s["factor"], "narrative": _fake_narrative(s["facts"], lanes.modes.get(s["factor"], "template"))}
                    for s in payload["sections"]]
    modes = [n["narrative"]["mode"] for s, n in zip(payload["sections"], out_sections) if s["status"] != "pass"]
    return {"sections": out_sections, "mode": max(modes, key=MODE_RANK.__getitem__),
            "segments": [{"factor": n["factor"], **seg} for n in out_sections for seg in n["narrative"]["segments"]],
            "headline": explain.headline(payload["verdict"], payload["sections"], payload["locale"]),
            "disclaimer": explain.disclaimer(payload["locale"])}


def _spy_executor(monkeypatch) -> list:
    """Replace ThreadPoolExecutor (module attribute and `concurrent.futures`) by a subclass recording max_workers."""
    created: list = []

    class Spy(_REAL_EXECUTOR):
        def __init__(self, *args, **kwargs):
            created.append(kwargs.get("max_workers", args[0] if args else None))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor", Spy)
    monkeypatch.setattr(explain, "ThreadPoolExecutor", Spy, raising=False)
    return created


def _assert_matches_reference(out: dict, payload: dict, lanes: Lanes) -> None:
    ref = _reference(payload, lanes)
    assert out["mode"] == ref["mode"]
    assert out["headline"] == ref["headline"]
    assert out["disclaimer"] == ref["disclaimer"]
    assert [s["factor"] for s in out["sections"]] == list(DISPLAY)                  # display order, not completion order
    for got, want, sec in zip(out["sections"], ref["sections"], payload["sections"]):
        if sec["status"] != "pass":
            assert got["narrative"] == want["narrative"], sec["factor"]
        else:
            # SPEC-AMBIGUITY: a pass section may be narrated inline or in a lane; only mode and text are pinned
            assert got["narrative"]["mode"] == "template"
            assert got["narrative"]["segments"] == explain.template_section(sec["facts"])["segments"]
    assert [s["factor"] for s in out["segments"]] == [s["factor"] for s in ref["segments"]]
    assert out["segments"] == ref["segments"]


def test_explain_uses_a_five_worker_thread_pool_and_matches_the_sequential_run(monkeypatch, payload):
    """§20.10 / D8.2: non-pass sections run on a ThreadPoolExecutor(max_workers=5); mode, headline, segments and
    section order equal a sequential run even though the lanes finish in reverse order."""
    created = _spy_executor(monkeypatch)
    lanes = Lanes({"horizon": "llm", "risque": "llm", "contraintes": "llm_retry"},
                  delays={"horizon": 0.3, "risque": 0.2, "contraintes": 0.1})        # finish order: reverse of display
    monkeypatch.setattr(explain, "narrate_section", lanes)
    out = explain.explain(copy.deepcopy(payload))
    assert created, "explain.explain must use concurrent.futures.ThreadPoolExecutor"
    assert all(w == 5 for w in created), created
    assert out["mode"] == "llm_retry"                                                   # worst of the non-pass modes
    _assert_matches_reference(out, payload, lanes)
    for factor in NON_PASS:
        assert factor in lanes.calls


def test_explain_output_is_independent_of_lane_timing(monkeypatch, payload):
    """D8.2: the same payload with opposite lane delays gives byte-identical explanations."""
    results = []
    for delays in ({"horizon": 0.2, "risque": 0.1, "contraintes": 0.0}, {"horizon": 0.0, "risque": 0.1, "contraintes": 0.2}):
        lanes = Lanes({"horizon": "llm", "risque": "llm_retry", "contraintes": "llm"}, delays=delays)
        monkeypatch.setattr(explain, "narrate_section", lanes)
        results.append(json.dumps(explain.explain(copy.deepcopy(payload)), sort_keys=True, ensure_ascii=False))
    assert results[0] == results[1]


def test_non_pass_sections_really_overlap(monkeypatch, payload):
    """§20.10: all three non-pass lanes are inside narration at the same time (a barrier that only opens when the
    three are running together), on worker threads, never on the calling thread."""
    lanes = Lanes({}, barrier_parties=len(NON_PASS))
    monkeypatch.setattr(explain, "narrate_section", lanes)
    explain.explain(copy.deepcopy(payload))
    assert lanes.overlapped == {f: True for f in NON_PASS}, lanes.overlapped
    idents = {lanes.threads[f] for f in NON_PASS}
    assert len(idents) == len(NON_PASS)
    assert threading.get_ident() not in idents


def test_total_wall_time_is_well_under_the_sum_of_section_sleeps(monkeypatch, payload):
    """§20.10: three lanes sleeping 0.4 s each (sum 1.2 s) finish in well under the sum."""
    lanes = Lanes({}, delays={f: 0.4 for f in NON_PASS})
    monkeypatch.setattr(explain, "narrate_section", lanes)
    t0 = time.perf_counter()
    explain.explain(copy.deepcopy(payload))
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.8, f"{elapsed:.2f}s: lanes did not overlap (sequential would take >= 1.2s)"


def test_exception_in_one_lane_falls_back_to_the_template_and_keeps_the_others(monkeypatch, payload):
    """§20.10 + §20.5: narration of `risque` raises; that section gets the template (as for LLMCacheMiss), the other
    sections keep their results, display order is kept, the call does not raise."""
    lanes = Lanes({"horizon": "llm", "risque": "llm", "contraintes": "llm"}, raise_for="risque")
    monkeypatch.setattr(explain, "narrate_section", lanes)
    out = explain.explain(copy.deepcopy(payload))
    by = {s["factor"]: s["narrative"] for s in out["sections"]}
    assert [s["factor"] for s in out["sections"]] == list(DISPLAY)
    facts = {s["factor"]: s["facts"] for s in payload["sections"]}
    assert by["risque"]["mode"] == "template"
    assert by["risque"]["segments"] == explain.template_section(facts["risque"])["segments"]
    assert by["horizon"] == _fake_narrative(facts["horizon"], "llm")
    assert by["contraintes"] == _fake_narrative(facts["contraintes"], "llm")
    assert out["mode"] == "template"                                                    # worst section mode
    flat = [(s["factor"], s["text"]) for s in out["segments"]]
    for factor in ("horizon", "risque", "contraintes"):
        assert any(f == factor for f, _ in flat), factor                                # nothing was lost
    assert out["headline"] == explain.headline(payload["verdict"], payload["sections"], payload["locale"])


def _good_writer(calls: list, boom_for: str | None = None, miss_for: str | None = None):
    """Stand-in for `extractors.explainer_call`: a verifier-clean answer (the template segments), or an error."""
    lock = threading.Lock()

    def explainer_call(facts: dict, retry_failures=None) -> dict:
        with lock:
            calls.append((facts["factor"], retry_failures))
        if facts["factor"] == boom_for:
            raise RuntimeError("writer exploded")
        if facts["factor"] == miss_for:
            raise bedrock.LLMCacheMiss("no recording")
        return {"output": {"segments": copy.deepcopy(explain.template_section(facts)["segments"])},
                "call_log": {"node": "explainer", "cache_hit": True}}

    return explainer_call


def test_real_narration_with_one_failing_writer_keeps_the_other_sections(monkeypatch, payload):
    """§20.10: with the real `narrate_section`, a writer exception in `risque` ends in the template for that section
    only; `horizon` and `contraintes` stay `llm`; the pass sections never reach the writer."""
    calls: list = []
    monkeypatch.setattr(extractors, "explainer_call", _good_writer(calls, boom_for="risque"))
    out = explain.explain(copy.deepcopy(payload))
    by = {s["factor"]: s["narrative"] for s in out["sections"]}
    assert [s["factor"] for s in out["sections"]] == list(DISPLAY)
    assert by["horizon"]["mode"] == "llm" and by["contraintes"]["mode"] == "llm"
    assert by["risque"]["mode"] == "template"
    facts = {s["factor"]: s["facts"] for s in payload["sections"]}
    assert by["risque"]["segments"] == explain.template_section(facts["risque"])["segments"]
    assert by["risque"]["verify"]["passed"] is True
    assert out["mode"] == "template"
    assert {f for f, _ in calls} <= set(NON_PASS)                                       # pass sections: no LLM call
    assert {f for f, _ in calls} >= {"horizon", "contraintes"}


def test_pass_sections_make_no_llm_call_in_the_lanes(monkeypatch, payload):
    """§20.5 / §20.10: the writer is only called for non-pass sections, once per section when it answers well; a
    cache miss on one section does not stop the others."""
    calls: list = []
    monkeypatch.setattr(extractors, "explainer_call", _good_writer(calls, miss_for="contraintes"))
    out = explain.explain(copy.deepcopy(payload))
    called = [f for f, _ in calls]
    for factor in PASS_FACTORS:
        assert factor not in called, factor
    assert sorted(called) == sorted(NON_PASS)                                           # one call each, no retry
    by = {s["factor"]: s["narrative"] for s in out["sections"]}
    assert by["horizon"]["mode"] == "llm" and by["risque"]["mode"] == "llm"
    assert by["contraintes"]["mode"] == "template"
    for factor in PASS_FACTORS:
        assert by[factor]["mode"] == "template"


# ---------------------------------------------------------------- part 2: profile_coherence in Flow C (§20.9, §20.10)

def _fake_finding(fid: str) -> dict:
    """A finding in the §20.9 shape (rule findings carry `values`, N6 findings carry `quotes`)."""
    base = {"id": fid, "status": "warning", "reason": None, "question": {"fr-CA": "Question?", "en-CA": "Question?"}}
    if fid.startswith("N6."):
        return {**base, "source": "llm:profile_consistency", "kind": "contradiction",
                "fields": ["objectives.primary", "values.notes"], "quotes": ["growth", "no tech"],
                "reason": "The two answers pull in different directions."}
    return {**base, "source": f"rule:{fid}", "kind": "contradiction",
            "fields": ["risk.declared", "objectives.primary"], "values": ["low", "growth"]}


def _fake_check_profile(ids=("C2", "C3", "N6.1"), gate_status="accepted"):
    log = {"node": N6_NODE, "model_id": "fixture:test", "prompt_version": "profile_consistency.v1",
           "input_hash": "sha256:" + "0" * 64, "cache_hit": True, "latency_ms": 0, "tokens_in": 0, "tokens_out": 0}

    def check_profile(profile, as_of, *, use_llm=True):
        return {"status": "warning", "findings": [_fake_finding(i) for i in ids],
                "llm": {"gate_status": gate_status, "call_log": copy.deepcopy(log)}}

    return check_profile


def _patch_check_profile(monkeypatch, fake) -> None:
    # SPEC-AMBIGUITY: §20.9 names `internal.check_profile`; the pipeline must reach it through the module attribute
    # (patched here); `pipeline.check_profile` is patched too in case it is imported by name.
    monkeypatch.setattr(internal, "check_profile", fake)
    monkeypatch.setattr(pipeline, "check_profile", fake, raising=False)


def _review(portfolio: str | None = None) -> tuple[dict, dict]:
    """Flow C on the Robert fixtures: (the saved report, the audit record). The store is the temp dir of the test."""
    rep = pipeline.review(FIX, portfolio)
    saved = json.loads((store.root() / "reports" / f"{rep['run_id']}.json").read_text(encoding="utf-8"))
    return saved, store.load_audit(rep["run_id"])


def _validator() -> Draft202012Validator:
    schema = json.loads((config.SCHEMAS_DIR / "report.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _expected_block() -> dict:
    raw = json.loads((FIX / "profile.json").read_text(encoding="utf-8"))
    res = _REAL_CHECK_PROFILE(raw, AS_OF)
    return store.plain({"status": res["status"], "findings": res["findings"]})


def test_robert_report_has_the_profile_coherence_block():
    """§20.9: every Flow C report carries `profile_coherence = {status, findings}`; Robert: status warning, findings
    C1 (rule) then N6.1 (llm), equal to `internal.check_profile(profile, as_of)` with as_of = portfolio.as_of."""
    rep, _ = _review()
    block = rep["profile_coherence"]
    assert set(block) == {"status", "findings"}                                         # no `llm` key in the report
    assert block["status"] == "warning"
    assert [f["id"] for f in block["findings"]] == ["C1", "N6.1"]
    c1, n6 = block["findings"]
    assert c1["source"] == "rule:C1" and c1["status"] == "warning"
    assert n6["source"] == "llm:profile_consistency" and n6["status"] in ("warning", "info")
    for f in block["findings"]:
        assert set(f["question"]) == {"fr-CA", "en-CA"} and all(f["question"].values())
        assert f["status"] != "violation"                                               # inv. 3
    assert block == _expected_block()


def test_review_calls_check_profile_once_with_the_portfolio_as_of(monkeypatch):
    """D1 + §20.9: `check_profile` runs once per review, with `as_of = portfolio.as_of` (2026-11-12), never the clock."""
    seen: list[tuple] = []

    def spy(profile, as_of, **kwargs):
        seen.append((profile["meta"]["profile_id"], as_of, kwargs))
        return _REAL_CHECK_PROFILE(profile, as_of, **kwargs)

    _patch_check_profile(monkeypatch, spy)
    _review()
    assert len(seen) == 1
    assert seen[0][0] == "persona-robert" and seen[0][1] == AS_OF


@pytest.mark.parametrize("portfolio,verdict", [(None, "coherent"), ("portfolio_review.json", "incoherent")])
def test_profile_coherence_never_changes_verdict_or_section_status(monkeypatch, portfolio, verdict):
    """§20.9 / D3: the block is separate; with `check_profile` returning 3 warnings the verdict, every section status,
    every check and the explanation mode stay what they are without it."""
    base, _ = _review(portfolio)
    assert base["verdict"] == verdict                                                   # measured context (D7 point 5)
    _patch_check_profile(monkeypatch, _fake_check_profile())
    rep, _ = _review(portfolio)
    assert rep["profile_coherence"]["status"] == "warning"
    assert [f["id"] for f in rep["profile_coherence"]["findings"]] == ["C2", "C3", "N6.1"]
    assert rep["verdict"] == base["verdict"]
    assert [(s["factor"], s["status"]) for s in rep["sections"]] == [(s["factor"], s["status"]) for s in base["sections"]]
    assert rep["checks_current"] == base["checks_current"]
    assert rep["unknowns"] == base["unknowns"]
    assert rep["explanation"]["headline"] == base["explanation"]["headline"]
    assert rep["explanation"]["mode"] == base["explanation"]["mode"]
    assert rep["profile_coherence"] != base["profile_coherence"]


def test_report_with_the_new_block_is_schema_valid_and_the_block_is_required():
    """§13 / §20.9: report.json requires `profile_coherence`; status in pass|info|warning (never violation, inv. 3)."""
    schema = json.loads((config.SCHEMAS_DIR / "report.json").read_text(encoding="utf-8"))
    assert "profile_coherence" in schema["required"]
    assert "profile_coherence" in schema["properties"]
    validator = _validator()
    rep, _ = _review()
    assert sorted(validator.iter_errors(rep), key=lambda e: list(e.absolute_path)) == []
    missing = {k: v for k, v in rep.items() if k != "profile_coherence"}
    assert list(validator.iter_errors(missing)), "a report without profile_coherence must be rejected"
    for status in ("pass", "info", "warning"):
        ok = {**rep, "profile_coherence": {"status": status, "findings": []}}
        assert list(validator.iter_errors(ok)) == [], status
    # SPEC-AMBIGUITY: §20.9 says N6 and the rules never produce `violation`; the stricter reading rejects it in the schema
    bad = {**rep, "profile_coherence": {"status": "violation", "findings": []}}
    assert list(validator.iter_errors(bad)), "status `violation` must not validate"
    nostatus = {**rep, "profile_coherence": {"findings": []}}
    assert list(validator.iter_errors(nostatus))


def test_report_with_three_warnings_validates_against_the_schema(monkeypatch):
    """§20.9 finding shape (rule findings with `values`, N6 findings with `quotes`) is accepted by report.json."""
    _patch_check_profile(monkeypatch, _fake_check_profile())
    rep, _ = _review()
    assert [f["id"] for f in rep["profile_coherence"]["findings"]] == ["C2", "C3", "N6.1"]
    errors = sorted(_validator().iter_errors(rep), key=lambda e: list(e.absolute_path))
    assert errors == [], [e.message for e in errors]


def test_audit_records_the_n6_call_and_its_gate_status():
    """SPEC §10 + §20.9: the audit `llm_calls` holds one `profile_consistency` entry with the call log fields and the
    gate status (`accepted` on Robert); it is the call log `check_profile` returned."""
    _, audit = _review()
    entries = [c for c in audit["llm_calls"] if c["node"] == N6_NODE]
    assert len(entries) == 1
    entry = entries[0]
    assert LLM_CALL_KEYS <= set(entry)
    assert entry["gate"] == "accepted"
    expected = _REAL_CHECK_PROFILE(json.loads((FIX / "profile.json").read_text(encoding="utf-8")), AS_OF)
    assert expected["llm"]["gate_status"] == "accepted"
    log = expected["llm"]["call_log"]
    for key in ("model_id", "prompt_version", "input_hash", "cache_hit", "tokens_in", "tokens_out"):
        assert entry[key] == log[key], key
    assert audit["as_of"] == AS_OF
    assert audit["flow"] == "C"


def test_audit_carries_the_gate_status_whatever_it_is(monkeypatch):
    """§20.9 / §11: a rejected N6 output is still logged with its gate status (`rejected:evidence`)."""
    _patch_check_profile(monkeypatch, _fake_check_profile(ids=("C2",), gate_status="rejected:evidence"))
    rep, audit = _review()
    entries = [c for c in audit["llm_calls"] if c["node"] == N6_NODE]
    assert len(entries) == 1 and entries[0]["gate"] == "rejected:evidence"
    assert entries[0]["input_hash"] == "sha256:" + "0" * 64
    assert [f["id"] for f in rep["profile_coherence"]["findings"]] == ["C2"]


def test_reports_are_deterministic_apart_from_the_run_id(tmp_path, monkeypatch):
    """Inv. 8: two reviews of the same inputs give identical reports once the run_id is masked (threads included)."""
    texts = []
    for name in ("one", "two"):
        monkeypatch.setenv("COHERENCE_ANALYZER_STORE_DIR", str(tmp_path / name))
        rep = pipeline.review(FIX, "portfolio_review.json")
        raw = (store.root() / "reports" / f"{rep['run_id']}.json").read_text(encoding="utf-8")
        texts.append(raw.replace(rep["run_id"], "<RUN_ID>"))
    assert texts[0] == texts[1]
    assert '"profile_coherence"' in texts[0]


# ---------------------------------------------------------------- cli (§12)

def _cli(argv: list[str]) -> list[str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(argv)
    return out.getvalue().splitlines()


def test_cli_review_prints_one_profile_line():
    """§12 + §20.9: `cli review` prints exactly one line `profile: warning (C1, N6.1)` for Robert."""
    lines = _cli(["review", str(FIX)])
    # SPEC-AMBIGUITY: the stricter reading is an unindented line, exactly as written in the task
    assert [ln.rstrip() for ln in lines].count(PROFILE_LINE) == 1
    assert len([ln for ln in lines if ln.startswith("profile:")]) == 1


def test_cli_review_profile_line_lists_the_finding_ids_in_order(monkeypatch):
    """§12: the line is `profile: <status> (<ids joined by ', '>)`, whatever the findings are."""
    _patch_check_profile(monkeypatch, _fake_check_profile())
    lines = _cli(["review", str(FIX), "--portfolio", "portfolio_review.json"])
    assert [ln.rstrip() for ln in lines].count("profile: warning (C2, C3, N6.1)") == 1


def test_cli_demo_prints_a_profile_line_per_review():
    """§12 + D7: `cli demo` reviews Robert's current and review portfolios, then every other persona whose fixtures are
    complete (P1.1 completes Amira and Mateo, §16), and prints the profile line for each report.
    Oracle fix (P1.1): the P0.12 version pinned exactly two lines, valid only while Amira/Mateo were incomplete."""
    import re
    lines = _cli(["demo"])
    profile_lines = [ln.rstrip() for ln in lines if ln.startswith("profile:")]
    others = [n for n in ("amira", "mateo")
              if all((ROOT / "fixtures" / n / f).exists() for f in ("profile.json", "events.jsonl", "portfolio.json"))]
    assert len(profile_lines) == 2 + len(others)
    assert profile_lines[:2] == [PROFILE_LINE, PROFILE_LINE]
    assert all(re.fullmatch(r"profile: (pass|info|warning)( \([A-Z0-9., ]+\))?", ln) for ln in profile_lines)
