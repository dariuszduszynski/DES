"""S3-backed retriever for DES shard files."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, List, Optional, Protocol, Tuple, cast

import boto3
from botocore.exceptions import ClientError
from botocore.response import StreamingBody

from .bigfiles import build_bigfile_key
from .cache import Cache, LRUCache, LRUCacheConfig
from .config import DESConfig
from .metrics import DES_RETRIEVAL_SECONDS, DES_RETRIEVALS_TOTAL, DES_S3_RANGE_CALLS_TOTAL
from .routing import locate_shard, normalize_uid
from .shard_io import (
    FOOTER_SIZE,
    HEADER_SIZE,
    ShardFileEntry,
    decompress_entry,
    parse_footer,
    parse_header,
    parse_index,
)


@dataclass(frozen=True)
class S3Config:
    """Configuration for accessing DES shards in S3-compatible storage."""

    bucket: str
    prefix: str = ""
    region_name: Optional[str] = None
    endpoint_url: Optional[str] = None


class S3ReadClientProtocol(Protocol):
    def list_objects_v2(self, Bucket: str, Prefix: str) -> Any: ...

    def get_object(self, Bucket: str, Key: str, Range: str | None = None) -> Any: ...

    def head_object(self, Bucket: str, Key: str) -> Any: ...


class S3WriteClientProtocol(Protocol):
    def put_object(self, Bucket: str, Key: str, Body: bytes) -> Any: ...


class S3ClientProtocol(S3ReadClientProtocol, S3WriteClientProtocol, Protocol):
    pass


def normalize_prefix(prefix: str) -> str:
    """Ensure prefix is either empty or ends with '/'."""

    if not prefix:
        return ""
    return prefix if prefix.endswith("/") else prefix + "/"


class S3ShardStorage:
    """Thin wrapper around boto3 client for DES shard access."""

    def __init__(self, config: S3Config, client: S3ReadClientProtocol | None = None) -> None:
        self._config = config
        self._client: S3ReadClientProtocol = cast(
            S3ReadClientProtocol,
            client
            or boto3.client(
                "s3",
                region_name=config.region_name,
                endpoint_url=config.endpoint_url,
            ),
        )
        self._prefix = normalize_prefix(config.prefix)

    def list_candidate_keys(self, date_dir: str, shard_hex: str) -> List[str]:
        """List object keys that may contain the shard for given date/shard."""

        prefix = f"{self._prefix}{date_dir}_{shard_hex}"
        response = self._client.list_objects_v2(Bucket=self._config.bucket, Prefix=prefix)
        contents = response.get("Contents", []) if response else []
        keys = [item["Key"] for item in contents if "Key" in item]
        return sorted(keys)

    def get_object_bytes(self, key: str) -> bytes:
        """Download the entire object and return its bytes."""

        response = self._client.get_object(Bucket=self._config.bucket, Key=key)
        body: StreamingBody = response["Body"]
        return body.read()

    def get_tail(self, key: str, length: int) -> tuple[bytes, int]:
        """Read the last `length` bytes of an object and return (data, total_size)."""

        response = self._client.get_object(Bucket=self._config.bucket, Key=key, Range=f"bytes=-{length}")
        total_size = _extract_total_size(response)
        body: StreamingBody = response["Body"]
        return body.read(), total_size

    def get_range(self, key: str, start: int, length: int) -> bytes:
        """Read a byte range from an object."""

        end = start + length - 1
        response = self._client.get_object(Bucket=self._config.bucket, Key=key, Range=f"bytes={start}-{end}")
        body: StreamingBody = response["Body"]
        return body.read()


def _extract_total_size(response: dict[str, Any]) -> int:
    content_range = response.get("ContentRange") or response.get("Content-Range") or response.get("Content-Range")
    if content_range:
        # format: bytes start-end/total
        total_str = content_range.split("/")[-1]
        return int(total_str)
    # fallback to ContentLength when full object is returned
    if "ContentLength" in response:
        return int(response["ContentLength"])
    raise ValueError("Unable to determine total object size from response.")


IndexCacheKey = Tuple[str, str]  # (bucket, object key)
CachedIndex = Tuple[int, dict[str, ShardFileEntry]]  # (version, parsed index)


class S3ShardRetriever:
    """Retriever that fetches shards from S3 and reads them in-memory using range GETs."""

    backend_name = "s3"

    def __init__(
        self,
        s3_storage: S3ShardStorage | S3Config,
        n_bits: int = 8,
        *,
        index_cache: Cache[IndexCacheKey, CachedIndex] | None = None,
        config: DESConfig | None = None,
        ext_retention_prefix: str | None = "_ext_retention",
    ) -> None:
        self._s3: S3ShardStorage
        if isinstance(s3_storage, S3Config):
            self._s3 = S3ShardStorage(s3_storage)
        else:
            self._s3 = s3_storage
        self._n_bits = n_bits
        self._index_cache = index_cache or LRUCache[IndexCacheKey, CachedIndex](LRUCacheConfig(max_size=1024))
        self._config = config or DESConfig.from_env()
        self._ext_retention_prefix = ext_retention_prefix.strip("/") if ext_retention_prefix else ""

    def has_file(self, uid: str | int, created_at: datetime) -> bool:
        normalized_uid = normalize_uid(uid)
        normalized_created_at = self._normalize_timestamp(created_at)
        if self._ext_retention_exists(normalized_uid, normalized_created_at):
            return True

        date_dir, shard_hex = self._resolve_key_components(normalized_uid, created_at)

        for key in self._s3.list_candidate_keys(date_dir, shard_hex):
            _, index = self._get_index_and_version(key)
            if normalized_uid in index:
                return True
        return False

    def get_file(self, uid: str | int, created_at: datetime) -> bytes:
        start = time.perf_counter()
        try:
            data = self._get_file_impl(uid, created_at)
        except Exception:
            DES_RETRIEVALS_TOTAL.labels(backend=self.backend_name, status="error").inc()
            DES_RETRIEVAL_SECONDS.labels(backend=self.backend_name).observe(time.perf_counter() - start)
            raise
        else:
            DES_RETRIEVALS_TOTAL.labels(backend=self.backend_name, status="ok").inc()
            DES_RETRIEVAL_SECONDS.labels(backend=self.backend_name).observe(time.perf_counter() - start)
            return data

    def _get_file_impl(self, uid: str | int, created_at: datetime) -> bytes:
        normalized_uid = normalize_uid(uid)
        normalized_created_at = self._normalize_timestamp(created_at)
        ext_data = self._get_from_ext_retention(normalized_uid, normalized_created_at)
        if ext_data is not None:
            return ext_data
        date_dir, shard_hex = self._resolve_key_components(normalized_uid, created_at)

        for key in self._s3.list_candidate_keys(date_dir, shard_hex):
            _, index = self._get_index_and_version(key)
            entry = index.get(normalized_uid)
            if entry is None:
                continue
            if entry.is_bigfile:
                return self._get_bigfile(key, entry)

            if entry.offset is None or entry.compressed_size is None:
                raise ValueError(f"Inline entry missing offsets for UID {normalized_uid!r}")
            DES_S3_RANGE_CALLS_TOTAL.labels(backend=self.backend_name, type="payload").inc()
            payload = self._s3.get_range(key, entry.offset, entry.compressed_size)
            return decompress_entry(entry, payload)

        raise KeyError(f"UID {normalized_uid!r} not found for date {created_at.date()}")

    def _get_index_and_version(self, key: str) -> CachedIndex:
        cache_key = (self._s3._config.bucket, key)
        cached = self._index_cache.get(cache_key)
        if cached is not None:
            return cached

        DES_S3_RANGE_CALLS_TOTAL.labels(backend=self.backend_name, type="header").inc()
        header_bytes = self._s3.get_range(key, 0, HEADER_SIZE)
        header = parse_header(header_bytes)
        DES_S3_RANGE_CALLS_TOTAL.labels(backend=self.backend_name, type="footer").inc()
        footer_bytes, total_size = self._s3.get_tail(key, FOOTER_SIZE)
        footer = parse_footer(footer_bytes, total_size=total_size)
        DES_S3_RANGE_CALLS_TOTAL.labels(backend=self.backend_name, type="index").inc()
        index_bytes = self._s3.get_range(key, footer.index_offset, footer.index_size)
        index = parse_index(index_bytes, data_section_end=footer.index_offset, version=header.version)
        result: CachedIndex = (header.version, index)
        self._index_cache.set(cache_key, result)
        return result

    def _get_bigfile(self, shard_key: str, entry: ShardFileEntry) -> bytes:
        if entry.bigfile_hash is None:
            raise ValueError("Bigfile entry missing hash.")
        bf_key = build_bigfile_key(shard_key, self._config.bigfiles_prefix, entry.bigfile_hash)
        DES_S3_RANGE_CALLS_TOTAL.labels(backend=self.backend_name, type="bigfile").inc()
        data = self._s3.get_object_bytes(bf_key)
        if entry.bigfile_size is not None and len(data) != entry.bigfile_size:
            raise ValueError(f"Bigfile size mismatch for UID {entry.uid}")
        return data

    def _ext_retention_exists(self, uid: str, created_at: datetime) -> bool:
        if not self._ext_retention_prefix:
            return False
        key = self._build_ext_retention_key(uid, created_at)
        client = getattr(self._s3, "_client", None)
        if client is None or not hasattr(client, "head_object"):
            return False
        try:
            client.head_object(Bucket=self._s3._config.bucket, Key=key)
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def _get_from_ext_retention(self, uid: str, created_at: datetime) -> bytes | None:
        if not self._ext_retention_prefix:
            return None
        key = self._build_ext_retention_key(uid, created_at)
        client = getattr(self._s3, "_client", None)
        if client is None or not hasattr(client, "head_object"):
            return None
        try:
            client.head_object(Bucket=self._s3._config.bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise

        response = client.get_object(Bucket=self._s3._config.bucket, Key=key)
        body: StreamingBody = response["Body"]
        return body.read()

    def _build_ext_retention_key(self, uid: str, created_at: datetime) -> str:
        prefix = self._ext_retention_prefix or "_ext_retention"
        normalized = created_at.astimezone(timezone.utc)
        ts = normalized.isoformat().replace("+00:00", "Z")
        date_prefix = normalized.strftime("%Y%m%d")
        return f"{prefix}/{date_prefix}/{uid}_{ts}.dat"

    @staticmethod
    def _normalize_timestamp(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _resolve_key_components(self, uid: str, created_at: datetime) -> tuple[str, str]:
        shard = locate_shard(uid=uid, created_at=created_at, n_bits=self._n_bits)
        return shard.date_dir, shard.shard_hex
