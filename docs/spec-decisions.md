# Spec decisions (team-approved, 2026-11-14)

Resolutions of SPEC.md ambiguities. SPEC.md is human-edited only; until these are folded into it,
this file is authoritative where it is more specific than SPEC.md.

## D1. Reference date ("today") = `portfolio.as_of`
Every rule that needs the current date uses `portfolio.as_of` (Robert: 2026-11-12), never the wall clock:
`objectives.effective_horizon_years` (months until first planned withdrawal, month granularity:
2026-11 -> 2030-06 = 43 months = 3.6 y), the capacity rule "withdrawals within 24 months", and N1's
"last 30 days" event window. Record the date used in the audit record. (SPEC §7.1, §6.2 N1, inv. 8)

## D2. Resizer: trust the engine (SPEC §8, §15 test_resizer)
On the starter data, Robert + NVDA funded from ZST.TO at 5% gives `p5_T` = 345 618 CAD < floor 360 000,
so H9 binds before H4. The resizer runs exactly as §8 describes (cheap scan, then engine checks with
binary search). `test_resizer.py` asserts the value the engine actually produces, with the binding check
it reports (expected: below 5%, binding H9 for funding from ZST.TO), recorded as golden values. Do not
skip-mark it. This replaces the "5.0% binding H4" expectation in §8/§15 and the skip allowance in the
P0.5 task note. Measured context: Robert + NVDA 10% also fails H8 (mdd -13.4% vs -12%) and H10
(Information Technology risk share 34.7% vs 30%) in addition to H3/H4.

## D3. Three verdicts only; unknowns never change the verdict (SPEC §7.5 wins over §15)
`verdict` stays `coherent | coherent_with_warnings | incoherent`. Unknowns are always listed in
`unknowns` ("what we can't verify") whatever the verdict. A Mateo report with only unknowns is
`coherent` with a non-empty `unknowns` list.

## D4. Data window stays 58 months (`iloc[2:-1]`, 2022-01 -> 2026-10)
No engine math change; golden test untouched. The dropped (full) Dec 2021 month is documented in
README "Assumptions".

## D5. Five-factor architecture, option C: rules decide, agents explain, code checks the agents
Adopted 2026-11-14. Full text: `docs/spec-factors.md` (paste-ready as SPEC.md §20). It wins over
§9 (one explanation call) and §13 (report gains `sections`, checks gain `factor`) and replaces
§17 steps 7-8 with: P0.7 factors + sections + template + numcheck, P0.8 per-section narration loop
(N5 + verifier V1-V5 + one retry + template), P0.9 pipeline flows B/C + cli demo. Intake loop is P1.
The analysis layer (§7, §8) is unchanged: no LLM decides a status, a verdict or a number.

## D6. No `temperature` in Bedrock calls (SPEC §6.1, inv. 8). Approved 2026-11-14
The team's Claude model on Bedrock rejects `temperature` ("deprecated for this model"), so
`call_tool` does not send it. Invariant 8 now reads: reproducibility rests on the input-hash cache
(record/live) and the recorded mock cache, which is what the tests and the demo run on, plus the fixed
bootstrap seed. Record the model id in every audit record. Verdicts and numbers never come from the
model, so wording variation cannot change a result.

## D7. Product focus: profile in, portfolio divergences out (Flow C is the headline)
Adopted 2026-11-14. Wins over SPEC §4 (Flow B as "the main demo"), §6.2 N1/N2/N4 priority, §12 UI list,
§16 persona matrix and §17 build order. D5 (five factors, narration loop) and the analysis layer (§7, §8 rules,
limits, hard checks) are unchanged.

**Goal.** Build the user's profile, then show where the *current* portfolio diverges from it, factor by factor,
and explain each divergence through the profile fields it comes from. Typical divergences: low risk tolerance
but concentrated tech/AI stocks (H3, H7, H8, H10, H13); a house down payment in 3 years but a long-horizon
equity portfolio (`planned_withdrawals` -> effective horizon -> capacity -> H4, H9); an excluded activity
held in the portfolio (H11).

**P0 pipeline (offline, mock mode):**
```
intake form (per factor, deterministic) + free-text notes
-> N3 values_parser -> gate (§11) -> user approves/rejects each parsed rule
-> Flow A refresh (rules only; no N1/N2)
-> Flow C review: engine.analyse(current) -> hard checks + S2/S4 -> verdict
-> five factor sections (§20.4) -> narration loop per non-pass section (§20.5) -> report + audit
```

1. **Intake = form + notes.** One fixed form per factor; questions and the `REQUIRED` checklist are code
   (§20.6), no LLM writes questions. Free text (`values.notes`) goes through N3 and the gate; parsed rules start
   `approved_by_user: false`, and only approved `exclude_activity` / `max_theme_weight` rules reach `limits`
   (invariant 2 unchanged). A parsed theme rule with no number (Amira's "pas tout en tech") makes the form ask for
   the number. Intake moves from P1 to P0.
2. **Narration is the end of the pipeline.** Each non-pass section explains the divergence *in relation to the
   profile*: which declared or derived field sets the limit, walking `derived_from` one level per segment where
   it helps (e.g. "down payment in June 2029 -> horizon 2.6 years -> capacity low -> minimum 30 % in bonds;
   the portfolio holds 12 %"). Writer N5 + verifier V1-V5 + one retry + template, exactly as §20.5.
3. **Deferred to P2 (code kept, not wired into the P0 pipeline):** Flow B (N4 `intent_parser`, B2 confirm form,
   trade diff), the resizer (§8), N1 `behaviour_classifier`, N2 `question_rubric`, and soft checks S1 and S3
   (they need N1). Their built code and frozen tests stay green; nothing is deleted. S2 stays, with
   `knowledge.score` computed from declared/quiz fields only while N2 is off.
4. **Data: the current starter universe only (31 assets).** No tickers are added. Divergence examples must use
   assets that exist (e.g. fossil fuels: XOM, CNQ.TO, ENB.TO; tech concentration: NVDA, QQQ, SHOP.TO). H11 needs
   sourced, human-written rows in `data/meta/activity_tags.csv` (still humans only); until they exist, H11 reports
   `unknown`, which is the intended "says what it doesn't know" behaviour.
5. **Robert's demo portfolio.** Measured on the starter data: Robert's current portfolio (`engine.ROBERT`) is
   `coherent` (H6, H10 `unknown` only); the divergences all come from the NVDA trade (`engine.ROBERT_NVDA`: H3, H4,
   H7, H8, H9, H10 violations). For a review demo, add `fixtures/robert/portfolio_review.json` holding the
   `ROBERT_NVDA` weights ("already bought NVDA"). `fixtures/robert/portfolio.json` stays as is, because frozen
   tests read it.

**Build order (replaces P0.9, adds P0.10):** P0.7 and P0.8 unchanged. P0.9 = pipeline Flow A (rules + N3) and
Flow C + `cli review` / `cli demo` (Robert review end-to-end offline, five sections, narration). P0.10 = intake
form + N3 schema/prompt + rule approval. Flow B wiring moves to P2.

## D9. `toolChoice` auto, not forced (SPEC §6.1). Approved 2026-11-14
The team's model (`us.anthropic.claude-sonnet-5-5`) rejects a forced tool: "tool_choice: type "tool" and
"any" are not supported for this model" (first `COHERENCE_ANALYZER_LLM=record` run). `call_tool` sends
`toolChoice: {"auto": {}}` with the node's single tool. Nothing else changes. A reply without the toolUse
block named after the node is invalid output: it gets one retry with the error, then `LLMOutputInvalid`, and
the extractor or narration falls back (gate keeps the old field, or the section uses the template). Cache keys
do not include `toolChoice`, so recorded entries stay valid.

## D8. Internal coherence check + parallel factor lanes (spec-factors §20.9, §20.10)
Requested by the team 2026-11-14 (whiteboard: isolate objectifs / risques / contraintes / préférences to
reduce context and run them in parallel, combine at the end, and add an LLM check for contradictions inside
a profile). Rule list C1-C5, N6 schema and gate details are a first proposal; change them here.

1. **Internal coherence** = contradiction rules C1-C5 (code) + N6 `profile_consistency` (LLM, gated). Profile
   only; never `violation`; never feeds `limits`; does not change the portfolio verdict. Shown as its own block
   with fixed clarification questions that feed the intake form.
2. **Parallel lanes.** Narration of the non-pass sections runs concurrently; output identical to sequential.
3. **Five factors kept.** Horizon stays its own lane (it owns H9); folding it into Objectif is a UI choice.

## D10. Describe, never prescribe (SPEC inv. 7, §7.3 H7, §8, §9.1-9.3, §13, §15, §18). Approved 2026-11-14
The product is informative only. This entry wins over SPEC.md until a human folds the prepared patch into it.
1. Invariant 7 widened: no recommendation of any kind, not only buy/sell. Nothing proposes a trade, ticker,
   funding source or size the user did not state, ranks choices, or presents a "next step".
2. Resizer (§8) evaluates the user's own funding only. The `pro_rata_*` and class funding modes it tried on its
   own are dropped (they stay valid as funding the user states in an intent). One result, stated as a fact in
   template text: "this trade crosses your limit H_ above X %". Report key `alternatives` becomes `headroom`
   (null | object); explanation segment kind `option` is removed; `test_resizer.py` checks the user's funding only.
3. H7 below-band info states value and bound only ("more conservative than your profile needs" removed).
4. Narration prompt rules and the V4 blocklist cover suggestions ("consider", "you could", "vous pourriez",
   "envisage", "rebalance", "next step", ...), not only buy/sell imperatives; matching is case- and
   accent-insensitive. Stating a measured effect of the user's own trade stays allowed.

## D11. Numeric note rules apply at once as provisional limits, capped at warning (SPEC inv. 1-3, §6.2 N3, §7.2, §7.4 S4). Approved 2026-11-15 (owner)
A clear numeric condition the user wrote ("max 20 % in tech") should not wait for a click to count; err on strictness,
and let the user correct a misreading.
1. A gated N3 rule with a number (`max_theme_weight` with `max_pct`) is promoted into `limits` at once as a
   **provisional** limit: `limits.max_theme_weight[theme]` carries `provisional: true`, and `limits_trace` names
   `values.parsed_notes[provisional]` (distinct from `[approved_by_user]`).
2. A check against a provisional limit is capped at `warning` (never `violation`), so invariant 3 holds: an LLM reading
   alone never makes a portfolio `incoherent`. The UI shows it flagged as read from the user's notes, with the quote.
3. User approval makes it a full-strength limit (`provisional` gone, breach = `violation`); rejection removes it.
4. Rules without a number (`exclude_activity`, theme rule with no `max_pct`) are unchanged: approval click, or the
   intake asks for the number. S4 keeps reminding about unapproved rules.
5. Invariant 1 is amended accordingly: limits are fed by `rule:` fields, user-declared values, and gated provisional
   N3 numeric rules (flagged, capped). CLAUDE.md and `tests/profile/test_limits_no_llm_inputs.py` need the owner's update.

## D12. "Today" = the real run date, recorded; fixtures keep a pinned date (replaces D1 for live use). Approved 2026-11-15 (owner)
1. Every rule that needs the current date (effective horizon, "withdrawals within 24 months", N1's 30-day window, G1
   goal dates) uses the run date in the user's time zone. The audit record stores `reference_date` and its source.
2. Reproducibility: a run can be replayed with the recorded `reference_date`. Fixtures, the demo and the tests pin it
   (`COHERENCE_ANALYZER_TODAY`, default for persona fixtures = `portfolio.as_of`), so goldens do not drift.
3. The market data window (D4) is unchanged; `portfolio.as_of` stays the valuation date of the holdings.

## D13. The trade flow asks nothing: a breach is a plain statement with facts and the user's own words (SPEC §4 B2-B9, §8, §9, §18, D10). Approved 2026-11-15 (owner)
To avoid an "AI chat" feel, nothing after a trade asks the user anything: no follow-up prompt, no action buttons, no
Q&A (§18 "no chatbot" stands; questions the user types stay events scored by N2, never answered).
1. Status is unchanged: a hard limit the trade crosses is still a `violation` (verdict `incoherent`).
2. Wording only: each crossed limit is one templated statement, FR and EN, of the form
   EN "Making this trade takes you outside your profile: <limit> (<limit value>). Facts: <value after the trade>
   [, <value before>]. From what you told us: '<quote>' (<source>)."
   The quote part appears only when the limit traces to the user's own words (a parsed note rule or an event); every
   number comes from the facts (numcheck). The §8 headroom sentence stays as the one figure about the trade's size.
3. The intake (missing profile fields) still asks its fixed questions; this decision covers the trade flow only.
4. AI failure fallback is confirmed as is: N5 retries once, then the deterministic template (math only).

## D14. A trade that worsens an existing breach counts as crossing it (SPEC §8 ambiguity). Approved 2026-11-15 (owner)
Confirms the P3.RESIZE reading: in the headroom, a limit already breached on the current portfolio binds when the trade
makes it worse; an unchanged or improved breach does not.

## D15. Deferred investment start (`objectives.start_date`). Approved 2026-11-15 (owner)
1. New optional user-declared field `objectives.start_date` (YYYY-MM). Absent or in the past = starts now.
2. Horizon end = start date + `horizon_years` ("from January 2027 for 4 years" ends January 2031; "4 years from now"
   ends 4 years after the reference date of D12). The effective horizon still runs from the reference date to
   min(horizon end, first planned withdrawal).
3. The intake asks for it as an optional question (empty answer = starts now). Not an input of any `llm:` field.

## UI note (2026-11-15)
Dark-cockpit presentation and the display of `unknown` are deferred: a colleague is revamping the UI.

## D16. Two fixed disclaimers: seed personas vs user-made profiles (SPEC §9.4). Approved 2026-11-15 (owner)
Seed persona profiles keep the §9.4 text ("... avec des profils fictifs" / "... with fictional profiles"). A profile
created by a user (`meta.origin = "user"`, set by POST /profiles; seeds are `"seed"`) gets the same text without the
fictional-profiles clause: FR « Simulation éducative. Ceci n'est pas un conseil financier personnalisé. Aucune
transaction n'est exécutée. » / EN "Educational simulation. This is not personalised financial advice. No transaction
is executed." Both live in `config.py`, never LLM-written; every report carries the one that matches its profile.

## D17. Holdings are typed in by the user (SPEC §18 stands). Approved 2026-11-15 (owner)
Real users enter their own weights and value (`PUT /profiles/{id}/portfolio`). No bank connection, no account or CSV
import; nothing is executed.

## D18. Live LLM model = Claude Haiku 5.5 on Bedrock. Approved 2026-11-15 (owner)
Live and record runs use `us.anthropic.claude-haiku-5-5`. Template fallbacks (D13 point 4) are accepted as the cost of
the smaller model. The mock tests keep reading the hand-written cache entries (`fixture:hand-written`). This governs
the offline recorded demo (`fixtures/llm_cache`, `cli demo`, see README "Real Bedrock recording"), which is
unaffected by the addendum below.

**Addendum, 2026-11-15:** the deployed `ApiFunction`/`FlowBFunction` Lambdas (live AWS account, not the offline
demo above) run `us.anthropic.claude-sonnet-5-5` instead. Haiku 5.5 needs an AWS Marketplace model subscription
(`aws-marketplace:ViewSubscriptions`/`Subscribe`) this sandbox account's role cannot grant without widening IAM
scope beyond `AppAccessPolicy`'s existing `bedrock:InvokeModel`-only grant; Sonnet 5.5 needed no such subscription
and was already the CFN template's `BedrockModelId`/`FoundationModelId` default. No code depends on which Claude
generation is live; `AppAccessPolicy`'s resource list and `BEDROCK_MODEL_ID` just need to agree, which they do.

## Scope cut for the demo (2026-11-15, owner)
The demo runs on the three persona fixtures only (the mock data provided). Out of scope, code to be removed:
- The intake questionnaire and user-created profiles (`intake.py`, intake routes, `POST /profiles`, `PUT /profiles/{id}/portfolio`,
  skipped-answer and no-portfolio handling, the intake and new-user UI). §20.6 and D7 point 1 no longer apply.
- AWS extras: the Step Functions version of Flow B (§17 item 15), the Lambda handler, the S3 store backend (§14 S3 part).
  Bedrock + Guardrail stay.
- Analytics extras: rate-stress block (§17 item 16), ETF sector look-through (§17 item 13; its data file was never provided),
  the what-if precompute and `design.html`.
- D12 (real-date today) and D15 (start date) are deferred: personas keep `portfolio.as_of` (D1 applies again).
- D16 keeps only the seed text (there are no user-made profiles); D17 is moot.
In scope and kept: engine, rules + limits, hard / soft checks, gate, N3 / N6 (and N1 / N4 for Flow B), the narration loop
(verifier + retry + template), Flow B trade + headroom (D10, D14) with the D13 statements, D11 provisional limits, the
correction loop, the static dashboard, the build loop.

## D19. ETF look-through back in scope, with country and single-company exposure. Approved 2026-11-15 (owner)
Reverses the "ETF sector look-through" line of the scope cut above (the rest of the cut stands).
1. Data: `data/meta/etf_sector_weights.csv` (SPEC §3.2), plus two new files with the same sourcing rules (every row
   has `source_url` and the issuer's holdings date `as_of`; weights are fractions):
   `etf_country_weights.csv` (`etf, country, weight, source_url, as_of`) and
   `etf_top_holdings.csv` (`etf, holding_ticker, holding_name, weight, source_url, as_of`, top holdings only).
   The owner allows these three files to be fetched from issuer pages by an agent and **reviewed by a human** before
   the demo (SOURCES.md records who fetched, when, and the review). `activity_tags.csv` stays human-only (inv. 6).
2. Engine output gains `exposure` (`coherence_analyzer/exposure.py`, pure): per dimension (sector, country, asset
   class with equity ETFs counted as stocks, listing currency) and per single company, each figure split into the
   holdings it comes from (`direct` or via an ETF). An ETF with no rows keeps its own bucket and is listed in
   `uncovered`; single-company figures are lower bounds (`partial: true`, top holdings only).
3. `exposure` is descriptive (D10): no status, no limit, never in the verdict, no LLM reads it. Checks keep reading
   `groups` (`groups.sector` is split by the sector file as SPEC §5 already says; `groups.country` stays per listing).
4. The engine golden test runs with an empty meta dir: v0 has no look-through, and the split is data, not math.

## D20. User-made profiles and the intake back in scope. Approved 2026-11-15 (owner)
Reverses the first bullet and the D16 line of the scope cut above (the rest of the cut stands, as amended by D19). The
team's demo app (`deploy/site/app.html`, branch `feat/site-e2e-flow`) builds a profile in conversation from the intake
API, so the demo needs it.
1. In scope again: `intake.py`, the intake routes, `POST /profiles`, `PUT /profiles/{id}/portfolio`, skipped-answer and
   no-portfolio handling, and the intake and new-user UI. §20.6 and D7 point 1 apply again.
2. D16 stands as approved: two fixed disclaimers, the seed text for persona profiles and the user text for profiles with
   `meta.origin = "user"`. D17 stands.
3. The queued removal task P7.CUTUSERS is dropped; its oracle tests (branch `loop/p7.cutusers`) are not merged.
