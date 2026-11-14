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

## D6. No `temperature` in Bedrock calls (SPEC §6.1, inv. 8)
The team's Claude model on Bedrock rejects `temperature` ("deprecated for this model"). `call_tool`
does not send it. Reproducibility rests on the input-hash cache (record/live) and the recorded mock
cache, which is what the tests run on. Record the model id in every audit record.
