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

## Disclaimer

Educational simulation with fictional profiles. This is not personalised financial advice. No transaction is executed.
