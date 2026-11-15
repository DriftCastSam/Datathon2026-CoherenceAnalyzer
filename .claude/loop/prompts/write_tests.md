You are in PHASE A (oracle) of task {id}: {title}.

Read CLAUDE.md, then these spec sections: {spec}. SPEC.md is the source of truth; docs/spec-decisions.md and docs/spec-factors.md win where they are more specific.

Write ONLY these test files: {tests}
Task notes: {notes}

Rules:
- Encode the spec's exact expectations (numbers, statuses, enums, invariants) as pytest assertions. Use the tolerances the spec gives; otherwise `pytest.approx(abs=1e-9)` for engine values and exact equality for rule outputs.
- Remove the module-level `pytestmark = pytest.mark.skip(...)` from the files you own. Do not add skips{skip_rule}.
- Tests must be deterministic and offline: no network, no clock reads without a fixed value, COHERENCE_ANALYZER_LLM=mock, fixed seeds.
- Tests are grouped in subfolders of tests/ (engine, profile, checks, trade, llm, flows, aws, dashboard, tooling); `tests/conftest.py` is shared. A file in `tests/<group>/` finds the repo root with `Path(__file__).resolve().parents[2]` (or the `root` fixture).
- Do NOT write or change any code outside tests/. The implementation does not exist yet, so the new tests are expected to FAIL now. That is correct.
- Import only the stub names that already exist (read the stub modules first). Tests must be collectable: they fail on NotImplementedError or wrong values, never on ImportError. If a name you need has no stub, import the module and call `getattr(module, "name")` inside the test, or write `.claude/loop/BLOCKED.md` naming the missing stub.
- Keep each write or edit call under about 250 lines; build long test files in several edits.
- Where the spec is ambiguous, pick the stricter reading and write a one-line `# SPEC-AMBIGUITY:` comment above the assertion explaining the choice. Never invent expected numbers the spec does not give; assert properties instead.
- Your tests must be satisfiable together with every test already frozen in `.claude/loop/frozen.json`: the implementation phase may not edit those. Before finishing, grep `tests/` for the fields, keys, paths, check ids and fixture values you assert on, and read the existing asserts on them. If one of yours would contradict an existing one (a value it pins, a key it requires, a list it fixes, a report key name, a status a persona must keep), change yours to agree with it. If the existing test is stale against the spec, keep yours and name the stale test + the spec line in a `# STALE-ORACLE:` comment at the top of your file. Prefer the exact report / schema key names that existing tests and `schemas/` already use.
- Assert on module-scoped fixtures only with snapshots taken at the moment you mean (e.g. "after the draft, before the confirm"), never on live shared state that a later step of the same fixture changes.
- Do not run git commands that change state (no commit, stash, reset, checkout).

When done, reply with one line per test function: `name: what it pins down (SPEC §x)`.
