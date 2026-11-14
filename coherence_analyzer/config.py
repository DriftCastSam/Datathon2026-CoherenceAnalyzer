"""Env + constants (SPEC §6.1, §7, §9.4). Single place for thresholds, seeds and paths."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
# Load .env before any os.getenv below. Real environment variables win (override=False),
# so `COHERENCE_ANALYZER_LLM=record python -m coherence_analyzer.cli demo` still overrides the file.
load_dotenv(ROOT / ".env", override=False)
DATA_DIR = Path(os.getenv("COHERENCE_ANALYZER_DATA", ROOT / "data" / "starter"))
META_DIR = ROOT / "data" / "meta"
FIXTURES_DIR = ROOT / "fixtures"
LLM_CACHE_DIR = FIXTURES_DIR / "llm_cache"   # recorded responses (mock/record)
# live-call cache; the Lambda (P2.15) points it at /tmp, its package directory being read-only
RUNTIME_CACHE_DIR = Path(os.getenv("COHERENCE_ANALYZER_RUNTIME_CACHE_DIR", ROOT / ".cache" / "llm"))
PROMPTS_DIR = Path(__file__).parent / "llm" / "prompts"
LLM_SCHEMAS_DIR = Path(__file__).parent / "llm" / "schemas"
SCHEMAS_DIR = Path(__file__).parent / "schemas"

# LLM / AWS
COHERENCE_ANALYZER_LLM = os.getenv("COHERENCE_ANALYZER_LLM", "mock")        # mock | record | live
COHERENCE_ANALYZER_STORE = os.getenv("COHERENCE_ANALYZER_STORE", "local")   # local | s3
COHERENCE_ANALYZER_S3_BUCKET = os.getenv("COHERENCE_ANALYZER_S3_BUCKET", "")   # required when STORE=s3
COHERENCE_ANALYZER_S3_PREFIX = os.getenv("COHERENCE_ANALYZER_S3_PREFIX", "")   # optional key prefix
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "")
BEDROCK_GUARDRAIL_ID = os.getenv("BEDROCK_GUARDRAIL_ID", "")
BEDROCK_GUARDRAIL_VERSION = os.getenv("BEDROCK_GUARDRAIL_VERSION", "")

# Engine defaults (SPEC §5)
ENGINE_N = 5000
ENGINE_N_CHEAP = 1000
ENGINE_BLOCK = 12
ENGINE_SEED = 7

# Gate / soft checks (SPEC §7.4, §11)
WARN_MIN_CONF = 0.5
BEHAVIOUR_MAX_CONF = 0.8
BEHAVIOUR_MAX_STEP = 1          # levels per run
KNOWLEDGE_LLM_MAX_DELTA = 5     # points per update
QUESTION_EWMA_ALPHA = 0.3
QUESTION_QUALITY_MAX_CONF = 0.8     # cap on the code-computed question_quality confidence (README Assumptions)

# N2 question_rubric is opt-in (docs/spec-decisions.md D7 point 3: deferred to P2). Off: Flow A calls no N2 and
# knowledge.score uses the declared/quiz fields plus whatever question_quality is already stored.
N2_QUESTION_RUBRIC = os.getenv("COHERENCE_ANALYZER_N2", "").strip().lower() in ("1", "true", "on", "yes")

# Resizer (SPEC §8)
RESIZER_STEP_PP = 0.5

DISCLAIMER = {
    "fr-CA": "Simulation éducative avec des profils fictifs. Ceci n'est pas un conseil financier "
             "personnalisé. Aucune transaction n'est exécutée.",
    "en-CA": "Educational simulation with fictional profiles. This is not personalised financial "
             "advice. No transaction is executed.",
}
