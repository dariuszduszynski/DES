"""Central configuration for DES writers/readers."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_BIG_FILE_THRESHOLD_BYTES = 10 * 1024 * 1024  # 10 MiB
DEFAULT_BIGFILES_PREFIX = "_bigFiles"


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
