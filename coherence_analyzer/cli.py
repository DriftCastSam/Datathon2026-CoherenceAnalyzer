"""`python -m coherence_analyzer.cli {refresh,check,review,audit,demo}` (SPEC §12, docs/spec-decisions.md D7).

`review` is the headline command; `check` is Flow B (trade check): without `--auto-confirm` it stops at B2 and shows the
N4 draft intent; with it, it runs B3-B9 and prints the verdict, the diff and the headroom of the trade. `demo` reviews every persona
whose fixtures are complete (profile.json, events.jsonl, portfolio.json) and prints a notice for the others.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import config, pipeline, store

PERSONAS = ("robert", "amira", "mateo")
REQUIRED_FILES = ("profile.json", "events.jsonl", "portfolio.json")
REVIEW_PORTFOLIO = "portfolio_review.json"      # Robert "already bought NVDA" (D7 point 5)


def _print_review(label: str, rep: dict) -> None:
    unknown = sorted({u["id"] for u in rep["unknowns"]})
    statuses = ", ".join(f"{s['factor']}={s['status']}" for s in rep["sections"])
    print(f"{label}: {rep['verdict']}  (explanation: {rep['explanation']['mode']})")
    print(f"  sections: {statuses}")
    if unknown:
        print(f"  cannot verify: {', '.join(unknown)}")
    print(f"  run_id {rep['run_id']}  report {store.root() / 'reports' / (rep['run_id'] + '.json')}")
    print(profile_line(rep["profile_coherence"]))


def _intent_line(intent: dict) -> str:
    f = intent.get("funding") or {}
    src = f.get("ticker") or f.get("asset_class") or f.get("amount_cad") or ""
    return (f"{intent.get('action')} {intent.get('ticker')} to {intent.get('target_weight_pct')} % "
            f"(funding: {f.get('kind')}{' ' + str(src) if src != '' else ''})")


def _print_draft(res: dict) -> None:
    """B2: the N4 draft the user would confirm or edit; nothing has run yet beyond N4 and nothing is saved."""
    print(f"order_intent {res['event']['event_id']}: \"{res['event']['text']}\"")
    draft = res["draft"]
    print(f"  draft intent (N4, gate {res['draft_gate']}): " + (_intent_line(draft) if draft else "none"))
    print(f"  intent {res['intent_id']} on file: {_intent_line(res['intent'])}")
    print("  waiting for confirmation: re-run with --auto-confirm to check this trade")


def _print_check(rep: dict) -> None:
    print(f"{rep['intent']['intent_id']}: {_intent_line(rep['intent'])}")
    flips = ", ".join(f"{f['id']} {f['from']}->{f['to']}" for f in rep["diff"]["flipped"])
    statuses = ", ".join(f"{s['factor']}={s['status']}" for s in rep["sections"])
    print(f"{rep['verdict']}  (explanation: {rep['explanation']['mode']})")
    print(f"  sections: {statuses}")
    if flips:
        print(f"  changed by the trade: {flips}")
    if rep["headroom"]:
        print(f"  {rep['headroom']['line']}")
    unknown = sorted({u["id"] for u in rep["unknowns"]})
    if unknown:
        print(f"  cannot verify: {', '.join(unknown)}")
    print(f"  run_id {rep['run_id']}  report {store.root() / 'reports' / (rep['run_id'] + '.json')}")
    print(profile_line(rep["profile_coherence"]))
    print(rep["disclaimer"])


def profile_line(block: dict) -> str:
    """`profile: warning (C1, N6.1)`; without findings just `profile: pass`."""
    ids = ", ".join(f["id"] for f in block["findings"])
    return f"profile: {block['status']} ({ids})" if ids else f"profile: {block['status']}"


def demo(fixtures_dir: Path | None = None) -> list[dict]:
    """Review each persona with complete fixtures (+ the review portfolio if the persona has one); notice otherwise."""
    fixtures_dir = Path(fixtures_dir or config.FIXTURES_DIR)
    reports: list[dict] = []
    for name in PERSONAS:
        d = fixtures_dir / name
        missing = [f for f in REQUIRED_FILES if not (d / f).exists()]
        if missing:
            print(f"notice: {name}: fixtures incomplete (missing {', '.join(missing)}); no report for {name}")
            continue
        portfolios = [(f"{name} (current portfolio)", None)]
        if (d / REVIEW_PORTFOLIO).exists():
            portfolios.append((f"{name} (review portfolio)", REVIEW_PORTFOLIO))
        for label, portfolio in portfolios:
            rep = pipeline.review(d, portfolio)
            _print_review(label, rep)
            reports.append(rep)
    print(config.DISCLAIMER["fr-CA"])
    return reports


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="coherence_analyzer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("refresh"); p.add_argument("fixture", type=Path)
    p = sub.add_parser("check"); p.add_argument("fixture", type=Path)
    p.add_argument("--intent", required=True); p.add_argument("--auto-confirm", action="store_true")
    p = sub.add_parser("review"); p.add_argument("fixture", type=Path)
    p.add_argument("--portfolio", default=None, help="portfolio file inside the fixture dir (default portfolio.json)")
    p = sub.add_parser("audit"); p.add_argument("run_id")
    sub.add_parser("demo")
    a = ap.parse_args(argv)

    if a.cmd == "refresh":
        out = pipeline.refresh(a.fixture)
        meta = out["profile"]["meta"]
        print(f"profile {meta['profile_id']} v{meta['version']}: {len(out['profile_diff'])} field(s) changed; "
              f"run_id {out['run_id']}")
    elif a.cmd == "check":
        res = pipeline.check(a.fixture, a.intent, auto_confirm=a.auto_confirm)
        if res.get("status") == pipeline.AWAITING:
            _print_draft(res)
        else:
            _print_check(res)
    elif a.cmd == "review":
        _print_review(a.fixture.name, pipeline.review(a.fixture, a.portfolio))
    elif a.cmd == "audit":
        print(json.dumps(store.load_audit(a.run_id), indent=2, ensure_ascii=False))
    elif a.cmd == "demo":
        demo()


if __name__ == "__main__":
    main()
