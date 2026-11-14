"""Thin FastAPI over pipeline + store (SPEC §12). P1 step 12."""
from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="Portée")


@app.get("/health")
def health() -> dict:
    return {"ok": True}
