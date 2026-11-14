"""Build-loop guard for the Strands coding agent (scripts/strands_coder.py) and the shared hook guard.

Offline: imports only the guard helpers, never the strands package or Bedrock.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


coder = _load("strands_coder_under_test", ROOT / "scripts" / "strands_coder.py")
guard = coder.guard


@pytest.mark.parametrize("cmd", [
    "COHERENCE_ANALYZER_LLM=mock python -m pytest -q",
    "python -m pytest tests/test_rules.py -q && git diff --stat",
    ".venv/bin/python -c 'import coherence_analyzer'",
    "grep -n limits SPEC.md | head -20",
    "sed -n 1,40p coherence_analyzer/rules/limits.py",
])
def test_allowed_shell(cmd):
    assert coder.shell_block_reason(cmd) is None


@pytest.mark.parametrize("cmd", [
    "git commit -am x", "git checkout -- tests/", "pip install requests", "curl https://example.com",
    "rm -rf coherence_analyzer", "aws s3 ls", "make build", "python -m pytest $(cat x)",
    "echo hi > SPEC.md",
    "python -c \"from pathlib import Path; p = Path('SPEC.md'); p.write_text('x')\"",
])
def test_blocked_shell(cmd):
    assert coder.shell_block_reason(cmd)


def test_hook_guard_catches_semicolon_split_python_write():
    # Regression: path and write_text separated by ';' used to pass guard.py.
    cmd = "python - <<'EOF'\nimport pathlib\nr = pathlib.Path('SPEC.md'); s = r.read_text()\nr.write_text(s)\nEOF"
    assert guard.check_bash(cmd, set())


def test_hook_guard_allows_reading_protected_files():
    assert guard.check_bash("grep -n verdict SPEC.md", set()) is None


@pytest.mark.parametrize("rel,ok", [
    ("coherence_analyzer/coherence/hard.py", True),
    (".claude/loop/BLOCKED.md", True),
    ("SPEC.md", False),
    ("data/starter/prices_daily.csv", False),
    (".claude/loop/frozen.json", False),
    (".claude/hooks/guard.py", False),
    (".git/config", False),
])
def test_write_paths(rel, ok):
    assert (coder.path_block_reason(str(ROOT / rel)) is None) is ok


def test_write_outside_repo_blocked():
    assert coder.path_block_reason("/etc/passwd")


def test_frozen_tests_blocked_for_writes(monkeypatch):
    monkeypatch.setattr(guard, "frozen", lambda: {"tests/test_rules.py"})
    assert coder.path_block_reason(str(ROOT / "tests" / "test_rules.py"))
