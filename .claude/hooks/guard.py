#!/usr/bin/env python3
"""PreToolUse guard: blocks writes to protected paths and to frozen oracle tests.

Exit 2 = block (stderr is shown to Claude). Exit 0 = allow.
This is defence in depth. scripts/agent_loop.py also verifies hashes after each phase.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR", Path(__file__).resolve().parents[2]))

# CLAUDE.md invariants: humans only / source of truth / golden reference.
PROTECTED = (
    "SPEC.md",
    "data/starter/",
    "data/meta/activity_tags.csv",
    "scripts/engine_v0.py",
)
WRITE_VERBS = re.compile(r"(>|\btee\b|\bsed\s+-i|\brm\b|\bmv\b|\bcp\b|\btruncate\b|\bchmod\b|write_text|open\([^)]*['\"][wa])")


def frozen() -> set[str]:
    try:
        return set(json.loads((ROOT / ".claude/loop/frozen.json").read_text())["files"])
    except (OSError, ValueError, KeyError):
        return set()


def rel(p: str) -> str:
    try:
        return Path(p).resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return p


def blocked(path: str, locked: set[str]) -> str | None:
    if path in locked:
        return f"{path} is a frozen oracle test (.claude/loop/frozen.json). If it contradicts SPEC.md, write .claude/loop/BLOCKED.md instead."
    for p in PROTECTED:
        if path == p or (p.endswith("/") and path.startswith(p)):
            return f"{path} is protected by CLAUDE.md invariants (humans only)."
    return None


def main() -> int:
    ev = json.load(sys.stdin)
    tool, ti = ev.get("tool_name", ""), ev.get("tool_input", {}) or {}
    locked = frozen()
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = rel(ti.get("file_path") or ti.get("notebook_path") or "")
        if msg := blocked(path, locked):
            print(f"BLOCKED: {msg}", file=sys.stderr)
            return 2
    elif tool == "Bash":
        cmd = ti.get("command", "")
        for target in [*PROTECTED, *locked]:
            t = re.escape(target.rstrip("/"))
            redirect = re.search(rf"(>>?|\btee(\s+-a)?)\s*['\"]?\S*{t}", cmd)
            mutate = WRITE_VERBS.search(cmd) and re.search(rf"\b(sed\s+-i|rm|mv|truncate|chmod)\b[^;&|]*{t}", cmd)
            pywrite = re.search(rf"{t}[^;&|]*(write_text|open\([^)]*['\"][wa])", cmd)
            if redirect or mutate or pywrite:
                print(f"BLOCKED: shell write touching {target}. {blocked(target, locked) or ''}", file=sys.stderr)
                return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
