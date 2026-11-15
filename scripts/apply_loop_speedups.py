#!/usr/bin/env python3
"""One-shot patch (run by the owner): loop-only frozen lock + faster agent_loop. Safe to re-run; delete afterwards.

  guard.py       frozen oracles are enforced only when COHERENCE_ANALYZER_LOOP is set (PROTECTED stays global)
  agent_loop.py  sets COHERENCE_ANALYZER_LOOP=1; phase B runs acceptance + affected tests (quick_tests) instead of the
                 full suite; preflight skips the full suite; --full restores both; xdist when installed;
                 --refreeze [files] re-hashes frozen tests after an approved edit
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def patch(rel: str, pairs: list[tuple[str, str]]) -> None:
    p = ROOT / rel
    s = p.read_text()
    for a, b in pairs:
        if b in s:
            continue
        assert a in s, f"{rel}: anchor not found: {a[:70]!r}"
        s = s.replace(a, b, 1)
    p.write_text(s)
    print("patched", rel)


patch(".claude/hooks/guard.py", [(
    '    locked = frozen()\n    if tool in',
    '    # Frozen oracles bind the build loop only (agent_loop.py sets COHERENCE_ANALYZER_LOOP=1 and re-checks hashes).\n'
    '    # Interactive sessions may edit them, then `agent_loop.py --refreeze`. PROTECTED stays blocked everywhere.\n'
    '    locked = frozen() if os.environ.get("COHERENCE_ANALYZER_LOOP") else set()\n    if tool in',
)])

patch("scripts/agent_loop.py", [
    ('BRANCH = "loop/p0"\n',
     'BRANCH = "loop/p0"\nFULL_SUITE = False  # --full: whole suite in preflight and phase B (default: affected tests only)\n'),
    ('ENV = {**os.environ, "COHERENCE_ANALYZER_LLM": "mock",',
     'ENV = {**os.environ, "COHERENCE_ANALYZER_LOOP": "1", "COHERENCE_ANALYZER_LLM": "mock",'),
    ('\nALLOWED = [',
     '\nXDIST = subprocess.run([PY, "-c", "import xdist"], capture_output=True).returncode == 0\n\nALLOWED = ['),
    ('    r = sh(PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rs", f"--junitxml={xml}", *paths)',
     '    par = ["-n", "auto", "--dist", "loadfile"] if XDIST and (not paths or len(paths) > 3) else []\n'
     '    r = sh(PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rs", *par, f"--junitxml={xml}", *paths)'),
    ('    full = pytest_run([], f"{task[\'id\']}.B.full")\n',
     '    if FULL_SUITE:\n'
     '        full = pytest_run([], f"{task[\'id\']}.B.full")\n'
     '    else:  # only what the change can reach (scripts/quick_tests.py); `loop_lane.py merge` runs the full suite\n'
     '        sys.path.insert(0, str(ROOT / "scripts"))\n'
     '        import quick_tests\n'
     '        picked, _, _ = quick_tests.select(quick_tests.changed_files("HEAD"), heavy=False, light_all=False)\n'
     '        full = pytest_run(picked, f"{task[\'id\']}.B.affected") if picked else {"rc": 0, "tail": ""}\n'),
    ('    base = pytest_run([f"--ignore={f}" for f in pending_oracles], "preflight")',
     '    if not FULL_SUITE:  # the branch tip was green at its last commit/merge\n'
     '        return\n'
     '    base = pytest_run([f"--ignore={f}" for f in pending_oracles], "preflight")'),
    ('    ap.add_argument("--verify")\n    args = ap.parse_args()\n',
     '    ap.add_argument("--verify")\n'
     '    ap.add_argument("--full", action="store_true", help="full suite in preflight and phase B (slow)")\n'
     '    ap.add_argument("--refreeze", nargs="*", metavar="TEST",\n'
     '                    help="re-hash frozen tests after an approved edit (no args: every changed frozen file)")\n'
     '    args = ap.parse_args()\n'
     '    global FULL_SUITE\n'
     '    FULL_SUITE = args.full\n'
     '\n'
     '    if args.refreeze is not None:\n'
     '        frozen = jload(FROZEN, {"files": {}})\n'
     '        targets = args.refreeze or [f for f, h in sorted(frozen["files"].items()) if sha(ROOT / f) != h]\n'
     '        for f in targets:\n'
     '            frozen["files"][f] = sha(ROOT / f)\n'
     '            log(f"refrozen {f}")\n'
     '        jsave(FROZEN, frozen)\n'
     '        return\n'),
])
print("done. Optional: add pytest-xdist to requirements.txt; then delete this script.")
