#!/usr/bin/env python3
"""Prototype: chat about a basket of trades the user picked (deploy/whatif.py `basket_scenario`).

The model only explains the facts payload built here from the deterministic report (verdict, checks, metrics,
the user's own trades). It never judges fit by itself: the verdict and the limits come from the hard checks.
Each answer is verified like an N5 narrative: no advisory wording (`explain.advisory_hits`) and every number must
exist in the facts (`explain.numcheck`). One retry with the failures, then a deterministic template answer.
"""
from __future__ import annotations

import json
import os
from typing import Any

from coherence_analyzer import config, explain
from coherence_analyzer.coherence import report as report_mod
from coherence_analyzer.factors import fmt

LOC = "en-CA"
MAX_TURNS = 12
MAX_TOKENS = 600
GUARDRAIL_BLOCKED = "\x00guardrail"
# The stack's classic-tier topic guardrail flags nearly every descriptive "your profile + this trade" answer as a
# personalised recommendation (false positives on plain facts). Opt in with BASKET_CHAT_GUARDRAIL=1; the
# deterministic verifier below runs on every answer either way.
USE_GUARDRAIL = os.getenv("BASKET_CHAT_GUARDRAIL", "0") == "1"
EXTRA_ADVISORY = ("i would", "i'd ", "my advice", "go for", "better option", "better choice", "best option",
                  "instead of", "you might", "you may", "it might be wise", "you need to", "recommend")

SYSTEM = """You are the explanation panel of CoherenceAnalyzer, an educational tool that compares a self-directed
investor's proposed trades with their OWN profile. You receive a JSON facts payload: the profile summary, the trades
the user picked (amounts in CAD), the portfolio metrics today and with all the trades applied together, the overall
verdict computed by deterministic rules, and every check that does not pass.

Rules, all mandatory:
- Answer only from the facts payload. If the answer is not in it, say the tool does not measure that.
- The verdict and the limit checks are computed by rules; repeat them, never override or soften them.
- Describe, never prescribe. Never tell the user to buy, sell, hold, reduce, increase, rebalance, switch or wait.
  Never write "you should", "you could", "consider", "I recommend", "it would be better", "next step", and never
  propose another ticker, size or funding source. Never rank options. Stating a measured effect is fine.
- Every number you write must appear in the facts payload, written the same way (copy the formatted strings).
  Do not compute new numbers (no differences, sums or ratios).
- Plain English, short: at most 5 sentences, no headings, no lists unless asked.
- If asked whether the trades "fit" the profile, answer with the verdict and the limits crossed, from the facts."""

VERDICT_TEXT = {"coherent": "coherent with the profile", "coherent_with_warnings": "coherent with warnings",
                "incoherent": "outside the profile (at least one hard limit is crossed)"}


def _pct(x: Any) -> str | None:
    return None if x is None else fmt.metric("vol", x, LOC)


def _metric_block(m: dict, value_cad: float) -> dict:
    out = {}
    for k in ("vol", "mdd", "p5_T", "p50_T", "p95_T", "loss_prob"):
        if k in m and m[k] is not None:
            out[k] = fmt.metric(k, m[k], LOC)
    out["portfolio_value"] = fmt.amount(value_cad, LOC)
    return out


def _check_line(c: dict) -> dict:
    unit = c.get("unit")
    shown = {**c, "value": fmt.check_value(c.get("value"), unit, LOC) if c.get("value") is not None else None,
             "limit": fmt.check_value(c.get("limit"), unit, LOC) if c.get("limit") is not None else None}
    shown = {k: v for k, v in shown.items() if v is not None}
    return {"id": c["id"], "subject": c.get("subject"), "status": c["status"], "text": explain._line(shown, LOC)}


def facts(ctx: dict, scen: dict) -> dict:
    """The only payload the model sees (and numcheck reads)."""
    p = ctx["profile"]
    risk = p.get("risk") or {}
    eff = risk.get("effective")
    eff = eff.get("value") if isinstance(eff, dict) else eff
    cap = risk.get("capacity")
    cap = cap.get("value") if isinstance(cap, dict) else cap
    obj = p.get("objectives") or {}
    return {
        "profile": {"name": (p.get("identity") or {}).get("name"), "risk_declared": risk.get("declared"),
                    "risk_effective": eff, "risk_capacity": cap,
                    "max_acceptable_drawdown": f"{risk.get('max_acceptable_drawdown_pct')} %"
                    if risk.get("max_acceptable_drawdown_pct") is not None else None,
                    "horizon_years": obj.get("horizon_years"),
                    "effective_horizon_years": (obj.get("effective_horizon_years") or {}).get("value")
                    if isinstance(obj.get("effective_horizon_years"), dict) else obj.get("effective_horizon_years"),
                    "primary_goal": obj.get("primary"),
                    "goals": [{"name": g.get("name"), "date": g.get("date"), "amount": fmt.amount(g["amount"], LOC)
                               if g.get("amount") is not None else None} for g in obj.get("goals") or []],
                    "planned_withdrawals": [{"date": w.get("date"), "amount": fmt.amount(w["amount"], LOC)
                                             if w.get("amount") is not None else None, "purpose": w.get("purpose")}
                                            for w in (p.get("situation") or {}).get("planned_withdrawals") or []]},
        "why_limits_exist": "Every hard limit (bond minimum, volatility band, drawdown, floor at horizon, single-stock "
                            "maximum) is set by rule from risk_effective, which is the lower of the declared risk "
                            "level and the risk capacity; capacity comes from the effective horizon (shortened by the "
                            "first planned withdrawal), income stability and the emergency fund. The floor at horizon "
                            "protects the planned withdrawals and goals.",
        "trades": [{"ticker": t["ticker"], "name": t["name"], "amount": fmt.amount(t["amount_cad"], LOC),
                    "funding": t["funding"], "weight_before": f"{t['weight_before_pct']} %",
                    "weight_after": f"{t['weight_after_pct']} %"} for t in scen["trades"]],
        "verdict_today": VERDICT_TEXT.get(report_mod.verdict(ctx["c0"]), report_mod.verdict(ctx["c0"])),
        "verdict_with_trades": VERDICT_TEXT.get(scen["verdict"], scen["verdict"]),
        "metrics_today": _metric_block(ctx["m0"], ctx["value"]),
        "metrics_with_trades": _metric_block(scen["metrics"], scen["value_cad"]),
        "checks_with_trades_not_passing": [_check_line(c) for c in scen["checks"] if c["status"] != "unknown"],
        "checks_today_not_passing": [_check_line(c) for c in ctx["c0"] if c["status"] not in ("pass", "unknown")],
        "checks_that_cannot_be_verified": sorted({c["id"] for c in scen["checks"] if c["status"] == "unknown"}),
        "horizon_note": "p5_T / p50_T / p95_T are the pessimistic / median / optimistic simulated values at the "
                        "horizon; vol is annual volatility; mdd is the maximum historical drawdown.",
    }


def template_answer(f: dict) -> str:
    """Deterministic fallback: verdict + first crossed limits, numbers straight from the facts."""
    lines = [f"With these trades, the portfolio is {f['verdict_with_trades']}."]
    bad = [c for c in f["checks_with_trades_not_passing"] if c["status"] in ("violation", "warning")]
    lines += [c["text"] for c in bad[:3]]
    if not bad:
        lines.append("No limit of your profile is crossed by these trades.")
    return " ".join(lines)


def _verify(text: str, f: dict) -> list[str]:
    if not text.strip():
        return ["empty answer"]
    low = explain._fold(text)
    problems = [f"advisory wording: {h!r}" for h in explain.advisory_hits(text)]
    problems += [f"advisory wording: {h!r}" for h in EXTRA_ADVISORY if explain._fold(h) in low]
    problems += [f"number not in the facts: {n}" for n in explain.numcheck(text, f)]
    return problems


def _converse(system: str, messages: list[dict]) -> str:
    import boto3
    client = boto3.client("bedrock-runtime", region_name=config.AWS_REGION)
    extra: dict[str, Any] = {}
    if USE_GUARDRAIL and config.BEDROCK_GUARDRAIL_ID and config.BEDROCK_GUARDRAIL_VERSION:
        extra["guardrailConfig"] = {"guardrailIdentifier": config.BEDROCK_GUARDRAIL_ID,
                                    "guardrailVersion": config.BEDROCK_GUARDRAIL_VERSION}
    resp = client.converse(modelId=config.BEDROCK_MODEL_ID, system=[{"text": system}], messages=messages,
                           inferenceConfig={"maxTokens": MAX_TOKENS}, **extra)
    if resp.get("stopReason") == "guardrail_intervened":
        return GUARDRAIL_BLOCKED
    return "".join(b.get("text", "") for b in resp["output"]["message"]["content"]).strip()


def answer(f: dict, history: list[dict]) -> dict:
    """history: [{role: user|assistant, text}], last one is the user's question.
    Returns {text, mode: llm|template, problems?}."""
    if config.COHERENCE_ANALYZER_LLM == "mock" or not config.BEDROCK_MODEL_ID:
        return {"text": template_answer(f), "mode": "template", "problems": ["llm not configured"]}
    turns = [h for h in history if h.get("role") in ("user", "assistant") and str(h.get("text", "")).strip()]
    turns = turns[-MAX_TURNS:]
    while turns and turns[0]["role"] != "user":
        turns = turns[1:]
    if not turns:
        return {"text": template_answer(f), "mode": "template"}
    payload = "FACTS (the only source you may use):\n" + json.dumps(f, ensure_ascii=False)
    msgs = [{"role": t["role"], "content": [{"text": str(t["text"])[:2000]}]} for t in turns]
    msgs[0] = {"role": "user", "content": [{"text": payload + "\n\nQUESTION: " + str(turns[0]["text"])[:2000]}]}
    if len(msgs) > 1:
        msgs[-1] = {"role": "user", "content": [{"text": "FACTS reminder: " + json.dumps(f, ensure_ascii=False)
                                                  + "\n\nQUESTION: " + str(turns[-1]["text"])[:2000]}]}
    problems: list[str] = []
    for _ in (1, 2):
        try:
            text = _converse(SYSTEM, msgs)
        except Exception as exc:  # noqa: BLE001 - prototype: any Bedrock failure falls back to the template
            problems = [f"bedrock: {exc}"]
            break
        if text == GUARDRAIL_BLOCKED:
            problems = ["blocked by the guardrail as a personalised recommendation: state the rule and the facts "
                        "only, do not say what any asset or asset class would do for the user"]
            text = "(blocked)"
        else:
            problems = _verify(text, f)
        if not problems:
            return {"text": text, "mode": "llm"}
        msgs = msgs + [{"role": "assistant", "content": [{"text": text}]},
                       {"role": "user", "content": [{"text": "Your answer failed verification: "
                                                     + "; ".join(problems)
                                                     + ". Rewrite it, same question, following every rule."}]}]
    return {"text": template_answer(f), "mode": "template", "problems": problems}
