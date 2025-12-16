"""FastAPI-based HTTP retriever for local DES shard files.

Environment variables:
    DES_BASE_DIR: base directory containing shard files (default ./data/des)
    DES_N_BITS:   routing shard bits (default 8)
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

from fastapi import FastAPI, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import AnyUrl, BaseModel

from .ext_retention import ExtendedRetentionManager, RetrieverProtocol
from .multi_s3_retriever import MultiS3ShardRetriever
from .retriever import LocalRetrieverConfig, LocalShardRetriever
from .s3_retriever import S3Config, S3ShardRetriever, S3ShardStorage
from .zone_config_loader import load_zones_config


class HttpRetrieverSettings(BaseModel):
    """Settings for the DES HTTP retriever service."""

    backend: Literal["local", "s3", "multi_s3"] = "local"

    # local backend
    base_dir: Path | None = None

    # shared routing
    n_bits: int = 8

    # s3 backend
    s3_bucket: str | None = None
    s3_region_name: str | None = None
    s3_endpoint_url: AnyUrl | str | None = None
    s3_prefix: str = ""

    # multi-s3 backend
    zones_config_path: Path | None = None

    # extended retention
    ext_retention_bucket: str | None = None
    ext_retention_prefix: str = "_ext_retention"


class RetentionPolicyRequest(BaseModel):
    """Request payload for extended retention policy updates."""

    created_at: datetime
    due_date: datetime


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

    retriever = build_retriever_from_settings(settings)
    ext_retention_mgr = _build_ext_retention_manager(settings, retriever)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics() -> Response:
        payload = generate_latest()
        return Response(content=payload, media_type=CONTENT_TYPE_LATEST)

    @app.get("/files/{uid}")
    async def get_file(uid: str, created_at: str) -> Response:
        """Return raw file bytes for given UID and created_at."""

        dt = _parse_created_at(created_at)

        try:
            data = retriever.get_file(uid, dt)
        except KeyError:
            raise HTTPException(status_code=404, detail="File not found")

        return Response(content=data, media_type="application/octet-stream")

    @app.put("/files/{uid}/retention-policy")
    async def set_retention_policy(uid: str, request: RetentionPolicyRequest) -> dict[str, object]:
        """Set or extend retention for a file, copying to extended storage if needed."""

        if ext_retention_mgr is None:
            raise HTTPException(status_code=503, detail="Extended retention not configured")

        try:
            return ext_retention_mgr.set_retention_policy(
                uid=uid,
                created_at=request.created_at,
                due_date=request.due_date,
                retriever=cast(RetrieverProtocol, retriever),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"File {uid} not found")
        except Exception as exc:  # pragma: no cover - unexpected server errors
            raise HTTPException(status_code=500, detail=str(exc))

    return app


def build_retriever_from_settings(
    settings: HttpRetrieverSettings,
) -> LocalShardRetriever | S3ShardRetriever | MultiS3ShardRetriever:
    """Instantiate the proper retriever based on settings."""

    if settings.backend == "local":
        if settings.base_dir is None:
            raise ValueError("base_dir must be provided for local backend")
        return LocalShardRetriever(LocalRetrieverConfig(base_dir=settings.base_dir, n_bits=settings.n_bits))

    if settings.backend == "s3":
        if not settings.s3_bucket:
            raise ValueError("s3_bucket must be provided for s3 backend")
        s3_config = S3Config(
            bucket=settings.s3_bucket,
            prefix=settings.s3_prefix,
            region_name=settings.s3_region_name,
            endpoint_url=str(settings.s3_endpoint_url) if settings.s3_endpoint_url else None,
        )
        storage = S3ShardStorage(s3_config)
        return S3ShardRetriever(
            storage,
            n_bits=settings.n_bits,
            ext_retention_prefix=settings.ext_retention_prefix,
        )

    if settings.backend == "multi_s3":
        if settings.zones_config_path is None:
            raise ValueError("zones_config_path must be provided for multi_s3 backend")
        n_bits, zones = load_zones_config(settings.zones_config_path)
        try:
            return MultiS3ShardRetriever(
                zones=zones,
                n_bits=n_bits,
                ext_retention_prefix=settings.ext_retention_prefix,
            )
        except TypeError:
            return MultiS3ShardRetriever(zones=zones, n_bits=n_bits)

    raise ValueError(f"Unsupported backend: {settings.backend}")


def _build_ext_retention_manager(
    settings: HttpRetrieverSettings,
    retriever: LocalShardRetriever | S3ShardRetriever | MultiS3ShardRetriever,
) -> ExtendedRetentionManager | None:
    bucket = settings.ext_retention_bucket
    if bucket is None and settings.backend == "s3":
        bucket = settings.s3_bucket
    if bucket is None:
        return None

    s3_client = None
    if isinstance(retriever, S3ShardRetriever):
        s3_client = getattr(getattr(retriever, "_s3", None), "_client", None)

    return ExtendedRetentionManager(bucket=bucket, s3_client=s3_client, prefix=settings.ext_retention_prefix)


def _load_settings_from_env() -> HttpRetrieverSettings:
    backend = os.environ.get("DES_BACKEND", "local").lower()
    n_bits = int(os.environ.get("DES_N_BITS", "8"))

    if backend == "multi_s3":
        zones_path_env = os.environ.get("DES_ZONES_CONFIG")
        if not zones_path_env:
            raise RuntimeError("DES_ZONES_CONFIG must be set when DES_BACKEND=multi_s3")
        return HttpRetrieverSettings(
            backend="multi_s3",
            zones_config_path=Path(zones_path_env),
            ext_retention_bucket=os.environ.get("DES_EXT_RETENTION_BUCKET"),
            ext_retention_prefix=os.environ.get("DES_EXT_RETENTION_PREFIX", "_ext_retention"),
        )

    if backend == "s3" or os.environ.get("DES_S3_BUCKET"):
        return HttpRetrieverSettings(
            backend="s3",
            n_bits=n_bits,
            s3_bucket=os.environ.get("DES_S3_BUCKET"),
            s3_region_name=os.environ.get("DES_S3_REGION"),
            s3_endpoint_url=os.environ.get("DES_S3_ENDPOINT_URL"),
            s3_prefix=os.environ.get("DES_S3_PREFIX", ""),
            ext_retention_bucket=os.environ.get("DES_EXT_RETENTION_BUCKET") or os.environ.get("DES_S3_BUCKET"),
            ext_retention_prefix=os.environ.get("DES_EXT_RETENTION_PREFIX", "_ext_retention"),
        )

    base_dir = Path(os.environ.get("DES_BASE_DIR", "./data/des"))
    base_dir.mkdir(parents=True, exist_ok=True)
    return HttpRetrieverSettings(
        backend="local",
        base_dir=base_dir,
        n_bits=n_bits,
        ext_retention_bucket=os.environ.get("DES_EXT_RETENTION_BUCKET"),
        ext_retention_prefix=os.environ.get("DES_EXT_RETENTION_PREFIX", "_ext_retention"),
    )


settings = _load_settings_from_env()
app = create_app(settings)
