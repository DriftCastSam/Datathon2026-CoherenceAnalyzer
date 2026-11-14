"""`python -m portee.cli {refresh,check,review,audit,demo}` (SPEC §12)."""
from __future__ import annotations

import argparse
from pathlib import Path

from . import pipeline, store


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="portee")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("refresh"); p.add_argument("fixture", type=Path)
    p = sub.add_parser("check"); p.add_argument("fixture", type=Path)
    p.add_argument("--intent", required=True); p.add_argument("--auto-confirm", action="store_true")
    p = sub.add_parser("review"); p.add_argument("fixture", type=Path)
    p = sub.add_parser("audit"); p.add_argument("run_id")
    sub.add_parser("demo")
    a = ap.parse_args(argv)

    if a.cmd == "refresh":
        pipeline.refresh(a.fixture)
    elif a.cmd == "check":
        pipeline.check(a.fixture, a.intent, auto_confirm=a.auto_confirm)
    elif a.cmd == "review":
        pipeline.review(a.fixture)
    elif a.cmd == "audit":
        print(store.load_audit(a.run_id))
    elif a.cmd == "demo":
        raise NotImplementedError("demo: Robert check + Amira/Mateo review (SPEC §4 matrix)")


if __name__ == "__main__":
    main()
