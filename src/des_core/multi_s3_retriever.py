"""Multi-zone S3 retriever that fans out reads across configured ranges."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List

from .routing import locate_shard
from .s3_retriever import S3Config, S3ShardRetriever


@dataclass(frozen=True)
class S3ZoneRange:
    """Inclusive shard index range handled by a zone."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < 0:
            raise ValueError("Range bounds must be non-negative")
        if self.start > self.end:
            raise ValueError("Range start must be <= end")

    def contains(self, index: int) -> bool:
        return self.start <= index <= self.end


@dataclass
class S3ZoneConfig:
    """Configuration of a single S3 zone."""

    name: str
    range: S3ZoneRange
    s3_config: S3Config


class MultiS3ShardRetriever:
    """Retriever that routes reads to multiple S3 zones based on shard index."""

    def __init__(
        self,
        zones: Iterable[S3ZoneConfig],
        n_bits: int = 8,
        *,
        ext_retention_prefix: str | None = "_ext_retention",
    ) -> None:
        self._zones: List[S3ZoneConfig] = list(zones)
        if not self._zones:
            raise ValueError("At least one S3 zone must be configured")
        self._n_bits = n_bits
        self._validate_zones()
        self._retrievers: List[S3ShardRetriever] = [
            self._build_zone_retriever(z, n_bits=n_bits, ext_retention_prefix=ext_retention_prefix)
            for z in self._zones
        ]

    def _validate_zones(self) -> None:
        max_index = (1 << self._n_bits) - 1
        coverage = [False] * (max_index + 1)

        for zone in self._zones:
            if zone.range.start > max_index or zone.range.end > max_index:
                raise ValueError(f"Zone {zone.name} range exceeds n_bits space")
            for idx in range(zone.range.start, zone.range.end + 1):
                if coverage[idx]:
                    raise ValueError(f"Overlapping zone range detected at index {idx}")
                coverage[idx] = True

    def _find_zone_index_for_shard(self, shard_index: int) -> int:
        for i, zone in enumerate(self._zones):
            if zone.range.contains(shard_index):
                return i
        raise KeyError(f"No S3 zone configured for shard index {shard_index}")

    def _build_zone_retriever(
        self,
        zone: S3ZoneConfig,
        *,
        n_bits: int,
        ext_retention_prefix: str | None,
    ) -> S3ShardRetriever:
        try:
            return S3ShardRetriever(zone.s3_config, n_bits=n_bits, ext_retention_prefix=ext_retention_prefix)
        except TypeError:
            # Support fakes in tests that don't accept the extra keyword.
            return S3ShardRetriever(zone.s3_config, n_bits=n_bits)

    def get_file(self, uid: str, created_at: datetime) -> bytes:
        """Return file contents by delegating to the zone responsible for the shard index."""

        location = locate_shard(uid, created_at, n_bits=self._n_bits)
        shard_index = location.shard_index
        zone_idx = self._find_zone_index_for_shard(shard_index)
        return self._retrievers[zone_idx].get_file(uid, created_at)
