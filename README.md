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

Everything else is stubbed with signatures and `NotImplementedError`. The matching tests are skip-marked with their build step.

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

## Disclaimer

Educational simulation with fictional profiles. This is not personalised financial advice. No transaction is executed.
