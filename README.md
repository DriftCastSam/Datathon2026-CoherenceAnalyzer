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

Everything else is stubbed with signatures and `NotImplementedError`. The matching tests are skip-marked with their build step.

Remaining TODOs:
- Convert `fixtures/robert/profile.json` to schema v2 (P0.3). It is still the v1 example.
- Amira and Mateo have portfolios only; their profiles and events are P1.

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

## Disclaimer

Educational simulation with fictional profiles. This is not personalised financial advice. No transaction is executed.
