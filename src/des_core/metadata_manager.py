"""S3 metadata manager for shard sidecar files."""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from botocore.exceptions import ClientError

from .bigfiles import build_bigfile_key
from .cache import Cache, LRUCache, LRUCacheConfig
from .compression import CompressionCodec
from .config import DESConfig
from .metrics import (
    checksum_computation_seconds,
    checksum_verifications_total,
    metadata_cache_hits_total,
    metadata_cache_misses_total,
    metadata_load_duration_seconds,
    metadata_rebuilds_total,
    tombstones_created_total,
)
from .shard_io import ShardFileEntry, ShardReader, decompress_entry
from .shard_metadata import ShardMetadata

logger = logging.getLogger(__name__)


class MetadataNotFoundError(FileNotFoundError):
    """Raised when metadata is missing and rebuild is disabled."""


def _is_not_found_error(exc: ClientError) -> bool:
    code = exc.response.get("Error", {}).get("Code")
    return code in {"404", "NoSuchKey", "NotFound"}


def _entry_to_dict(entry: ShardFileEntry) -> dict[str, Any]:
    return {
        "uid": entry.uid,
        "offset": entry.offset,
        "length": entry.length,
        "codec": entry.codec.value if entry.codec is not None else None,
        "compressed_size": entry.compressed_size,
        "uncompressed_size": entry.uncompressed_size,
        "is_bigfile": entry.is_bigfile,
        "bigfile_hash": entry.bigfile_hash,
        "bigfile_size": entry.bigfile_size,
        "meta": entry.meta,
    }


def entry_from_dict(data: dict[str, Any]) -> ShardFileEntry:
    if not isinstance(data, dict):
        raise ValueError("Entry must be a mapping")
    uid = data.get("uid")
    if not isinstance(uid, str):
        raise ValueError("Entry uid must be a string")
    codec_value = data.get("codec")
    codec = CompressionCodec(codec_value) if codec_value is not None else None
    meta = data.get("meta")
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        raise ValueError("Entry meta must be a mapping")

    return ShardFileEntry(
        uid=uid,
        offset=data.get("offset"),
        length=data.get("length"),
        codec=codec,
        compressed_size=data.get("compressed_size"),
        uncompressed_size=data.get("uncompressed_size"),
        is_bigfile=bool(data.get("is_bigfile", False)),
        bigfile_hash=data.get("bigfile_hash"),
        bigfile_size=data.get("bigfile_size"),
        meta=meta,
    )


def _parse_entry_created_at(meta: dict[str, Any]) -> datetime | None:
    value = meta.get("created_at")
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None
    return None


def _meta_key(shard_key: str) -> str:
    if shard_key.endswith(".des"):
        return shard_key[:-4] + ".meta"
    return f"{shard_key}.meta"


class MetadataManager:
    """Manages .meta sidecar files for DES shards."""

    def __init__(
        self,
        s3_client: Any,
        bucket: str,
        *,
        cache_size: int = 1000,
        cache: Cache[str, ShardMetadata] | None = None,
    ) -> None:
        self.s3 = s3_client
        self.bucket = bucket
        self._cache = cache or LRUCache[str, ShardMetadata](LRUCacheConfig(max_size=cache_size))

    def get_metadata(self, shard_key: str, *, rebuild_on_missing: bool = True) -> ShardMetadata:
        """Load metadata for shard, using cache when available."""

        cached = self._cache.get(shard_key)
        if cached is not None:
            metadata_cache_hits_total.inc()
            logger.info("Metadata cache hit for %s", shard_key)
            return cached

        metadata_cache_misses_total.inc()
        start = time.perf_counter()
        meta_key = _meta_key(shard_key)

        try:
            response = self.s3.get_object(Bucket=self.bucket, Key=meta_key)
            body = response["Body"].read()
            if isinstance(body, bytes):
                payload = body.decode("utf-8")
            else:
                payload = str(body)
            meta = ShardMetadata.from_json(payload)
        except ClientError as exc:
            if _is_not_found_error(exc):
                if not rebuild_on_missing:
                    raise MetadataNotFoundError(f"Metadata not found for {shard_key}") from exc
                logger.warning("Metadata missing for %s; rebuilding", shard_key)
                metadata_rebuilds_total.inc()
                meta = self._rebuild_metadata(shard_key)
            else:
                logger.exception("Failed to load metadata for %s", shard_key)
                raise
        except (ValueError, TypeError) as exc:
            if not rebuild_on_missing:
                raise
            logger.warning("Invalid metadata for %s; rebuilding (%s)", shard_key, exc)
            metadata_rebuilds_total.inc()
            meta = self._rebuild_metadata(shard_key)
        finally:
            metadata_load_duration_seconds.observe(time.perf_counter() - start)

        self._cache.set(shard_key, meta)
        return meta

    def _fetch_entry_payload(self, shard_key: str, entry: ShardFileEntry) -> bytes:
        """Fetch payload bytes for a shard entry."""

        if entry.is_bigfile:
            if entry.bigfile_hash is None:
                raise ValueError("Bigfile entry missing hash.")
            config = DESConfig.from_env()
            bigfile_key = build_bigfile_key(shard_key, config.bigfiles_prefix, entry.bigfile_hash)
            response = self.s3.get_object(Bucket=self.bucket, Key=bigfile_key)
            body = response["Body"].read()
            if not isinstance(body, (bytes, bytearray)):
                raise ValueError("Bigfile payload is not bytes")
            return bytes(body)

        if entry.offset is None:
            raise ValueError("Inline entry missing offset.")
        length = entry.length if entry.length is not None else entry.compressed_size
        if length is None:
            raise ValueError("Inline entry missing length.")
        response = self.s3.get_object(
            Bucket=self.bucket,
            Key=shard_key,
            Range=f"bytes={entry.offset}-{entry.offset + length - 1}",
        )
        body = response["Body"].read()
        if not isinstance(body, (bytes, bytearray)):
            raise ValueError("Shard payload is not bytes")
        return bytes(body)

    def _rebuild_metadata(self, shard_key: str) -> ShardMetadata:
        """Rebuild metadata by reading the shard index."""

        response = self.s3.get_object(Bucket=self.bucket, Key=shard_key)
        body = response["Body"].read()
        if not isinstance(body, (bytes, bytearray)):
            raise ValueError("Shard payload is not bytes")

        shard_size = len(body)
        reader = ShardReader.from_bytes(bytes(body))

        index: dict[str, dict[str, Any]] = {}
        for uid, entry in reader.index.items():
            created_at = _parse_entry_created_at(entry.meta)
            if created_at is not None:
                key = ShardMetadata.build_key(uid, created_at)
            else:
                key = uid
            entry_dict = _entry_to_dict(entry)
            payload = self._fetch_entry_payload(shard_key, entry)
            if entry.is_bigfile:
                data = payload
            else:
                data = decompress_entry(entry, payload)
            start = time.perf_counter()
            checksum = hashlib.sha256(data).hexdigest()
            checksum_computation_seconds.labels(operation="rebuild").observe(time.perf_counter() - start)
            entry_dict["checksum"] = checksum
            entry_dict["checksum_algo"] = "sha256"
            index[key] = entry_dict

        now = datetime.now(timezone.utc)
        created_at = response.get("LastModified")
        if isinstance(created_at, datetime):
            shard_created = created_at.astimezone(timezone.utc)
        else:
            shard_created = now

        meta = ShardMetadata(
            version=1,
            shard_file=Path(shard_key).name,
            shard_size=shard_size,
            created_at=shard_created,
            last_updated=now,
            index=index,
            tombstones={},
            stats={"entries": len(index), "deleted_files": 0, "deletion_ratio": 0.0},
        )

        self.save_metadata(shard_key, meta)
        return meta

    def verify_entry_checksum(
        self,
        shard_key: str,
        uid: str,
        created_at: datetime,
        data: bytes,
    ) -> bool:
        """Verify checksum for a file entry."""

        meta = self.get_metadata(shard_key)
        entry = meta.get_entry(uid, created_at)

        if entry is None:
            raise KeyError(f"Entry not found: {uid}")

        stored_checksum = entry.get("checksum")
        if stored_checksum is None:
            checksum_verifications_total.labels(status="missing").inc()
            logger.warning("No checksum for %s (old format)", uid)
            return False
        if not isinstance(stored_checksum, str):
            checksum_verifications_total.labels(status="failure").inc()
            logger.warning("Invalid checksum type for %s: %s", uid, type(stored_checksum).__name__)
            return False

        algo = entry.get("checksum_algo", "sha256")
        if not isinstance(algo, str) or algo != "sha256":
            checksum_verifications_total.labels(status="failure").inc()
            logger.warning("Unknown checksum algo: %s", algo)
            return False

        start = time.perf_counter()
        computed = hashlib.sha256(data).hexdigest()
        checksum_computation_seconds.labels(operation="verify").observe(time.perf_counter() - start)

        match = computed == stored_checksum

        if match:
            checksum_verifications_total.labels(status="success").inc()
        else:
            checksum_verifications_total.labels(status="failure").inc()
            logger.error(
                "Checksum mismatch for %s: expected=%s computed=%s",
                uid,
                stored_checksum,
                computed,
            )

        return match

    def add_tombstone(
        self,
        shard_key: str,
        uid: str,
        created_at: datetime,
        deleted_by: str,
        reason: str,
        ticket_id: Optional[str] = None,
    ) -> None:
        """Add tombstone to metadata and save."""

        meta = self.get_metadata(shard_key)
        entry = meta.get_entry(uid, created_at)
        if entry is None:
            raise KeyError(f"UID {uid!r} not found in shard {shard_key}")

        key = ShardMetadata.build_key(uid, created_at)
        meta.tombstones[key] = {
            "uid": uid,
            "created_at": ShardMetadata.format_timestamp(created_at),
            "deleted_at": ShardMetadata.format_timestamp(datetime.now(timezone.utc)),
            "deleted_by": deleted_by,
            "reason": reason,
            "ticket_id": ticket_id,
        }
        meta.last_updated = datetime.now(timezone.utc)

        total_entries = len(meta.index)
        deleted_files = len(meta.tombstones)
        meta.stats["entries"] = total_entries
        meta.stats["deleted_files"] = deleted_files
        meta.stats["deletion_ratio"] = deleted_files / total_entries if total_entries else 0.0

        tombstones_created_total.labels(reason=reason).inc()
        self.save_metadata(shard_key, meta)

    def save_metadata(self, shard_key: str, meta: ShardMetadata) -> None:
        """Write metadata to S3 and update cache."""

        meta_key = _meta_key(shard_key)
        payload = meta.to_json().encode("utf-8")
        self.s3.put_object(Bucket=self.bucket, Key=meta_key, Body=payload, ContentType="application/json")
        self._cache.set(shard_key, meta)
