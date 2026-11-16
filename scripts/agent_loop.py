#!/usr/bin/env python3
"""Scripted agentic build loop for CoherenceAnalyzer (SPEC §17).

Coding agent backends (--backend):
  strands  (default) Strands Agents on Amazon Bedrock, see scripts/strands_coder.py
  claude   headless Claude Code (`claude -p`)

For each pending task in .claude/loop/tasks.json:
  Phase A  the agent writes the oracle tests from SPEC.md  -> verify (only tests/ changed, collected,
           no skips, red)  -> freeze test hashes  -> git commit
  Phase B  the agent implements until frozen tests pass     -> verify (hashes intact, protected
           paths untouched, acceptance green with 0 skips, full suite green)  -> git commit
  Failure  retry with the verifier output appended (max --retries), then stop for a human.

Determinism comes from the deterministic parts: a fixed task order, oracle tests frozen before any
implementation, a verifier that is pure pytest + git + sha256, one commit per phase (every state is
reproducible from git), and a pinned environment (COHERENCE_ANALYZER_LLM=mock, PYTHONHASHSEED=0, TZ=UTC).
The LLM only proposes diffs. It never decides whether a step is done.

Usage:
  python scripts/agent_loop.py                  # run all pending tasks
  python scripts/agent_loop.py --only P0.4      # one task
  python scripts/agent_loop.py --stop-after-tests   # human reviews oracle tests before code
  python scripts/agent_loop.py --dry-run        # print prompts + plan, change nothing
  python scripts/agent_loop.py --verify P0.4    # just run the verifier for a task
  python scripts/agent_loop.py --backend claude  # use claude -p instead of Strands
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOOP = ROOT / ".claude" / "loop"
TASKS, STATE, FROZEN, RUNS = LOOP / "tasks.json", LOOP / "state.json", LOOP / "frozen.json", LOOP / "runs"
BLOCKED = LOOP / "BLOCKED.md"
PROTECTED = ("SPEC.md", "data/starter/", "data/meta/activity_tags.csv", "scripts/engine_v0.py")
BRANCH = "loop/p0"
FULL_SUITE = False  # --full: whole suite in preflight and phase B (default: affected tests only)

try:  # BEDROCK_MODEL_ID / AWS_* for the Strands backend; never overrides the pinned values below
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

ENV = {**os.environ, "COHERENCE_ANALYZER_LOOP": "1", "COHERENCE_ANALYZER_LLM": "mock", "PYTHONHASHSEED": "0", "TZ": "UTC",
       "PATH": f"{ROOT / '.venv' / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}"}
PY = str(ROOT / ".venv" / "bin" / "python") if (ROOT / ".venv" / "bin" / "python").exists() else sys.executable

XDIST = subprocess.run([PY, "-c", "import xdist"], capture_output=True).returncode == 0

ALLOWED = ["Read", "Edit", "Write", "Glob", "Grep",
           "Bash(python -m pytest:*)", "Bash(COHERENCE_ANALYZER_LLM=mock python -m pytest:*)", "Bash(pytest:*)",
           "Bash(python -m coherence_analyzer.cli:*)", "Bash(COHERENCE_ANALYZER_LLM=mock python -m coherence_analyzer.cli:*)",
           "Bash(python -c:*)", "Bash(python scripts/quick_tests.py:*)", "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)", "Bash(ls:*)"]
DISALLOWED = ["Bash(git commit:*)", "Bash(git reset:*)", "Bash(git checkout:*)", "Bash(git stash:*)",
              "Bash(git push:*)", "Bash(pip install:*)", "Bash(curl:*)", "Bash(wget:*)", "WebFetch", "WebSearch"]


# ---------- small helpers ----------

def sh(*cmd: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, env=ENV, text=True, capture_output=True, check=check)


def jload(p: Path, default):
    return json.loads(p.read_text()) if p.exists() else default


def jsave(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "MISSING"


def changed_paths() -> list[str]:
    out = sh("git", "status", "--porcelain", "--untracked-files=all").stdout
    return sorted(line[3:].split(" -> ")[-1] for line in out.splitlines() if line.strip())


PREEXISTING: set[str] = set()  # untracked files present at preflight (build/, editor junk): not the agent's doing


def agent_changes() -> list[str]:
    return [p for p in changed_paths() if p not in PREEXISTING]


def blocked() -> bool:
    """The agent raised a spec/test contradiction in THIS run (BLOCKED.md new or changed vs HEAD)."""
    return BLOCKED.exists() and ".claude/loop/BLOCKED.md" in changed_paths()


def log(msg: str) -> None:
    print(f"[loop {time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------- pytest as the oracle ----------

def pytest_run(paths: list[str], tag: str) -> dict:
    xml = RUNS / f"{tag}.junit.xml"
    xml.parent.mkdir(parents=True, exist_ok=True)
    par = ["-n", "auto", "--dist", "loadfile"] if XDIST and (not paths or len(paths) > 3) else []
    r = sh(PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rs", *par, f"--junitxml={xml}", *paths)
    stats = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    if xml.exists():
        for s in ET.parse(xml).getroot().iter("testsuite"):
            for k in stats:
                stats[k] += int(s.get(k, 0))
    stats["rc"] = r.returncode
    stats["tail"] = (r.stdout + r.stderr)[-4000:]
    return stats


# ---------- verifiers (pure, deterministic) ----------

def verify_tests_phase(task: dict) -> list[str]:
    errs = []
    if blocked():
        return ["agent reported a blocker:\n" + BLOCKED.read_text()]
    stray = [p for p in agent_changes() if not p.startswith("tests/") and not p.startswith(".claude/loop/")]
    if stray:
        errs.append(f"Phase A may only touch tests/. Also changed: {stray}")
    for t in task["tests"]:
        if not (ROOT / t).exists():
            errs.append(f"missing test file {t}")
        elif "pytest.mark.skip(" in (ROOT / t).read_text() and not task.get("allow_skips"):
            errs.append(f"{t} still has a skip mark")
    st = pytest_run(task["tests"], f"{task['id']}.A")
    if st["tests"] == 0:
        errs.append("no tests collected")
    if st["errors"] and "ERROR collecting" in st["tail"]:
        errs.append("collection error:\n" + st["tail"])
    if st["skipped"] and not task.get("allow_skips"):
        errs.append(f"{st['skipped']} tests skipped")
    if st["failures"] + st["errors"] == 0:
        log(f"warning: {task['id']} oracle tests already pass against the stubs; check they assert something")
    return errs


def verify_impl_phase(task: dict) -> list[str]:
    errs = []
    frozen = jload(FROZEN, {"files": {}})["files"]
    for f, h in sorted(frozen.items()):
        if sha(ROOT / f) != h:
            errs.append(f"frozen test modified: {f}")
    for p in agent_changes():
        if any(p == x or (x.endswith("/") and p.startswith(x)) for x in PROTECTED):
            errs.append(f"protected path modified: {p}")
    if blocked():
        errs.append("agent reported a spec/test contradiction:\n" + BLOCKED.read_text())
        return errs
    acc = pytest_run(task["tests"], f"{task['id']}.B.acceptance")
    if acc["failures"] or acc["errors"] or acc["rc"] not in (0,):
        errs.append("acceptance tests failing:\n" + acc["tail"])
    if acc["skipped"] and not task.get("allow_skips"):
        errs.append(f"acceptance tests skipped: {acc['skipped']}")
    if errs:  # fail fast: the retry prompt only needs the acceptance failure
        return errs
    if FULL_SUITE:
        full = pytest_run([], f"{task['id']}.B.full")
    else:  # only what the change can reach (scripts/quick_tests.py); `loop_lane.py merge` runs the full suite
        sys.path.insert(0, str(ROOT / "scripts"))
        import quick_tests
        picked, _, _ = quick_tests.select(quick_tests.changed_files("HEAD"), heavy=False, light_all=False)
        picked = [p for p in picked if p not in task["tests"]]  # acceptance already ran above
        full = pytest_run(picked, f"{task['id']}.B.affected") if picked else {"rc": 0, "tail": ""}
    if full["rc"] != 0:
        errs.append("full suite failing:\n" + full["tail"])
    return errs


# ---------- claude -p ----------

def render(name: str, task: dict, retry: str = "") -> str:
    tpl = (LOOP / "prompts" / f"{name}.md").read_text()
    skip_rule = " (this task allows a reasoned conditional skip, see notes)" if task.get("allow_skips") else ""
    return tpl.format(id=task["id"], title=task["title"], spec=", ".join(task["spec"]),
                      tests=", ".join(task["tests"]), tests_sp=" ".join(task["tests"]), code=", ".join(task.get("code", [])),
                      notes=task.get("notes", ""), skip_rule=skip_rule, retry=retry)


def agent(prompt: str, tag: str, args) -> dict:
    return strands(prompt, tag, args) if args.backend == "strands" else claude(prompt, tag, args)


def strands(prompt: str, tag: str, args) -> dict:
    sys.path.insert(0, str(ROOT / "scripts"))
    import strands_coder
    os.environ.update(ENV)  # the agent's shell runs in-process: same pinned env as pytest_run
    log(f"strands  [{tag}]")
    out = strands_coder.run(prompt, max_turns=args.max_turns, timeout=args.timeout, model_id=args.model)
    jsave(RUNS / f"{tag}.strands.json", out)
    u = out.get("usage") or {}
    log(f"  tool_calls={out.get('num_turns')} tokens_in={u.get('inputTokens')} tokens_out={u.get('outputTokens')} "
        f"blocked={len(out.get('guard_cancellations') or [])} error={out.get('is_error')}")
    if out.get("is_error"):
        log("  " + str(out.get("result"))[:500])
    return out


def claude(prompt: str, tag: str, args) -> dict:
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--max-turns", str(args.max_turns),
           "--permission-mode", "acceptEdits",
           "--allowedTools", *ALLOWED, "--disallowedTools", *DISALLOWED]
    if args.model:
        cmd += ["--model", args.model]
    log(f"claude -p  [{tag}]")
    r = subprocess.run(cmd, cwd=ROOT, env=ENV, text=True, capture_output=True, timeout=args.timeout)
    try:
        out = json.loads(r.stdout)
    except ValueError:
        out = {"is_error": True, "raw": r.stdout[-4000:], "stderr": r.stderr[-4000:]}
    jsave(RUNS / f"{tag}.claude.json", out)
    log(f"  turns={out.get('num_turns')} cost=${out.get('total_cost_usd', 0):.2f} error={out.get('is_error')}")
    return out


def commit(msg: str) -> None:
    paths = agent_changes()  # never sweep pre-existing untracked junk (build/, ...) into a loop commit
    if paths:
        sh("git", "add", "-A", "--", *paths, check=True)
    r = sh("git", "commit", "-q", "-m", msg + "\n\nAuthored by scripts/agent_loop.py (Claude Code, headless).")
    if r.returncode != 0 and "nothing to commit" not in r.stdout:
        raise SystemExit(r.stdout + r.stderr)


def phase(task: dict, name: str, verify, args) -> bool:
    retry = ""
    for attempt in range(args.retries + 1):
        tag = f"{task['id']}.{'A' if name == 'write_tests' else 'B'}.{attempt}"
        agent(render(name, task, retry), tag, args)
        errs = verify(task)
        jsave(RUNS / f"{tag}.verify.json", {"errors": errs})
        if not errs:
            return True
        log(f"  verify failed ({len(errs)} issue(s)); see .claude/loop/runs/{tag}.verify.json")
        if blocked():
            return False
        retry = ("\nRETRY: the deterministic verifier rejected your previous attempt. Fix exactly these and "
                 "nothing else:\n" + "\n---\n".join(errs)[:6000] + "\n")
    return False


# ---------- main ----------

def preflight(args) -> None:
    PREEXISTING.update(changed_paths())  # untracked only, given the check below
    if sh("git", "status", "--porcelain", "--untracked-files=no").stdout.strip():
        raise SystemExit("working tree not clean: commit or stash first (each phase must start from a commit)")
    branch = sh("git", "branch", "--show-current").stdout.strip()
    if branch in ("main", "master"):
        log(f"on {branch}: switching to {BRANCH}")
        r = sh("git", "switch", BRANCH)
        if r.returncode != 0:
            sh("git", "switch", "-c", BRANCH, check=True)
    # Oracle tests of a task frozen but not yet implemented (state "tests") are red by design: resuming
    # at that task's phase B must not be blocked by them.
    state = jload(STATE, {"done": {}})
    pending_oracles = [f for t in jload(TASKS, {})["tasks"] if state["done"].get(t["id"]) == "tests" for f in t["tests"]]
    if not FULL_SUITE:  # the branch tip was green at its last commit/merge
        return
    base = pytest_run([f"--ignore={f}" for f in pending_oracles], "preflight")
    if base["rc"] != 0:
        raise SystemExit("suite is red before starting:\n" + base["tail"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--max-turns", type=int, default=80)
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--model", help="Bedrock model id (strands) or Claude model alias (claude)")
    ap.add_argument("--backend", choices=("strands", "claude"), default="strands")
    ap.add_argument("--stop-after-tests", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify")
    ap.add_argument("--full", action="store_true", help="full suite in preflight and phase B (slow)")
    ap.add_argument("--refreeze", nargs="*", metavar="TEST",
                    help="re-hash frozen tests after an approved edit (no args: every changed frozen file)")
    args = ap.parse_args()
    global FULL_SUITE
    FULL_SUITE = args.full

    if args.refreeze is not None:
        frozen = jload(FROZEN, {"files": {}})
        targets = args.refreeze or [f for f, h in sorted(frozen["files"].items()) if sha(ROOT / f) != h]
        for f in targets:
            frozen["files"][f] = sha(ROOT / f)
            log(f"refrozen {f}")
        jsave(FROZEN, frozen)
        return

    tasks = jload(TASKS, {})["tasks"]
    state = jload(STATE, {"done": {}})

    if args.verify:
        t = next(t for t in tasks if t["id"] == args.verify)
        errs = verify_impl_phase(t)
        print("OK" if not errs else "\n\n".join(errs))
        sys.exit(1 if errs else 0)

    pending = [t for t in tasks if state["done"].get(t["id"]) != "impl" and (not args.only or t["id"] == args.only)]
    if args.dry_run:
        for t in pending:
            ph = "B" if state["done"].get(t["id"]) == "tests" else "A+B"
            print(f"== {t['id']} [{ph}] {t['title']}\n{render('write_tests', t)}\n{render('implement', t)}")
        return

    preflight(args)
    for t in pending:
        log(f"== {t['id']}: {t['title']}")
        if state["done"].get(t["id"]) != "tests":
            if not phase(t, "write_tests", verify_tests_phase, args):
                raise SystemExit(f"{t['id']} phase A failed. Working tree left as-is for review.")
            frozen = jload(FROZEN, {"files": {}})
            frozen["files"].update({f: sha(ROOT / f) for f in t["tests"]})
            jsave(FROZEN, frozen)
            state["done"][t["id"]] = "tests"
            jsave(STATE, state)
            commit(f"test({t['id']}): oracle tests for {t['title']}")
            if args.stop_after_tests:
                log("stopped after tests: review them, then re-run to implement")
                return
        if not phase(t, "implement", verify_impl_phase, args):
            raise SystemExit(f"{t['id']} phase B failed. Working tree left as-is; frozen tests unchanged.")
        state["done"][t["id"]] = "impl"
        jsave(STATE, state)
        commit(f"feat({t['id']}): {t['title']}")
        log(f"   {t['id']} done")
    log("queue empty")


if __name__ == "__main__":
    main()
