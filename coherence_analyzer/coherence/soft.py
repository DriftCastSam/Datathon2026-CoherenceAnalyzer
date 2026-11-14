"""Soft checks S1-S4 (SPEC §7.4). Never `violation` (invariant 3).

P0.9 (docs/spec-decisions.md D7 point 3): Flow C runs S2 and S4 only: S1 and S3 need N1 `behaviour_classifier`, which is
not in the Flow C pipeline. Pure functions, fractions for weights.

S1 and S3 (P2.FB, Flow B only) read the gated N1 field `risk.behavioural`: `run_all(..., behaviour=True)` adds them, Flow C
does not. S1 = behaviour gap, S3 = hype pattern on the intent's own event. Both `warning | info | pass | unknown`.
S2 knowledge vs complexity: `knowledge.score < 50` and the portfolio holds crypto, TLT (long duration) or a single stock
above 5 %  -> `warning`. The source field must carry `confidence >= WARN_MIN_CONF`; otherwise `info`
("insufficient evidence"). No score at all -> `unknown`.
S4 parsed values rules: one `info` reminder per parsed rule the user has not approved (\"you wrote: '<quote>'\").
Approved rules are user declarations: the limit kinds feed H11/H13, the others are already acknowledged by the user.
"""
from __future__ import annotations

from ..config import WARN_MIN_CONF
from ..levels import LEVELS, idx
from .hard import Check

ALLOWED = {"pass", "warning", "info", "unknown"}

S2_MIN_SCORE = 50.0
S2_SINGLE_STOCK_WEIGHT = 0.05
S2_LONG_DURATION = ("TLT",)
EPS = 1e-9


def _val(node):
    return node["value"] if isinstance(node, dict) and "value" in node else node


class _AttrCheck(dict):
    """A `Check` dict that also reads as attributes (`c.status`): the N2 tests (P2.N2) read S2 checks that way.
    Still a plain dict for JSON, equality and every existing `c["status"]` caller."""

    def __getattr__(self, name: str):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def _check(cid: str, status: str, subject: str | None, value, limit, unit: str, derived_from: list[str],
           source: str, confidence: float, message_key: str) -> Check:
    return _AttrCheck(id=cid, kind="soft", status=status, subject=subject, value=value, limit=limit, unit=unit,
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


class SoftCheck(Check, total=False):
    evidence_events: list[str]     # S1 / S3: the real event_ids the (gated) N1 answer cites (invariant 5)


S1_WARN_GAP = 2
S3_SIGNAL = "hype_chasing"


def _behaviour(profile: dict) -> tuple[dict | None, list[dict]]:
    """The gated N1 field (`risk.behavioural`) and its evidence items; (None, []) when N1 never produced a value."""
    node = profile.get("risk", {}).get("behavioural")
    if not isinstance(node, dict) or _val(node) not in LEVELS:
        return None, []
    return node, [e for e in node.get("evidence", []) if isinstance(e, dict) and e.get("event_id")]


def _soft(cid: str, status: str, subject: str | None, value, limit, unit: str, derived_from: list[str], source: str,
          confidence: float, message_key: str, events: list[str] | None = None) -> SoftCheck:
    c = SoftCheck(**_check(cid, status, subject, value, limit, unit, derived_from, source, confidence, message_key))
    if events is not None:
        c["evidence_events"] = events
    return c


def s1(profile: dict) -> list[Check]:
    """Behaviour gap: `idx(behavioural) - idx(effective)`: >= 2 warning, 1 info, <= 0 pass. The source field must carry
    `confidence >= WARN_MIN_CONF` (else `info`, insufficient evidence); no N1 value -> `unknown`. Cites the evidence."""
    der = ["risk.behavioural", "risk.effective", "risk.declared", "risk.capacity"]
    source = "rule:behaviour_gap"
    node, evidence = _behaviour(profile)
    eff = _val(profile.get("risk", {}).get("effective"))
    if node is None or eff not in LEVELS:
        return [_soft("S1", "unknown", None, None, S1_WARN_GAP, "levels", der, source, 1.0, "behaviour_missing", [])]
    ids = list(dict.fromkeys(e["event_id"] for e in evidence))
    conf = float(node.get("confidence", 0.0))
    gap = idx(_val(node)) - idx(eff)
    if conf < WARN_MIN_CONF:
        return [_soft("S1", "info", None, gap, S1_WARN_GAP, "levels", der, source, conf, "insufficient_evidence", ids)]
    status = "warning" if gap >= S1_WARN_GAP else "info" if gap == 1 else "pass"
    key = {"warning": "behaviour_above_effective", "info": "behaviour_slightly_above", "pass": "behaviour_ok"}[status]
    return [_soft("S1", status, None, gap, S1_WARN_GAP, "levels", der, source, conf, key, ids)]


def s3(profile: dict, intent: dict | None) -> list[Check]:
    """Hype pattern on the trade itself: the intent's event is cited by the gated N1 answer with `hype_chasing` ->
    `warning`, attached to the trade (so only on the proposed side). Same confidence rule as S1. Never a violation."""
    if not intent or not intent.get("event_id"):
        return []
    der = ["risk.behavioural"]
    source = "rule:hype_pattern"
    node, evidence = _behaviour(profile)
    if node is None:
        return [_soft("S3", "unknown", intent.get("ticker"), None, None, "text", der, source, 1.0, "behaviour_missing", [])]
    conf = float(node.get("confidence", 0.0))
    hit = [e["event_id"] for e in evidence if e["event_id"] == intent["event_id"] and e.get("signal") == S3_SIGNAL]
    if not hit:
        return [_soft("S3", "pass", intent.get("ticker"), None, None, "text", der, source, conf, "no_hype_pattern", [])]
    status = "warning" if conf >= WARN_MIN_CONF else "info"
    key = "hype_pattern" if status == "warning" else "insufficient_evidence"
    return [_soft("S3", status, intent.get("ticker"), S3_SIGNAL, None, "text", der, source, conf, key, hit)]


def s4(profile: dict) -> list[Check]:
    node = profile.get("values", {}).get("parsed_notes")
    rules = list(_val(node) or []) if node is not None else []
    conf = float(node.get("confidence", 0.8)) if isinstance(node, dict) else 0.8
    return [_check("S4", "info", r["quote"], None, None, "text", ["values.parsed_notes", "values.notes"],
                   "rule:unapproved_parsed_rule", conf, "parsed_rule_not_approved")
            for r in rules if isinstance(r, dict) and not r.get("approved_by_user") and r.get("quote")]


def run_all(profile: dict, intent: dict | None, weights: dict[str, float] | None = None, assets=None, *,
            behaviour: bool = False) -> list[Check]:
    """S2 (needs `weights` fractions and `assets`; skipped without them) then S4: Flow C (D7 point 3).
    `behaviour=True` (Flow B, N1 ran through the gate) adds S1 first and, when `intent` is given, S3 before S4.
    S3 is attached to the trade, so the caller passes `intent` for the proposed side only."""
    out: list[Check] = []
    if behaviour:
        out += s1(profile)
    if weights is not None and assets is not None:
        out += s2(profile, weights, assets)
    if behaviour:
        out += s3(profile, intent)
    return out + s4(profile)
