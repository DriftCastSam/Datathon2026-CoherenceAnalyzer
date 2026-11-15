#!/usr/bin/env python3
"""Build the dashboard's report JSON for the CloudFront prototype (deploy/cloudfront.yaml).

Real today: engine.analyse (golden-tested), rules.derive + rules.limits (P0.2), limits_trace.
Preview until the build loop lands them: hard checks H1-H10/H14 (P0.4), intent.apply + resizer (P0.5),
pipeline/report (P0.8), explanation (P0.7, template mode only, no LLM). Every preview report carries
`preview: {...}` saying which parts are preview code, and the dashboard shows that banner.
When `python -m coherence_analyzer.cli demo` writes real reports to reports/, build_site.py publishes
those instead and this module is no longer used.

Output shape follows SPEC §13 plus dashboard extras (`whatif`, `profile_card`, `limits_trace`).
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from coherence_analyzer import data, engine  # noqa: E402
from coherence_analyzer.coherence import hard as hard_mod  # noqa: E402
from coherence_analyzer.rules import derive, limits as limits_mod  # noqa: E402

FIXTURE = ROOT / "fixtures" / "robert"
SEED, N, BLOCK = 7, 5000, 12
DISCLAIMER = {
    "fr": "Simulation éducative avec des profils fictifs. Ceci n'est pas un conseil financier personnalisé. Aucune transaction n'est exécutée.",
    "en": "Educational simulation with fictional profiles. This is not personalised financial advice. No transaction is executed.",
}
LEVELS = ["low", "low_moderate", "moderate", "moderate_high", "high"]
TYPE_OF = {"equity": ["stocks"], "etf_equity": ["etf"], "etf_bond": ["bonds", "etf"],
           "etf_commodity": ["gold", "etf"], "crypto": ["crypto"]}


def _val(x):
    return x["value"] if isinstance(x, dict) and "value" in x else x


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return x


def _clean(o):
    """numpy -> plain JSON."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if hasattr(o, "item"):
        return o.item()
    return o


# ---------- preview: intent.apply (P0.5) ----------

def apply_intent(w_pct: dict, assets, ticker: str, target_pct: float, funding: dict) -> dict | None:
    w = {k: float(v) for k, v in w_pct.items()}
    have = w.get(ticker, 0.0)
    need = target_pct - have
    if need <= 0:
        return w
    kind = funding["kind"]
    if kind == "specific":
        src = funding["ticker"]
        if w.get(src, 0.0) + 1e-9 < need:
            return None
        w[src] -= need
    else:
        pool = [t for t in w if t != ticker and (kind == "pro_rata_all" or assets.loc[t, "asset_class"] == funding["asset_class"])]
        tot = sum(w[t] for t in pool)
        if tot + 1e-9 < need:
            return None
        for t in pool:
            w[t] -= need * w[t] / tot
    w[ticker] = target_pct
    return {k: round(v, 6) for k, v in w.items() if v > 1e-9}


# ---------- preview: hard checks (P0.4) ----------

def _chk(cid, status, subject, value, limit, unit, limit_key, trace, msg, **extra):
    df = [f"limits.{limit_key}"] + trace.get(limit_key, []) if limit_key else []
    return {"id": cid, "kind": "hard", "status": status, "subject": subject, "value": value, "limit": limit,
            "unit": unit, "derived_from": df, "source": f"rule:{msg}", "confidence": 1.0, "message_key": msg, **extra}


def hard_checks(w_pct: dict, metrics: dict, lim: dict, trace: dict, profile: dict, assets) -> list[dict]:
    w = {t: v / 100 for t, v in w_pct.items()}
    cls = {t: assets.loc[t, "asset_class"] for t in w}
    out = []
    bad = sorted(t for t in w if set(TYPE_OF[cls[t]]) & set(lim["excluded_asset_types"]))
    out.append(_chk("H1", "violation" if bad else "pass", ",".join(bad) or "portfolio", len(bad), 0, "count",
                    "excluded_asset_types", trace, "excluded_asset_type"))
    crypto = sum(v for t, v in w.items() if cls[t] == "crypto")
    out.append(_chk("H2", "violation" if crypto > lim["max_crypto_weight"] + 1e-9 else "pass", "crypto", round(crypto, 4),
                    lim["max_crypto_weight"], "weight", "max_crypto_weight", trace, "crypto_over"))
    stocks = {t: v for t, v in w.items() if cls[t] == "equity"}
    over = {t: v for t, v in stocks.items() if v > lim["max_single_stock_weight"] + 1e-9}
    for t, v in sorted(over.items()):
        out.append(_chk("H3", "violation", t, round(v, 4), lim["max_single_stock_weight"], "weight",
                        "max_single_stock_weight", trace, "single_stock_over"))
    if not over:
        t, v = max(stocks.items(), key=lambda kv: kv[1]) if stocks else ("none", 0.0)
        out.append(_chk("H3", "pass", t, round(v, 4), lim["max_single_stock_weight"], "weight",
                        "max_single_stock_weight", trace, "single_stock_over"))
    fi = sum(v for t, v in w.items() if cls[t] == "etf_bond")
    out.append(_chk("H4", "violation" if fi < lim["min_fixed_income_weight"] - 1e-9 else "pass", "fixed income", round(fi, 4),
                    lim["min_fixed_income_weight"], "weight", "min_fixed_income_weight", trace, "fixed_income_under"))
    fx = sum(v for t, v in w.items() if assets.loc[t, "currency"] != "CAD")
    out.append(_chk("H5", "violation" if fx > lim["max_foreign_currency_weight"] + 1e-9 else "pass", "foreign currency", round(fx, 4),
                    lim["max_foreign_currency_weight"], "weight", "max_foreign_currency_weight", trace, "foreign_ccy_over"))
    emp = profile["identity"].get("employer_sector")
    es = sum(v for t, v in w.items() if cls[t] == "equity" and assets.loc[t, "sector"] == emp)
    out.append(_chk("H6", "violation" if es > lim["max_employer_sector_weight"] + 1e-9 else "pass", emp, round(es, 4),
                    lim["max_employer_sector_weight"], "weight", "max_employer_sector_weight", trace, "employer_sector_over"))
    etfs = sorted(t for t in w if cls[t].startswith("etf_") and cls[t] != "etf_bond" and cls[t] != "etf_commodity")
    if etfs:
        out.append(_chk("H6", "unknown", ",".join(etfs), None, lim["max_employer_sector_weight"], "weight",
                        "max_employer_sector_weight", trace, "employer_sector_etf_no_lookthrough"))
    lo, hi = lim["vol_band_annual"]
    vol = metrics["vol"]
    out.append(_chk("H7", "violation" if vol > hi else ("info" if vol < lo else "pass"), "portfolio", vol, [lo, hi], "vol",
                    "vol_band_annual", trace, "vol_band"))
    mdd_lim = lim["max_drawdown_pct"] / 100
    out.append(_chk("H8", "violation" if metrics["mdd"] < mdd_lim - 1e-12 else "pass", "portfolio", metrics["mdd"], mdd_lim, "drawdown",
                    "max_drawdown_pct", trace, "drawdown_over", date=metrics.get("mdd_date")))
    out.append(_chk("H9", "violation" if metrics["p5_T"] < lim["floor_at_horizon"] else "pass", "portfolio", metrics["p5_T"],
                    lim["floor_at_horizon"], "cad", "floor_at_horizon", trace, "floor_breached"))
    secs = [s for s in metrics["groups"]["sector"] if not s["name"].startswith("Broad market")]
    top = max(secs, key=lambda s: s["risk"])
    out.append(_chk("H10", "violation" if top["risk"] > lim["max_sector_risk_share"] + 1e-9 else "pass", top["name"],
                    round(top["risk"], 4), lim["max_sector_risk_share"], "risk_share", "max_sector_risk_share", trace, "sector_risk_over"))
    if any(s["name"].startswith("Broad market") for s in metrics["groups"]["sector"]):
        out.append(_chk("H10", "unknown", "Broad market (diversified)", None, lim["max_sector_risk_share"], "risk_share",
                        "max_sector_risk_share", trace, "sector_no_lookthrough"))
    react = profile["risk"].get("reaction_to_20pct_drop")
    if react in ("sell_some", "sell_all") and metrics["mdd"] < -0.20:
        out.append(_chk("H14", "info", "portfolio", metrics["mdd"], -0.20, "drawdown", None, trace, "stated_reaction_vs_history",
                        date=metrics.get("mdd_date")))
    return out


def real_checks(w_pct: dict, metrics: dict, prof_l: dict, assets_df, meta: dict) -> list[dict]:
    """The P0.4 hard checks (coherence/hard.py), tagged with their factor (docs/spec-factors.md §20.2)."""
    out = hard_mod.run_all({t: v / 100 for t, v in w_pct.items()}, metrics, prof_l, assets_df, meta)
    return [{**c, "factor": CHECK_FACTOR[c["id"]]} for c in _clean(out)]


# ---------- five factors (docs/spec-factors.md §20.2-20.4; preview of P0.7) ----------

FACTORS = [
    ("objectif", "01", "Objectif", "Pourquoi investir ?", "Why invest?"),
    ("horizon", "02", "Horizon", "Quand l'argent sera-t-il requis ?", "When will the money be needed?"),
    ("risque", "03", "Risque", "Quelle perte peut être absorbée ?", "What loss can be absorbed?"),
    ("contraintes", "04", "Contraintes", "Quelles limites s'appliquent ?", "Which limits apply?"),
    ("preferences", "05", "Préférences", "Qu'est-ce qui oriente les choix ?", "What guides the choices?"),
]
CHECK_FACTOR = {"G1": "objectif", "O1": "objectif", "H9": "horizon",
                **{k: "risque" for k in ("H4", "H7", "H8", "H14", "S1", "S3")},
                **{k: "contraintes" for k in ("H1", "H2", "H3", "H5", "H6", "H10", "H11", "H12", "H13", "S2")},
                "S4": "preferences"}
CHECK_LABEL = {"H1": "Excluded asset types", "H2": "Crypto weight", "H3": "Single-stock weight",
               "H4": "Fixed-income weight", "H5": "Foreign-currency weight", "H6": "Employer-sector exposure",
               "H7": "Volatility", "H8": "Worst drawdown since 2022", "H9": "Pessimistic value at horizon",
               "H10": "Sector risk concentration", "H14": "Stated reaction vs history", "S1": "Behaviour vs effective risk"}
RANK = {"violation": 0, "warning": 1, "unknown": 2, "info": 3, "pass": 4}
# display wording for the profile's asset-type codes (the preview text never uses the word of the code `options`)
_ASSET_TYPE_LABEL = {"options": "derivative contracts"}


def _fmt(c: dict, v):
    if v is None:
        return "n/a"
    if isinstance(v, list):
        return " to ".join(_fmt(c, x) for x in v)
    if c.get("unit") == "cad":
        return cad(v)
    if c.get("unit") == "level":
        return str(v).replace("_", "-")
    if isinstance(v, (int, float)):
        return pct(v)
    return str(v)


def sections(prof: dict, lim: dict, checks: list[dict], metrics: dict) -> list[dict]:
    """Five sections in display order: status = worst check, facts, template narrative (mode=template)."""
    o, r, s, v, pr = prof["objectives"], prof["risk"], prof["situation"], prof["values"], prof["preferences"]
    lvl = lambda x: str(_val(x)).replace("_", "-")  # noqa: E731
    eh, wd = _val(o["effective_horizon_years"]), (s.get("planned_withdrawals") or [{}])[0]
    excl = sorted({e for k in ("environment", "social", "governance") for e in v[k].get("exclusions", [])})
    facts = {
        "objectif": [("declared", "Primary goal", o["primary"].replace("_", " ")),
                     ("declared", "Secondary", ", ".join(o.get("secondary", [])) or "none"),
                     *[("declared", f"Goal {i + 1}", f"{g['name']} · {g['date']} · {cad(g['amount'])}") for i, g in enumerate(o.get("goals", []))],
                     ("derived", "Life stage", lvl(prof["identity"]["life_stage"]))],
        "horizon": [("declared", "Horizon", f"{o['horizon_years']} years"),
                    ("declared", "First withdrawal", f"{wd.get('date')} · {cad(wd.get('amount', 0))}"),
                    ("derived", "Effective horizon", f"{eh} years"),
                    ("derived", "Floor at horizon", cad(lim["floor_at_horizon"]))],
        "risque": [("declared", "Declared tolerance", lvl(r["declared"])),
                   ("declared", "Max acceptable drawdown", f"{r['max_acceptable_drawdown_pct']} %"),
                   ("declared", "Reaction to a 20 % drop", str(r.get("reaction_to_20pct_drop", "")).replace("_", " ")),
                   ("derived", "Capacity", lvl(r["capacity"])),
                   ("derived", "Effective risk (lower of the two)", lvl(r["effective"])),
                   ("inference", f"Behaviour (LLM, conf. {r['behavioural'].get('confidence')})", lvl(r["behavioural"]))],
        "contraintes": [("derived", "Emergency fund", f"{_val(s['emergency_fund_months'])} months"),
                        ("declared", "Employer sector", prof["identity"].get("employer_sector") or "none"),
                        ("declared", "Refused asset types", ", ".join(_ASSET_TYPE_LABEL.get(x, x) for x in pr.get("asset_types_refused", [])) or "none"),
                        ("declared", "Excluded activities", ", ".join(x.replace("_", " ") for x in excl) or "none"),
                        ("declared", "Max fee (MER)", f"{pr.get('max_fee_mer_pct')} %"),
                        ("derived", "Single stock max", pct(lim["max_single_stock_weight"], 0)),
                        ("derived", "Sector risk share max", pct(lim["max_sector_risk_share"], 0))],
        "preferences": [("declared", "Importance E / S / G", " / ".join(str(v[k]["importance"]) for k in ("environment", "social", "governance"))),
                        ("declared", "Notes", f"“{v.get('notes', '')}”"),
                        ("declared", "Dividends / large caps / home bias",
                         " / ".join("yes" if pr.get(k) else "no" for k in ("prefers_dividends", "prefers_large_caps", "home_bias_ok")))],
    }
    why = {
        "objectif": f"The goal is {o['primary'].replace('_', ' ')}, with retirement planned for {wd.get('date')}.",
        "horizon": f"The first planned withdrawal ({wd.get('date')}, {cad(wd.get('amount', 0))}) sets the effective horizon at {eh} years instead of {o['horizon_years']}.",
        "risque": f"Effective risk is the lower of declared ({lvl(r['declared'])}) and capacity ({lvl(r['capacity'])}): {lvl(r['effective'])}. Capacity follows from the {eh}-year effective horizon.",
        "contraintes": f"Limits follow from the effective risk ({lvl(r['effective'])}): at most {pct(lim['max_single_stock_weight'], 0)} in one stock and {pct(lim['max_sector_risk_share'], 0)} of portfolio risk in one sector.",
        "preferences": "Preferences guide choices inside the limits; they can inform, never block.",
    }
    not_covered = {"objectif": ["Objective vs portfolio check (O1, P1)"], "contraintes": ["Taxes: account type (TFSA, RRSP, non-registered), US withholding"],
                   "preferences": ["Parsed notes ranking (S4, P1)"]}
    out = []
    for fid, num, title, q_fr, q_en in FACTORS:
        mine = [c for c in checks if c.get("factor") == fid]
        status = min((c["status"] for c in mine), key=RANK.get, default="pass")
        bad = sorted((c for c in mine if c["status"] not in ("pass",)), key=lambda c: (RANK[c["status"]], c["id"]))
        segs = [{"kind": "fact", "text": why[fid], "refs": [p for p in ("risk.effective", "objectives.effective_horizon_years") if fid in ("horizon", "risque", "contraintes")] or ["objectives"]}]
        for c in bad:
            if c["status"] == "unknown":
                segs.append({"kind": "unknown", "text": f"{CHECK_LABEL.get(c['id'], c['id'])} ({c.get('subject') or 'portfolio'}): cannot be verified without ETF look-through data.", "refs": [c["id"]]})
            elif c["kind"] == "soft":
                segs.append({"kind": "inference", "text": f"{CHECK_LABEL.get(c['id'], c['id'])}: behaviour reads as {_fmt(c, c['value'])} against an effective level of {_fmt(c, c['limit'])}. An inference: it can warn, never block.", "refs": [c["id"]]})
            else:
                subj = f" ({c['subject']})" if c.get("subject") else ""
                rel = "below the minimum of" if c["id"] in ("H4", "H9") else "beyond your limit of"
                segs.append({"kind": "fact", "text": f"{CHECK_LABEL.get(c['id'], c['id'])}{subj}: {_fmt(c, c['value'])}, {rel} {_fmt(c, c['limit'])}.", "refs": [c["id"]]})
        out.append({"factor": fid, "num": num, "title": title, "question": q_fr, "question_en": q_en, "status": status,
                    "checks": sorted({c["id"] for c in mine}, key=lambda x: (x[0], int(x[1:]))),
                    "unknowns": sorted({c["id"] for c in mine if c["status"] == "unknown"}),
                    "facts": [{"kind": k, "label": a, "value": b} for k, a, b in facts[fid]],
                    "not_covered": not_covered.get(fid, []),
                    "narrative": {"mode": "template", "segments": segs}})
    return out


# ---------- internal coherence (docs/spec-factors.md §20.9, D8; preview of P0.11) ----------

_C_Q = {"C1": "Votre horizon est de {h} ans, mais l'objectif « {goal} » est daté de {date}. Lequel faut-il ajuster?",
        "C2": "Une perte maximale de {dd} % ne correspond pas au niveau de risque déclaré. Lequel reflète votre intention?",
        "C3": "Un objectif de croissance avec un risque déclaré faible : lequel prime?",
        "C4": "Ce type d'actif est à la fois permis et refusé. Lequel est le bon?",
        "C5": "Votre réaction à une baisse de 20 % ne correspond pas au risque déclaré. Laquelle reflète votre intention?"}
_N6_Q = "Vos réponses pour « {a} » et « {b} » semblent tirer dans des directions différentes. Laquelle reflète votre intention?"
_STATUS = {"contradiction": "warning", "tension": "info"}


def _ym(s: str) -> int:
    y, m = s[:7].split("-")
    return int(y) * 12 + int(m) - 1


def profile_coherence(profile: dict, as_of: str) -> dict:
    """C1-C5 rules (code) + the N6 output that P0.11 records for Robert (hand-written until then)."""
    obj, risk, pref = profile.get("objectives", {}), profile.get("risk", {}), profile.get("preferences", {})
    out = []
    h = obj.get("horizon_years")
    if isinstance(h, (int, float)):
        end = _ym(as_of) + round(h * 12)
        for i, g in enumerate(obj.get("goals") or []):
            if g.get("date") and _ym(g["date"]) > end:
                out.append({"id": "C1", "source": "rule:C1", "kind": "contradiction",
                            "fields": ["objectives.horizon_years", f"objectives.goals[{i}].date"], "values": [f"{h} ans", g["date"]],
                            "reason": None, "question": _C_Q["C1"].format(h=h, goal=g.get("name", ""), date=g["date"])})
    lv, dd = risk.get("declared"), risk.get("max_acceptable_drawdown_pct")
    if lv in LEVELS and isinstance(dd, (int, float)):
        i = LEVELS.index(lv)
        tight = limits_mod.LIMITS_TABLE[LEVELS[i - 1]]["max_dd_pct"] if i > 0 else None
        loose = limits_mod.LIMITS_TABLE[LEVELS[i + 1]]["max_dd_pct"] if i < len(LEVELS) - 1 else None
        if (tight is not None and dd > tight) or (loose is not None and dd < loose):
            out.append({"id": "C2", "source": "rule:C2", "kind": "contradiction", "fields": ["risk.declared", "risk.max_acceptable_drawdown_pct"],
                        "values": [lv, f"{dd} %"], "reason": None, "question": _C_Q["C2"].format(dd=dd)})
    if obj.get("primary") in ("growth", "aggressive_growth") and lv in ("low", "low_moderate"):
        out.append({"id": "C3", "source": "rule:C3", "kind": "contradiction", "fields": ["objectives.primary", "risk.declared"],
                    "values": [obj["primary"], lv], "reason": None, "question": _C_Q["C3"]})
    both = sorted(set(pref.get("asset_types_allowed") or []) & set(pref.get("asset_types_refused") or []))
    if both:
        out.append({"id": "C4", "source": "rule:C4", "kind": "contradiction",
                    "fields": ["preferences.asset_types_allowed", "preferences.asset_types_refused"], "values": [both[0], both[0]],
                    "reason": None, "question": _C_Q["C4"]})
    r = risk.get("reaction_to_20pct_drop")
    if lv in LEVELS and ((r == "buy_more" and LEVELS.index(lv) <= 1) or (r == "sell_all" and LEVELS.index(lv) >= 3)):
        out.append({"id": "C5", "source": "rule:C5", "kind": "contradiction", "fields": ["risk.reaction_to_20pct_drop", "risk.declared"],
                    "values": [r, lv], "reason": None, "question": _C_Q["C5"]})
    notes = (profile.get("values") or {}).get("notes") or ""
    quote = "No hype stocks unless I can explain the business."
    if "new_opportunities" in (pref.get("interests") or []) and quote in notes:
        out.append({"id": "N6.1", "source": "llm:profile_consistency", "kind": "tension",
                    "fields": ["preferences.interests", "values.notes"], "quotes": ["new_opportunities", quote],
                    "reason": "Interest in new opportunities sits next to a refusal of hype stocks the investor cannot explain.",
                    "question": _N6_Q.format(a="preferences.interests", b="values.notes")})
    for f in out:
        f["status"] = _STATUS[f["kind"]]
    st = "warning" if any(f["status"] == "warning" for f in out) else "info" if out else "pass"
    return {"status": st, "findings": out, "llm": {"gate_status": "preview:hand-written", "call_log": None}}


def soft_checks(profile: dict) -> list[dict]:
    b = profile["risk"]["behavioural"]
    gap = LEVELS.index(b["value"]) - LEVELS.index(_val(profile["risk"]["effective"]))
    conf = b.get("confidence", 0)
    status = "info" if conf < 0.5 else ("warning" if gap >= 2 else "info" if gap == 1 else "pass")
    return [{"id": "S1", "kind": "soft", "factor": "risque", "status": status, "subject": "behaviour", "value": b["value"],
             "limit": _val(profile["risk"]["effective"]), "unit": "level",
             "derived_from": ["risk.behavioural", "risk.effective"], "source": b["by"], "confidence": conf,
             "message_key": "behaviour_gap", "evidence": b.get("evidence", [])}]


def verdict(checks: list[dict]) -> str:
    s = {c["status"] for c in checks}
    return "incoherent" if "violation" in s else "coherent_with_warnings" if "warning" in s else "coherent"


def _metrics(a: dict) -> dict:
    keep = ["vol", "vol_local", "mdd", "mdd_date", "cone", "width", "loss_prob", "groups", "drivers", "corr_eq_bond",
            "p5_T", "p50_T", "p95_T", "lookthrough"]
    return _clean({k: a[k] for k in keep if k in a})


# ---------- explanation (template mode, SPEC §9.4 fallback; no LLM) ----------

def pct(x, d=1):
    return f"{x * 100:.{d}f} %"


def cad(x):
    return f"{x:,.0f} $".replace(",", " ")


def explain(intent, cur, prop, checks_p, headroom, profile, lim) -> dict:
    v = [c for c in checks_p if c["status"] == "violation"]
    seg = [{"kind": "fact", "text": f"You asked to bring {intent['ticker']} to {intent['target_weight_pct']} % of the portfolio, funded from {intent['funding'].get('ticker', intent['funding']['kind'])}.",
            "refs": ["intent"]},
           {"kind": "fact", "text": f"Historical volatility goes from {pct(cur['vol'])} to {pct(prop['vol'])}; the worst monthly drawdown since 2022 goes from {pct(cur['mdd'])} to {pct(prop['mdd'])}.",
            "refs": ["metrics_current.vol", "metrics_proposed.vol", "metrics_proposed.mdd"]},
           {"kind": "assumption", "text": f"In the bootstrap simulation, the pessimistic (5th percentile) value at horizon moves from {cad(cur['p5_T'])} to {cad(prop['p5_T'])}. This uses team drift assumptions, not a forecast.",
            "refs": ["metrics_proposed.p5_T"]}]
    for c in v:
        why = {"H3": "comes from your effective risk level, which is the lower of what you declared and what your situation can carry",
               "H4": "comes from your effective risk level, set by your first planned withdrawal in June 2030 (3.6 years away)",
               "H8": "is the drawdown you said you can accept",
               "H9": "protects 90 % of today's value at your horizon, because withdrawals start soon",
               "H10": "keeps any single sector from dominating the portfolio's risk"}.get(c["id"], "comes from your profile")
        seg.append({"kind": "fact", "text": f"{c['id']}: {c['subject']} crosses one of your own limits; that limit {why}.", "refs": [c["id"]]})
    b = profile["risk"]["behavioural"]
    seg.append({"kind": "inference", "text": f"Inference (confidence {b['confidence']}): your recent questions suggest a risk appetite of {b['value'].replace('_', '-')}, above your effective level. This is a reading of behaviour, never a block.",
                "refs": ["risk.behavioural"]})
    seg.append({"kind": "unknown", "text": "We cannot verify sector or employer exposure inside broad ETFs (no look-through data yet).", "refs": ["H6", "H10"]})
    if headroom and not headroom.get("none_fit"):    # SPEC §8 EN template, flow B only; a fact, never a proposal (D10)
        seg.append({"kind": "fact", "text": f"With the funding you gave ({intent['funding'].get('ticker', intent['funding']['kind'])}), this trade crosses your limit {headroom['binding_check']} above {headroom['max_weight_pct']:.1f} %.",
                    "refs": ["headroom"]})
    return {"mode": "template", "headline": f"Asking for {intent['ticker']} at {intent['target_weight_pct']} % crosses {len(v)} of your own limits.",
            "segments": seg, "disclaimer": DISCLAIMER["en"]}


# ---------- build ----------

def build(out_dir: Path) -> list[Path]:
    mk = engine.load_market(ROOT / "data" / "starter")
    assets = mk.assets
    profile = json.loads((FIXTURE / "profile.json").read_text())
    portfolio = json.loads((FIXTURE / "portfolio.json").read_text())
    intents = json.loads((FIXTURE / "intents.json").read_text())
    intent = intents[0] if isinstance(intents, list) else (intents.get("intents") or [intents])[0]
    as_of, value = portfolio["as_of"], float(portfolio["value_cad"])
    horizon_m = 48
    prof = derive.derive_all(copy.deepcopy(profile), portfolio_value_cad=value, as_of=as_of)
    lim, trace = limits_mod.build_limits(prof, value)
    prof_l = {**prof, "limits": lim, "limits_trace": trace}
    lim = {k: v for k, v in lim.items() if not k.startswith("_") and k != "by"}
    w0 = {k: float(v) for k, v in portfolio["weights_pct"].items() if float(v) > 0}
    assets_df = data.load_assets()
    meta = {n: data.load_meta(n) for n in ("asset_meta", "etf_sector_weights", "activity_tags")}

    def run(w):
        return _metrics(engine.analyse(mk, w, horizon_m, value, n=N, block=BLOCK, seed=SEED))

    def checks(w, m):
        return real_checks(w, m, prof_l, assets_df, meta)

    cur = run(w0)
    cur_checks = checks(w0, cur)  # S1/S3 deferred to P2 (D7); behaviour stays a labelled inference fact

    # what-if grid for the user's own funding only (D10): the stand-in for the resizer
    f = intent["funding"]
    rows = []
    for i in range(0, int(intent["target_weight_pct"] * 2) + 1):
        tw = i / 2
        w = apply_intent(w0, assets, intent["ticker"], tw, f)
        if w is None:
            continue
        m = run(w)
        ch = checks(w, m)
        rows.append({"w": tw, "vol": m["vol"], "mdd": m["mdd"], "p5_T": m["p5_T"], "p50_T": m["p50_T"],
                     "loss_prob": m["loss_prob"], "cone": {k: m["cone"][k] for k in ("p5", "p50", "p95")},
                     "violations": [c["id"] for c in ch if c["status"] == "violation"]})
    whatif = {f"from {f.get('ticker', f['kind'])}": {"funding": f, "rows": rows}}

    # headroom (SPEC §8 step 4): the contiguous passing prefix of the grid, for the user's funding only
    prefix, first_bad = None, None
    for r in rows:
        if r["violations"]:
            first_bad = r
            break
        prefix = r
    if first_bad is None:
        headroom = None                      # the whole requested weight crosses no limit
    elif prefix is None:
        headroom = {"none_fit": True, "binding_checks": sorted(first_bad["violations"])}
    else:
        headroom = {"funding": f, "max_weight_pct": prefix["w"], "binding_check": sorted(first_bad["violations"])[0],
                    "metrics": prefix}

    w1 = apply_intent(w0, assets, intent["ticker"], intent["target_weight_pct"], intent["funding"])
    prop = run(w1)
    prop_checks = checks(w1, prop)

    def key(c):
        return (c["id"], c["subject"])
    before = {key(c): c["status"] for c in cur_checks}
    flipped = [{"id": c["id"], "subject": c["subject"], "from": before.get(key(c), "absent"), "to": c["status"]}
               for c in prop_checks if before.get(key(c), "absent") != c["status"]]
    delta = {k: prop[k] - cur[k] for k in ("vol", "mdd", "p5_T", "width", "loss_prob")}
    unknowns = [c for c in prop_checks if c["status"] == "unknown"]
    assumptions = engine.analyse(mk, w0, horizon_m, value, n=200, seed=SEED)["assumptions"]
    card = {"name": "Robert", "age": prof["identity"]["age"], "occupation": prof["identity"]["occupation"],
            "life_stage": _val(prof["identity"]["life_stage"]), "declared": prof["risk"]["declared"],
            "capacity": _val(prof["risk"]["capacity"]), "effective": _val(prof["risk"]["effective"]),
            "behavioural": prof["risk"]["behavioural"]["value"], "behavioural_conf": prof["risk"]["behavioural"]["confidence"],
            "effective_horizon_years": _val(prof["objectives"]["effective_horizon_years"]),
            "ef_months": _val(prof["situation"]["emergency_fund_months"]), "knowledge_score": _val(prof["knowledge"]["score"]),
            "value_cad": value, "as_of": as_of, "first_withdrawal": prof["situation"]["planned_withdrawals"][0]}
    preview = {"real": ["engine.analyse", "rules.derive", "rules.limits", "limits_trace", "hard checks H1-H10, H14"],
               "preview": ["what-if grid for the funding the user gave + headroom (stand-in for intent.apply + resizer)",
                           "factor sections + template text (P0.7)", "LLM narration (P0.8, not run)", "report assembly (P0.9)",
                           "profile coherence C1-C5 + hand-written N6 (P0.11)"],
               "built_by": "deploy/preview_report.py"}

    # D7 headline demo: Flow C review of a portfolio that already holds the NVDA position
    w_rev = {k: float(v) for k, v in engine.ROBERT_NVDA.items() if float(v) > 0}
    rev = run(w_rev)
    rev_checks = checks(w_rev, rev)
    rev_secs = sections(prof, lim, rev_checks, rev)
    diverging = [s["title"] for s in rev_secs if s["status"] in ("violation", "warning")]
    common = {"profile_id": profile["meta"]["profile_id"], "profile_version": profile["meta"].get("version"),
              "portfolio_id": portfolio["portfolio_id"], "assumptions": assumptions,
              "data_window": ["2022-01-31", "2026-10-31"], "disclaimer": DISCLAIMER, "limits": lim,
              "limits_trace": trace, "profile_card": card, "weights_current": w0, "preview": preview,
              "profile_coherence": profile_coherence(profile, as_of)}
    reports = {
        "robert-review-nvda": {"run_id": "preview-robert-review-nvda", "flow": "C", "intent": None, "verdict": verdict(rev_checks),
                               "checks_current": rev_checks, "checks_proposed": None, "diff": None, "metrics_current": rev,
                               "metrics_proposed": None, "headroom": None, "whatif": None, "weights_current": w_rev,
                               "unknowns": [c for c in rev_checks if c["status"] == "unknown"], "sections": rev_secs,
                               "explanation": {"mode": "template",
                                               "headline": f"This portfolio diverges from Robert's profile on {len(diverging)} of 5 factors: {', '.join(diverging)}.",
                                               "segments": [], "disclaimer": DISCLAIMER["en"]},
                               "portfolio_label": "Robert's portfolio with NVDA already at 10 % (fixtures/robert/portfolio_review.json, D7)",
                               **{k: v for k, v in common.items() if k != "weights_current"}},
        "robert-nvda": {"run_id": "preview-robert-nvda", "flow": "B", "intent": intent, "verdict": verdict(prop_checks),
                        "checks_current": cur_checks, "checks_proposed": prop_checks,
                        "diff": {"flipped": flipped, "metrics_delta": delta}, "metrics_current": cur,
                        "metrics_proposed": prop, "weights_proposed": w1, "headroom": headroom, "unknowns": unknowns,
                        "explanation": explain(intent, cur, prop, prop_checks, headroom, prof, lim), "whatif": whatif, **common},
        "robert-review": {"run_id": "preview-robert-review", "flow": "C", "intent": None, "verdict": verdict(cur_checks),
                          "checks_current": cur_checks, "checks_proposed": None, "diff": None, "metrics_current": cur,
                          "metrics_proposed": None, "headroom": None, "whatif": None,
                          "unknowns": [c for c in cur_checks if c["status"] == "unknown"],
                          "explanation": {"mode": "template", "headline": "Robert's current portfolio stays within all of his own limits.",
                                          "segments": [{"kind": "fact", "text": f"Volatility {pct(cur['vol'])}, inside his band of {pct(lim['vol_band_annual'][0], 0)} to {pct(lim['vol_band_annual'][1], 0)}; worst drawdown {pct(cur['mdd'])} ({cur['mdd_date']}).", "refs": ["H7", "H8"]},
                                                       {"kind": "assumption", "text": f"Pessimistic value at horizon {cad(cur['p5_T'])}, above his floor of {cad(lim['floor_at_horizon'])}.", "refs": ["H9"]},
                                                       {"kind": "unknown", "text": "Sector and employer exposure inside broad ETFs cannot be verified yet.", "refs": ["H6", "H10"]}],
                                          "disclaimer": DISCLAIMER["en"]}, **common},
    }
    reports["robert-nvda"]["sections"] = sections(prof, lim, prop_checks, prop)
    reports["robert-review"]["sections"] = sections(prof, lim, cur_checks, cur)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, rep in reports.items():
        p = out_dir / f"{name}.json"
        p.write_text(json.dumps(_clean(rep), ensure_ascii=False, separators=(",", ":")))
        paths.append(p)
    (out_dir / "index.json").write_text(json.dumps([
        {"file": "robert-review-nvda.json", "label": "Robert · review · NVDA held", "verdict": reports["robert-review-nvda"]["verdict"]},
        {"file": "robert-nvda.json", "label": "Robert · trade · NVDA 10 %", "verdict": reports["robert-nvda"]["verdict"]},
        {"file": "robert-review.json", "label": "Robert · review · today", "verdict": reports["robert-review"]["verdict"]},
    ]))
    return paths


if __name__ == "__main__":
    for p in build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".cache" / "site" / "reports"):
        print(p, p.stat().st_size)
