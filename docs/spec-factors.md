# §20. Five-factor architecture: rules decide, agents explain, code checks the agents

Status: team-approved 2026-11-14 (docs/spec-decisions.md D5). Paste-ready as SPEC.md §20.
Where this section and §9 / §13 / §17 disagree, this section wins.

The profile, the checks and the report are organised by the five investor-profile factors:

| # | Factor (`id`) | Question shown to the user (fr-CA / en-CA) |
|---|---|---|
| 01 | Objectif (`objectif`) | Pourquoi investir? / Why invest? |
| 02 | Horizon (`horizon`) | Quand l'argent sera-t-il requis? / When will the money be needed? |
| 03 | Risque (`risque`) | Quelle perte peut être absorbée? / What loss can be absorbed? |
| 04 | Contraintes (`contraintes`) | Quelles limites s'appliquent? / Which limits apply? |
| 05 | Préférences (`preferences`) | Qu'est-ce qui oriente les choix? / What guides the choices? |

Principle (shown in the UI): *Les contraintes établissent les limites. Les préférences orientent les choix
à l'intérieur de ces limites.* This is invariant 3 restated: rules and user-approved limits can produce
`violation`; preferences and LLM-derived fields produce `warning | info | unknown` only.

## 20.1 Three layers

```
1. INTAKE     (agent loop per factor, P1)   is the profile complete? ask -> gate -> fill -> re-check
2. ANALYSIS   (code only, no LLM)           rules/derive -> limits -> checks -> verdict -> 5 sections
3. NARRATION  (agent loop per section)      writer(section facts) -> verifier -> retry once -> template
```

- The LLM never decides a status, a verdict or a number. Layer 2 is unchanged by this section
  (§7, §8 still apply); it only gains the `factor` tag and the section builder.
- Each LLM loop has a stopping rule written in code: intake stops when the factor's checklist is
  complete or the user skips (field marked `unknown`); narration stops at the first verifier pass,
  after one retry, or on the template.

## 20.2 Factor mapping (fixed table, `coherence_analyzer/factors/__init__.py`)

Compute order follows the dependencies: `horizon -> contraintes -> risque -> objectif -> preferences`
(horizon and liquidity feed capacity; capacity feeds effective risk and the limits; preferences act
inside the limits). Display order is the slide order 01..05.

| Factor | Profile fields shown (declared or derived) | Checks owned | Engine metrics quoted |
|---|---|---|---|
| objectif | `objectives.primary`, `objectives.secondary`, `objectives.goals`, `identity.life_stage` | G1 (P2), O1 (P1) | `p50_T` |
| horizon | `objectives.horizon_years`, `objectives.effective_horizon_years`, `situation.planned_withdrawals`, `identity.retirement_target_age` | H9 | `p5_T`, `p50_T`, `p95_T`, `width` |
| risque | `risk.declared`, `risk.capacity`, `risk.effective`, `risk.behavioural`, `risk.max_acceptable_drawdown_pct`, `risk.reaction_to_20pct_drop` | H4, H7, H8, H14, S1, S3 | `vol`, `mdd`, `loss_prob` |
| contraintes | `situation.emergency_fund_months`, `situation.income_stability`, `identity.employer_sector`, `preferences.asset_types_refused`, `preferences.max_fee_mer_pct`, `knowledge.score`, `values.*.exclusions`, approved parsed rules | H1, H2, H3, H5, H6, H10, H11, H12, H13, S2 | sector risk shares |
| preferences | `values.*.importance`, `values.notes`, `preferences.prefers_dividends`, `preferences.prefers_large_caps`, `preferences.home_bias_ok`, `preferences.interests`, unapproved parsed rules | S4 | none |

- `CHECK_FACTOR: dict[str, str]` maps every check id of §7.3 and §7.4 (plus G1, O1) to exactly one factor.
  A check id missing from the table is a programming error (test).
- **ESG split.** Explicit exclusions the user approved (`values.*.exclusions`, approved
  `exclude_activity` / `max_theme_weight` rules) are **contraintes** (H11, H13, can be `violation`).
  Leanings and importance scores, and unapproved parsed rules, are **preferences** (S4, `info` only).
- **Not covered yet** (listed per section in `not_covered`, shown in the UI, never a check):
  contraintes `fiscalite` (account type TFSA/RRSP/non-registered, US withholding) and
  objectif `objective_vs_portfolio` until O1 lands.
- **O1 (P1, info only):** `objectives.primary = capital_preservation` and `engine.mdd < risk.max_acceptable_drawdown_pct`
  -> `info` "your stated goal is capital preservation; the historical drawdown is X". Rule-based, but kept
  at `info` until the team calibrates it.

## 20.3 Factor modules (`coherence_analyzer/factors/<id>.py`)

A factor module is a **view**, not a second implementation: it names what belongs to the factor and
builds its section facts. Rules stay in `rules/`, checks stay in `coherence/`.

```python
ID: str                         # "risque"
TITLE: dict[str, str]           # {"fr-CA": "Risque", "en-CA": "Risk"}
QUESTION: dict[str, str]        # 20 table above
FIELDS: tuple[str, ...]         # profile paths (20.2)
METRICS: tuple[str, ...]        # engine metric keys (20.2)
REQUIRED: tuple[str, ...]       # intake checklist (20.6), P1
NOT_COVERED: tuple[str, ...]

def facts(ctx: SectionContext) -> dict   # pure; the section facts payload (20.4)
```

`SectionContext` carries: profile, limits, limits_trace, checks (current and proposed), metrics
(current and proposed), intent, events, locale. `factors.build_sections(ctx) -> list[Section]`
returns the five sections in display order.

## 20.4 Section (report) and section facts (LLM input)

Section object, in `report.sections` (5 items, display order):

```json
{"factor": "risque", "title": "Risque", "question": "Quelle perte peut être absorbée?",
 "status": "violation", "checks": ["H4", "H7", "H8", "H14", "S1"],
 "unknowns": [], "not_covered": [],
 "facts": {...20.4 payload...},
 "narrative": {"mode": "llm|llm_retry|template", "segments": [...], "verify": {"passed": true, "failures": []}}}
```

- `status` = worst status among the section's checks for the checks the report is about
  (`checks_proposed` in flow B, `checks_current` in flow C), ranked
  `violation > warning > unknown > info > pass`. A section with no checks is `pass`.
- `checks` lists ids only; the full check objects stay in `checks_current` / `checks_proposed`,
  which gain a `factor` key (added by the report builder, hard.py and soft.py unchanged).
- **Consistency:** `report.verdict` (§7.5, computed from the checks) always equals the verdict derived
  from the section statuses (any `violation` -> incoherent; else any `warning` -> coherent_with_warnings;
  else coherent). `unknown` never changes the verdict (D3).

Section facts payload (the only thing the writer sees for that section). Numbers are pre-formatted
strings in `locale`, so the writer copies them:

```json
{"factor": "risque", "locale": "fr-CA", "question": "...", "status": "violation", "verdict": "incoherent",
 "intent": {"ticker": "NVDA", "target_weight": "10 %", "funding": "ZST.TO"} | null,
 "declared": {"risk.declared": "moderate", "risk.max_acceptable_drawdown_pct": "-12 %"},
 "derived": {"risk.effective": {"value": "low_moderate", "by": "rule:effective_risk",
             "because": ["risk.declared = moderate", "risk.capacity = low_moderate"]}},
 "checks": [{"id": "H8", "status": "violation", "value": "-13,4 %", "limit": "-12,0 %",
             "derived_from": ["limits.max_drawdown_pct", "risk.effective", "..."]}],
 "metrics": {"current": {"vol": "6,1 %"}, "proposed": {"vol": "9,8 %"}},
 "evidence_events": [{"event_id": "ev_robert_0007", "date": "2026-11-14", "text": "..."}],
 "unknowns": []}
```

- Only non-`pass` checks are listed. `evidence_events` only for checks that cite events (S1, S3).
- `derived[*].because` is one level of the `derived_from` / `limits_trace` chain, formatted.

## 20.5 Narration loop (N5 `explainer`, one call per section)

- Writer: N5 with one prompt `prompts/explainer.v<N>.md` (common rules + one guidance block per
  factor) and schema `schemas/explainer.json`:
  `{"segments": [{"kind": "fact|assumption|inference|unknown", "text": "<= 300 chars", "refs": ["H8", "metrics.proposed.vol", "ev_robert_0007"]}]}`,
  1 to 4 segments.
- Only sections with `status != pass` call the writer. `pass` sections use the template (no LLM).
- **Verifier** (`explain.verify_section(output, facts) -> list[str]`, deterministic, all must pass):
  - V1 refs: every ref resolves to a check id in `facts.checks`, a dotted key path in `facts`, or an
    `event_id` in `facts.evidence_events`; every segment has >= 1 ref.
  - V2 numcheck (§9.3 rules): every number in the text exists in `facts`.
  - V3 coverage: every check id in `facts.checks` with status `violation` or `warning` is referenced.
  - V4 advisory blocklist (§9.3, FR + EN).
  - V5 shape: 1-4 segments, each <= 300 chars; `inference` segments only when the section has an
    `llm:`-derived field or evidence events.
- Loop: call -> verify -> pass: `mode = llm`. Fail: call again with the failure list appended to the
  payload (`retry_failures`, so it is a distinct cache key) -> pass: `llm_retry`; fail:
  `template_section(facts)`, `mode = template`. Never more than 2 LLM calls per section.
- `template_section(facts)` is deterministic (same facts -> byte-identical) and always passes V1-V5.
- Report-level text stays deterministic: `explanation.headline` is a template from the verdict and the
  failing check ids; `explanation.segments` = the section segments in display order, each with a
  `factor` key; options (resizer, §8) and the disclaimer (§9.4) are template text, never LLM text.
  `explanation.mode` = the worst section mode (`template > llm_retry > llm`).
- Cost: at most 2 x (number of non-pass sections) LLM calls per report. Robert + NVDA: 3 sections.

## 20.6 Intake loop (P1)

- Each factor has `REQUIRED` profile paths. `intake.missing(profile, factor) -> list[str]` is a pure
  function. `intake.next_question(profile, factor)` returns a fixed template question for the first
  missing path (no LLM). The answer is an event; free-text answers go through the existing N2/N3
  extractors and the gate (§11). Stops when `missing` is empty or the user skips (the field gets
  `{"value": null, "by": "user:skipped"}` and the related checks report `unknown`).
- Showcase: Amira's "pas tout en tech" with no number -> contraintes asks for a max tech weight.

## 20.7 UI

Five factor cards in slide order (title, question, status chip, the declared vs derived facts, the
narrative with fact / assumption / inference / unknown tags, its checks with the `derived_from`
chain, `not_covered`). The verdict hero, cone and alternatives stay above the cards.

## 20.8 Tests

- `test_factors.py`: `CHECK_FACTOR` covers every id in §7.3 / §7.4 + G1, O1 exactly once; every
  factor's `FIELDS` path exists in the Robert fixture; Robert + NVDA 10% from ZST gives 5 sections
  in display order with statuses horizon `violation` (H9), risque `violation` (H4, H7, H8),
  contraintes `violation` (H3, H10), objectif `pass`, preferences `pass`; verdict consistency;
  `template_section` deterministic and passes V1-V5 for every section.
- `test_numcheck.py`: §15 as written.
- `test_explain.py`: verifier V1-V5 each rejects a crafted bad output; mock cache with a good
  response -> `llm`; bad then good -> `llm_retry`; bad twice -> `template`; `pass` sections make no
  LLM call; disclaimer is the fixed text.

## 20.9 Internal coherence: does the profile contradict itself? (D8)

Runs on the **profile alone** (no portfolio, no market data), after Flow A refresh and before Flow C.
Module `coherence_analyzer/coherence/internal.py`. Two layers; the rules run first.

**Layer 1: contradiction rules (code, deterministic).** `as_of` = `portfolio.as_of` (D1). Each rule reads
declared fields only and yields at most one finding, status `warning`:

| id | Fires when | fields |
|---|---|---|
| C1 | a goal in `objectives.goals` has `date` (YYYY-MM) later than `as_of` + `objectives.horizon_years` years (month precision); one finding per goal | `objectives.horizon_years`, `objectives.goals[i].date` |
| C2 | `risk.max_acceptable_drawdown_pct` is tighter than the `max_dd_pct` of the level one below `risk.declared`, or looser than the level one above (LIMITS_TABLE; no neighbour = no bound on that side) | `risk.declared`, `risk.max_acceptable_drawdown_pct` |
| C3 | `objectives.primary` in {`growth`, `aggressive_growth`} and `risk.declared` in {`low`, `low_moderate`} | `objectives.primary`, `risk.declared` |
| C4 | `preferences.asset_types_allowed` and `preferences.asset_types_refused` share an item | both |
| C5 | `risk.reaction_to_20pct_drop` = `buy_more` with `risk.declared` <= `low_moderate`, or `sell_all` with `risk.declared` >= `moderate_high` | both |

A missing field makes the rule skip (no finding, no error). Robert: C1 fires once (horizon 4 years from
2026-11 ends 2030-11; goal "Help children with a down payment" is dated 2032-01); C2-C5 do not fire.

**Layer 2: N6 `profile_consistency` (LLM, one call).** Finds what rules cannot express, mostly free text
against fields. Input payload (`n6_payload(profile)`): the **declared** fields only (plain values; no
`{value, by}` objects, no `limits`, no `llm:` fields), `values.notes`, and the ids + fields of the C-findings
already raised (so it does not repeat them). Output schema:
`{abstain: bool, findings: [{kind: "tension"|"contradiction", fields: [path, path, ...], quotes: [str, ...], reason: str}]}`.
The prompt asks for at most 5 findings, each between two or more fields, `reason` <= 200 chars, no numbers,
no advice. Prompt `llm/prompts/profile_consistency.v1.md`, schema `llm/schemas/profile_consistency.json`.

**Gate branch `profile_consistency`** (`gate.gate`, context carries `profile`): abstain -> kept:abstain.
Then every finding must satisfy: each path resolves in the profile to a declared value (not a `{value, by}`
object; list indices like `goals[1].date` allowed); >= 2 distinct paths; one quote per path, in order, and
each quote equals the value at its path (`str()` for scalars, or one element for a list), except for
`values.notes` whose quote must be an exact substring; `reason` <= 200 chars, contains no digit and no
`explain.ADVISORY_BLOCKLIST` phrase; <= 5 findings. Any failure -> `rejected:evidence` (paths/quotes) or
`rejected:schema` (rest) and **the whole output is dropped**. Accepted -> `{value: findings, ...stamp}`.

**Status and effect.** `contradiction` -> `warning`, `tension` -> `info`. N6 can never produce `violation`
(invariant 3) and nothing in this section feeds `limits` (invariant 2). A finding whose path set equals a
C-finding's path set is dropped (rules win). Internal coherence **does not change the portfolio verdict**:
it is reported as a separate block `profile_coherence = {status, findings}` where `status` is the worst
finding status or `pass`. Each finding carries a fixed clarification question (fr-CA / en-CA) chosen by
code from its source (one template per C-rule; one generic template for N6 that names the two first
fields), never written by the LLM (D7 point 1).

Finding shape: `{id: "C1".."C5" | "N6.<k>", source: "rule:C1" | "llm:profile_consistency", kind, status,
fields: [...], values: [...] (rules) | quotes: [...] (N6), reason: str | null, question: {fr-CA, en-CA}}`.

`internal.check_profile(profile, as_of, *, use_llm=True) -> {status, findings, llm: {gate_status, call_log} | null}`.
In mock mode a missing recording (`LLMCacheMiss`) leaves the rules' findings only and
`llm.gate_status = "skipped:cache_miss"`.

## 20.10 Parallel factor lanes (D8)

The five sections are independent once the shared computation (derive -> limits -> engine -> hard checks ->
`tag_checks`) is done. `explain.explain()` narrates the non-pass sections concurrently
(`concurrent.futures.ThreadPoolExecutor`, `max_workers = 5`); results are reassembled in display order, so the
output is identical to the sequential run. N6 runs as a sixth lane alongside. The aggregator stays code:
verdict = worst section status (D3), template headline, `profile_coherence` block, disclaimer.
