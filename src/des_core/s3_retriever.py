"""S3-backed retriever for DES shard files."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, List, Optional

import boto3
from botocore.client import BaseClient
from botocore.response import StreamingBody

from .routing import locate_shard, normalize_uid
from .shard_io import ShardReader


@dataclass(frozen=True)
class S3Config:
    """Configuration for accessing DES shards in S3-compatible storage."""

    bucket: str
    prefix: str = ""
    region_name: Optional[str] = None
    endpoint_url: Optional[str] = None


def normalize_prefix(prefix: str) -> str:
    """Ensure prefix is either empty or ends with '/'."""

    if not prefix:
        return ""
    return prefix if prefix.endswith("/") else prefix + "/"


class S3ShardStorage:
    """Thin wrapper around boto3 client for DES shard access."""

    def __init__(self, config: S3Config, client: BaseClient | None = None) -> None:
        self._config = config
        self._client = client or boto3.client(
            "s3",
            region_name=config.region_name,
            endpoint_url=config.endpoint_url,
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


class S3ShardRetriever:
    """Retriever that fetches shards from S3 and reads them in-memory."""

    def __init__(self, s3_storage: S3ShardStorage, n_bits: int = 8) -> None:
        self._s3 = s3_storage
        self._n_bits = n_bits

    def has_file(self, uid: str | int, created_at: datetime) -> bool:
        normalized_uid = normalize_uid(uid)
        date_dir, shard_hex = self._resolve_key_components(normalized_uid, created_at)

        for key in self._s3.list_candidate_keys(date_dir, shard_hex):
            data = self._s3.get_object_bytes(key)
            with ShardReader.from_bytes(data) as reader:
                if reader.has_uid(normalized_uid):
                    return True
        return False

    def get_file(self, uid: str | int, created_at: datetime) -> bytes:
        normalized_uid = normalize_uid(uid)
        date_dir, shard_hex = self._resolve_key_components(normalized_uid, created_at)

        for key in self._s3.list_candidate_keys(date_dir, shard_hex):
            data = self._s3.get_object_bytes(key)
            with ShardReader.from_bytes(data) as reader:
                if reader.has_uid(normalized_uid):
                    return reader.read_file(normalized_uid)

        raise KeyError(f"UID {normalized_uid!r} not found for date {created_at.date()}")

    def _resolve_key_components(self, uid: str, created_at: datetime) -> tuple[str, str]:
        shard = locate_shard(uid=uid, created_at=created_at, n_bits=self._n_bits)
        return shard.date_dir, shard.shard_hex
