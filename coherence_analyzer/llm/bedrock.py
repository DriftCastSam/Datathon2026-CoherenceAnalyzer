"""Bedrock Converse wrapper: forced tool use, schema validation, retry, cache, mock (SPEC §6.1). P0 step 6.

Modes (``COHERENCE_ANALYZER_LLM``):
  mock    reads ``fixtures/llm_cache`` only; a miss raises ``LLMCacheMiss``. Never imports boto3.
  record  cache hit -> no call; miss -> Bedrock call, result written to ``fixtures/llm_cache``.
  live    cache hit -> no call; miss -> Bedrock call, result written to ``.cache/llm``.

boto3 is imported lazily, inside the call path, so mock mode and plain imports never need it.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from ..config import (AWS_REGION, BEDROCK_GUARDRAIL_ID, BEDROCK_GUARDRAIL_VERSION, BEDROCK_MODEL_ID,  # noqa: F401
                      COHERENCE_ANALYZER_LLM, LLM_CACHE_DIR, RUNTIME_CACHE_DIR)

# Model id stamped on hand-written cache entries, and used for the key when BEDROCK_MODEL_ID is unset in mock mode.
MOCK_MODEL_ID = "fixture:hand-written"
MODES = ("mock", "record", "live")
DEFAULT_MAX_TOKENS = 1024
MAX_TOKENS: dict[str, int] = {"behaviour_classifier": 1024, "intent_parser": 1024}   # per node (SPEC §6.1)


class LLMOutputInvalid(Exception):
    pass


class GuardrailIntervened(LLMOutputInvalid):
    """The Bedrock Guardrail blocked the explainer answer (stopReason 'guardrail_intervened'; SPEC §6.1, §9.3).
    Not retried and never cached. `call_log` carries the log of the blocked call (guardrail id / version,
    guardrail_intervened True), since call_tool returns nothing in that case."""

    def __init__(self, message: str, call_log: dict | None = None) -> None:
        super().__init__(message)
        self.call_log = call_log or {}


class LLMCacheMiss(Exception):
    pass


GUARDRAIL_NODES = ("explainer",)   # SPEC §6.1: the guardrail applies to the explainer call only


def _guardrail(node: str) -> tuple[str, str] | None:
    """(id, version) when the node is guarded and both settings are present, else None."""
    if node in GUARDRAIL_NODES and BEDROCK_GUARDRAIL_ID and BEDROCK_GUARDRAIL_VERSION:
        return BEDROCK_GUARDRAIL_ID, BEDROCK_GUARDRAIL_VERSION
    return None


def _canon(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def cache_key(node: str, prompt_version: str, model_id: str, user_payload: dict) -> str:
    return hashlib.sha256("\x1f".join([node, prompt_version, model_id, _canon(user_payload)]).encode()).hexdigest()


def input_hash(user_payload: dict) -> str:
    """`sha256:` of the node input as canonical JSON (same convention as the profile fixtures)."""
    return "sha256:" + hashlib.sha256(_canon(user_payload).encode()).hexdigest()


def _mode() -> str:
    mode = COHERENCE_ANALYZER_LLM
    if mode not in MODES:
        raise ValueError(f"COHERENCE_ANALYZER_LLM must be one of {MODES}, got {mode!r}")
    return mode


def _entry_path(directory: Path, node: str, key: str) -> Path:
    return Path(directory) / f"{node}.{key}.json"


def _read_entry(directory: Path, node: str, key: str) -> dict | None:
    p = _entry_path(directory, node, key)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def write_entry(directory: Path, node: str, prompt_version: str, model_id: str, user_payload: dict,
                output: dict, call_log: dict) -> Path:
    """One file per call: ``<node>.<cache_key>.json``. Also used by scripts/handwrite_llm_cache.py."""
    key = cache_key(node, prompt_version, model_id, user_payload)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    doc = {"node": node, "prompt_version": prompt_version, "model_id": model_id, "cache_key": key,
           "input_hash": input_hash(user_payload), "user_payload": user_payload, "output": output,
           "call_log": call_log}
    p = _entry_path(directory, node, key)
    p.write_text(json.dumps(doc, sort_keys=True, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def _result(output: dict, log: dict) -> dict:
    return {"output": copy.deepcopy(output), "call_log": log}


def _hit(entry: dict, node: str, prompt_version: str, model_id: str, user_payload: dict) -> dict:
    stored = entry.get("call_log", {})
    log = {"node": node, "model_id": entry.get("model_id", model_id), "prompt_version": prompt_version,
           "input_hash": input_hash(user_payload), "cache_hit": True, "latency_ms": 0,
           "tokens_in": stored.get("tokens_in", 0), "tokens_out": stored.get("tokens_out", 0)}
    return _result(entry["output"], log)


def _tool_input(response: dict, node: str) -> dict | None:
    for block in response.get("output", {}).get("message", {}).get("content", []):
        tu = block.get("toolUse")
        if tu and tu.get("name") == node:
            return tu.get("input")
    return None


def _converse(client: Any, model_id: str, node: str, schema: dict, system: str, messages: list[dict],
              guardrail: tuple[str, str] | None = None) -> dict:
    extra: dict[str, Any] = {}
    if guardrail:
        extra["guardrailConfig"] = {"guardrailIdentifier": guardrail[0], "guardrailVersion": guardrail[1],
                                    "trace": "enabled"}
    return client.converse(
        modelId=model_id,
        system=[{"text": system}],
        messages=messages,
        toolConfig={"tools": [{"toolSpec": {"name": node, "inputSchema": {"json": schema}}}],
                    # D9: the team's model rejects a forced tool ("tool" / "any"); with one tool and `auto`, a reply
                    # without the toolUse block is invalid output (one retry with the error, then LLMOutputInvalid).
                    "toolChoice": {"auto": {}}},
        # No temperature (docs/spec-decisions.md D6): the model rejects it. Reproducibility comes from
        # the input-hash cache (record/live) and the recorded mock cache.
        inferenceConfig={"maxTokens": MAX_TOKENS.get(node, DEFAULT_MAX_TOKENS)},
        **extra,   # guardrailConfig: explainer only, and only when id and version are both set (SPEC §6.1)
    )


def call_tool(node: str, schema: dict, system: str, user_payload: dict, prompt_version: str) -> dict:
    """mock: read fixtures/llm_cache only (raise LLMCacheMiss). record: call + write fixtures/llm_cache.
    live: call with .cache/llm. No temperature (D6), toolChoice auto with the one `node` tool (D9), jsonschema-validate,
    retry once with validator message, then raise LLMOutputInvalid. Returns {output, call_log}."""
    mode = _mode()
    model_id = BEDROCK_MODEL_ID
    cache_dir = Path(LLM_CACHE_DIR if mode in ("mock", "record") else RUNTIME_CACHE_DIR)

    if mode == "mock":
        # Hand-written entries are keyed with MOCK_MODEL_ID; a configured real model id is tried first.
        for mid in dict.fromkeys([model_id or MOCK_MODEL_ID, MOCK_MODEL_ID]):
            entry = _read_entry(cache_dir, node, cache_key(node, prompt_version, mid, user_payload))
            if entry is not None:
                return _hit(entry, node, prompt_version, mid, user_payload)
        raise LLMCacheMiss(f"{node} {prompt_version}: no recorded response in {cache_dir} for this input "
                           f"(model {model_id or MOCK_MODEL_ID}); record it with COHERENCE_ANALYZER_LLM=record")

    if not model_id:
        raise RuntimeError(f"BEDROCK_MODEL_ID is required in {mode} mode")
    entry = _read_entry(cache_dir, node, cache_key(node, prompt_version, model_id, user_payload))
    if entry is not None:
        return _hit(entry, node, prompt_version, model_id, user_payload)

    import boto3   # lazy: only record/live reach Bedrock

    client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    guardrail = _guardrail(node)
    guard_log: dict[str, Any] = ({"guardrail_id": guardrail[0], "guardrail_version": guardrail[1],
                                  "guardrail_intervened": False} if guardrail else {})
    validator = Draft202012Validator(schema)
    messages: list[dict] = [{"role": "user", "content": [{"text": _canon(user_payload)}]}]
    tokens_in = tokens_out = 0
    started = time.perf_counter()
    output: dict | None = None
    for attempt in (1, 2):
        response = _converse(client, model_id, node, schema, system, messages, guardrail)
        usage = response.get("usage", {})
        tokens_in += int(usage.get("inputTokens", 0))
        tokens_out += int(usage.get("outputTokens", 0))
        if guardrail and response.get("stopReason") == "guardrail_intervened":
            blocked = {"node": node, "model_id": model_id, "prompt_version": prompt_version,
                       "input_hash": input_hash(user_payload), "cache_hit": False,
                       "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                       "tokens_in": tokens_in, "tokens_out": tokens_out,
                       **{**guard_log, "guardrail_intervened": True}}
            raise GuardrailIntervened(f"{node}: blocked by Bedrock guardrail {guardrail[0]} "
                                      f"(version {guardrail[1]})", blocked)
        candidate = _tool_input(response, node)
        if candidate is None:
            problem = f"no toolUse block named {node!r} in the response"
        else:
            errors = sorted(validator.iter_errors(candidate), key=lambda e: [str(p) for p in e.absolute_path])
            problem = "; ".join(e.message for e in errors)
        if not problem:
            output = candidate
            break
        if attempt == 2:
            raise LLMOutputInvalid(f"{node}: output failed schema validation twice: {problem}")
        # Retry once: replay the model's tool call, then the validator message as a user turn (toolResult).
        tool_use_id = None
        for block in response.get("output", {}).get("message", {}).get("content", []):
            if block.get("toolUse"):
                tool_use_id = block["toolUse"].get("toolUseId", "retry")
        if tool_use_id is None:
            # D9 (toolChoice auto): a text-only reply has no tool call to answer with a toolResult; Bedrock rejects a
            # toolResult whose id matches no toolUse, so the correction goes in as a plain user message.
            feedback = {"role": "user", "content": [{"text": f"{problem}. Answer only by calling the tool "
                                                             f"{node!r} with a valid input."}]}
        else:
            feedback = {"role": "user", "content": [{"toolResult": {
                "toolUseId": tool_use_id, "status": "error",
                "content": [{"text": f"Schema validation failed: {problem}. Call the tool again with a valid input."}]}}]}
        messages = messages + [response["output"]["message"], feedback]
    assert output is not None
    log = {"node": node, "model_id": model_id, "prompt_version": prompt_version,
           "input_hash": input_hash(user_payload), "cache_hit": False,
           "latency_ms": round((time.perf_counter() - started) * 1000, 1),
           "tokens_in": tokens_in, "tokens_out": tokens_out, **guard_log}
    write_entry(cache_dir, node, prompt_version, model_id, user_payload, output, log)
    return _result(output, log)
