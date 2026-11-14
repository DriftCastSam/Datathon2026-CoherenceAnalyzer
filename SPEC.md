# CoherenceAnalyzer: profile ↔ portfolio coherence pipeline

Spec for implementation. Target reader: Claude Code working in this repo.
Status: v1, 2026-11-14. Owner: Sam's team (Datathon 2026 / PolyFinances AlphaForge AI).

---

## 0. One-paragraph summary

CoherenceAnalyzer checks whether a self-directed investor's **portfolio** (or a **trade they want to make**) is coherent with their **own profile**: age, horizon, goals, risk tolerance and capacity, knowledge, values. The pipeline is a scripted DAG, not a free-roaming agent. Deterministic rules own everything that can block or limit. LLM nodes (AWS Bedrock) fill narrow, schema-constrained slots: behaviour reading, question quality, values parsing, intent parsing, explanation. Every LLM output passes through a deterministic **gate** before it touches the profile, and LLM-derived fields can only raise **warnings**, never violations. Output: a coherence report with a verdict, per-check results traced back to profile fields, risk metrics from `engine.py`, options that fit within the user's own limits, and a plain-language explanation whose numbers are machine-verified.

The persona the case highlights is **Robert asking for 10% NVDA**. The system must flag the incoherence, explain the risk, and put the request back in context. It must not just say yes or no.

---

## 1. Hard invariants (do not break these)

1. **Two kinds of fields.** A plain value is declared by the user, and only the user changes it. An object `{value, by, confidence, ...}` is computed. `by` is either `rule:<name>` (deterministic code) or `llm:<name>` (inferred).
2. **`limits` is computed from `rule:` fields and user-declared values only.** No `llm:` field may be an input to `limits`, directly or transitively. Enforced by a test (see §11).
3. **LLM-derived checks can only produce `warning`, `info` or `unknown`.** Only hard checks can produce `violation`.
4. **Every LLM output is schema-validated and gated** (§6) before it's merged. If it fails, the previous value stays and the failure goes in the audit log.
5. **Every LLM evidence item must reference something that exists**: an `event_id` in the event log, or a substring of the user's own notes. Invented evidence means the whole output is rejected.
6. **Every number in the explanation must appear in the facts payload** (within rounding tolerance). Otherwise regenerate once, then fall back to a template.
7. **Simulation only.** No order execution and no buy/sell recommendations. The alternatives shown are "what fits within *your own* stated limits", computed by code. Every user-facing output carries the disclaimer (§9.4).
8. **Reproducible.** Same inputs + same `prompt_version` + same model give the same report (LLM responses are cached by input hash, temperature 0, fixed bootstrap seed). `COHERENCE_ANALYZER_LLM=mock` runs the whole pipeline offline from recorded responses.
9. **Facts, assumptions and inferences are labelled** everywhere they appear: report, explanation and UI. This is a jury requirement.

---

## 2. Repo layout

```
coherence_analyzer/
├── CLAUDE.md                  # short working rules, points here
├── SPEC.md                    # this file
├── README.md                  # deps, data, config, reproduction (jury deliverable)
├── requirements.txt           # pandas, numpy, boto3, jsonschema, pydantic, fastapi, uvicorn, pytest
├── data/
│   ├── starter/               # alphaforge_starter CSVs, unmodified
│   ├── meta/
│   │   ├── asset_meta.csv     # MER, look-through availability, themes (hand-built, sourced)
│   │   ├── etf_sector_weights.csv   # ETF look-through by sector (issuer pages, dated)
│   │   ├── activity_tags.csv  # ticker -> activity/controversy tags, each with source URL + date
│   │   └── SOURCES.md         # source, date, processing, limits for every meta file
├── coherence_analyzer/
│   ├── __init__.py
│   ├── config.py              # env + constants (thresholds, seeds, paths)
│   ├── data.py                # loaders + adapter from starter filenames
│   ├── engine.py              # refactor of current engine.py (importable, see §5)
│   ├── levels.py              # risk-level ordering + helpers
│   ├── rules/
│   │   ├── derive.py          # life_stage, income_stability, ef_months, horizon, capacity, effective, knowledge.score
│   │   ├── limits.py          # LIMITS_TABLE + build_limits() + trace
│   │   └── registry.py        # @rule decorator: name, inputs (profile paths), fn
│   ├── llm/
│   │   ├── bedrock.py         # Converse wrapper: forced tool use, retry, cache, mock
│   │   ├── prompts/           # behaviour_classifier.v1.md, question_rubric.v1.md, ...
│   │   ├── schemas/           # *.json (JSON Schema draft 2020-12)
│   │   └── extractors.py      # one function per LLM node
│   ├── gate.py                # §6
│   ├── coherence/
│   │   ├── hard.py            # H-checks
│   │   ├── soft.py            # S-checks
│   │   └── report.py          # aggregation, verdict, schema
│   ├── intent.py              # apply a confirmed intent to a portfolio
│   ├── resizer.py             # options that fit within limits
│   ├── explain.py             # explainer call + numcheck + template fallback
│   ├── pipeline.py            # flows A/B/C (§4)
│   ├── store.py               # profiles, events, portfolios, audit (local FS or S3)
│   ├── api.py                 # FastAPI (thin)
│   └── cli.py                 # `python -m coherence_analyzer.cli ...`
├── fixtures/
│   ├── robert/  {profile.json, events.jsonl, portfolio.json, intents.json}
│   ├── amira/   {...}
│   ├── mateo/   {...}
│   └── llm_cache/             # recorded Bedrock responses for mock mode
└── tests/
```

`Datathon2026/portee/investor_profile.example.json` becomes `fixtures/robert/profile.json`, with the schema changes from §3.2 applied.

---

## 3. Data model

### 3.1 Market data (starter pack)

Read-only. Use `data.py` to load these and map them to the names the engine expects:

| Engine name | Starter file | Columns used |
|---|---|---|
| `prices` | `prices_daily.csv` | `date, ticker, adj_close` |
| `assets` | `assets_universe.csv` | `ticker, name, asset_class, currency, sector` (index `ticker`) |
| `fx` | `fx_usdcad_daily.csv` | `date, usdcad` |
| `macro` | `macro_rates.csv` | not used in v1 (P2: rate-scenario stress) |

Known data facts the code and docs must respect (from the starter README):
- Prices are in listing currency. `.TO` is CAD, everything else USD. Convert with month-end USDCAD, forward-filled (FX has no Canadian-holiday rows).
- No calendar alignment. Monthly resampling (`ME`, last) takes care of TSX/US/crypto calendar differences. Document this in README.
- Use `adj_close` for returns.
- Window 2021-11-12 → 2026-11-12. The engine's `pct_change().iloc[2:-1]` leaves monthly returns 2022-01 → 2026-10 (58 months). It drops the partial last month, and also Dec 2021, which was actually a full month. Keep this for the golden test, then decide as a team whether to switch to `iloc[1:-1]` (59 months) and re-record. The window includes 2022, when stocks and bonds fell together. Say so when you show max drawdown.
- Survivorship bias: the universe was picked in 2026. Flag it as a limitation in every risk metric's tooltip/report `assumptions`.
- No ESG, news or fundamentals in the pack. Anything ESG comes from `data/meta/activity_tags.csv` with a source. Without a sourced tag, the status is `unknown`, never `pass`.

Asset-class → profile asset type mapping (used by checks):

| `asset_class` | profile type | fixed income? |
|---|---|---|
| `equity` | `stocks` | no |
| `etf_equity` | `etf` | no |
| `etf_bond` | `bonds` (and `etf`) | **yes** |
| `etf_commodity` | `gold` (and `etf`) | no |
| `crypto` | `crypto` | no |

### 3.2 Hand-built meta files

**`asset_meta.csv`**: `ticker, mer_pct, mer_source_url, mer_as_of, has_lookthrough, themes`
- `mer_pct`: 0 for single stocks and crypto. For ETFs, fill from the issuer's page and record the date. **Don't guess.** If it's not filled, check H12 returns `unknown` for that ticker.
- `themes`: semicolon list, e.g. `QQQ → tech_heavy`, `NVDA → ai;semis`, `TSLA → ev;high_beta`, `ESGV → esg_screened`. Used by soft checks and theme limits.

**`etf_sector_weights.csv`**: `etf, sector, weight, source_url, as_of`
- GICS sector weights from issuer holdings pages for XIU.TO, SPY, QQQ, ESGV. Without it, ETF sector exposure is `Broad market` and the employer-sector and tech-exposure checks are partial: report `unknown` for the ETF part.

**`activity_tags.csv`**: `ticker, tag, evidence_summary, source_url, as_of, added_by`
- Tags like `tobacco`, `accounting_fraud_last_5y`, `significant_environmental_controversy`, `significant_social_controversy`, `fossil_fuel_producer`.
- `added_by` is always a human team member. **An LLM must not create rows here.** (An LLM may *propose* rows in a separate review file. P2.)

### 3.3 Investor profile (`profile.json`)

The base is `investor_profile.example.json`. Changes:

1. Every `llm:` object gains `prompt_version`, `model_id`, `updated_at`, `input_hash`.
2. `risk.behavioural.evidence` becomes `[{event_id, signal, note}]` instead of free strings.
3. `knowledge.question_quality.evidence` becomes `[{event_id, score}]`. The question text lives in the event log, not duplicated here.
4. `values.parsed_notes.value[]` items gain `rule_kind`, `params`, `quote` (exact substring of `values.notes`), `approved_by_user`.
5. New `limits_trace`: `{limit_name: [profile paths it depends on, fully expanded]}`. Built by `rules/limits.py`.
6. New `meta.schema_version: 2`.

Risk levels (ordered, index used for comparisons):
`low(0) < low_moderate(1) < moderate(2) < moderate_high(3) < high(4)`.

Write `schemas/profile.v2.json` (JSON Schema) and validate on every load and save.

### 3.4 Event log (`events.jsonl`)

One JSON object per line, append-only:

```json
{"event_id": "ev_robert_0007", "profile_id": "persona-robert", "ts": "2026-11-14T10:02:00-05:00",
 "type": "order_intent", "text": "I want to put 10% in NVDA, everyone's talking about it",
 "payload": null, "source": "ui_chat"}
```

`type` ∈ `question | order_intent | quiz_result | profile_edit | note | intent_confirmed`.
- `quiz_result.payload = {"score": 0.8, "quiz_id": "kq_v1"}`
- `profile_edit.payload = {"path": "risk.declared", "old": "...", "new": "..."}`
- `intent_confirmed.payload` = the confirmed intent (§3.6). Links back with `"parent_event_id"`.

### 3.5 Portfolio (`portfolio.json`)

```json
{"portfolio_id": "robert-main", "profile_id": "persona-robert", "as_of": "2026-11-12",
 "value_cad": 400000, "weights_pct": {"XIU.TO": 18, "RY.TO": 7, "...": 0}}
```

Weights in percent, summing to 100 ± 0.01 (validate). Tickers must exist in `assets_universe`.

Fixture portfolios (from current `engine.py`):
- `ROBERT = {XIU.TO 18, RY.TO 7, ENB.TO 6, FTS.TO 7, JNJ 5, PG 5, SPY 12, XBB.TO 22, ZST.TO 13, GLD 5}`, value 400 000, horizon 48 months
- `AMIRA = {QQQ 22, NVDA 8, SHOP.TO 8, BTC-USD 10, ETH-USD 5, SPY 15, XIU.TO 10, CNR.TO 6, GLD 6, AGG 10}`, value 60 000, horizon 180 months
- `MATEO`: to create. Suggested: `{XIU.TO 15, ESGV 20, SPY 10, RY.TO 5, CNR.TO 5, FTS.TO 5, ENB.TO 5, XOM 5, MSFT 5, XBB.TO 20, AGG 5}`, value 150 000, horizon 120 months. The point is to contain energy names whose status depends on sourced tags.

### 3.6 Intent

```json
{"intent_id": "in_0001", "event_id": "ev_robert_0007", "action": "buy",
 "ticker": "NVDA", "target_weight_pct": 10,
 "funding": {"kind": "specific", "ticker": "ZST.TO"},
 "status": "confirmed", "confirmed_at": "..."}
```

`funding.kind` ∈ `specific | pro_rata_all | pro_rata_class` (with `asset_class`) `| cash_contribution` (with `amount_cad`).
Default when the user doesn't say: `pro_rata_all`, shown to the user as the default before they confirm.

The fixture `ROBERT_NVDA` in engine.py corresponds to `NVDA 10%, funding specific ZST.TO`.

---

## 4. Flows

Three flows, all in `pipeline.py`. Each run gets a `run_id` (ULID) and writes one audit record (§10).

### Flow A: profile refresh

Trigger: a new `question`, `quiz_result`, `profile_edit` or `note` event, or `coherence_analyzer refresh <profile>`.

```
load profile + events
→ A1 rules.derive (all rule: fields)
→ A2 llm extractors in parallel (only those whose inputs changed):
      behaviour_classifier   (on question/order_intent events, last 30 days, max 20)
      question_rubric        (on each new question event)
      values_parser          (if values.notes changed)
→ A3 gate each output
→ A4 rules.derive again (knowledge.score depends on question_quality)
→ A5 rules.limits.build_limits + limits_trace
→ A6 validate profile schema, bump meta.version, save, audit
```

### Flow B: trade check (the main demo)

Trigger: an `order_intent` event.

```
B1 intent_parser (LLM) → draft intent
B2 UI shows draft (prefilled form). The user confirms or edits. Emits intent_confirmed.
     The confirmed intent is user-declared data from here on.
B3 Flow A (refresh) using events up to and including this order_intent
B4 intent.apply(portfolio, intent) → proposed portfolio
B5 engine.analyse(current) and engine.analyse(proposed)  (same seed)
B6 coherence.hard + coherence.soft on both → diff (which checks flipped)
B7 if proposed has violations → resizer (§8)
B8 explain (§9)
B9 report.save + audit
```

In CLI/mock mode, B2 auto-confirms when `--auto-confirm` is passed (for tests and the video).

### Flow C: portfolio review

Trigger: `coherence_analyzer review <profile>`. It's B5–B9 without an intent: a baseline coherence report for the current portfolio. This is used for Amira and Mateo, who aren't making a specific trade.

Persona demo matrix (the case asks for ≥ 2 contrasting situations):

| Persona | Flow | What it should show |
|---|---|---|
| Robert | B: NVDA 10% from ZST | incoherent: single-stock and fixed-income violations, behaviour-gap warning, options within limits |
| Amira | C | high risk accepted, but crypto above the high-tier cap and tech concentration vs her own "not only tech" note. "High risk ≠ anything goes" |
| Mateo | C | ESG: energy names are `unknown` until sourced tags exist, `violation` once a sourced tag matches. "Says what it doesn't know" |

---

## 5. Engine (`coherence_analyzer/engine.py`)

Refactor the current script into a module **without changing the math**:

- `load_market(data_dir) -> Market` (r_cad, r_loc, fxr, assets), cached once per process.
- `analyse(market, weights_pct, horizon_m, start_cad, *, n=5000, block=12, seed=7, drift="assumed", monthly_contribution=0.0, goals=None, return_paths=False) -> dict`
- Keep the output keys: `vol, vol_local, mdd, mdd_date, cone{p5..p95}, width, loss_prob, groups{sector,country,class}, drivers, corr_eq_bond`.
- Add:
  - `monthly_contribution`: added to NAV at each step in the bootstrap paths (paths in CAD, not normalised). Default 0 keeps the current behaviour.
  - `goal_probs`: for each goal `{name, date, amount}` within the horizon, the share of paths at or above `amount` at that month.
  - `p5_T`, `p50_T`, `p95_T`: terminal values in CAD.
  - `sector_lookthrough`: when `etf_sector_weights.csv` exists, split ETF weight and risk contribution across sectors by weight. Otherwise keep `Broad market` and set `lookthrough: false`.
  - `assumptions`: list of strings, always including the drift table (`ASSUMED`, labelled as a team assumption), the data window, block bootstrap with block 12, survivorship bias, and the 2022 joint stock-bond drawdown being in the sample.
- Remove the hardcoded `/home/claude/...` paths. The CLI keeps a `python -m coherence_analyzer.engine --data data/starter` entry that prints what the current script prints.
- Golden test: on the starter data, `analyse(ROBERT, 48, 400000)` produces the same `vol`, `mdd`, cone as the current script (record once, compare to 1e-9).

---

## 6. LLM layer

### 6.1 Bedrock wrapper (`llm/bedrock.py`)

```python
def call_tool(node: str, schema: dict, system: str, user_payload: dict,
              prompt_version: str) -> dict
```

- `bedrock-runtime.converse` with `toolConfig.tools = [one toolSpec whose inputSchema.json = schema]` and `toolChoice = {"tool": {"name": node}}`. Temperature 0, `maxTokens` set per node.
- Extracts the `toolUse.input` block and validates it with `jsonschema` (draft 2020-12).
- On a validation error, it retries once with the validator message appended as a user turn. On a second failure it raises `LLMOutputInvalid`.
- Cache key = sha256(node, prompt_version, model_id, canonical-json(user_payload)), stored as files in `.cache/llm/`. Cache hit → no call.
- `COHERENCE_ANALYZER_LLM=mock` reads only from `fixtures/llm_cache/` and raises on a miss. `COHERENCE_ANALYZER_LLM=record` calls Bedrock and writes to `fixtures/llm_cache/`.
- Config: `AWS_REGION`, `BEDROCK_MODEL_ID` (team picks a Claude model enabled in their account), optional `BEDROCK_GUARDRAIL_ID` + version applied to the explainer call only.
- Logs per call: node, model_id, prompt_version, latency, input/output tokens, cache hit. All of it goes to the audit record.

### 6.2 Nodes

Each node has a prompt file `prompts/<node>.v<N>.md` and a schema `schemas/<node>.json`. Bump the version whenever the prompt changes.

#### N1 `behaviour_classifier`

- **Input:** the last 30 days of `question` + `order_intent` events (max 20): `[{event_id, ts, type, text}]`. **Don't include the declared risk or any profile field**, to avoid anchoring. It reads behaviour only.
- **Output:**
  ```json
  {"abstain": false,
   "value": "moderate_high",
   "confidence": 0.55,
   "evidence": [{"event_id": "ev_robert_0007", "signal": "hype_chasing", "note": "asks 10% in one stock after news"}]}
  ```
  `signal` ∈ `hype_chasing, concentration_request, panic_selling_intent, loss_aversion, overtrading, market_timing, long_term_framing, diversification_request, risk_questioning`.
- **Prompt rules:** classify the risk appetite the events reveal, not the person. Abstain if fewer than 2 relevant events. Every claim cites an event_id. Confidence ≤ 0.8 by construction (behaviour is noisy).

#### N2 `question_rubric`

- **Input:** one question event `{event_id, text}`.
- **Output:** sub-scores only; **code computes the score**.
  ```json
  {"specificity": 0-3, "correct_concepts": 0-3, "risk_awareness": 0-3, "horizon_awareness": 0-3,
   "is_investing_question": true, "rationale": "<=150 chars"}
  ```
- Code: `score = sum/12`. If `is_investing_question = false`, ignore it. `question_quality.value` = EWMA(α = 0.3) over the scored questions, oldest first.
- Fixture expectations (approx., used as sanity checks, not exact asserts): the XBB-duration question ≥ 0.75, "next big AI stock" ≤ 0.35.

#### N3 `values_parser`

- **Input:** `values.notes` (string).
- **Output:** `{"rules": [{"rule_kind", "params", "applies_to", "quote"}]}`
  - `rule_kind` ∈ `exclude_activity` (params `{tag}` from the activity_tags vocabulary) `| max_theme_weight` (`{theme, max_pct}`) `| prefer_attribute` (`{attribute}`) `| avoid_attribute` | `other`.
  - `applies_to` ∈ `all | single_stocks | etf`.
  - `quote` must be an exact substring of `notes`.
- Parsed rules start with `approved_by_user: false`. Only approved rules of kind `exclude_activity` or `max_theme_weight` get **promoted** into `limits` (`excluded_activities`, `max_theme_weight`). Approval is a user action, which makes the rule user-declared, so invariant 2 holds. Other kinds stay soft reminders (S4).

#### N4 `intent_parser`

- **Input:** the order_intent text + universe tickers/names + the current portfolio weights.
- **Output:** a draft intent (§3.6 fields) + `ambiguous_fields: []` + `unresolved_mentions: []` (e.g. "the next big AI stock" with no ticker).
- Ticker must be in the universe (enum in the schema, generated at runtime). Never auto-executed. Always confirmed by the user (B2).

#### N5 `explainer`

See §9.

---

## 7. Rules and checks

### 7.1 Derived fields (`rules/derive.py`)

Each one is a pure function registered with `@rule(name, inputs=[profile paths])`. The registry is what builds `limits_trace` and enforces invariant 2.

| Field | Rule |
|---|---|
| `identity.life_stage` | age < 35 → `early_accumulation`; < 55 → `accumulation`; < retirement_target_age → `pre_retirement`; else `retired` |
| `situation.income_stability` | salaried → `high`; self_employed → `medium`; contract / commission → `low`; unemployed / retired-without-pension → `low` |
| `situation.emergency_fund_months` | `emergency_fund / monthly_expenses`, 1 decimal |
| `objectives.effective_horizon_years` | `min(horizon_years, months until first planned_withdrawal / 12)`, 1 decimal. Robert: 2026-11 → 2030-06 = 43 mo = 3.6 ✓ |
| `risk.capacity` | Base from effective horizon: < 3 y `low`, 3–5 `low_moderate`, 5–10 `moderate`, 10–15 `moderate_high`, ≥ 15 `high`. Down one notch (floor `low`) for **each**: ef_months < 3; income_stability `low`; withdrawals within 24 months > 10% of portfolio value |
| `risk.effective` | `min(declared, capacity)` by level index |
| `knowledge.score` | `0.4·quiz·100 + 0.3·declared_map + 0.3·question_quality·100`, with `declared_map = {novice: 20, intermediate: 50, advanced: 78, expert: 95}` (calibrated so the Robert fixture = 74). The question-quality term moves at most **5 points** per update relative to the previous run. Append to `history`. |

`knowledge.score` mixes an `llm:` input. It's a `rule:` field with `confidence < 1`, and it is **not** an input to `limits` (only to soft check S2).

### 7.2 Limits (`rules/limits.py`)

`LIMITS_TABLE[risk.effective]`. **Team assumptions**: calibrate and justify them in the docs.

| level | vol band | max DD (table) | min fixed inc. | max single stock | max sector risk share | max crypto | max foreign ccy | floor ratio |
|---|---|---|---|---|---|---|---|---|
| low | 0.02–0.06 | −8% | 0.50 | 0.05 | 0.25 | 0.00 | 0.30 | 0.95 |
| low_moderate | 0.05–0.09 | −12% | 0.30 | 0.08 | 0.30 | 0.00 | 0.40 | 0.90 |
| moderate | 0.08–0.12 | −20% | 0.20 | 0.10 | 0.35 | 0.02 | 0.50 | 0.85 |
| moderate_high | 0.11–0.16 | −28% | 0.10 | 0.12 | 0.40 | 0.05 | 0.60 | 0.75 |
| high | 0.14–0.22 | −40% | 0.00 | 0.15 | 0.45 | 0.10 | 0.70 | 0.60 |

Then apply the profile:
- `max_drawdown_pct` = the less negative of the table value and `risk.max_acceptable_drawdown_pct`.
- `floor_at_horizon` = `portfolio.value_cad × floor_ratio` (Robert: 400 000 × 0.90 = 360 000 ✓).
- `max_employer_sector_weight` = 0.15 (all levels).
- `excluded_asset_types` = `preferences.asset_types_refused` ∪ (`crypto` if max_crypto = 0).
- `excluded_activities` = union of `values.*.exclusions` + approved `exclude_activity` parsed rules.
- `max_fee_mer_pct` = `preferences.max_fee_mer_pct`.
- `max_theme_weight` = approved `max_theme_weight` parsed rules (e.g. Amira `tech_heavy ≤ 50%` if she approves it).

`limits_trace` example (must be generated, not hand-written):
```json
"min_fixed_income_weight": ["risk.effective", "risk.declared", "risk.capacity",
  "objectives.effective_horizon_years", "objectives.horizon_years", "situation.planned_withdrawals",
  "situation.emergency_fund_months", "situation.emergency_fund", "situation.monthly_expenses",
  "situation.income_stability", "identity.employment_type"]
```

### 7.3 Hard checks (`coherence/hard.py`)

Each check returns:
```json
{"id": "H3", "kind": "hard", "status": "pass|violation|info|unknown",
 "subject": "NVDA", "value": 0.10, "limit": 0.08, "unit": "weight",
 "derived_from": ["limits.max_single_stock_weight", "...trace"],
 "source": "rule:max_single_stock", "confidence": 1.0, "message_key": "single_stock_over"}
```

| ID | Check | Value | Status logic |
|---|---|---|---|
| H1 | Excluded asset types | holdings whose type ∈ excluded | any → violation |
| H2 | Crypto weight | Σ crypto | > max → violation |
| H3 | Single-stock weight | each `equity` weight | > max → violation (one result per ticker over) |
| H4 | Fixed income | Σ `etf_bond` | < min → violation |
| H5 | Foreign currency | Σ non-CAD weight (USD stocks, ETFs, GLD, crypto) | > max → violation |
| H6 | Employer sector | Σ equity weight in `employer_sector` (+ ETF look-through if available) | > max → violation; ETF part missing → also emit `unknown` for the ETF part |
| H7 | Volatility band | `engine.vol` | > band hi → violation; < band lo → info ("more conservative than your profile needs") |
| H8 | Max drawdown | `engine.mdd` (historical, monthly, 2022-01→2026-10) | worse than limit → violation |
| H9 | Floor at horizon | `engine.p5_T` | < floor → violation |
| H10 | Sector risk concentration | max sector `risk` share from `groups.sector` | > max → violation. "Broad market" is excluded from the max when look-through is off, and flagged `unknown` |
| H11 | Excluded activities | holdings with a sourced tag ∈ excluded | match → violation; ETF without look-through, or a ticker with no tag rows in a category the user excludes → `unknown` |
| H12 | Fees | each ETF `mer_pct` | > max → violation; missing MER → unknown |
| H13 | Theme weight | Σ weight with theme (ETF counts fully if theme tagged) | > max_theme_weight → violation |
| H14 | Stated reaction vs history | if `reaction_to_20pct_drop ∈ {sell_some, sell_all}` and `engine.mdd < −0.20` | → info with the drawdown and its date (deterministic, factual) |
| G1 | Goal probabilities | `engine.goal_probs` | always `info`, with assumption labels |

### 7.4 Soft checks (`coherence/soft.py`)

They only run if the source field passed the gate and its confidence ≥ `WARN_MIN_CONF` (0.5). If not → `info` "insufficient evidence".

| ID | Check | Status |
|---|---|---|
| S1 | Behaviour gap: `idx(behavioural) − idx(effective)` | ≥ 2 → warning; 1 → info; ≤ 0 → pass. Message cites the evidence event_ids |
| S2 | Knowledge vs complexity: `knowledge.score < 50` and holding crypto, TLT (long duration) or a single stock > 5% | warning |
| S3 | Hype pattern on the intent itself: the intent's event is cited by N1 with `hype_chasing` | warning, attached to the trade |
| S4 | Unapproved or non-checkable parsed values rules (e.g. Robert's `prefer_understandable_business` on single stocks) | info reminder: "you wrote: '<quote>'" |

### 7.5 Verdict

- Any `violation` → `incoherent`
- Otherwise any `warning` → `coherent_with_warnings`
- Otherwise → `coherent`
- `unknown` items are always listed separately under "what we can't verify", whatever the verdict.

For flow B, also compute `diff`: checks whose status changed between current and proposed, plus metric deltas (vol, mdd, p5_T, width, loss_prob, top-3 drivers).

---

## 8. Resizer (`resizer.py`)

Goal: show what *would* fit within the user's own limits. These are facts about the profile, not advice.

For a failing intent `(ticker, target w*, funding)`:
1. For each funding mode in `[user's funding, pro_rata_all, pro_rata_class=etf_equity, pro_rata_class=equity]`, de-duplicated:
   - Scan `w` from 0 to w* in 0.5 pp steps. Run the **cheap** checks (H1–H6, H11–H13) at each step and stop at the first failure.
   - At the last passing `w`, run the engine checks (H7–H10) with `n=1000`. If any fails, binary-search downward on `w` with `n=1000`.
   - Confirm the final `w` with full `n=5000`.
2. Return up to 3 options: `{funding, max_weight_pct, binding_check, metrics}`. `binding_check` names the limit that stops it.
3. If the max is 0 under every funding mode, return `{none_fit: true, binding_checks: [...]}`.

Expected for Robert, NVDA from ZST: H4 binds first. Fixed income = 22 + (13 − w) ≥ 30 → **w ≤ 5%**. With pro-rata funding from equities, fixed income stays at 35% and H3 binds at **8%**. Both are subject to H7–H10 passing (verify on the real data and record in golden tests).

---

## 9. Explanation (`explain.py`)

### 9.1 Facts payload (the only thing the LLM sees)

```json
{"persona_summary": {...profile fields relevant to failing/warning checks only...},
 "intent": {...}, "verdict": "incoherent",
 "checks": [...non-pass checks with value, limit, derived_from, source, confidence...],
 "metrics_current": {...}, "metrics_proposed": {...}, "diff": {...},
 "alternatives": [...], "unknowns": [...], "assumptions": [...],
 "evidence_events": [{"event_id", "date", "text"}],
 "locale": "fr-CA"}
```

Numbers are pre-formatted in the payload (e.g. `"vol_pct": "7.4 %"`), so the LLM copies them instead of computing.

### 9.2 Output schema

```json
{"headline": "<= 140 chars",
 "segments": [{"kind": "fact|assumption|inference|option|unknown",
               "text": "...", "refs": ["H3", "metrics_proposed.vol", "ev_robert_0007"]}],
 "disclaimer": "..."}
```

Sections in order: what you asked → what it changes (numbers) → which of *your* limits it crosses and **why that limit exists** (walk `derived_from`: e.g. "your 30% minimum in bonds comes from your effective risk level, low-moderate, which comes from your first planned withdrawal in June 2030, 3.6 years from now") → behaviour note (kind `inference`, with confidence) → what we can't verify → options within your limits → disclaimer.

Prompt rules: only use payload content; every segment has ≥ 1 ref; no imperative buy/sell language ("you should buy/sell"); `inference` segments must state they are inferences; write in `locale` (fr-CA default, en-CA supported).

### 9.3 Post-checks (deterministic)

1. Every `ref` resolves to a payload key, check id or event_id.
2. **numcheck:** regex all numbers in `headline` + `segments[].text` (handles `7,4 %`, `7.4%`, `360 000 $`, `$360,000`, dates). Each must match a payload number within ±0.05 absolute for percentages, ±0.5% relative for amounts, exact for dates and years. Small integers ≤ 10 used as counts are exempt.
3. A blocklist of advisory phrasing (FR + EN), e.g. `tu devrais acheter`, `je recommande`, `you should buy`, `we recommend`.
4. If anything fails → regenerate once with the failure list. If it fails again → `template_explain(payload)`: a deterministic French/English template built from the same checks. The report records `explanation.mode = llm | llm_retry | template`.

Optional: apply a Bedrock Guardrail (denied topic: personalised investment recommendations) to this call. It's a visible AWS integration point.

### 9.4 Disclaimer (fixed text, not LLM-generated)

FR: « Simulation éducative avec des profils fictifs. Ceci n'est pas un conseil financier personnalisé. Aucune transaction n'est exécutée. »
EN: "Educational simulation with fictional profiles. This is not personalised financial advice. No transaction is executed."

---

## 10. Persistence and audit (`store.py`)

Backend `local` (default) or `s3` via `COHERENCE_ANALYZER_STORE`. Same layout:

```
profiles/<profile_id>/profile.v<N>.json
profiles/<profile_id>/events.jsonl
portfolios/<portfolio_id>.json
reports/<run_id>.json
audit/<run_id>.json
```

Audit record:
```json
{"run_id", "flow", "started_at", "ended_at", "profile_id", "profile_version_in", "profile_version_out",
 "events_considered": ["ev_..."],
 "llm_calls": [{"node", "model_id", "prompt_version", "input_hash", "cache_hit", "latency_ms",
                "tokens_in", "tokens_out", "gate": "accepted|rejected:<reason>|clamped"}],
 "profile_diff": [{"path", "old", "new", "by"}],
 "engine": {"seed", "n", "block", "drift", "data_window"},
 "verdict", "explanation_mode"}
```

`coherence_analyzer audit <run_id>` pretty-prints this. It's the "show your work" screen for the jury.

---

## 11. Gate (`gate.py`)

`gate(node, old_field, llm_output, context) -> (new_field, status)`. Steps, in order:

1. **Abstain:** `abstain: true` → keep the old value, status `kept:abstain`.
2. **Evidence:** every `event_id` ∈ the context events; every `quote` is a substring of `values.notes`. Any miss → `rejected:evidence`.
3. **Enum/range:** already enforced by the schema, re-asserted.
4. **Movement clamp:**
   - `risk.behavioural`: moves at most **1 level** per run from the previous value.
   - `knowledge.question_quality`: EWMA already damps it. The resulting knowledge-score delta from this term is clamped to ±5 pts.
5. **Confidence:** stored as-is. `warn_enabled = confidence ≥ WARN_MIN_CONF`.
6. **Stamp:** `by`, `prompt_version`, `model_id`, `updated_at`, `input_hash`.

Status ∈ `accepted | clamped | kept:abstain | rejected:evidence | rejected:schema`.

---

## 12. API (`api.py`, thin FastAPI) and CLI

| Method | Path | Does |
|---|---|---|
| GET | `/profiles/{id}` | current profile |
| POST | `/profiles/{id}/events` | append event; triggers Flow A (question/quiz/edit/note) or B1 (order_intent) and returns the draft intent |
| POST | `/intents/{intent_id}/confirm` | body = edited intent → Flow B3–B9 → report |
| POST | `/profiles/{id}/review` | Flow C → report |
| GET | `/reports/{run_id}` | report |
| GET | `/audit/{run_id}` | audit |

CLI:
```
python -m coherence_analyzer.cli refresh fixtures/robert
python -m coherence_analyzer.cli check fixtures/robert --intent in_0001 --auto-confirm
python -m coherence_analyzer.cli review fixtures/amira
python -m coherence_analyzer.cli audit <run_id>
python -m coherence_analyzer.cli demo            # runs the 3-persona matrix, writes reports/, prints verdicts
```

The UI (existing design: cone, risk decomposition, drivers) consumes `reports/<run_id>.json`. UI requirements for this feature:
- Check list with status chips; clicking a check shows its `derived_from` chain.
- Fact / assumption / inference / unknown tags on every explanation segment.
- Before/after cone overlay for flow B.
- Intent confirmation form (B2).
- Approve/reject buttons for parsed values rules (N3).

---

## 13. Report schema (`schemas/report.json`)

```json
{"run_id", "flow", "profile_id", "profile_version", "portfolio_id",
 "intent": null | {...},
 "verdict": "coherent|coherent_with_warnings|incoherent",
 "checks_current": [...], "checks_proposed": [...] | null,
 "diff": null | {"flipped": [{"id", "from", "to"}], "metrics_delta": {...}},
 "metrics_current": {...engine...}, "metrics_proposed": null | {...},
 "alternatives": [...], "unknowns": [...],
 "assumptions": [...], "data_window": ["2022-01-31", "2026-10-31"],
 "explanation": {"mode", "headline", "segments", "disclaimer"},
 "disclaimer": "..."}
```

---

## 14. AWS footprint

The case demands "real and demonstrable" AWS use without multiplying services. Each one below has a reason:

| Service | Role | Priority |
|---|---|---|
| **Bedrock (Converse, tool use)** | N1–N5. The core GenAI integration | P0 |
| **Bedrock Guardrails** | Denied topic "personalised investment advice" on the explainer | P1 |
| **S3** | Profiles, events, reports, audit (`COHERENCE_ANALYZER_STORE=s3`); data files | P1 |
| **Step Functions** | Flow B as a state machine (parallel LLM branch, gate, engine, explain). The diagram goes straight into the video and the docs | P2 |
| Lambda | Hosts the steps if Step Functions is used | P2 |

IAM: one role, least privilege (`bedrock:InvokeModel` on the chosen model ARN, the S3 prefix only). No credentials in the repo. Use `.env.example`.

---

## 15. Tests (`tests/`, all run with `COHERENCE_ANALYZER_LLM=mock`)

- `test_engine_golden.py`: refactored engine == original script output on starter data (Robert, Robert-NVDA, Amira).
- `test_rules.py`: Robert fixture gives life_stage `pre_retirement`, ef_months 4.6, effective_horizon 3.6, capacity `low_moderate`, effective `low_moderate`, knowledge.score 74, limits equal to the example JSON's `limits` block.
- `test_limits_no_llm_inputs.py`: walk the rule registry; assert no `limits` input path resolves to a field with `by` starting with `llm:` (invariant 2). Also assert that unapproved parsed rules don't appear in limits.
- `test_hard.py`:
  - Robert baseline: H1–H6 pass (fixed income 35%, max single stock RY 7%, foreign currency 27%, crypto 0).
  - Robert + NVDA 10% from ZST: H3 violation (NVDA 0.10 > 0.08), H4 violation (0.25 < 0.30), H5 pass (0.37 ≤ 0.40). Verdict `incoherent`.
  - Amira baseline: H2 violation (crypto 0.15 > 0.10 at `high`), assuming her fixture derives effective `high`.
  - Mateo: XOM with no tag rows and an excluded controversy category → H11 `unknown`; add a sourced tag row in a temp meta → `violation`.
- `test_gate.py`: invented event_id → rejected; quote not in notes → rejected; behavioural jump low_moderate→high → clamped to moderate; abstain keeps the old value.
- `test_resizer.py`: Robert NVDA from ZST → max 5.0% (binding H4); pro-rata equities → 8.0% (binding H3). Skip-mark if engine checks bind earlier on the real data, then update the golden values.
- `test_numcheck.py`: accepts `7,4 %`, `7.4%`, `360 000 $`; rejects a number not in the payload; rejects advisory phrases.
- `test_pipeline_offline.py`: `cli demo` end-to-end in mock mode → 3 reports, schema-valid, verdicts `incoherent` / `incoherent` / `coherent_with_warnings|unknown-bearing` as fixtures dictate.

---

## 16. Fixtures to create

- **robert/**: profile from the example JSON (schema v2 conversion). Events: ev_0001 quiz 0.8 (2026-11-01); ev_0004 question "Which AI stock is the next big one?" (2026-11-10); ev_0005 question "What does XBB's duration mean for my bonds if rates drop?" (2026-11-12); ev_0007 order_intent "I want to put 10% in NVDA, everyone's talking about it" (2026-11-14). Portfolio ROBERT. Intent in_0001 = NVDA 10% from ZST.TO.
- **amira/**: 26, software engineer (`employer_sector: Information Technology` → H6 matters with NVDA/SHOP), single, no dependants, intermediate, horizon 15+, declared `high`, notes "I love AI and new tech but I don't want everything in tech." → N3 should parse `max_theme_weight {theme: tech_heavy}` with no number. The UI asks her for one; the fixture approves 50%. Fictional figures for income, expenses and emergency fund (keep ef_months ≥ 3 so capacity stays `high`). Portfolio AMIRA, value 60 000. A few events showing long-term framing plus one crypto FOMO question.
- **mateo/**: 41, operations manager, partner + 2 children, intermediate, horizon 10, declared `moderate`; values `environment.importance 5, exclusions [significant_environmental_controversy]`, `social.importance 4, exclusions [significant_social_controversy]`. Notes: "Avoid companies with major environmental or social controversies. Open to new tech if it fits, nothing speculative." Portfolio MATEO (§3.5).
- **llm_cache/**: record with `COHERENCE_ANALYZER_LLM=record` once Bedrock access works. Until then, hand-write plausible responses that pass the schemas, so the pipeline and tests run.

All persona financial figures beyond the case brief are **fictional team assumptions**. List them in README.

---

## 17. Build order

**P0: the Robert demo works end-to-end offline**
1. `data.py` + engine refactor + golden test
2. `levels.py`, `rules/registry.py`, `rules/derive.py`, `rules/limits.py` + tests
3. Profile v2 schema + Robert fixture conversion
4. `coherence/hard.py` H1–H10, H14 + `report.py` verdict
5. `intent.py`, `resizer.py`
6. `llm/bedrock.py` with mock mode; N1, N4 schemas/prompts; `gate.py`
7. `explain.py` with template fallback first, then N5 + numcheck
8. `pipeline.py` flows B and C, `cli.py demo`

**P1: three personas + real Bedrock**
9. N2, N3, soft checks S1–S4
10. Amira + Mateo fixtures; `asset_meta.csv` (MERs sourced), `activity_tags.csv` (sourced), H11–H13
11. Record the real Bedrock cache; Guardrail on the explainer; S3 store
12. FastAPI + wiring to the UI

**P2: polish**
13. `etf_sector_weights.csv` look-through (removes most `unknown`s on H6/H10/H11)
14. Contributions + goal_probs in the engine
15. Step Functions version of flow B
16. Rate-scenario stress from `macro_rates.csv` (e.g. +100 bp shock on bond ETFs via duration, labelled as an assumption)

---

## 18. Non-goals

- No chatbot. Text input exists only to capture events (questions, intents). It's not a conversational advisor.
- No portfolio optimisation and no "best portfolio" output. The resizer only answers "how much fits within your limits".
- No real accounts, no real banking data, no execution.
- No LLM-generated ESG facts. ESG status comes only from sourced, human-entered tags.

---

## 19. Known limitations (document them, show them in the UI)

- 5-year window. Block bootstrap reuses that history. Drift is a team assumption (`ASSUMED`), not a forecast.
- Survivorship bias in the universe.
- Without ETF look-through, sector, employer and activity checks on ETFs are `unknown`.
- Behaviour classification is noisy (confidence capped at 0.8) and needs ≥ 2 events. It's a warning, never a block.
- Limits table values are team calibrations, not regulation. KYC suitability in Québec (AMF/CIRO) is the real-world analogue and is out of scope.
