"""FastAPI-based HTTP retriever for local DES shard files.

Environment variables:
    DES_BASE_DIR: base directory containing shard files (default ./data/des)
    DES_N_BITS:   routing shard bits (default 8)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Literal, cast

from fastapi import FastAPI, Header, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import AnyUrl, BaseModel

from .auth import PublicKeyAuthenticator
from .ext_retention import ExtendedRetentionManager, RetrieverProtocol
from .metadata_manager import MetadataManager
from .multi_s3_retriever import MultiS3ShardRetriever
from .retriever import LocalRetrieverConfig, LocalShardRetriever
from .routing import normalize_uid
from .s3_retriever import S3Config, S3ShardRetriever, S3ShardStorage
from .shard_metadata import ShardMetadata, TombstoneError
from .zone_config_loader import load_zones_config

logger = logging.getLogger(__name__)


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

    # deletion API
    delete_api_key: str | None = None

    # public key authentication
    authorized_keys_path: Path | None = None
    require_authentication: bool = False


class DeletionReason(str, Enum):
    """Reasons accepted for tombstone creation."""

    GDPR = "GDPR"
    retention_expired = "retention_expired"
    user_request = "user_request"


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
    authenticator = None
    if settings.authorized_keys_path:
        authenticator = PublicKeyAuthenticator(settings.authorized_keys_path)
        authenticator.install_signal_handler()
    elif settings.require_authentication:
        logger.warning("Authentication required but authorized_keys_path is not configured")

    def _authorize_request(
        *,
        uid: str,
        created_at_raw: str,
        action: str,
        x_des_public_key: str | None,
        x_des_signature: str | None,
        x_des_timestamp: str | None,
        x_des_nonce: str | None,
    ) -> None:
        if authenticator is None:
            if settings.require_authentication:
                raise HTTPException(status_code=503, detail="Authentication not configured")
            return

        if not any([x_des_public_key, x_des_signature, x_des_timestamp, x_des_nonce]):
            if settings.require_authentication:
                raise HTTPException(status_code=401, detail="Authentication required")
            return

        if not all([x_des_public_key, x_des_signature, x_des_timestamp, x_des_nonce]):
            raise HTTPException(status_code=401, detail="Missing authentication headers")

        assert x_des_public_key is not None
        assert x_des_signature is not None
        assert x_des_timestamp is not None
        assert x_des_nonce is not None

        canonical = f"{uid}|{created_at_raw}|{x_des_timestamp}|{x_des_nonce}"
        is_valid, key, error = authenticator.verify_signature(
            public_key_b64=x_des_public_key,
            signature_b64=x_des_signature,
            canonical_data=canonical,
            timestamp=x_des_timestamp,
            nonce=x_des_nonce,
        )
        if not is_valid or key is None:
            if error == "rate_limited":
                raise HTTPException(status_code=429, detail="Rate limit exceeded")
            raise HTTPException(status_code=401, detail="Invalid signature")

        resource_path = normalize_uid(uid)
        if not authenticator.check_permission(key, action, resource_path):
            raise HTTPException(status_code=403, detail="Permission denied")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics() -> Response:
        payload = generate_latest()
        return Response(content=payload, media_type=CONTENT_TYPE_LATEST)

    @app.get("/files/{uid}")
    async def get_file(
        uid: str,
        created_at: str,
        x_des_public_key: str | None = Header(default=None, alias="X-DES-Public-Key"),
        x_des_signature: str | None = Header(default=None, alias="X-DES-Signature"),
        x_des_timestamp: str | None = Header(default=None, alias="X-DES-Timestamp"),
        x_des_nonce: str | None = Header(default=None, alias="X-DES-Nonce"),
    ) -> Response:
        """Return raw file bytes for given UID and created_at."""

        _authorize_request(
            uid=uid,
            created_at_raw=created_at,
            action="read",
            x_des_public_key=x_des_public_key,
            x_des_signature=x_des_signature,
            x_des_timestamp=x_des_timestamp,
            x_des_nonce=x_des_nonce,
        )

        dt = _parse_created_at(created_at)

        try:
            data = retriever.get_file(uid, dt)
        except TombstoneError:
            raise HTTPException(status_code=410, detail="File deleted")
        except KeyError:
            raise HTTPException(status_code=404, detail="File not found")

        return Response(content=data, media_type="application/octet-stream")

    @app.delete("/files/{uid}")
    async def delete_file(
        uid: str,
        created_at: str,
        deleted_by: str,
        reason: DeletionReason,
        ticket_id: str | None = None,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
        x_des_public_key: str | None = Header(default=None, alias="X-DES-Public-Key"),
        x_des_signature: str | None = Header(default=None, alias="X-DES-Signature"),
        x_des_timestamp: str | None = Header(default=None, alias="X-DES-Timestamp"),
        x_des_nonce: str | None = Header(default=None, alias="X-DES-Nonce"),
    ) -> dict[str, str]:
        """Mark file as deleted (create tombstone)."""

        _authorize_request(
            uid=uid,
            created_at_raw=created_at,
            action="write",
            x_des_public_key=x_des_public_key,
            x_des_signature=x_des_signature,
            x_des_timestamp=x_des_timestamp,
            x_des_nonce=x_des_nonce,
        )

        if settings.delete_api_key is None:
            raise HTTPException(status_code=503, detail="Delete API not configured")
        if x_api_key != settings.delete_api_key:
            raise HTTPException(status_code=401, detail="Unauthorized")
        if not deleted_by.strip():
            raise HTTPException(status_code=400, detail="deleted_by is required")

        dt = _parse_created_at(created_at)

        target = _resolve_deletion_retriever(retriever, uid, dt)
        if target is None:
            raise HTTPException(status_code=503, detail="Deletion not supported for this backend")
        if target.metadata_manager is None:
            raise HTTPException(status_code=503, detail="Metadata manager not configured")

        shard_key, meta = _find_shard_for_delete(target, uid, dt)
        if shard_key is None:
            raise HTTPException(status_code=404, detail="File not found")
        if meta is not None and meta.is_tombstoned(normalize_uid(uid), dt):
            raise HTTPException(status_code=410, detail="File already deleted")

        try:
            target.metadata_manager.add_tombstone(
                shard_key=shard_key,
                uid=normalize_uid(uid),
                created_at=dt,
                deleted_by=deleted_by,
                reason=reason.value,
                ticket_id=ticket_id,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="File not found")

        logger.info(
            "Tombstoned uid=%s created_at=%s reason=%s deleted_by=%s ticket_id=%s shard=%s",
            uid,
            dt.isoformat(),
            reason.value,
            deleted_by,
            ticket_id or "",
            shard_key,
        )

        return {"status": "tombstoned"}

    @app.put("/files/{uid}/retention-policy")
    async def set_retention_policy(
        uid: str,
        request: RetentionPolicyRequest,
        x_des_public_key: str | None = Header(default=None, alias="X-DES-Public-Key"),
        x_des_signature: str | None = Header(default=None, alias="X-DES-Signature"),
        x_des_timestamp: str | None = Header(default=None, alias="X-DES-Timestamp"),
        x_des_nonce: str | None = Header(default=None, alias="X-DES-Nonce"),
    ) -> dict[str, object]:
        """Set or extend retention for a file, copying to extended storage if needed."""

        _authorize_request(
            uid=uid,
            created_at_raw=request.created_at.isoformat(),
            action="extend_retention",
            x_des_public_key=x_des_public_key,
            x_des_signature=x_des_signature,
            x_des_timestamp=x_des_timestamp,
            x_des_nonce=x_des_nonce,
        )

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
        s3_client = getattr(storage, "_client", None)
        metadata_manager = None
        if s3_client is not None:
            metadata_manager = MetadataManager(s3_client, bucket=s3_config.bucket)
        return S3ShardRetriever(
            storage,
            n_bits=settings.n_bits,
            ext_retention_prefix=settings.ext_retention_prefix,
            metadata_manager=metadata_manager,
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


def _resolve_deletion_retriever(
    retriever: LocalShardRetriever | S3ShardRetriever | MultiS3ShardRetriever,
    uid: str,
    created_at: datetime,
) -> S3ShardRetriever | None:
    if isinstance(retriever, S3ShardRetriever):
        return retriever
    if isinstance(retriever, MultiS3ShardRetriever):
        try:
            return retriever.get_zone_retriever(uid, created_at)
        except KeyError:
            return None
    return None


def _find_shard_for_delete(
    retriever: S3ShardRetriever,
    uid: str,
    created_at: datetime,
) -> tuple[str | None, ShardMetadata | None]:
    normalized_uid = normalize_uid(uid)
    date_dir, shard_hex = retriever._resolve_key_components(normalized_uid, created_at)
    metadata_manager = retriever.metadata_manager

    for key in retriever._s3.list_candidate_keys(date_dir, shard_hex):
        meta = None
        if metadata_manager is not None:
            try:
                meta = metadata_manager.get_metadata(key)
            except Exception as exc:
                logger.warning("Failed to load metadata for %s: %s", key, exc)
                meta = None
        if meta is not None:
            if meta.get_entry(normalized_uid, created_at) is not None:
                return key, meta
        _, index = retriever._get_index_and_version(key)
        if index.get(normalized_uid) is not None:
            return key, meta

    return None, None


def _load_settings_from_env() -> HttpRetrieverSettings:
    backend = os.environ.get("DES_BACKEND", "local").lower()
    n_bits = int(os.environ.get("DES_N_BITS", "8"))
    auth_path_env = os.environ.get("DES_AUTHORIZED_KEYS_PATH")
    auth_path = Path(auth_path_env) if auth_path_env else None
    require_auth_raw = os.environ.get("DES_REQUIRE_AUTHENTICATION", "false").lower()
    require_auth = require_auth_raw in {"1", "true", "yes", "y"}

    if backend == "multi_s3":
        zones_path_env = os.environ.get("DES_ZONES_CONFIG")
        if not zones_path_env:
            raise RuntimeError("DES_ZONES_CONFIG must be set when DES_BACKEND=multi_s3")
        return HttpRetrieverSettings(
            backend="multi_s3",
            zones_config_path=Path(zones_path_env),
            ext_retention_bucket=os.environ.get("DES_EXT_RETENTION_BUCKET"),
            ext_retention_prefix=os.environ.get("DES_EXT_RETENTION_PREFIX", "_ext_retention"),
            delete_api_key=os.environ.get("DES_DELETE_API_KEY"),
            authorized_keys_path=auth_path,
            require_authentication=require_auth,
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
            delete_api_key=os.environ.get("DES_DELETE_API_KEY"),
            authorized_keys_path=auth_path,
            require_authentication=require_auth,
        )

    base_dir = Path(os.environ.get("DES_BASE_DIR", "./data/des"))
    base_dir.mkdir(parents=True, exist_ok=True)
    return HttpRetrieverSettings(
        backend="local",
        base_dir=base_dir,
        n_bits=n_bits,
        ext_retention_bucket=os.environ.get("DES_EXT_RETENTION_BUCKET"),
        ext_retention_prefix=os.environ.get("DES_EXT_RETENTION_PREFIX", "_ext_retention"),
        delete_api_key=os.environ.get("DES_DELETE_API_KEY"),
        authorized_keys_path=auth_path,
        require_authentication=require_auth,
    )


settings = _load_settings_from_env()
app = create_app(settings)
