"""Soft checks S1-S4 (SPEC §7.4). Never `violation` (invariant 3).

P0.9 (docs/spec-decisions.md D7 point 3): S2 and S4 only. S1 and S3 need N1 `behaviour_classifier`, which is not in the
P0 pipeline, so `run_all` does not emit them (they come back with P2). Pure functions, fractions for weights.

S2 knowledge vs complexity: `knowledge.score < 50` and the portfolio holds crypto, TLT (long duration) or a single stock
above 5 %  -> `warning`. The source field must carry `confidence >= WARN_MIN_CONF`; otherwise `info`
("insufficient evidence"). No score at all -> `unknown`.
S4 parsed values rules: one `info` reminder per parsed rule the user has not approved (\"you wrote: '<quote>'\").
Approved rules are user declarations: the limit kinds feed H11/H13, the others are already acknowledged by the user.
"""
from __future__ import annotations

from ..config import WARN_MIN_CONF
from .hard import Check

ALLOWED = {"pass", "warning", "info", "unknown"}

S2_MIN_SCORE = 50.0
S2_SINGLE_STOCK_WEIGHT = 0.05
S2_LONG_DURATION = ("TLT",)
EPS = 1e-9


def _val(node):
    return node["value"] if isinstance(node, dict) and "value" in node else node


def _check(cid: str, status: str, subject: str | None, value, limit, unit: str, derived_from: list[str],
           source: str, confidence: float, message_key: str) -> Check:
    return Check(id=cid, kind="soft", status=status, subject=subject, value=value, limit=limit, unit=unit,
                 derived_from=derived_from, source=source, confidence=confidence, message_key=message_key)


def complex_holdings(weights: dict[str, float], assets) -> list[str]:
    """Tickers that make S2 relevant: crypto, long-duration bonds (TLT), single stocks above 5 %. Sorted."""
    out = []
    for t in sorted(t for t, w in weights.items() if w > 0):
        cls = assets.loc[t, "asset_class"]
        if cls == "crypto" or t in S2_LONG_DURATION or (cls == "equity" and weights[t] > S2_SINGLE_STOCK_WEIGHT + EPS):
            out.append(t)
    return out


def s2(profile: dict, weights: dict[str, float], assets) -> list[Check]:
    node = profile.get("knowledge", {}).get("score")
    der = ["knowledge.score", "knowledge.quiz_score", "knowledge.declared_level", "knowledge.question_quality"]
    source = "rule:knowledge_vs_complexity"
    if node is None or _val(node) is None:
        return [_check("S2", "unknown", None, None, S2_MIN_SCORE, "score", der, source, 1.0, "knowledge_score_missing")]
    score = float(_val(node))
    conf = float(node.get("confidence", 1.0)) if isinstance(node, dict) else 1.0
    if conf < WARN_MIN_CONF:
        return [_check("S2", "info", None, score, S2_MIN_SCORE, "score", der, source, conf, "insufficient_evidence")]
    held = complex_holdings(weights, assets) if score < S2_MIN_SCORE else []
    if held:
        return [_check("S2", "warning", ", ".join(held), score, S2_MIN_SCORE, "score", der, source, conf,
                       "knowledge_below_complexity")]
    return [_check("S2", "pass", None, score, S2_MIN_SCORE, "score", der, source, conf, "knowledge_ok")]


def s4(profile: dict) -> list[Check]:
    node = profile.get("values", {}).get("parsed_notes")
    rules = list(_val(node) or []) if node is not None else []
    conf = float(node.get("confidence", 0.8)) if isinstance(node, dict) else 0.8
    return [_check("S4", "info", r["quote"], None, None, "text", ["values.parsed_notes", "values.notes"],
                   "rule:unapproved_parsed_rule", conf, "parsed_rule_not_approved")
            for r in rules if isinstance(r, dict) and not r.get("approved_by_user") and r.get("quote")]


def run_all(profile: dict, intent: dict | None, weights: dict[str, float] | None = None, assets=None) -> list[Check]:
    """S2 (needs `weights` fractions and `assets`; skipped without them) then S4. `intent` is for S3 (P2, not run)."""
    out: list[Check] = []
    if weights is not None and assets is not None:
        out += s2(profile, weights, assets)
    return out + s4(profile)
