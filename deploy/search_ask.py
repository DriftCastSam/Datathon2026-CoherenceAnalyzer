#!/usr/bin/env python3
"""Prototype: "Search and ask" free text -> the search screen's own filters (+ tickers the user named).

The model maps the user's words to the fixed filter vocabulary of `deploy/site/js/screens/search.js` and to tickers
of the universe the user mentioned by name or ticker. It never sees the profile, never ranks, never picks: the reply
text is written by the client from the applied filters, and every value is validated against the allowed enums.
"""
from __future__ import annotations

from typing import Any

from coherence_analyzer import config, data

CLS = ["etf", "equity", "etf_equity", "etf_bond", "etf_commodity", "crypto"]
MKT = ["us", "ca", "cx"]
SEC = ["tech", "fin", "nrg", "hea", "con", "utl", "ind", "com", "bnd", "brd", "gld", "cry"]
CCY = ["CAD", "USD"]

SYSTEM = """You translate an investor's search request into filters for a fixed asset list. You never recommend,
rank or choose assets: you only map the words the user wrote to filters, and to tickers the user explicitly named
(by ticker or company/fund name). Never add a ticker the user did not name. Filter keys:
cls (asset class): etf=any ETF, equity=single stocks, etf_equity=equity ETF, etf_bond=bond ETF, etf_commodity=gold ETF, crypto
mkt (market): us=United States, ca=Canada (TSX), cx=crypto 24/7
sec (sector): tech, fin (banks, insurance), nrg (energy, oil), hea (health), con (consumer), utl (utilities),
  ind (industrials), com (communication), bnd (bonds), brd (broad market index), gld (gold), cry (crypto)
ccy (listing currency): CAD, USD
mode: "add" when the user adds to the current filters ("also", "and add"), otherwise "replace".
clear: true only if the user asks to clear/reset. If nothing maps, return empty lists.
Call the tool `search_filters` exactly once."""


def _schema(tickers: list[str]) -> dict:
    arr = lambda enum: {"type": "array", "items": {"type": "string", "enum": enum}}  # noqa: E731
    return {"type": "object", "required": ["cls", "mkt", "sec", "ccy", "tickers", "mode", "clear"],
            "properties": {"cls": arr(CLS), "mkt": arr(MKT), "sec": arr(SEC), "ccy": arr(CCY),
                           "tickers": arr(tickers), "mode": {"type": "string", "enum": ["add", "replace"]},
                           "clear": {"type": "boolean"}}}


def parse(text: str) -> dict[str, Any]:
    """Returns {cls, mkt, sec, ccy, tickers, mode, clear}, each value validated. Raises RuntimeError without an LLM."""
    if config.COHERENCE_ANALYZER_LLM == "mock" or not config.BEDROCK_MODEL_ID:
        raise RuntimeError("llm not configured")
    import boto3
    assets = data.load_assets()
    universe = [f"{t} = {r['name']} ({r['asset_class']}, {r['market']}, {r['sector']})" for t, r in assets.iterrows()]
    tickers = list(assets.index)
    client = boto3.client("bedrock-runtime", region_name=config.AWS_REGION)
    resp = client.converse(
        modelId=config.BEDROCK_MODEL_ID, system=[{"text": SYSTEM}],
        messages=[{"role": "user", "content": [{"text": "Asset list:\n" + "\n".join(universe)
                                                + "\n\nUser request: " + text[:500]}]}],
        toolConfig={"tools": [{"toolSpec": {"name": "search_filters", "inputSchema": {"json": _schema(tickers)}}}],
                    "toolChoice": {"auto": {}}},
        inferenceConfig={"maxTokens": 400})
    out = next((b["toolUse"]["input"] for b in resp["output"]["message"]["content"]
                if b.get("toolUse", {}).get("name") == "search_filters"), None)
    if not isinstance(out, dict):
        raise RuntimeError("no tool call")
    pick = lambda key, enum: [v for v in out.get(key) or [] if v in enum]  # noqa: E731
    return {"cls": pick("cls", CLS), "mkt": pick("mkt", MKT), "sec": pick("sec", SEC), "ccy": pick("ccy", CCY),
            "tickers": [t for t in out.get("tickers") or [] if t in set(tickers) and _named(t, assets, text)],
            "mode": "add" if out.get("mode") == "add" else "replace", "clear": out.get("clear") is True}


def _named(ticker: str, assets, text: str) -> bool:
    """Keep a ticker only if the user's text contains its ticker root or a word of its name (no model-added picks)."""
    low = text.lower()
    root = ticker.lower().replace(".to", "").replace("-usd", "")
    if root and root in low:
        return True
    words = [w for w in str(assets.loc[ticker, "name"]).lower().replace(",", " ").split() if len(w) >= 4]
    return any(w in low for w in words[:2])
