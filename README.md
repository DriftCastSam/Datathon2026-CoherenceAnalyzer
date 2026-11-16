# Compass

**Does your portfolio say what you want?** Investors state what they want once: a horizon, the risk they accept,
goals, values, sometimes a note like "not only tech". Then trade after trade, the portfolio drifts away from it and
nobody shows them. Compass is that mirror: it shows the investor, with their own numbers and their own words, where
the portfolio (or a trade they are about to make) no longer matches what they said they want. Then the decision is
theirs.

- **The AI listens.** Every move (questionnaire answer, note, question, proposed trade) becomes an event; Amazon
  Bedrock reads what it says about the investor.
- **A gate keeps only what it can prove.** Every AI claim must cite a real event or an exact quote of the user's notes,
  or it is rejected and the previous value stays.
- **Rules, not the AI, set the limits and check the portfolio.** AI-derived findings can only warn, never block.
- **No advice, no execution.** Compass describes the gap; it never says buy, sell or rebalance.

Example: Robert, 59, low-to-moderate risk, 4-year horizon, wants 10 % in NVDA funded from a bond fund. Compass shows
6 of the limits from Robert's own profile crossed, before and after, explained in plain language with every number
machine-checked.

**Live app:** https://d36xzk6fhvfvuw.cloudfront.net/app.html ·
[one-pager](docs/deliverables/one-pager.pdf) · [architecture](docs/deliverables/architecture-aws.pdf)

## Quick start

Python 3.11. Everything runs offline: `COHERENCE_ANALYZER_LLM=mock` replays recorded Bedrock responses from
`fixtures/llm_cache/`, so no AWS account is needed.

```
git clone https://github.com/DriftCastSam/Datathon2026-CoherenceAnalyzer.git && cd Datathon2026-CoherenceAnalyzer
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
COHERENCE_ANALYZER_LLM=mock pytest -q -n auto --dist loadfile                                            # full suite, ~1 min
COHERENCE_ANALYZER_STORE_DIR=.cache/store COHERENCE_ANALYZER_LLM=mock python -m coherence_analyzer.cli demo   # Robert + NVDA, end to end
COHERENCE_ANALYZER_LLM=mock python deploy/serve_local.py                                                   # the app at http://127.0.0.1:8000
```

Live Bedrock calls (`COHERENCE_ANALYZER_LLM=record`) need AWS credentials and `BEDROCK_MODEL_ID` in `.env`
(see `.env.example`). Deploying to AWS: [docs/aws-deploy.md](docs/aws-deploy.md).

## Disclaimer

Educational simulation with fictional profiles. This is not personalised financial advice. No transaction is executed.
