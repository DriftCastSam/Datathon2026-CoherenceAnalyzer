You are in PHASE B (implement) of task {id}: {title}.

Read CLAUDE.md, then SPEC.md sections {spec}. SPEC.md is the source of truth.

Goal: make these FROZEN tests pass without skips{skip_rule}: {tests}
Files you will most likely touch: {code}
Task notes: {notes}

Rules:
- The frozen test files are the oracle. You cannot edit them (a hook blocks it and the verifier checks their hashes). If you believe a frozen test contradicts SPEC.md, STOP: write the contradiction to `.claude/loop/BLOCKED.md` (test name, spec section, why) and end your turn without working around it.
- Keep every CLAUDE.md invariant. Pure functions in rules/ and coherence/. Fractions inside code, percent only at the edges.
- Do not edit: SPEC.md, data/starter/**, data/meta/activity_tags.csv, scripts/engine_v0.py. Do not change engine math.
- Determinism: no unseeded randomness, no wall-clock in computed values (timestamps only in audit metadata), sorted dict/set iteration wherever it reaches output.
- Run `PORTEE_LLM=mock python -m pytest -q` before you finish. The full suite must be green (skips for later build steps are fine).
- Record any new assumption in README.md → "Assumptions". Update README "Status" with this step.
- Do not run git commands that change state (no commit, stash, reset, checkout). The loop commits for you.
{retry}
When done, reply with: files changed, one line each, and any SPEC-AMBIGUITY you resolved.
