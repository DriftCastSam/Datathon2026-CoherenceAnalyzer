# CoherenceAnalyzer

Checks whether a self-directed investor's portfolio, or a trade they want to make, is coherent with their own profile. Deterministic rules own every limit. Bedrock LLM nodes fill narrow, gated slots. Datathon 2026 / PolyFinances AlphaForge AI.

- **Spec (source of truth):** [SPEC.md](SPEC.md)
- **Working rules:** [CLAUDE.md](CLAUDE.md)

## Setup

```
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # only needed for COHERENCE_ANALYZER_LLM=record (AWS creds + BEDROCK_MODEL_ID)
COHERENCE_ANALYZER_LLM=mock pytest -q
python -m coherence_analyzer.engine       # engine summary for the fixture portfolios
```

## Layout

```
data/starter/                alphaforge starter pack, unmodified (README.md + build_dataset.py from the organisers)
data/meta/                   hand-built, sourced meta files (see data/meta/SOURCES.md)
coherence_analyzer/          package: engine, rules, coherence, llm, gate, intent, resizer, explain, pipeline, store, api, cli
coherence_analyzer/schemas/  profile.v2.json, report.json (JSON Schema)
fixtures/                    personas (robert, amira, mateo) + llm_cache for mock mode
scripts/                     engine_v0.py: original engine script, kept as the golden-test reference
tests/                       pytest, all offline (COHERENCE_ANALYZER_LLM=mock)
```

## Status

The build order follows SPEC §17. Done:
- **P0.1:** `data.py`, the engine refactor, and the golden test.
- **P0.2:** `levels.py`, `rules/registry.py`, `rules/derive.py`, `rules/limits.py` + tests.
- **P0.3:** `coherence_analyzer/schemas/profile.v2.json` (JSON Schema draft 2020-12) and the Robert fixture converted to schema v2 (`fixtures/robert/{profile.json, events.jsonl, intents.json}`).

- **P0.5:** `intent.py` (`apply` for every `funding.kind`) and `resizer.py` (SPEC §8 as written, docs/spec-decisions.md D2) + `tests/test_intent.py`, `tests/test_resizer.py`. Measured on the starter data: Robert + NVDA from ZST.TO tops out at 1.5% (H9 binds; SPEC §8's 5.0% / H4 is only the cheap-scan stop), pro_rata_all 2.0%, pro-rata etf_equity 2.0%, pro-rata equity 1.5%, all binding H9.
- **P0.6:** `llm/bedrock.py` (Converse wrapper, mock / record / live, lazy boto3), N1 `behaviour_classifier` + N4 `intent_parser` (schemas, `*.v1.md` prompts, extractors), `gate.py` (N1 and N3 paths, SPEC §11), hand-written Robert N1 + N4 responses in `fixtures/llm_cache/` (regenerate with `python scripts/handwrite_llm_cache.py`) + `tests/test_gate.py`, `tests/test_bedrock_mock.py`.
- **P0.10:** `intake.py` (`REQUIRED` per factor, pure `missing` / `next_question`, `skip`, `answer`; fixed fr-CA / en-CA questions, no LLM), N3 `values_parser` (schema, `values_parser.v1.md`, extractor, hand-written Robert and Amira cache entries via `scripts/handwrite_llm_cache.py`), approval of parsed rules into `limits` + `tests/test_intake.py`.

Everything else is stubbed with signatures and `NotImplementedError`. The matching tests are skip-marked with their build step.

- **P0.7:** `factors/` (check -> factor mapping `CHECK_FACTOR` / `factor_of` / `tag_checks`, the five factor views, `SectionContext`, `build_sections`), `coherence/report.py` (`verdict_from_statuses`, `sections_verdict`, `build_sections`, `tag_checks`), `explain.numcheck` (engine in `numfacts.py`) and `explain.template_section` + `tests/test_factors.py`, `tests/test_numcheck.py`. Measured sections on Robert + NVDA 10% (D2): horizon `violation` (H9), risque `violation` (H4, H7, H8), contraintes `violation` (H3, H10), objectif and preferences `pass`; Robert baseline: contraintes `unknown` (H6, H10), the rest `pass`, verdict `coherent`. No LLM yet: `verify_section` and the narration loop are P0.8.
- **P0.8:** narration loop (docs/spec-factors.md §20.5): N5 `explainer` (`schemas/explainer.json`, `prompts/explainer.v1.md`, `extractors.explainer_call`), `explain.verify_section` (V1-V5), `explain.narrate_section` (`llm` -> `llm_retry` -> `template`, at most 2 calls per section, none for `pass` sections) and `explain.explain` (headline, disclaimer and pass sections are template text). Hand-written Robert + NVDA entries in `fixtures/llm_cache/` (`python scripts/handwrite_explainer_cache.py`): horizon `llm`, risque `llm_retry`, contraintes `template`. `tests/test_explain.py` uses its own temporary cache.

Remaining TODOs:
- Amira and Mateo have portfolios only; their profiles and events are P1.
- Robert's `llm:` fields are hand-written (`model_id: fixture:hand-written`) until the Bedrock cache is recorded (P1.11).

## Data

- **Prices:** listing currency (`.TO` is CAD, everything else USD). Converted to CAD with month-end USDCAD, forward-filled.
- **Calendars:** no calendar alignment. Monthly resampling (`ME`, last) absorbs TSX/US/crypto calendar differences.
- **Window:** monthly returns 2022-01 → 2026-10 (58 months). `iloc[2:-1]` also drops Dec 2021; this is kept for golden parity (SPEC §3.1). The window includes 2022, when stocks and bonds fell together.
- **Survivorship bias:** the universe was selected in 2026.

## Assumptions

All persona financial figures beyond the case brief are fictional team assumptions.

- **Engine drift (annual, CAD):** Stocks / Equity ETFs 6%, Bonds 3%, Gold 3%, Crypto 6%. A team assumption, not a forecast.
- **Bootstrap:** circular block, block = 12 months, n = 5000, seed = 7.
- **Limits table (SPEC §7.2):** team calibrations, not regulation. *TODO: justify each column.*
- **Knowledge score weights:** 0.4 quiz / 0.3 declared / 0.3 question quality, with `declared_map` calibrated so Robert = 74.
- **Mateo portfolio:** suggested in SPEC §3.5 (fictional).
- **Schema location:** `profile.v2.json` lives in `coherence_analyzer/schemas/` (`config.SCHEMAS_DIR`), which is what SPEC §3.3's `schemas/profile.v2.json` refers to.
- **Robert v2 conversion (P0.3):**
  - The `llm:` field values (behavioural `moderate_high` @ 0.55, question quality 0.62 @ 0.6, parsed notes @ 0.8) are kept from the v1 example. They are hand-written stand-ins, stamped `model_id: fixture:hand-written`, `prompt_version: <node>.v1`, `updated_at: 2026-11-14T10:03:00-05:00`.
  - `input_hash` = `sha256:` of the node input as canonical JSON (sorted keys, `,`/`:` separators, UTF-8): behaviour = the question/order_intent events `[{event_id, ts, type, text}]` (all three, including ev_0007 dated after `portfolio.as_of`); question rubric = `[{event_id, text}]` for the question events; values parser = the `values.notes` string.
  - Question quality 0.62 is not the N2 EWMA of the two scores (that would give 0.48). It is kept because knowledge.score = 74 is calibrated on it; N2 recomputes it in P1.
  - The v1 free-text parsed rule `prefer_understandable_business` (approved) is split into two approved items, one per sentence of the notes: `prefer_attribute {attribute: understandable_business}` and `avoid_attribute {attribute: hype}`, both `applies_to: single_stocks`. Neither kind is promoted into `limits` (§6.2 N3), so limits are unchanged.
  - Event timestamps carry the Montreal local offset (-05:00; DST ended 2026-11-01 at 02:00, before the quiz event); intent `in_0001` is confirmed 3 minutes after ev_0007.
  - `limits_trace` is the output of `rules.limits.build_trace()`, written verbatim (a test checks it matches).

- **intent.apply (P0.5), cases SPEC §3.6 leaves open:**
  - Only `buy` with a target at or above the current weight is applied; anything else raises `ValueError`.
  - `specific`: a funding ticker holding less than the needed weight raises `ValueError` (never negative weights). Tickers that reach 0 are dropped from the result.
  - `pro_rata_class`: the pool is every held ticker of that `asset_class` (from `assets_universe`) *except the target*, which never funds itself. An empty or too-small pool raises `ValueError`.
  - `cash_contribution`: new value = value + amount; nothing is sold, so existing holdings keep their CAD value. The ticker gets the cash up to its target (of the enlarged portfolio). An under-funded purchase leaves the ticker below target. Cash beyond the target is spread pro rata over the other holdings, so weights always sum to 100.
- **Resizer (P0.5):**
  - The portfolio dict passed in carries `horizon_months` (SPEC §3.5 fixture value, 48 for Robert), since §8 gives the resizer no horizon argument.
  - "Binary search downward" runs on the 0.5 pp grid of the cheap scan (`config.RESIZER_STEP_PP`), so results are grid values. After the n=1000 search, the n=5000 confirmation steps down while H7-H10 fail and up while the next grid point still passes. A held target ticker starts the grid at its current weight.
  - Returned options are the modes with a max above the current weight, ranked by max weight (ties by mode order: user's funding, `pro_rata_all`, etf_equity, equity). The user's own funding is always kept in the top 3 when it fits. `binding_check` is the first violated check at the next grid point (`None` when the whole target fits, `FUNDING` when the source cannot cover it). When no mode fits, `binding_checks` lists the sorted union.
  - Golden values are in `tests/test_resizer.py` (D2 replaces the "5.0% H4 / 8.0% H3" expectation).
- **LLM wrapper, N1, N4, gate (P0.6):**
  - Cache files: one per call, `<node>.<cache_key>.json` (the key is `cache_key()`), holding payload, output and call log. `mock` and `record` use `fixtures/llm_cache/`, `live` uses `.cache/llm/`; `mock` never falls back to the live cache. `record` reuses a fixtures hit instead of calling Bedrock again.
  - Mock model id: the key uses `BEDROCK_MODEL_ID`, then falls back to `fixture:hand-written`, the model id stamped on the hand-written entries, so the offline suite passes whatever the local `.env` says. `record` / `live` require `BEDROCK_MODEL_ID`.
  - The hand-written N1 response for Robert is `moderate_high` @ 0.55 citing ev_robert_0007 and ev_robert_0004 as `hype_chasing` (same as the profile fixture). The hand-written N4 draft is NVDA 10% with the default funding `pro_rata_all`, and `funding` listed in `ambiguous_fields`, since the sentence names no source.
  - Retry on a schema failure: the model's tool call is replayed, then a user turn carrying a `toolResult` with `status: error` and the validator message.
  - N1 input is `{"events": [{event_id, ts, type, text}]}`, oldest first, newest 20. With `as_of` (D1) it drops events older than 30 days before it and has no upper bound, so ev_robert_0007 (dated after `portfolio.as_of`) is included. Without `as_of` there is no window filter.
  - N1 schema: `abstain` and `evidence` are always required; `value` and `confidence` (0 to 0.8) are required when `abstain` is false, with at least one evidence item. The gate also treats an empty evidence list on a non-abstaining output as `rejected:evidence`.
  - N4 schema is one flat object (the §3.6 fields minus `intent_id`, `event_id`, `status`, which the LLM does not produce) plus `ambiguous_fields` and `unresolved_mentions`. `ticker` (and `funding.specific.ticker`) get an enum generated from the universe at runtime. A mention that maps to no ticker cannot be expressed as a valid draft yet: the call fails validation. Handling that case is a P1 pipeline task.
  - Gate: confidence outside 0 to `BEHAVIOUR_MAX_CONF` (0.8) is `rejected:schema`. A value that is not in the level enum, or an unknown signal, is `rejected:schema`. A first behavioural value (no previous one) is not clamped. N3: a rule identical to one the user already approved keeps `approved_by_user: true`, every other rule starts `false`. N3 has no confidence in its output, so the previous confidence is kept, with 0.8 as the fallback. Gates for N2 are a P1 step; the gate returns `rejected:schema` for nodes it does not know.
- **Intake and N3 (P0.10):**
  - `intake.REQUIRED` paths (team choice, §20.6 fixes none): objectif `objectives.primary, objectives.goals`; horizon `objectives.horizon_years, identity.retirement_target_age, situation.planned_withdrawals`; risque `risk.declared, risk.max_acceptable_drawdown_pct, risk.reaction_to_20pct_drop`; contraintes `situation.emergency_fund, situation.monthly_expenses, identity.employer_sector, preferences.asset_types_refused`; preferences `preferences.prefers_dividends, prefers_large_caps, home_bias_ok`. Only user-declared fields: never `rule:` or `llm:` fields.
  - A path is missing when absent, `null`, or a blank string. `0`, `false`, `[]` are real answers. Only `{value: null, by: "user:skipped"}` counts as answered-by-skip.
  - A question is `{factor, path, text, locale}`. A parsed `max_theme_weight` rule with no numeric `max_pct` adds the path `values.parsed_notes.value[<i>].params.max_pct` to `contraintes.missing` (approved or not); the question quotes the user's words and names the theme.
  - `build_limits` promotes an approved `max_theme_weight` only when `max_pct` is a number, and an approved `exclude_activity` only when `tag` is a string. An approved rule whose number is still unanswered or skipped changes nothing.
  - N3 payload is `{"notes": <values.notes>}` (the cache key). The `input_hash` stamped in Robert's fixture (hash of the bare notes string) therefore differs from a fresh run's. Hand-written N3 cache entries cover Robert's notes (two soft rules) and Amira's notes (`prefer_attribute` + `max_theme_weight {theme: tech_heavy}` with no number); the tag vocabulary `data/meta/activity_tags.csv` is human-owned and was not touched.
- **Factors, sections, numcheck, template (P0.7):**
  - Section `checks` lists the id of every check of the factor present in the reported checks (pass ones included, once each, in run order); `facts.checks` and `unknowns` hold the non-pass entries only. Status = worst owned check (`violation > warning > unknown > info > pass`) on `checks_proposed` in flow B, `checks_current` in flow C. A section whose only non-pass checks are `unknown` is `unknown`; the verdict still ignores unknowns (D3).
  - ESG split for parsed rules (§20.2 is ambiguous on approved `prefer_attribute` / `avoid_attribute`): `contraintes` shows approved `exclude_activity` / `max_theme_weight` rules (the only kinds that reach `limits`); every other parsed rule, approved or not, is `preferences` (S4, "non-checkable"). Both show up under the `values.parsed_notes` key, labelled `llm:values_parser`.
  - Facts numbers are pre-formatted strings: fr-CA `13,4 %` and `360 000 $` (NBSP before the unit, NBSP thousands), en-CA `13.4%` and `$360,000`. Percent check values and engine metrics keep 1 decimal, amounts are rounded to the dollar, a missing value or limit is an em dash. Plain strings (enums, sector names) are copied verbatim; `*_pct` profile fields are percent points (`-12 %`).
  - `derived[*].because` is one level of the rule registry inputs (`path = value`); for `llm:` fields it lists the cited event ids and signals. A check entry also carries `subject` (so the writer can name NVDA / Information Technology) and keeps its full `derived_from`. `evidence_events` only come from `ctx.events` for S1 (profile `risk.behavioural.evidence`) and S3 (intent `event_id`); an id with no event is dropped.
  - numcheck (`numfacts.py`): `±0.05` absolute on percentages and unit-less decimals, `±0.5 %` relative on amounts, exact on dates and years (a payload date also provides its `YYYY-MM` and `YYYY`). Signs are ignored. A unit-less integer 0-10 is a count and exempt; `5 %` is not. Digits glued to letters or `_` (`H10`, `p5_T`, `ev_robert_0007`) are identifiers. A comma followed by exactly three digits is thousands next to a `$` or with a 3-digit integer part (`$360,000`, `360,000`), a decimal comma otherwise (`7,400 %`). A unit-less 4-digit integer in 1900-2100 is a year. JSON numbers in a payload match a text percentage either as written or times 100.
  - `template_section`: at most 4 segments of at most 300 characters; one segment per violation/warning check (overflow is merged into the last slot, refs kept), one merged `unknown` segment, an optional `info` segment, and a "why" segment naming the profile field the first violated limit comes from when that field is in the facts. `pass` sections get one fact segment referencing `status`.
- **Narration loop (P0.8):**
  - Verifier failures are strings that start with their rule id (`V1: ...` to `V5: ...`); all rules are evaluated, so one answer can fail several. The list returned for the first answer is, as is, the `retry_failures` of the second call.
  - V1 resolves a ref as a check id of `facts.checks`, an `event_id` of `facts.evidence_events`, or a dotted key path of the facts. Keys may contain dots (`declared.objectives.horizon_years`), so every key that prefixes the remaining path is tried; list items are addressed by index. A ref to a `pass` check (not in `facts.checks`) does not resolve.
  - V4 matches `ADVISORY_BLOCKLIST` (lower case) against the casefolded segment text. Beyond the §9.3 examples it holds `vous devriez acheter/vendre`, `je conseille`, `nous conseillons`, `i suggest you`, `we advise`; it is a blocklist, not a proof of no advice, hence the prompt rule and the Bedrock guardrail option.
  - `explanation.mode` is the worst mode among the non-pass sections. With no non-pass section it is `template` (pass sections are template text). Pass sections get `mode: template` in their narrative.
  - A recorded response missing in mock mode (`LLMCacheMiss`) sends the section straight to the template without a retry; the narrative then carries `llm_error`. An output that stays schema-invalid (`LLMOutputInvalid`) counts as a failed attempt (`V5: ...`). Each narrative also carries `attempts` (failures and call log per N5 call) for the audit record.
  - `explanation.headline` is `<verdict phrase>: écarts H9, H4, ...` (or `gaps`), failing = violation and warning ids in section display order, shortened from the end to stay within 140 characters.

## Disclaimer

Educational simulation with fictional profiles. This is not personalised financial advice. No transaction is executed.
