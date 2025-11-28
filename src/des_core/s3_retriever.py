"""S3-backed retriever for DES shard files."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional, Protocol, Tuple, cast

import boto3
from botocore.response import StreamingBody

from .cache import Cache, LRUCache, LRUCacheConfig
from .metrics import DES_RETRIEVAL_SECONDS, DES_RETRIEVALS_TOTAL, DES_S3_RANGE_CALLS_TOTAL
from .routing import locate_shard, normalize_uid
from .shard_io import (
    FOOTER_SIZE,
    decompress_entry,
    parse_footer,
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


class S3ShardRetriever:
    """Retriever that fetches shards from S3 and reads them in-memory using range GETs."""

    backend_name = "s3"

    def __init__(
        self,
        s3_storage: S3ShardStorage | S3Config,
        n_bits: int = 8,
        *,
        index_cache: Cache[IndexCacheKey, dict[str, Any]] | None = None,
    ) -> None:
        self._s3: S3ShardStorage
        if isinstance(s3_storage, S3Config):
            self._s3 = S3ShardStorage(s3_storage)
        else:
            self._s3 = s3_storage
        self._n_bits = n_bits
        self._index_cache = index_cache or LRUCache[IndexCacheKey, dict[str, Any]](LRUCacheConfig(max_size=1024))

    def has_file(self, uid: str | int, created_at: datetime) -> bool:
        normalized_uid = normalize_uid(uid)
        date_dir, shard_hex = self._resolve_key_components(normalized_uid, created_at)

        for key in self._s3.list_candidate_keys(date_dir, shard_hex):
            index = self._get_index(key)
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
        date_dir, shard_hex = self._resolve_key_components(normalized_uid, created_at)

        for key in self._s3.list_candidate_keys(date_dir, shard_hex):
            index = self._get_index(key)
            entry = index.get(normalized_uid)
            if entry is None:
                continue
            DES_S3_RANGE_CALLS_TOTAL.labels(backend=self.backend_name, type="payload").inc()
            payload = self._s3.get_range(key, entry.offset, entry.compressed_size)
            return decompress_entry(entry, payload)

        raise KeyError(f"UID {normalized_uid!r} not found for date {created_at.date()}")

    def _get_index(self, key: str) -> dict[str, Any]:
        cache_key = (self._s3._config.bucket, key)
        cached = self._index_cache.get(cache_key)
        if cached is not None:
            return cached

        DES_S3_RANGE_CALLS_TOTAL.labels(backend=self.backend_name, type="footer").inc()
        footer_bytes, total_size = self._s3.get_tail(key, FOOTER_SIZE)
        footer = parse_footer(footer_bytes, total_size=total_size)
        DES_S3_RANGE_CALLS_TOTAL.labels(backend=self.backend_name, type="index").inc()
        index_bytes = self._s3.get_range(key, footer.index_offset, footer.index_size)
        index = parse_index(index_bytes, data_section_end=footer.index_offset)
        self._index_cache.set(cache_key, index)
        return index

    def _resolve_key_components(self, uid: str, created_at: datetime) -> tuple[str, str]:
        shard = locate_shard(uid=uid, created_at=created_at, n_bits=self._n_bits)
        return shard.date_dir, shard.shard_hex
