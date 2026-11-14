"""Strands Agents coding worker for scripts/agent_loop.py (--backend strands, the default).

One call = one headless coding session on Amazon Bedrock, the Strands equivalent of `claude -p`:
  model    BedrockModel(BEDROCK_MODEL_ID, AWS_REGION) from .env
  tools    strands.vended_tools read / write / edit / shell, rooted at the repo
  guards   a BeforeToolCallEvent hook that cancels (with a reason the model sees):
             - writes/edits outside the repo, to protected paths, or to frozen oracle tests
               (same rules as .claude/hooks/guard.py, imported from it)
             - shell commands not on the allowlist (no git state changes, no installs, no network)
             - any tool call past the turn budget
The loop still decides "done" with its own deterministic verifier; this module only proposes diffs.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shlex
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("ca_guard", ROOT / ".claude" / "hooks" / "guard.py")
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)  # type: ignore[union-attr]

# First word(s) of each shell segment the agent may run. Mirrors agent_loop.ALLOWED for claude -p.
SHELL_ALLOW = ("python -m pytest", "pytest", "python -m coherence_analyzer", "python -c", "python scripts/",
               "git status", "git diff", "git log", "git show", "ls", "cat", "head", "tail", "grep", "rg", "find",
               "wc", "sed -n", "diff", "sort", "uniq", "echo", "jq", "mkdir")
SHELL_DENY = re.compile(r"\bgit\s+(commit|reset|checkout|stash|push|rebase|merge|clean|switch|restore)\b|\bpip\b|\bcurl\b|"
                        r"\bwget\b|\bsudo\b|\brm\s+-rf\b|\bssh\b|\baws\b")
ENV_ASSIGN = re.compile(r"^[A-Z_][A-Z0-9_]*=\S*$")

SYSTEM = """You are a careful senior Python engineer working headless inside the CoherenceAnalyzer repo.
Repo root: {root}. The shell starts in the repo root. File tools need absolute paths under the repo root.
Project rules (CLAUDE.md, verbatim):
---
{claude_md}
---
Tools: read, write, edit (old_str must be unique), shell (allowlisted: pytest, python -m/-c, read-only git, ls/cat/grep/find/sed -n).
A guard cancels forbidden calls and tells you why; do not try to work around it. You cannot commit: the loop commits for you.
Work in small verified steps: read the relevant SPEC sections and code first, then edit, then run the tests.
Keep each write or edit call under about 250 lines: create a large file with a first write, then append
the rest with edit calls. One huge tool call can hit the output token limit and be lost."""


def _segments(cmd: str) -> list[str]:
    return [s.strip() for s in re.split(r"&&|\|\||;|\|", cmd) if s.strip()]


def _strip_env(seg: str) -> str:
    try:
        toks = shlex.split(seg)
    except ValueError:
        return seg
    while toks and ENV_ASSIGN.match(toks[0]):
        toks.pop(0)
    return " ".join(toks)


def shell_block_reason(cmd: str) -> str | None:
    if SHELL_DENY.search(cmd):
        return "command not allowed in the build loop (git state changes, installs, network, rm -rf are human-only)"
    if "$(" in cmd or "`" in cmd:
        return "command substitution is not allowed; run the inner command on its own"
    for seg in _segments(cmd):
        s = _strip_env(seg)
        s = re.sub(r"^(\S*/)?python3?(\s)", r"python\2", s)  # .venv/bin/python -> python
        if s.startswith("cd "):
            continue
        if not any(s == a or s.startswith(a + " ") or (a.endswith("/") and s.startswith(a)) for a in SHELL_ALLOW):
            return f"'{s.split()[0] if s else seg}' is not on the shell allowlist {list(SHELL_ALLOW)}"
    if msg := guard.check_bash(cmd):
        return msg
    return None


def path_block_reason(path: str) -> str | None:
    try:
        rel = Path(path).resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return f"{path} is outside the repo root {ROOT}"
    if rel.startswith(".git/") or rel == ".git":
        return ".git is off limits"
    if rel.startswith(".claude/loop/") and not rel.startswith(".claude/loop/BLOCKED.md"):
        return "loop state is owned by scripts/agent_loop.py (you may only write .claude/loop/BLOCKED.md)"
    if rel.startswith(".claude/hooks/"):
        return "hooks are human-only"
    return guard.blocked(rel, guard.frozen())


def run(prompt: str, *, max_turns: int, timeout: int, model_id: str | None = None, max_tokens: int = 32000) -> dict:
    """Run one coding session. Returns a dict shaped like `claude -p --output-format json` (subset)."""
    from strands import Agent
    from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry
    from strands.models import BedrockModel
    from strands.vended_tools import edit, read, shell, write

    model_id = model_id or os.environ.get("BEDROCK_MODEL_ID")
    if not model_id:
        return {"is_error": True, "result": "BEDROCK_MODEL_ID is not set (.env)"}
    deadline = time.monotonic() + timeout
    stats = {"tool_calls": 0, "cancelled": []}

    class Guard(HookProvider):
        def register_hooks(self, registry: HookRegistry, **_) -> None:
            registry.add_callback(BeforeToolCallEvent, self.check)

        def check(self, event: BeforeToolCallEvent) -> None:
            stats["tool_calls"] += 1
            name, ti = event.tool_use["name"], event.tool_use.get("input") or {}
            reason = None
            if stats["tool_calls"] > max_turns or time.monotonic() > deadline:
                reason = "turn or time budget exhausted: stop calling tools and give your final summary now"
            elif name in ("write", "edit"):
                reason = path_block_reason(ti.get("path", ""))
            elif name == "read":
                p = ti.get("path", "")
                if p and not Path(p).resolve().is_relative_to(ROOT.resolve()):
                    reason = f"{p} is outside the repo root"
            elif name == "shell":
                reason = shell_block_reason(ti.get("command", ""))
                if not reason:
                    ti["timeout"] = min(int(ti.get("timeout") or 600), 900)
            if reason:
                stats["cancelled"].append({"tool": name, "reason": reason})
                event.cancel_tool = f"BLOCKED by the build-loop guard: {reason}"

    claude_md = (ROOT / "CLAUDE.md").read_text()
    model = BedrockModel(model_id=model_id, region_name=os.environ.get("AWS_REGION", "us-east-1"), max_tokens=max_tokens)
    agent = Agent(model=model, tools=[read, write, edit, shell], hooks=[Guard()], callback_handler=None,
                  system_prompt=SYSTEM.format(root=ROOT, claude_md=claude_md), name="ca-coder")
    cwd = os.getcwd()
    os.chdir(ROOT)
    t0 = time.monotonic()
    try:
        res = agent(prompt)
        usage = dict(res.metrics.accumulated_usage)
        return {"is_error": False, "result": str(res)[-4000:], "num_turns": stats["tool_calls"],
                "usage": usage, "model_id": model_id, "duration_s": round(time.monotonic() - t0, 1),
                "stop_reason": str(getattr(res, "stop_reason", "")), "guard_cancellations": stats["cancelled"]}
    except Exception as e:  # surfaced to the loop log; the verifier still decides
        return {"is_error": True, "result": f"{type(e).__name__}: {e}"[:4000], "num_turns": stats["tool_calls"],
                "model_id": model_id, "guard_cancellations": stats["cancelled"]}
    finally:
        os.chdir(cwd)
