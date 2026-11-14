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

from coherence_analyzer import engine  # noqa: E402
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


def soft_checks(profile: dict) -> list[dict]:
    b = profile["risk"]["behavioural"]
    gap = LEVELS.index(b["value"]) - LEVELS.index(_val(profile["risk"]["effective"]))
    conf = b.get("confidence", 0)
    status = "info" if conf < 0.5 else ("warning" if gap >= 2 else "info" if gap == 1 else "pass")
    return [{"id": "S1", "kind": "soft", "status": status, "subject": "behaviour", "value": b["value"],
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


def explain(intent, cur, prop, checks_p, alts, profile, lim) -> dict:
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
                "refs": ["S1"]})
    seg.append({"kind": "unknown", "text": "We cannot verify sector or employer exposure inside broad ETFs (no look-through data yet).", "refs": ["H6", "H10"]})
    for a in alts:
        seg.append({"kind": "option", "text": f"Within your own limits, funding {a['funding_label']} would fit up to {a['max_weight_pct']} % (binding check: {a['binding_check']}).",
                    "refs": ["alternatives"]})
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
    lim = {k: v for k, v in lim.items() if not k.startswith("_") and k != "by"}
    w0 = {k: float(v) for k, v in portfolio["weights_pct"].items() if float(v) > 0}

    def run(w):
        return _metrics(engine.analyse(mk, w, horizon_m, value, n=N, block=BLOCK, seed=SEED))

    cur = run(w0)
    cur_checks = hard_checks(w0, cur, lim, trace, prof, assets) + soft_checks(prof)

    # what-if grid for the dashboard slider (and the preview resizer)
    fundings = [("from ZST.TO", intent["funding"]), ("pro rata, all holdings", {"kind": "pro_rata_all"}),
                ("pro rata, equity ETFs", {"kind": "pro_rata_class", "asset_class": "etf_equity"}),
                ("pro rata, single stocks", {"kind": "pro_rata_class", "asset_class": "equity"})]
    whatif = {}
    for label, f in fundings:
        rows = []
        for i in range(0, int(intent["target_weight_pct"] * 2) + 1):
            tw = i / 2
            w = apply_intent(w0, assets, intent["ticker"], tw, f)
            if w is None:
                continue
            m = run(w)
            ch = hard_checks(w, m, lim, trace, prof, assets)
            rows.append({"w": tw, "vol": m["vol"], "mdd": m["mdd"], "p5_T": m["p5_T"], "p50_T": m["p50_T"],
                         "loss_prob": m["loss_prob"], "cone": {k: m["cone"][k] for k in ("p5", "p50", "p95")},
                         "violations": [c["id"] for c in ch if c["status"] == "violation"]})
        whatif[label] = {"funding": f, "rows": rows}

    alts = []
    for label, d in whatif.items():
        ok = [r for r in d["rows"] if not r["violations"]]
        first_bad = next((r for r in d["rows"] if r["violations"]), None)
        mx = max((r["w"] for r in ok), default=0.0)
        # only contiguous passing prefix counts (scan stops at first failure, SPEC §8)
        prefix = 0.0
        for r in d["rows"]:
            if r["violations"]:
                break
            prefix = r["w"]
        mx = prefix
        if mx > 0:
            alts.append({"funding": d["funding"], "funding_label": label, "max_weight_pct": mx,
                         "binding_check": ",".join(first_bad["violations"]) if first_bad else None,
                         "metrics": next(r for r in d["rows"] if r["w"] == mx)})
    alts = sorted(alts, key=lambda a: -a["max_weight_pct"])[:3]

    w1 = apply_intent(w0, assets, intent["ticker"], intent["target_weight_pct"], intent["funding"])
    prop = run(w1)
    prop_checks = hard_checks(w1, prop, lim, trace, prof, assets) + soft_checks(prof)

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
    preview = {"real": ["engine.analyse", "rules.derive", "rules.limits", "limits_trace"],
               "preview": ["hard checks (P0.4)", "intent.apply + resizer (P0.5)", "explanation template (P0.7)", "report assembly (P0.8)"],
               "built_by": "deploy/preview_report.py"}
    common = {"profile_id": profile["meta"]["profile_id"], "profile_version": profile["meta"].get("version"),
              "portfolio_id": portfolio["portfolio_id"], "assumptions": assumptions,
              "data_window": ["2022-01-31", "2026-10-31"], "disclaimer": DISCLAIMER, "limits": lim,
              "limits_trace": trace, "profile_card": card, "weights_current": w0, "preview": preview}
    reports = {
        "robert-nvda": {"run_id": "preview-robert-nvda", "flow": "B", "intent": intent, "verdict": verdict(prop_checks),
                        "checks_current": cur_checks, "checks_proposed": prop_checks,
                        "diff": {"flipped": flipped, "metrics_delta": delta}, "metrics_current": cur,
                        "metrics_proposed": prop, "weights_proposed": w1, "alternatives": alts, "unknowns": unknowns,
                        "explanation": explain(intent, cur, prop, prop_checks, alts, prof, lim), "whatif": whatif, **common},
        "robert-review": {"run_id": "preview-robert-review", "flow": "C", "intent": None, "verdict": verdict(cur_checks),
                          "checks_current": cur_checks, "checks_proposed": None, "diff": None, "metrics_current": cur,
                          "metrics_proposed": None, "alternatives": [], "whatif": None,
                          "unknowns": [c for c in cur_checks if c["status"] == "unknown"],
                          "explanation": {"mode": "template", "headline": "Robert's current portfolio stays within all of his own limits.",
                                          "segments": [{"kind": "fact", "text": f"Volatility {pct(cur['vol'])}, inside his band of {pct(lim['vol_band_annual'][0], 0)} to {pct(lim['vol_band_annual'][1], 0)}; worst drawdown {pct(cur['mdd'])} ({cur['mdd_date']}).", "refs": ["H7", "H8"]},
                                                       {"kind": "assumption", "text": f"Pessimistic value at horizon {cad(cur['p5_T'])}, above his floor of {cad(lim['floor_at_horizon'])}.", "refs": ["H9"]},
                                                       {"kind": "unknown", "text": "Sector and employer exposure inside broad ETFs cannot be verified yet.", "refs": ["H6", "H10"]}],
                                          "disclaimer": DISCLAIMER["en"]}, **common},
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, rep in reports.items():
        p = out_dir / f"{name}.json"
        p.write_text(json.dumps(_clean(rep), ensure_ascii=False, separators=(",", ":")))
        paths.append(p)
    (out_dir / "index.json").write_text(json.dumps([
        {"file": "robert-nvda.json", "label": "Robert · trade check · NVDA 10 % from ZST.TO", "verdict": reports["robert-nvda"]["verdict"]},
        {"file": "robert-review.json", "label": "Robert · portfolio review", "verdict": reports["robert-review"]["verdict"]},
    ]))
    return paths


if __name__ == "__main__":
    for p in build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".cache" / "site" / "reports"):
        print(p, p.stat().st_size)
