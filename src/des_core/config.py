"""Central configuration for DES writers/readers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

DEFAULT_BIG_FILE_THRESHOLD_BYTES = 10 * 1024 * 1024  # 10 MiB
DEFAULT_BIGFILES_PREFIX = "_bigFiles"
DEFAULT_S3_SOURCE_MAX_RETRIES = 3
DEFAULT_S3_SOURCE_RETRY_DELAY_SECONDS = 2.0


@dataclass(frozen=True)
class DESConfig:
    """Configuration shared by DES writers/readers."""

    big_file_threshold_bytes: int = DEFAULT_BIG_FILE_THRESHOLD_BYTES
    bigfiles_prefix: str = DEFAULT_BIGFILES_PREFIX

    @classmethod
    def from_env(
        cls,
        *,
        big_file_threshold_bytes: int | None = None,
        bigfiles_prefix: str | None = None,
    ) -> "DESConfig":
        """Build config using environment overrides if provided."""

        threshold_env = os.environ.get("DES_BIG_FILE_THRESHOLD_BYTES")
        if threshold_env is not None:
            threshold = int(threshold_env)
        elif big_file_threshold_bytes is not None:
            threshold = big_file_threshold_bytes
        else:
            threshold = DEFAULT_BIG_FILE_THRESHOLD_BYTES
        if threshold <= 0:
            raise ValueError("big_file_threshold_bytes must be positive")

        prefix_env = os.environ.get("DES_BIGFILES_PREFIX")
        if prefix_env is not None:
            prefix = prefix_env
        elif bigfiles_prefix is not None:
            prefix = bigfiles_prefix
        else:
            prefix = DEFAULT_BIGFILES_PREFIX
        if not prefix:
            raise ValueError("bigfiles_prefix must be a non-empty string")

        return cls(big_file_threshold_bytes=threshold, bigfiles_prefix=prefix)


@dataclass(frozen=True)
class S3SourceConfig:
    """Configuration for reading source files directly from S3."""

    enabled: bool = False
    region_name: str | None = None
    endpoint_url: str | None = None
    max_retries: int = DEFAULT_S3_SOURCE_MAX_RETRIES
    retry_delay_seconds: float = DEFAULT_S3_SOURCE_RETRY_DELAY_SECONDS

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "S3SourceConfig":
        enabled = bool(raw.get("enabled", False))
        region_name = raw.get("region_name")
        endpoint_url = raw.get("endpoint_url")
        max_retries = int(raw.get("max_retries", DEFAULT_S3_SOURCE_MAX_RETRIES))
        retry_delay_seconds = float(raw.get("retry_delay_seconds", DEFAULT_S3_SOURCE_RETRY_DELAY_SECONDS))

        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if retry_delay_seconds <= 0:
            raise ValueError("retry_delay_seconds must be positive")

        return cls(
            enabled=enabled,
            region_name=region_name,
            endpoint_url=endpoint_url,
            max_retries=max_retries,
            retry_delay_seconds=retry_delay_seconds,
        )
