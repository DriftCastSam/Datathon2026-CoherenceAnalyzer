#!/usr/bin/env python3
"""Run build-loop tasks in parallel lanes (one git worktree per task) that Claude Code can track.

Why this exists: lanes launched with `setsid nohup ... &` detach from Claude Code, so the VS Code
panel never shows them and nobody can stop them from the UI. This script stays in the FOREGROUND for
the lifetime of its lane, so launch each lane as its own tracked task:

  - Bash tool with run_in_background: true   -> one background task per lane in the panel
  - or one Agent per lane that runs it        -> one agent card per lane

Never wrap it in nohup / setsid / `&`.

Usage:
  python scripts/loop_lane.py run P2.APIFB [--base loop/p0] [-- <agent_loop.py args>]
  python scripts/loop_lane.py status
  python scripts/loop_lane.py merge P2.APIFB      # into the current branch; rolls back if the suite goes red

Lane layout (matches the existing convention):
  worktree  .claude/worktrees/<id lower, '.' -> '-'>     e.g. .claude/worktrees/p2-apifb
  branch    loop/<id lower>                               e.g. loop/p2.apifb
  log       .claude/worktrees/<slug>.log  (appended; also echoed to stdout for the task view)
  lock      .claude/worktrees/<slug>.lock (flock held while the lane runs: no double launches)
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WT = ROOT / ".claude" / "worktrees"


def slug(task_id: str) -> str:
    return task_id.lower().replace(".", "-")


def git(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True)


def ensure_worktree(task_id: str, base: str) -> Path:
    path, branch = WT / slug(task_id), f"loop/{task_id.lower()}"
    if not path.exists():
        exists = git("rev-parse", "--verify", "--quiet", branch).returncode == 0
        r = git("worktree", "add", str(path), branch) if exists else git("worktree", "add", "-b", branch, str(path), base)
        if r.returncode != 0:
            raise SystemExit(r.stderr)
    for name in (".venv", ".env"):  # share the main checkout's venv and credentials
        link = path / name
        if not link.exists() and not link.is_symlink() and (ROOT / name).exists():
            link.symlink_to(ROOT / name)
    return path


def try_lock(task_slug: str):
    """Return an open, flock-held file, or None if another process holds the lane."""
    WT.mkdir(parents=True, exist_ok=True)
    fh = open(WT / f"{task_slug}.lock", "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    return fh


def run(task_id: str, base: str, extra: list[str]) -> int:
    s = slug(task_id)
    lock = try_lock(s)
    if lock is None:
        print(f"lane {task_id} is already running (lock .claude/worktrees/{s}.lock). Use `status`.", file=sys.stderr)
        return 3
    path = ensure_worktree(task_id, base)
    lock.seek(0), lock.truncate(), lock.write(f"{os.getpid()}\n"), lock.flush()
    py = path / ".venv" / "bin" / "python"
    cmd = [str(py if py.exists() else sys.executable), "scripts/agent_loop.py", "--only", task_id, *extra]
    with open(WT / f"{s}.log", "a", buffering=1) as log:
        log.write(f"[lane {time.strftime('%H:%M:%S')}] start pid={os.getpid()} cwd={path} cmd={' '.join(cmd[1:])}\n")
        child = subprocess.Popen(cmd, cwd=path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 stdin=subprocess.DEVNULL)

        def stop(signum, _frame):  # stopping the task in the UI stops the loop too
            child.terminate()
            try:
                child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                child.kill()
            log.write(f"[lane {time.strftime('%H:%M:%S')}] stopped by signal {signum}\n")
            sys.exit(128 + signum)

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGHUP, stop)
        assert child.stdout is not None
        for line in child.stdout:
            sys.stdout.write(line), sys.stdout.flush()
            log.write(line)
        rc = child.wait()
        log.write(f"[lane {time.strftime('%H:%M:%S')}] exit rc={rc}\n")
    return rc


def status() -> int:
    if not WT.exists():
        print("no lanes")
        return 0
    rows = []
    for d in sorted(p for p in WT.iterdir() if p.is_dir() and (p / "scripts" / "agent_loop.py").exists()):
        lock = try_lock(d.name)
        running = lock is None
        if lock:
            lock.close()
        legacy = "" if running else _legacy_pid(d)
        logf = WT / f"{d.name}.log"
        last = logf.read_text().strip().splitlines()[-1][:110] if logf.exists() and logf.stat().st_size else "-"
        branch = git("branch", "--show-current", cwd=d).stdout.strip()
        rows.append((d.name, branch, "RUNNING" if running else (f"DETACHED pid {legacy}" if legacy else "idle"), last))
    w = [max(len(r[i]) for r in rows) for i in range(3)] if rows else [0, 0, 0]
    for r in rows:
        print(f"{r[0]:<{w[0]}}  {r[1]:<{w[1]}}  {r[2]:<{w[2]}}  {r[3]}")
    return 0


def merge(task_id: str) -> int:
    """Merge lane loop/<id> into the current branch (the trunk). Known loop-file conflicts are resolved here:
    .claude/loop/state.json = union of both sides (impl wins), .claude/loop/runs/* = trunk's copy. Any other conflict,
    or a red full suite after the merge, aborts / rolls back the merge and returns non-zero."""
    branch = f"loop/{task_id.lower()}"
    if git("status", "--porcelain", "--untracked-files=no").stdout.strip():
        print("tracked files have uncommitted changes: commit them first (the rollback would discard them)", file=sys.stderr)
        return 4
    head = git("rev-parse", "HEAD").stdout.strip()
    r = git("merge", "--no-ff", branch, "-m", f"Merge {branch}")
    if r.returncode != 0:
        unmerged = git("diff", "--name-only", "--diff-filter=U").stdout.split()
        auto = {".claude/loop/state.json", ".claude/loop/frozen.json", "README.md"}
        other = [f for f in unmerged if f not in auto and not f.startswith(".claude/loop/runs/")]
        if other:
            git("merge", "--abort")
            print(f"merge of {branch} has real conflicts, aborted: {other}", file=sys.stderr)
            return 4
        for f in unmerged:
            if f.startswith(".claude/loop/runs/"):
                git("checkout", "--ours", f)
        if ".claude/loop/state.json" in unmerged:
            side = lambda n: json.loads(git("show", f":{n}:.claude/loop/state.json").stdout)
            ours, theirs = side(2), side(3)
            done = {**ours["done"], **theirs["done"]}
            for k in done:
                if "impl" in (ours["done"].get(k), theirs["done"].get(k)):
                    done[k] = "impl"
            (ROOT / ".claude/loop/state.json").write_text(json.dumps({**ours, "done": done}, indent=2, sort_keys=True) + "\n")
        if ".claude/loop/frozen.json" in unmerged:   # 3-way per file: the lane's hash wins where the lane changed it
            side = lambda n: json.loads(git("show", f":{n}:.claude/loop/frozen.json").stdout)["files"]
            base, ours, theirs = side(1), side(2), side(3)
            files = {**ours, **{f: h for f, h in theirs.items() if base.get(f) != h}}
            (ROOT / ".claude/loop/frozen.json").write_text(json.dumps({"files": files}, indent=2, sort_keys=True) + "\n")
        if "README.md" in unmerged:   # lanes append bullets: keep both sides of each hunk, show them for review
            text = (ROOT / "README.md").read_text(encoding="utf-8")
            hunks = re.findall(r"<<<<<<< [^\n]*\n(.*?)=======\n(.*?)>>>>>>> [^\n]*\n", text, flags=re.S)
            text = re.sub(r"<<<<<<< [^\n]*\n(.*?)=======\n(.*?)>>>>>>> [^\n]*\n", lambda m: m.group(1) + m.group(2),
                          text, flags=re.S)
            if "<<<<<<<" in text or ">>>>>>>" in text:
                git("merge", "--abort")
                print("README.md conflict markers left after union, aborted", file=sys.stderr)
                return 4
            (ROOT / "README.md").write_text(text, encoding="utf-8")
            for o, t in hunks:
                print(f"README union hunk:\n  ours:   {o.strip()[:160]}\n  theirs: {t.strip()[:160]}")
        git("add", *unmerged)
        c = git("commit", "--no-edit")
        if c.returncode != 0:
            print(c.stdout + c.stderr, file=sys.stderr)
            return 4
    env = {**os.environ, "COHERENCE_ANALYZER_LLM": "mock", "PYTHONHASHSEED": "0", "TZ": "UTC"}
    py = ROOT / ".venv" / "bin" / "python"
    t = subprocess.run([str(py if py.exists() else sys.executable), "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        "-n", "auto", "--dist", "loadfile"],
                       cwd=ROOT, env=env, text=True, capture_output=True)
    tail = (t.stdout + t.stderr).strip().splitlines()[-1:] or [""]
    if t.returncode != 0:
        git("reset", "--hard", head)
        print(f"full suite red after merging {branch}; merge rolled back to {head[:8]}\n" + (t.stdout + t.stderr)[-3000:],
              file=sys.stderr)
        return 5
    print(f"merged {branch}: {tail[0]}")
    return 0


def _legacy_pid(d: Path) -> str:
    """Loops started the old way (setsid nohup) hold no lock: find them by cwd."""
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            argv = (p / "cmdline").read_text().split("\0")
            if any(a.endswith("agent_loop.py") for a in argv[1:2]) and os.readlink(p / "cwd") == str(d):
                return p.name
        except OSError:
            continue
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("task")
    r.add_argument("--base", default="loop/p0", help="branch a new lane starts from")
    r.add_argument("extra", nargs=argparse.REMAINDER, help="after --: passed to agent_loop.py")
    sub.add_parser("status")
    m = sub.add_parser("merge", help="merge a finished lane into the current branch, then run the full suite")
    m.add_argument("task")
    a = ap.parse_args()
    if a.cmd == "status":
        return status()
    if a.cmd == "merge":
        return merge(a.task)
    return run(a.task, a.base, [x for x in a.extra if x != "--"])


if __name__ == "__main__":
    sys.exit(main())
