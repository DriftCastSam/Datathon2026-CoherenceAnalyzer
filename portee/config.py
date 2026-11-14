"""Env + constants (SPEC §6.1, §7, §9.4). Single place for thresholds, seeds and paths."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("PORTEE_DATA", ROOT / "data" / "starter"))
META_DIR = ROOT / "data" / "meta"
FIXTURES_DIR = ROOT / "fixtures"
LLM_CACHE_DIR = FIXTURES_DIR / "llm_cache"   # recorded responses (mock/record)
RUNTIME_CACHE_DIR = ROOT / ".cache" / "llm"  # live-call cache
PROMPTS_DIR = Path(__file__).parent / "llm" / "prompts"
LLM_SCHEMAS_DIR = Path(__file__).parent / "llm" / "schemas"
SCHEMAS_DIR = Path(__file__).parent / "schemas"

# LLM / AWS
PORTEE_LLM = os.getenv("PORTEE_LLM", "mock")        # mock | record | live
PORTEE_STORE = os.getenv("PORTEE_STORE", "local")   # local | s3
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

# Resizer (SPEC §8)
RESIZER_STEP_PP = 0.5

DISCLAIMER = {
    "fr-CA": "Simulation éducative avec des profils fictifs. Ceci n'est pas un conseil financier "
             "personnalisé. Aucune transaction n'est exécutée.",
    "en-CA": "Educational simulation with fictional profiles. This is not personalised financial "
             "advice. No transaction is executed.",
}
