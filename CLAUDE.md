# CLAUDE.md: Portée

Read `SPEC.md` before any change. It is the source of truth. If code and spec disagree, raise it; don't silently pick one.

## Invariants (never break)
- `limits` is computed from `rule:` fields and user-declared values only. No `llm:` field may feed it (test: `tests/test_limits_no_llm_inputs.py`).
- LLM-derived checks produce `warning | info | unknown` only. Only hard checks produce `violation`.
- Every LLM output goes through `portee/gate.py`. Evidence must reference real `event_id`s or exact substrings of the user's notes.
- Every number in an explanation must exist in the facts payload (`explain.numcheck`). Otherwise retry once, then use the template.
- No buy/sell recommendations, no execution. Disclaimer text is fixed (SPEC §9.4).
- Never write rows to `data/meta/activity_tags.csv` from code or an LLM. Humans only, with a source URL.
- Don't change engine math without updating the golden test, and say why.

## Commands
```
pip install -r requirements.txt
PORTEE_LLM=mock pytest -q
PORTEE_LLM=mock python -m portee.cli demo
PORTEE_LLM=record python -m portee.cli demo   # needs AWS creds + BEDROCK_MODEL_ID
```

## Conventions
- Python 3.11, type hints, pure functions in `rules/` and `coherence/`.
- Weights: percent in `portfolio.json`, fractions (0–1) everywhere inside the code. Convert once in `data.py`/`intent.py`.
- Currency: CAD. USD assets are converted with month-end USDCAD (ffill).
- Prompt changes bump the version in the filename (`prompts/<node>.v<N>.md`) and re-record the mock cache.
- Every assumption (calibration, drift, fictional persona figure) goes in `README.md` → "Assumptions".
- Build order: SPEC §17. Finish P0 before touching P1.
