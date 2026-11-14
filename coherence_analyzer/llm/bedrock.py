"""Bedrock Converse wrapper: forced tool use, schema validation, retry, cache, mock (SPEC §6.1). P0 step 6."""
from __future__ import annotations

import hashlib
import json


class LLMOutputInvalid(Exception):
    pass


class LLMCacheMiss(Exception):
    pass


def cache_key(node: str, prompt_version: str, model_id: str, user_payload: dict) -> str:
    canon = json.dumps(user_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256("\x1f".join([node, prompt_version, model_id, canon]).encode()).hexdigest()


def call_tool(node: str, schema: dict, system: str, user_payload: dict, prompt_version: str) -> dict:
    """mock: read fixtures/llm_cache only (raise LLMCacheMiss). record: call + write fixtures/llm_cache.
    live: call with .cache/llm. Temperature 0, toolChoice forced to `node`, jsonschema-validate,
    retry once with validator message, then raise LLMOutputInvalid. Returns {output, call_log}."""
    raise NotImplementedError
