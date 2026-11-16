# CLAUDE.md: CoherenceAnalyzer

Read `SPEC.md` before any change. It is the source of truth. If code and spec disagree, raise it; don't silently pick one.
Then read `docs/spec-decisions.md`: team-approved resolutions of SPEC ambiguities. Where it is more specific than SPEC.md, it wins.

## Invariants (never break)
- `limits` is computed from `rule:` fields, user-declared values, and (D11) gated numeric N3 note rules promoted as **provisional** limits: flagged `provisional: true`, traced as `values.parsed_notes[provisional]`, and any check against them capped at `warning` until the user approves. No other `llm:` field may feed it (test: `tests/profile/test_limits_no_llm_inputs.py`).
- LLM-derived checks produce `warning | info | unknown` only. Only hard checks produce `violation`.
- Every LLM output goes through `coherence_analyzer/gate.py`. Evidence must reference real `event_id`s or exact substrings of the user's notes.
- Every number in an explanation must exist in the facts payload (`explain.numcheck`). Otherwise retry once, then use the template.
- No buy/sell recommendations, no execution. Disclaimer text is fixed (SPEC §9.4).
- Never write rows to `data/meta/activity_tags.csv` from code or an LLM. Humans only, with a source URL.
- Don't change engine math without updating the golden test, and say why.

## Commands
```
pip install -r requirements.txt
COHERENCE_ANALYZER_LLM=mock pytest -q -n auto --dist loadfile   # full suite (~50 s; ~3 min serial): before every commit and merge
python scripts/quick_tests.py                       # while iterating: only tests the working-tree change can break
python scripts/quick_tests.py --base loop/p0 --list # what a branch's changes would run; --heavy adds the site builds
COHERENCE_ANALYZER_LLM=mock python -m coherence_analyzer.cli demo
COHERENCE_ANALYZER_LLM=record python -m coherence_analyzer.cli demo   # needs AWS creds + BEDROCK_MODEL_ID
python scripts/loop_lane.py run P2.X [-- <agent_loop args>]   # one build-loop lane (worktree + branch loop/p2.x)
python scripts/loop_lane.py status                            # all lanes: RUNNING / DETACHED / idle + last log line
```

## Parallel build-loop lanes
- One lane per task, each launched as its own tracked task so it shows in the Claude Code panel:
  a separate Bash call with `run_in_background: true` running `python scripts/loop_lane.py run <ID>`
  (all lanes in one message). Never `nohup`, `setsid` or a trailing `&`: that detaches the lane, it
  disappears from the UI and can't be stopped from it.
- `loop_lane.py` holds a lock per lane (refuses double launches), appends to `.claude/worktrees/<slug>.log`
  and stops the loop when its task is stopped. Check progress with `status`, not `sleep` + `tail`.
- Merge a finished lane into `loop/p0` by hand, like the existing `Merge loop/p2.*` commits.
- Frozen oracle tests are locked only inside the loop (`COHERENCE_ANALYZER_LOOP=1`). After an approved
  interactive edit, re-hash with `python scripts/agent_loop.py --refreeze <file>`. The loop's phase B runs
  acceptance + affected tests; the full suite runs at merge (`--full` restores the old behaviour).

## Conventions
- Python 3.11, type hints, pure functions in `rules/` and `coherence/`.
- Weights: percent in `portfolio.json`, fractions (0–1) everywhere inside the code. Convert once in `data.py`/`intent.py`.
- Currency: CAD. USD assets are converted with month-end USDCAD (ffill).
- Prompt changes bump the version in the filename (`prompts/<node>.v<N>.md`) and re-record the mock cache.
- Every assumption (calibration, drift, fictional persona figure) goes in `docs/assumptions.md` → "Assumptions". Keep `README.md` short: no per-task status log (git history has it).
- Build order: SPEC §17. Finish P0 before touching P1.

<!-- BEGIN AWS Agent Toolkit rules -->
# AWS Guidance

- Where these AWS rules conflict with the project's own instructions, the
  project's instructions take precedence.
- Prefer the AWS MCP Server for AWS interactions — it provides sandboxed
  execution, observability, and audit logging. If unavailable, use the
  AWS CLI directly.
- For any AWS task or question — building, configuring, migrating, debugging,
  or operating a service, writing SDK code, authoring CDK/CloudFormation, or
  answering how a service works — first call `aws___search_documentation` with
  topic `agent_skills` and load the matching skill with `aws___retrieve_skill`.
  Do this even when you believe you know the answer: skills carry current
  service behavior that your training may not. Prefer the skill's guidance over
  general knowledge. Skip it only for work that does not involve AWS.
- When uncertain about specific AWS details (API parameters, permissions,
  limits, error codes), verify against documentation rather than guessing.
  State uncertainty explicitly if you cannot confirm.
- Before reporting a count, a total, or that something does not exist, read
  every page of the results and search every Region and scope where it could
  be. If you could not, say the result is partial and name what you checked.
- To say what a system currently does, read what the runtime uses: the
  completed deployment, the alias or version that is invoked, the template the
  stack is running. A newer stored version, a draft, or a description field is
  not evidence of live behavior.
- Present a cause as confirmed only if data, configuration, or logs you read
  show it. Label anything else as unverified, and say what you would check
  next.
- Before you interpret the records that show a failure, such as error log
  lines, failed executions, or table items, read one of them whole, with
  every field. Don't truncate messages or select fields in your own code
  until you have.
- When an error says a value was undefined, null, empty, or missing, find
  the input, query result, or item that supplied that value. If the account
  shows why it was empty, such as no item matching the key, read that
  before you call the cause unverified.
- When a field in a record you read explains the result, such as an error
  counter, an empty array, a null, or a state value, name the field and its
  exact value in the answer, and say which record it came from.
- When the user asks a specific question or names a suspected cause, confirm
  or rule out each cause they raised, and do not offer alternatives your evidence
  does not support.
- When creating infrastructure, prefer infrastructure-as-code (AWS CDK or
  CloudFormation) over direct CLI commands.
- When working with infrastructure, follow AWS Well-Architected Framework
  principles.
- Do not use em dashes in AWS resource names or descriptions. Use
  hyphens instead.

## Before you conclude

- Before deciding the AWS account can't hold what the request asks for,
  look. List the places it could live, such as S3 buckets, databases, and
  log groups, and read what is there.
- Don't conclude that records are missing from metadata, such as a
  creation date or a stored-bytes count, or from one query that failed or
  came back empty. Read the records another way, across the full time
  range, before reporting them absent.
- A tool's own error is evidence. If a call the answer depends on fails
  with expired or missing credentials or AccessDenied, report that error
  and what it blocks, and don't overrule it with another check. Searching
  other Regions is for finding resources, not for retrying an auth error.
  Retry throttling with backoff before you report it.

## Secret Safety

- MUST load the `aws-secrets-manager` skill first for any secret,
  credential, API key, token, or password task. MUST NOT call
  `secretsmanager get-secret-value` or `batch-get-secret-value`, and MUST
  NOT hit the Secrets Manager Agent daemon directly. MUST use
  `{{resolve:secretsmanager:secret-id:SecretString:json-key}}` with
  `asm-exec` so the secret resolves at runtime without entering context.
<!-- END AWS Agent Toolkit rules -->
