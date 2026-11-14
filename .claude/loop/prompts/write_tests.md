You are in PHASE A (oracle) of task {id}: {title}.

Read CLAUDE.md, then these spec sections: {spec}. SPEC.md is the source of truth; docs/spec-decisions.md and docs/spec-factors.md win where they are more specific.

Write ONLY these test files: {tests}
Task notes: {notes}

Rules:
- Encode the spec's exact expectations (numbers, statuses, enums, invariants) as pytest assertions. Use the tolerances the spec gives; otherwise `pytest.approx(abs=1e-9)` for engine values and exact equality for rule outputs.
- Remove the module-level `pytestmark = pytest.mark.skip(...)` from the files you own. Do not add skips{skip_rule}.
- Tests must be deterministic and offline: no network, no clock reads without a fixed value, COHERENCE_ANALYZER_LLM=mock, fixed seeds.
- Do NOT write or change any code outside tests/. The implementation does not exist yet, so the new tests are expected to FAIL now. That is correct.
- Where the spec is ambiguous, pick the stricter reading and write a one-line `# SPEC-AMBIGUITY:` comment above the assertion explaining the choice. Never invent expected numbers the spec does not give; assert properties instead.
- Do not run git commands that change state (no commit, stash, reset, checkout).

When done, reply with one line per test function: `name: what it pins down (SPEC §x)`.
