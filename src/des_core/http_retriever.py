"""FastAPI-based HTTP retriever for local DES shard files.

Environment variables:
    DES_BASE_DIR: base directory containing shard files (default ./data/des)
    DES_N_BITS:   routing shard bits (default 8)
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

from .retriever import LocalShardRetriever, make_local_config


class HttpRetrieverSettings(BaseModel):
    """Configuration for the HTTP retriever service."""

    base_dir: Path
    n_bits: int = 8


def _parse_created_at(value: str) -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid created_at format") from exc


def create_app(settings: HttpRetrieverSettings) -> FastAPI:
    """Create a FastAPI app exposing a read-only DES retriever over HTTP."""

    app = FastAPI(title="DES HTTP Retriever", version="0.1.0")

    retriever = LocalShardRetriever(make_local_config(settings.base_dir, settings.n_bits))

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/files/{uid}")
    async def get_file(uid: str, created_at: str) -> Response:
        """Return raw file bytes for given UID and created_at."""

        dt = _parse_created_at(created_at)

        try:
            data = retriever.get_file(uid, dt)
        except KeyError:
            raise HTTPException(status_code=404, detail="File not found")

        return Response(content=data, media_type="application/octet-stream")

    return app


_default_base_dir = Path(os.environ.get("DES_BASE_DIR", "./data/des"))
_default_base_dir.mkdir(parents=True, exist_ok=True)
settings = HttpRetrieverSettings(
    base_dir=_default_base_dir,
    n_bits=int(os.environ.get("DES_N_BITS", "8")),
)
app = create_app(settings)
