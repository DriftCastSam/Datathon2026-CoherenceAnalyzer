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
