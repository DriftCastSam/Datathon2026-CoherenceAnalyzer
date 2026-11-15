#!/usr/bin/env python3
"""Run only the tests a change can break, for fast iteration. The full suite stays the gate for commits and merges.

Selection:
  1. Changed files = `git diff --name-only <base>` + untracked files (default base: HEAD, i.e. the working tree).
  2. A test file is selected when it depends on a changed file: it imports the changed module directly or through any
     chain of imports (static import graph of coherence_analyzer/, deploy/, scripts/, tests/), or it names the changed
     file's path in its source (tests load deploy/build_site.py and scripts by path, read README.md, etc.).
  3. Data that every flow reads (data/, fixtures/, schemas, prompts, conftest.py): every light test.
  4. GUARDS always run: the invariant tests (limits never read llm: fields, the gate, numcheck, engine golden).
  5. HEAVY tests (the tests/dashboard modules that build the whole site, ~55 s each, about 80% of the suite's time)
     run only when something under deploy/ changed, or with --heavy.

Usage:
  python scripts/quick_tests.py                  # working-tree changes vs HEAD
  python scripts/quick_tests.py --base loop/p0   # everything this branch changed
  python scripts/quick_tests.py --list           # print the selection, run nothing
  python scripts/quick_tests.py --heavy          # include the site-build tests
  python scripts/quick_tests.py --light          # every test except the heavy ones (about 1 min)
  python scripts/quick_tests.py -- -x -k flow_b  # extra pytest args after --
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
CODE_DIRS = ("coherence_analyzer", "deploy", "scripts", "tests")
GUARDS = (
    "tests/profile/test_limits_no_llm_inputs.py",   # CLAUDE.md invariant: limits never read an llm: field
    "tests/llm/test_gate.py",                       # every LLM output goes through the gate
    "tests/llm/test_numcheck.py",                   # every number in an explanation is in the facts
    "tests/engine/test_engine_golden.py",           # engine math
)
# Changes here can move any flow's output: run every light test.
GLOBAL = (re.compile(r"^(data|fixtures)/"), re.compile(r"^coherence_analyzer/(schemas|llm/prompts|llm/schemas)/"),
          re.compile(r"^tests/conftest\.py$"), re.compile(r"^(pytest\.ini|pyproject\.toml)$"))
HEAVY_MARKERS = re.compile(r"build_site|whatif\.py")


def git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line.strip()]


def changed_files(base: str) -> list[str]:
    files = set(git("diff", "--name-only", base)) | set(git("ls-files", "--others", "--exclude-standard"))
    return sorted(files)


def module_of(rel: str) -> str | None:
    if not rel.endswith(".py"):
        return None
    parts = rel[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def py_files() -> dict[str, Path]:
    """{module name: path} for every .py under the code dirs."""
    out = {}
    for d in CODE_DIRS:
        for p in (ROOT / d).rglob("*.py"):
            if "__pycache__" in p.parts or ".claude" in p.parts:
                continue
            mod = module_of(p.relative_to(ROOT).as_posix())
            if mod:
                out[mod] = p
    return out


def imports_of(path: Path, mod: str, known: set[str]) -> set[str]:
    """Known modules that `mod` imports (absolute and relative), including `from pkg import submodule`."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return set()
    pkg = mod if path.name == "__init__.py" else mod.rpartition(".")[0]
    found: set[str] = set()

    def add(name: str) -> None:
        while name:                                   # a.b.c counts as a dependency on a.b.c, a.b and a
            if name in known:
                found.add(name)
            name = name.rpartition(".")[0]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base_parts = pkg.split(".")
                base = ".".join(base_parts[: len(base_parts) - node.level + 1])
                target = f"{base}.{node.module}" if node.module else base
            else:
                target = node.module or ""
            add(target)
            for a in node.names:                      # `from coherence_analyzer import gate` imports a submodule
                add(f"{target}.{a.name}")
    return found


def reverse_closure(changed_mods: set[str], graph: dict[str, set[str]]) -> set[str]:
    """Every module that imports a changed module, directly or transitively (the changed ones included)."""
    users: dict[str, set[str]] = {}
    for m, deps in graph.items():
        for d in deps:
            users.setdefault(d, set()).add(m)
    out, todo = set(changed_mods), list(changed_mods)
    while todo:
        for u in users.get(todo.pop(), ()):
            if u not in out:
                out.add(u)
                todo.append(u)
    return out


def test_files() -> list[str]:
    return sorted(p.relative_to(ROOT).as_posix() for p in TESTS.rglob("test_*.py") if "__pycache__" not in p.parts)


def heavy_tests(tests: list[str]) -> set[str]:
    return {t for t in tests if t.startswith("tests/dashboard/") and HEAVY_MARKERS.search((ROOT / t).read_text("utf-8"))}


def select(changed: list[str], heavy: bool, light_all: bool) -> tuple[list[str], list[str], str]:
    tests = test_files()
    heavies = heavy_tests(tests)
    # --light means "every test except the site builds", whatever changed; otherwise a deploy/ change brings them back.
    heavy = heavy or (not light_all and any(f.startswith("deploy/") for f in changed))
    if light_all or any(p.search(f) for f in changed for p in GLOBAL):
        why = "--light" if light_all else "shared data / config changed"
        picked = set(tests)
    else:
        files = py_files()
        known = set(files)
        graph = {m: imports_of(p, m, known) for m, p in files.items()}
        changed_mods = {m for f in changed if (m := module_of(f)) in known}
        affected = reverse_closure(changed_mods, graph)
        picked = {t for t in tests if module_of(t) in affected}
        # path references: tests that name a changed file (build_site.py by path, README.md, docs/index.html, ...)
        names = [f for f in changed if not f.startswith("tests/")]
        if names:
            pats = [re.escape(f) for f in names] + [re.escape(Path(f).name) for f in names if Path(f).suffix != ".py"]
            rx = re.compile("|".join(pats))
            picked |= {t for t in tests if rx.search((ROOT / t).read_text("utf-8"))}
        picked |= {f for f in changed if f in tests}          # a changed test file runs itself
        why = "import graph + path references"
    picked |= {g for g in GUARDS if (ROOT / g).exists()}
    skipped = sorted(picked & heavies) if not heavy else []
    return sorted(picked - set(skipped)), skipped, why


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="HEAD", help="git ref to diff against (default HEAD: working-tree changes)")
    ap.add_argument("--list", action="store_true", help="print the selection and exit")
    ap.add_argument("--heavy", action="store_true", help="include the site-build tests")
    ap.add_argument("--light", action="store_true", help="every test except the heavy ones")
    ap.add_argument("pytest_args", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    changed = changed_files(a.base)
    picked, skipped, why = select(changed, a.heavy, a.light)
    print(f"[quick_tests] {len(changed)} changed file(s) vs {a.base}; {len(picked)} test file(s) selected ({why})")
    for t in picked:
        print("  run ", t)
    for t in skipped:
        print("  skip", t, "(heavy: site build; --heavy or a deploy/ change runs it)")
    if a.list or not picked:
        return 0
    extra = [x for x in a.pytest_args if x != "--"]
    env = {**os.environ, "COHERENCE_ANALYZER_LLM": os.environ.get("COHERENCE_ANALYZER_LLM", "mock")}
    return subprocess.call([sys.executable, "-m", "pytest", "-q", *picked, *extra], cwd=ROOT, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
