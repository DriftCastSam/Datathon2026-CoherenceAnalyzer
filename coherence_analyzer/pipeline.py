"""Flows A (profile refresh) and C (portfolio review) (SPEC §4; docs/spec-decisions.md D7). Flow B is P2.

P0.9 scope (D7): Flow A = rules (derive -> limits + trace) plus N3 `values_parser` through the gate, only when
`values.notes` changed; no N1/N2. Flow C = engine.analyse(current) -> hard checks + S2/S4 -> verdict -> five sections
-> narration loop (N5 + verifier V1-V5 + one retry + template) -> report + audit. `check` (Flow B) stays unwired.

Inputs live in a fixture directory (`profile.json`, `events.jsonl`, `portfolio*.json`); those files are never rewritten.
Profile versions, portfolios, reports and audit records go to the store (`store.py`). The reference date is
`portfolio.as_of` (D1), never the wall clock; the clock is read only for audit/profile timestamps and the run id.
"""
from __future__ import annotations

import copy
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from . import config, data, engine, explain, factors, gate, store, stress
from .coherence import hard, internal, report as report_mod, soft
from .llm import bedrock, extractors
from .rules import derive, limits as limits_mod

META_NAMES = ("asset_meta", "etf_sector_weights", "activity_tags")
DEFAULT_PORTFOLIO = "portfolio.json"
DEFAULT_LOCALE = "fr-CA"
N3_NODE = extractors.N3_NODE
N2_NODE = extractors.N2_NODE
N6_NODE = extractors.N6_NODE

# Flow C assumptions on top of the engine's own list (README -> Assumptions).
FLOW_C_ASSUMPTIONS = (
    "Review of the current portfolio only: no trade is simulated, no resizer, no before/after diff (Flow B is P2).",
    "Horizon for the simulation = the declared objectives.horizon_years x 12 months; limits use the effective horizon.",
    "Soft checks S1 and S3 are not run (they need the behaviour classifier, which is not in this pipeline); "
    "S2 and S4 are.",
    "ETF look-through is not available: employer-sector and sector-share checks on ETFs are reported as unknown.",
)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _val(node: Any) -> Any:
    return node["value"] if isinstance(node, dict) and "value" in node else node


# ---------------------------------------------------------------- loading and validation

@lru_cache(maxsize=1)
def _profile_validator() -> Draft202012Validator:
    schema = json.loads((config.SCHEMAS_DIR / "profile.v2.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def validate_profile(profile: dict, where: str) -> None:
    errors = sorted(_profile_validator().iter_errors(profile), key=lambda e: [str(p) for p in e.absolute_path])
    if errors:
        raise ValueError(f"{where}: profile does not match profile.v2.json: "
                         + "; ".join(f"{'.'.join(map(str, e.absolute_path))}: {e.message}" for e in errors[:5]))


def _load_profile(fixture_dir: Path) -> dict:
    path = Path(fixture_dir) / "profile.json"
    profile = json.loads(path.read_text(encoding="utf-8"))
    validate_profile(profile, str(path))
    return profile


def _resolve_portfolio(fixture_dir: Path, portfolio: str | Path | None) -> Path:
    name = Path(portfolio) if portfolio else Path(DEFAULT_PORTFOLIO)
    path = name if name.is_absolute() else Path(fixture_dir) / name
    if not path.exists() and portfolio and Path(portfolio).exists():
        path = Path(portfolio)
    if not path.exists():
        raise FileNotFoundError(f"portfolio file not found: {path}")
    return path


def _load_portfolio(fixture_dir: Path, portfolio: str | Path | None, profile: dict, assets) -> dict:
    path = _resolve_portfolio(fixture_dir, portfolio)
    pf = data.load_portfolio(path, assets)
    pid = profile["meta"]["profile_id"]
    if pf.get("profile_id") != pid:
        raise ValueError(f"{path}: portfolio belongs to {pf.get('profile_id')!r}, the profile is {pid!r}")
    return pf


def _events(fixture_dir: Path, upto_event_id: str | None = None) -> list[dict]:
    events = store.read_events(Path(fixture_dir) / "events.jsonl")
    if upto_event_id is None:
        return events
    ids = [e["event_id"] for e in events]
    if upto_event_id not in ids:
        raise ValueError(f"unknown event_id {upto_event_id!r}")
    return events[:ids.index(upto_event_id) + 1]


# ---------------------------------------------------------------- Flow A pieces

def notes_changed(profile: dict) -> bool:
    """True when `values.notes` needs N3: no parsed result yet, or the parsed rules do not account for the notes.
    The notes are unchanged if the stored `input_hash` is the hash of the N3 payload, or (hand-written fixtures,
    hashed differently) if the quotes of the parsed rules cover all of the notes."""
    values = profile.get("values", {})
    notes = values.get("notes")
    if not isinstance(notes, str) or not notes.strip():
        return False
    node = values.get("parsed_notes")
    if not isinstance(node, dict):
        return True
    if node.get("input_hash") == bedrock.input_hash(extractors.n3_payload(notes)):
        return False
    rest = notes
    for r in node.get("value") or []:
        quote = r.get("quote") if isinstance(r, dict) else None
        if isinstance(quote, str) and quote:
            rest = rest.replace(quote, " ")
    return bool(re.sub(r"[\W_]+", "", rest))


def _call_entry(log: dict, gate_status: str, section: str | None = None) -> dict:
    entry = {k: log.get(k) for k in ("node", "model_id", "prompt_version", "input_hash", "cache_hit", "latency_ms",
                                     "tokens_in", "tokens_out")}
    entry["gate"] = gate_status
    if section:
        entry["section"] = section
    return entry


def _run_n3(profile: dict, events: list[dict]) -> tuple[dict, list[dict], list[dict]]:
    """A2/A3 for N3 only: returns (profile, llm_call entries, errors). The previous value stays on any failure."""
    if not notes_changed(profile):
        return profile, [], []
    notes = profile["values"]["notes"]
    try:
        result = extractors.values_parser_call(notes)
    except (bedrock.LLMCacheMiss, bedrock.LLMOutputInvalid) as exc:
        return profile, [], [{"node": N3_NODE, "error": f"{type(exc).__name__}: {exc}"}]
    log = result["call_log"]
    context = {"event_ids": {e["event_id"] for e in events}, "notes": notes, "model_id": log["model_id"],
               "prompt_version": log["prompt_version"], "input_hash": log["input_hash"], "updated_at": _now()}
    new_field, status = gate.gate(N3_NODE, profile["values"].get("parsed_notes"), result["output"], context)
    out = copy.deepcopy(profile)
    if status == "accepted" and new_field is not None:
        out["values"]["parsed_notes"] = new_field
    return out, [_call_entry(log, status)], []


def _run_n2(profile: dict, events: list[dict]) -> tuple[dict, list[dict], list[dict]]:
    """A2/A3 for N2 (opt-in, config.N2_QUESTION_RUBRIC, D7): one call per question event, oldest first, each output
    gated into knowledge.question_quality. A failed call or a rejected output keeps the previous value; the other
    questions still apply. Returns (profile, llm_call entries, errors)."""
    if not config.N2_QUESTION_RUBRIC:
        return profile, [], []
    out = copy.deepcopy(profile)
    calls: list[dict] = []
    errors: list[dict] = []
    ids = {e["event_id"] for e in events}
    by_id = {e["event_id"]: e for e in events}
    for ev in extractors.n2_events(events):
        try:
            result = extractors.question_rubric_call(ev)
        except (bedrock.LLMCacheMiss, bedrock.LLMOutputInvalid) as exc:
            errors.append({"node": N2_NODE, "event_id": ev["event_id"], "error": f"{type(exc).__name__}: {exc}"})
            continue
        log = result["call_log"]
        context = {"event_ids": ids, "events_by_id": by_id, "event_id": ev["event_id"], "model_id": log["model_id"],
                   "prompt_version": log["prompt_version"], "input_hash": log["input_hash"], "updated_at": _now()}
        new_field, status = gate.gate(N2_NODE, out["knowledge"].get("question_quality"), result["output"], context)
        if status in ("accepted", "clamped") and new_field is not None:
            out["knowledge"]["question_quality"] = new_field
        calls.append(_call_entry(log, status))
    return out, calls, errors


_SKIP_KEYS = ("_note", "_conventions", "by", "history", "updated_at")


def _leaves(node: Any, path: str = ""):
    """(path, value, by) for every field: computed fields `{value, by}` count as one leaf; sorted for determinism."""
    if isinstance(node, dict):
        if "value" in node and "by" in node:
            yield path, node["value"], node["by"]
            return
        for k in sorted(node):
            if k not in _SKIP_KEYS:
                yield from _leaves(node[k], f"{path}.{k}" if path else k)
    else:
        yield path, node, None


def profile_diff(old: dict, new: dict) -> list[dict]:
    """SPEC §10 `profile_diff`: [{path, old, new, by}] for every field whose value changed (values only: a new `by`
    label, a history entry or a timestamp is not a change). `meta` is ignored."""
    def flat(p: dict) -> dict:
        body = store.plain({k: v for k, v in p.items() if k != "meta"})
        return {path: (v, by) for path, v, by in _leaves(body)}
    a, b = flat(old), flat(new)
    out = []
    for path in sorted(set(a) | set(b)):
        if a.get(path, (None, None))[0] != b.get(path, (None, None))[0]:
            by = b.get(path, (None, None))[1] or ("rule:limits_table" if path.startswith("limits") else "user:declared")
            out.append({"path": path, "old": a.get(path, (None, None))[0], "new": b.get(path, (None, None))[0],
                        "by": by})
    return out


def _refresh(profile_in: dict, pf: dict, events: list[dict]) -> dict:
    """A1-A6 on an in-memory profile: N3 if the notes changed (gated), N2 per question when its flag is on (gated),
    derive, limits + trace, validate, version. Saves the new profile version only when a value changed.
    Rules and N3 only by default: no N1 (D7); N2 is opt-in (config.N2_QUESTION_RUBRIC)."""
    p, calls, errors = _run_n3(profile_in, events)
    p, n2_calls, n2_errors = _run_n2(p, events)
    calls, errors = calls + n2_calls, errors + n2_errors
    # N3's output is not an input of any rule, and N2's only feeds knowledge.score (never limits), so deriving once
    # after them equals A1 + A4 of SPEC §4.
    p = derive.derive_all(p, portfolio_value_cad=pf["value_cad"], as_of=pf["as_of"])
    lim, trace = limits_mod.build_limits(p, pf["value_cad"])
    p["limits"], p["limits_trace"] = lim, trace
    p = store.plain(p)      # plain JSON types (tuples -> lists)
    diff = profile_diff(profile_in, p)
    version_in = int(profile_in["meta"].get("version", 1))
    version_out = version_in
    if diff:
        version_out = version_in + 1
        p["meta"]["version"] = version_out
        p["meta"]["updated_at"] = _now()
    validate_profile(p, "refreshed profile")
    if diff:
        store.save_profile(p)
    return {"profile": p, "version_in": version_in, "version_out": version_out, "diff": diff, "llm_calls": calls,
            "llm_errors": errors}


def _model_id(calls: list[dict]) -> str:
    if calls:
        return str(calls[0]["model_id"])
    return config.BEDROCK_MODEL_ID or bedrock.MOCK_MODEL_ID


def _audit(*, run_id: str, flow: str, started_at: str, ended_at: str, profile_id: str, as_of: str, version_in: int,
           version_out: int, events: list[dict], calls: list[dict], errors: list[dict], diff: list[dict],
           engine_info: dict | None, verdict: str | None, explanation_mode: str | None,
           section_modes: dict | None) -> dict:
    return {"run_id": run_id, "flow": flow, "started_at": started_at, "ended_at": ended_at,
            "profile_id": profile_id, "profile_version_in": version_in, "profile_version_out": version_out,
            "as_of": as_of, "events_considered": [e["event_id"] for e in events], "model_id": _model_id(calls),
            "llm_calls": calls, "llm_errors": errors, "profile_diff": diff, "engine": engine_info,
            "verdict": verdict, "explanation_mode": explanation_mode, "section_modes": section_modes}


def refresh(fixture_dir: Path, *, upto_event_id: str | None = None, portfolio: str | Path | None = None) -> dict:
    """Flow A: rules (+ N3 when `values.notes` changed, through the gate) -> limits -> validate/save/audit.
    Returns `{run_id, profile, profile_diff, audit}`."""
    fixture_dir = Path(fixture_dir)
    started = _now()
    run_id = store.new_run_id()
    profile_in = _load_profile(fixture_dir)
    pf = _load_portfolio(fixture_dir, portfolio, profile_in, data.load_assets())
    events = _events(fixture_dir, upto_event_id)
    res = _refresh(profile_in, pf, events)
    audit = _audit(run_id=run_id, flow="A", started_at=started, ended_at=_now(),
                   profile_id=profile_in["meta"]["profile_id"], as_of=pf["as_of"], version_in=res["version_in"],
                   version_out=res["version_out"], events=events, calls=res["llm_calls"], errors=res["llm_errors"],
                   diff=res["diff"], engine_info=None, verdict=None, explanation_mode=None, section_modes=None)
    store.save_audit(audit)
    return {"run_id": run_id, "profile": res["profile"], "profile_diff": res["diff"], "audit": audit}


def check(fixture_dir: Path, intent_id: str, *, auto_confirm: bool = False) -> dict:
    """Flow B: B1 intent_parser ... B9 report + audit. P2 (D7 point 3): not wired."""
    raise NotImplementedError("Flow B (N4 intent_parser, confirm form, trade diff) is P2; use `review` (docs/spec-decisions.md D7)")


# ---------------------------------------------------------------- Flow C

def _narrations(explanation: dict) -> tuple[list[dict], list[dict]]:
    """Writer calls for the audit, in call order, and the sections whose writer could not run (cache miss...)."""
    calls: list[dict] = []
    errors: list[dict] = []
    for sec in explanation["sections"]:
        nar = sec["narrative"]
        for att in nar.get("attempts", []):
            if att.get("call_log"):
                calls.append(_call_entry(att["call_log"], "accepted" if not att["failures"] else "rejected:verifier",
                                         sec["factor"]))
        if nar.get("llm_error"):
            errors.append({"node": extractors.N5_NODE, "section": sec["factor"], "error": nar["llm_error"]})
    return calls, errors


def _n6_audit(result: dict) -> tuple[list[dict], list[dict]]:
    """Audit entries for the N6 call of `check_profile`: its call log with the gate status; a skipped call (cache
    miss in mock mode) is an error entry instead."""
    llm = result.get("llm") or {}
    log, status = llm.get("call_log"), llm.get("gate_status")
    if log:
        return [_call_entry(log, str(status))], []
    if status:
        return [], [{"node": N6_NODE, "error": str(status)}]
    return [], []


def _goals_block(market, pf: dict, profile: dict, horizon_m: int, metrics: dict) -> tuple[dict, list[dict]]:
    """G1 (P2.14): a second simulation with the user-declared `situation.monthly_contribution` and
    `objectives.goals` (never `limits`, invariant 2). `metrics` (no contribution) stays the basis of H7-H10 and H14.
    Returns the report's `goals` block and the tagged G1 checks (shown in objectif facts, outside the verdict)."""
    contribution = float(_val((profile.get("situation") or {}).get("monthly_contribution")) or 0.0)
    goals = list((profile.get("objectives") or {}).get("goals") or [])
    block = {"monthly_contribution": contribution, "horizon_months": horizon_m, "goal_probs": [], "checks": [],
             "assumptions": list(metrics["assumptions"])}
    if not goals:
        return block, []
    gm = store.plain(engine.analyse(market, pf["weights_pct"], horizon_m, pf["value_cad"],
                                    monthly_contribution=contribution, goals=goals, as_of=pf["as_of"]))
    checks = report_mod.tag_checks(hard.g1(gm, profile))
    block.update(goal_probs=gm["goal_probs"], checks=store.plain(checks),
                 assumptions=list(checks[0]["assumptions"]))
    return block, checks


def review(fixture_dir: Path, portfolio: str | Path | None = None) -> dict:
    """Flow C: baseline coherence report for the current portfolio (B5-B9 without an intent). Writes the report and
    the audit record to the store and returns the report. `portfolio` = file name inside the fixture directory
    (default `portfolio.json`)."""
    fixture_dir = Path(fixture_dir)
    started = _now()
    run_id = store.new_run_id()
    assets = data.load_assets()
    profile_in = _load_profile(fixture_dir)
    pf = _load_portfolio(fixture_dir, portfolio, profile_in, assets)
    events = _events(fixture_dir)

    res = _refresh(profile_in, pf, events)                       # Flow A: rules + N3 (only if the notes changed)
    profile = res["profile"]
    locale = profile["meta"].get("locale", DEFAULT_LOCALE)
    horizon_m = int(round(float(_val(profile["objectives"]["horizon_years"])) * 12))
    market = engine.load_market(str(config.DATA_DIR))
    metrics = store.plain(engine.analyse(market, pf["weights_pct"], horizon_m, pf["value_cad"]))
    goals_block, goal_checks = _goals_block(market, pf, profile, horizon_m, metrics)
    meta = {n: data.load_meta(n) for n in META_NAMES}
    checks = (hard.run_all(pf["weights"], metrics, profile, assets, meta)
              + soft.run_all(profile, None, pf["weights"], assets))
    tagged = report_mod.tag_checks(checks)
    ctx = factors.SectionContext(profile=profile, limits=profile["limits"], limits_trace=profile["limits_trace"],
                                 checks_current=tagged, metrics_current=metrics, events=events, locale=locale,
                                 goal_checks=goal_checks)
    sections = report_mod.build_sections(ctx)
    verdict = report_mod.verdict(tagged)

    with ThreadPoolExecutor(max_workers=1) as pool:              # N6 = sixth lane next to the section lanes (§20.10)
        n6_lane = pool.submit(lambda: internal.check_profile(profile, pf["as_of"]))     # D1: as_of = portfolio.as_of
        explanation = explain.explain({"verdict": verdict, "locale": locale, "sections": sections})
        n6_res = n6_lane.result()
    profile_coherence = store.plain({"status": n6_res["status"], "findings": n6_res["findings"]})
    n6_calls, n6_errors = _n6_audit(n6_res)
    for sec, narrated in zip(sections, explanation["sections"]):
        nar = narrated["narrative"]
        sec["narrative"] = {"mode": nar["mode"], "segments": nar["segments"], "verify": nar["verify"]}
    n5_calls, n5_errors = _narrations(explanation)

    rep = report_mod.build_review(
        run_id=run_id, profile_id=profile["meta"]["profile_id"], profile_version=res["version_out"],
        portfolio_id=pf["portfolio_id"], checks=tagged, metrics=metrics, sections=sections,
        explanation={k: explanation[k] for k in ("mode", "headline", "segments", "disclaimer")},
        assumptions=list(metrics["assumptions"]) + list(FLOW_C_ASSUMPTIONS), data_window=list(market.window),
        disclaimer=config.DISCLAIMER[locale], profile_coherence=profile_coherence, goals=goals_block)
    rep["stress"] = stress.stress_block(pf["weights"], pf["value_cad"], assets, data.load_macro(), as_of=pf["as_of"],
                                        asset_meta=meta["asset_meta"], locale=locale)    # P2.16: info only
    store.save_portfolio(pf)
    store.save_report(rep)
    audit = _audit(
        run_id=run_id, flow="C", started_at=started, ended_at=_now(), profile_id=rep["profile_id"], as_of=pf["as_of"],
        version_in=res["version_in"], version_out=res["version_out"], events=events,
        calls=res["llm_calls"] + n5_calls + n6_calls, errors=res["llm_errors"] + n5_errors + n6_errors,
        diff=res["diff"],
        engine_info={"seed": config.ENGINE_SEED, "n": config.ENGINE_N, "block": config.ENGINE_BLOCK,
                     "drift": "assumed", "data_window": rep["data_window"], "horizon_months": horizon_m},
        verdict=verdict, explanation_mode=rep["explanation"]["mode"],
        section_modes={s["factor"]: s["narrative"]["mode"] for s in sections})
    store.save_audit(audit)
    return rep
