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


PY_WRITE = re.compile(r"write_text|write_bytes|open\([^)]*['\"][wax+]|shutil\.|os\.(remove|unlink|rename|replace)|\.unlink\(")


def check_bash(cmd: str, locked: set[str] | None = None) -> str | None:
    """Return a block message if a shell command writes to a protected or frozen path."""
    locked = frozen() if locked is None else locked
    for target in [*PROTECTED, *locked]:
        t = re.escape(target.rstrip("/"))
        if not re.search(t, cmd):
            continue
        redirect = re.search(rf"(>>?|\btee(\s+-a)?)\s*['\"]?\S*{t}", cmd)
        mutate = WRITE_VERBS.search(cmd) and re.search(rf"\b(sed\s+-i|rm|mv|cp|truncate|chmod)\b[^;&|]*{t}", cmd)
        # Any Python write API anywhere in a command that names the path. Stricter than matching
        # path-then-write on one segment: `p = Path("x"); p.write_text(...)` used to slip through.
        pywrite = PY_WRITE.search(cmd)
        if redirect or mutate or pywrite:
            return f"shell write touching {target}. {blocked(target, locked) or ''}"
    return None


def main() -> int:
    ev = json.load(sys.stdin)
    tool, ti = ev.get("tool_name", ""), ev.get("tool_input", {}) or {}
    # Frozen oracles bind the build loop only (agent_loop.py sets COHERENCE_ANALYZER_LOOP=1 and re-checks hashes).
    # Interactive sessions may edit them, then `agent_loop.py --refreeze`. PROTECTED stays blocked everywhere.
    locked = frozen() if os.environ.get("COHERENCE_ANALYZER_LOOP") else set()
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = rel(ti.get("file_path") or ti.get("notebook_path") or "")
        if msg := blocked(path, locked):
            print(f"BLOCKED: {msg}", file=sys.stderr)
            return 2
    elif tool == "Bash":
        if msg := check_bash(ti.get("command", ""), locked):
            print(f"BLOCKED: {msg}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
