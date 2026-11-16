"""scripts/agent_loop.py fast path: no stale-BLOCKED stalls, no pre-existing junk in checks, fail-fast phase B."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def loop(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("agent_loop_under_test", ROOT / "scripts" / "agent_loop.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    monkeypatch.setattr(mod, "BLOCKED", tmp_path / "BLOCKED.md")
    monkeypatch.setattr(mod, "FROZEN", tmp_path / "frozen.json")
    return mod


def test_committed_blocked_file_does_not_block(loop, monkeypatch):
    loop.BLOCKED.write_text("# RESOLVED: old note\n")
    monkeypatch.setattr(loop, "changed_paths", lambda: [])
    assert loop.blocked() is False


def test_blocked_file_written_this_run_blocks(loop, monkeypatch):
    loop.BLOCKED.write_text("contradiction\n")
    monkeypatch.setattr(loop, "changed_paths", lambda: [".claude/loop/BLOCKED.md"])
    assert loop.blocked() is True


def test_preexisting_untracked_files_are_not_stray(loop, monkeypatch):
    monkeypatch.setattr(loop, "changed_paths", lambda: ["build/x.js", "tests/tooling/new_test.py"])
    loop.PREEXISTING.add("build/x.js")
    assert loop.agent_changes() == ["tests/tooling/new_test.py"]


def test_phase_b_fails_fast_without_running_affected_tests(loop, monkeypatch):
    calls = []

    def fake_run(paths, tag):
        calls.append(tag)
        return {"tests": 1, "failures": 1, "errors": 0, "skipped": 0, "rc": 1, "tail": "boom"}

    monkeypatch.setattr(loop, "pytest_run", fake_run)
    monkeypatch.setattr(loop, "changed_paths", lambda: [])
    errs = loop.verify_impl_phase({"id": "T.X", "tests": ["tests/x.py"]})
    assert calls == ["T.X.B.acceptance"]
    assert errs and "acceptance tests failing" in errs[0]


def test_implement_prompt_points_at_targeted_tests(loop):
    task = {"id": "T.X", "title": "t", "spec": ["§1"], "tests": ["tests/a.py", "tests/b.py"]}
    text = loop.render("implement", task)
    assert "pytest -q -x tests/a.py tests/b.py" in text
    assert "scripts/quick_tests.py" in text
