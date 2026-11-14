#!/usr/bin/env python3
"""Precompute the dashboard's "Search and ask" universe and "What-if" scenarios with the real engine.

For each asset of the starter universe: put it at 5 % or 10 % of Robert's current portfolio, funded pro rata from
all holdings or from the largest bond holding. Each scenario runs the same code as a review: `intent.apply` ->
`engine.analyse` -> hard + soft checks -> five factor sections (flow-B context: current vs proposed) -> the
deterministic `explain.template_section` text. No LLM call, no recommendation: the user picks the asset.

Output (under <out>/): universe.json, whatif.json ({ticker: {asset, scenarios}})
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from coherence_analyzer import data, engine, explain, factors, intent as intent_mod, pipeline, store  # noqa: E402
from coherence_analyzer import config  # noqa: E402
from coherence_analyzer.coherence import hard, report as report_mod, soft  # noqa: E402

FIXTURE = ROOT / "fixtures" / "robert"
TARGETS = (5.0, 10.0)
METRIC_KEYS = ("vol", "mdd", "p5_T", "p50_T", "p95_T", "loss_prob", "width")


def _val(x):
    return x.get("value") if isinstance(x, dict) and "value" in x else x


def _cone(m: dict) -> dict:
    return {k: [round(v) for v in m["cone"][k]] for k in ("p5", "p25", "p50", "p75", "p95")}


def _shares(weights_pct: dict, assets) -> dict:
    cls = {t: assets.loc[t, "asset_class"] for t in weights_pct if t in assets.index}
    return {"single_stocks": sum(w for t, w in weights_pct.items() if cls.get(t) == "equity") / 100,
            "bonds": sum(w for t, w in weights_pct.items() if cls.get(t) == "etf_bond") / 100}


def build(out: Path) -> dict:
    assets = data.load_assets()
    profile_in = pipeline._load_profile(FIXTURE)
    pf = pipeline._load_portfolio(FIXTURE, None, profile_in, assets)
    profile = pipeline._refresh(profile_in, pf, pipeline._events(FIXTURE))["profile"]
    horizon_m = int(round(float(_val(profile["objectives"]["horizon_years"])) * 12))
    market = engine.load_market(str(config.DATA_DIR))
    meta = {n: data.load_meta(n) for n in pipeline.META_NAMES}
    w0, value = pf["weights_pct"], pf["value_cad"]

    def run(weights_pct: dict) -> tuple[dict, list]:
        m = store.plain(engine.analyse(market, weights_pct, horizon_m, value))
        fr = {t: v / 100 for t, v in weights_pct.items()}
        checks = report_mod.tag_checks(hard.run_all(fr, m, profile, assets, meta) + soft.run_all(profile, None, fr, assets))
        return m, checks

    m0, c0 = run(w0)
    ctx0 = factors.SectionContext(profile=profile, limits=profile["limits"], limits_trace=profile["limits_trace"],
                                  checks_current=c0, metrics_current=m0, locale="fr-CA")
    base_secs = {s["factor"]: s["status"] for s in report_mod.build_sections(ctx0)}
    bonds = sorted((t for t in w0 if assets.loc[t, "asset_class"] == "etf_bond"), key=lambda t: -w0[t])
    fundings = [("pro_rata_all", {"kind": "pro_rata_all"}, "Au prorata de toutes les positions")]
    if bonds:
        fundings.append((f"specific:{bonds[0]}", {"kind": "specific", "ticker": bonds[0]},
                         f"Depuis {assets.loc[bonds[0], 'name']} ({bonds[0]})"))

    universe = [{"ticker": t, "name": r["name"], "asset_class": r["asset_class"], "market": r["market"],
                 "currency": r["currency"], "sector": r["sector"], "held_pct": round(w0.get(t, 0.0), 2)}
                for t, r in sorted(assets.iterrows(), key=lambda kv: kv[1]["name"].lower())]
    base = {"verdict": report_mod.verdict(c0), "sections": base_secs, "metrics": {k: m0[k] for k in METRIC_KEYS},
            "cone": _cone(m0), "shares": _shares(w0, assets), "value_cad": value, "as_of": pf["as_of"],
            "weights_pct": w0, "fundings": [{"id": i, "label": lab} for i, _, lab in fundings], "targets": list(TARGETS),
            "checks": {f"{c['id']}|{c.get('subject')}": c["status"] for c in c0}}
    (out / "universe.json").write_text(json.dumps({"assets": universe, "base": base}, ensure_ascii=False))

    all_scen = {}
    for a in universe:
        t = a["ticker"]
        scen = {}
        for target in TARGETS:
            for fid, funding, _ in fundings:
                key = f"{int(target)}|{fid}"
                try:
                    w1, _ = intent_mod.apply(w0, {"action": "buy", "ticker": t, "target_weight_pct": target,
                                                  "funding": funding}, value)
                except ValueError as exc:
                    scen[key] = {"error": str(exc)}
                    continue
                m1, c1 = run(w1)
                ctx = factors.SectionContext(profile=profile, limits=profile["limits"], limits_trace=profile["limits_trace"],
                                             checks_current=c0, checks_proposed=c1, metrics_current=m0, metrics_proposed=m1,
                                             intent={"action": "buy", "ticker": t, "target_weight_pct": target,
                                                     "funding": funding}, locale="fr-CA")
                secs = report_mod.build_sections(ctx)
                scen[key] = {
                    "verdict": report_mod.verdict(c1), "metrics": {k: m1[k] for k in METRIC_KEYS}, "cone": _cone(m1),
                    "shares": _shares(w1, assets), "weights_pct": {k: round(v, 3) for k, v in w1.items()},
                    "checks": [{k: c.get(k) for k in ("id", "subject", "status", "value", "limit", "unit", "factor")}
                               for c in c1 if c["status"] != "pass"],
                    "sections": [{"factor": s["factor"], "title": s["title"], "before": base_secs[s["factor"]],
                                  "after": s["status"], "segments": explain.template_section(s["facts"])["segments"]}
                                 for s in secs],
                }
        all_scen[t] = {"asset": a, "scenarios": scen}
    (out / "whatif.json").write_text(json.dumps(all_scen, ensure_ascii=False, separators=(",", ":")))
    return base


if __name__ == "__main__":
    import time
    t0 = time.time()
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".cache" / "site" / "reports")
    print(f"done in {time.time() - t0:.0f}s")
