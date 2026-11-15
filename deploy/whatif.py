#!/usr/bin/env python3
"""Precompute the dashboard's "Search and ask" universe and "What-if" scenarios with the real engine.

A scenario is a set of inputs the user picks in the Simulation tab: the asset, the size (target weight, % of the
portfolio) and the funding (pro rata from all holdings, or a specific holding). This script precomputes the grid of
those inputs for each asset of the starter universe against the current portfolio of ONE profile directory (the
`profile_dir` argument of `build`: profile.json, events.jsonl, portfolio.json; any persona seed or user). Each scenario runs the same
code as a review: `intent.apply` -> `engine.analyse` -> hard + soft checks -> five factor sections (flow-B context:
current vs proposed) -> the deterministic `explain.template_section` text. No LLM call.

Describe, never prescribe (docs/spec-decisions.md D10): this script makes no recommendation, and nothing is
compared or ordered against anything else. Scenarios are keyed by the user-selectable inputs (size, then funding),
never ordered or selected by outcome; the universe is listed by name.

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

FIXTURES = ROOT / "fixtures"
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


def default_profile_dir() -> Path:
    """The profile directory used when `build` gets none: the first one under fixtures/, in name order."""
    found = sorted(d for d in FIXTURES.iterdir() if (d / "profile.json").is_file() and (d / "portfolio.json").is_file())
    if not found:
        raise FileNotFoundError(f"no profile directory under {FIXTURES}")
    return found[0]


def context(profile_dir: Path, locale: str) -> dict:
    """Load profile, portfolio, assets and prepare the what-if context.

    Args:
        profile_dir: directory holding profile.json, events.jsonl, portfolio.json
        locale: locale for section narratives (e.g. "en-CA", "fr-CA")

    Returns:
        Context dict with all data needed for universe() and scenarios()
    """
    assets = data.load_assets()
    profile_in = pipeline._load_profile(profile_dir)
    pf = pipeline._load_portfolio(profile_dir, None, profile_in, assets)
    profile = pipeline._refresh(profile_in, pf, pipeline._events(profile_dir))["profile"]
    horizon_m = pipeline._horizon_months(profile)
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
                                  checks_current=c0, metrics_current=m0, locale=locale)
    base_secs = {s["factor"]: s["status"] for s in report_mod.build_sections(ctx0)}
    bonds = sorted((t for t in w0 if assets.loc[t, "asset_class"] == "etf_bond"), key=lambda t: -w0[t])

    if locale == "fr-CA":
        fundings = [("pro_rata_all", {"kind": "pro_rata_all"}, "Au prorata de toutes les positions")]
        if bonds:
            fundings.append((f"specific:{bonds[0]}", {"kind": "specific", "ticker": bonds[0]},
                             f"Depuis {assets.loc[bonds[0], 'name']} ({bonds[0]})"))
    else:
        fundings = [("pro_rata_all", {"kind": "pro_rata_all"}, "Pro rata from all holdings")]
        if bonds:
            fundings.append((f"specific:{bonds[0]}", {"kind": "specific", "ticker": bonds[0]},
                             f"From {assets.loc[bonds[0], 'name']} ({bonds[0]})"))

    return {
        "assets": assets, "profile": profile, "w0": w0, "value": value, "as_of": pf["as_of"],
        "m0": m0, "c0": c0, "base_secs": base_secs, "fundings": fundings, "run": run, "locale": locale
    }


def universe(ctx: dict) -> dict:
    """Build the universe block: assets list and base portfolio state.

    Args:
        ctx: context from context()

    Returns:
        {"assets": [...], "base": {...}} suitable for universe.json
    """
    assets, w0, m0, c0 = ctx["assets"], ctx["w0"], ctx["m0"], ctx["c0"]
    universe_assets = [
        {"ticker": t, "name": r["name"], "asset_class": r["asset_class"], "market": r["market"],
         "currency": r["currency"], "sector": r["sector"], "held_pct": round(w0.get(t, 0.0), 2)}
        for t, r in sorted(assets.iterrows(), key=lambda kv: kv[1]["name"].lower())
    ]
    base = {
        "verdict": report_mod.verdict(c0), "sections": ctx["base_secs"], "metrics": {k: m0[k] for k in METRIC_KEYS},
        "cone": _cone(m0), "shares": _shares(w0, assets), "value_cad": ctx["value"], "as_of": ctx["as_of"],
        "weights_pct": w0, "fundings": [{"id": i, "label": lab} for i, _, lab in ctx["fundings"]], "targets": list(TARGETS),
        "checks": {f"{c['id']}|{c.get('subject')}": c["status"] for c in c0}
    }
    return {"assets": universe_assets, "base": base}


def scenarios(ctx: dict, tickers: list[str]) -> dict:
    """Build what-if scenarios for the given tickers.

    Args:
        ctx: context from context()
        tickers: list of tickers to compute scenarios for

    Returns:
        {ticker: {"asset": {...}, "scenarios": {key: {...}}}} suitable for whatif.json
    """
    assets = ctx["assets"]
    universe_assets = [
        {"ticker": t, "name": assets.loc[t, "name"], "asset_class": assets.loc[t, "asset_class"],
         "market": assets.loc[t, "market"], "currency": assets.loc[t, "currency"],
         "sector": assets.loc[t, "sector"], "held_pct": round(ctx["w0"].get(t, 0.0), 2)}
        for t in tickers if t in assets.index
    ]

    all_scen = {}
    for a in universe_assets:
        t = a["ticker"]
        scen = {}
        for target in TARGETS:
            for fid, funding, _ in ctx["fundings"]:
                key = f"{int(target)}|{fid}"
                try:
                    w1, _ = intent_mod.apply(ctx["w0"], {"action": "buy", "ticker": t, "target_weight_pct": target,
                                                  "funding": funding}, ctx["value"])
                except ValueError as exc:
                    scen[key] = {"error": str(exc)}
                    continue
                m1, c1 = ctx["run"](w1)
                secs_ctx = factors.SectionContext(
                    profile=ctx["profile"], limits=ctx["profile"]["limits"], limits_trace=ctx["profile"]["limits_trace"],
                    checks_current=ctx["c0"], checks_proposed=c1, metrics_current=ctx["m0"], metrics_proposed=m1,
                    intent={"action": "buy", "ticker": t, "target_weight_pct": target, "funding": funding}, locale=ctx["locale"])
                secs = report_mod.build_sections(secs_ctx)
                scen[key] = {
                    "verdict": report_mod.verdict(c1), "metrics": {k: m1[k] for k in METRIC_KEYS}, "cone": _cone(m1),
                    "shares": _shares(w1, assets), "weights_pct": {k: round(v, 3) for k, v in w1.items()},
                    "checks": [{k: c.get(k) for k in ("id", "subject", "status", "value", "limit", "unit", "factor")}
                               for c in c1 if c["status"] != "pass"],
                    "sections": [{"factor": s["factor"], "title": s["title"], "before": ctx["base_secs"][s["factor"]],
                                  "after": s["status"], "segments": explain.template_section(s["facts"])["segments"]}
                                 for s in secs],
                }
        all_scen[t] = {"asset": a, "scenarios": scen}
    return all_scen


def build(out: Path, profile_dir: Path | None = None, locale: str = "fr-CA") -> dict:
    """Write universe.json and whatif.json into `out` for the profile directory `profile_dir` (its profile.json,
    events.jsonl and portfolio.json). Nothing is shared between two calls."""
    fixture = Path(profile_dir) if profile_dir is not None else default_profile_dir()
    ctx = context(fixture, locale)
    univ = universe(ctx)
    (out / "universe.json").write_text(json.dumps(univ, ensure_ascii=False), encoding="utf-8")
    all_tickers = [a["ticker"] for a in univ["assets"]]
    all_scen = scenarios(ctx, all_tickers)
    (out / "whatif.json").write_text(json.dumps(all_scen, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return univ["base"]


if __name__ == "__main__":
    import time
    t0 = time.time()
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".cache" / "site" / "reports")
    print(f"done in {time.time() - t0:.0f}s")
