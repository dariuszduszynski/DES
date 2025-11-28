"""Planner for grouping files into DES shards without performing IO.

The planner assigns files to shard keys using the routing contract and splits
shards when their accumulated size would exceed a soft limit. No filesystem or
network operations occur here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List

from .routing import locate_shard


@dataclass(frozen=True)
class FileToPack:
    """Descriptor of a file to be packed into DES."""

    uid: str
    created_at: datetime
    size_bytes: int
    source_path: str | None = None


@dataclass(frozen=True)
class ShardKey:
    """Shard identifier used for grouping files."""

    date_dir: str
    shard_hex: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.date_dir}/{self.shard_hex}"


@dataclass(frozen=True)
class PlannerConfig:
    """Configuration that controls shard routing and sizing."""

    max_shard_size_bytes: int = 1_000_000_000
    n_bits: int = 8


@dataclass
class PlannedShard:
    """A single shard with assigned files and total size."""

    key: ShardKey
    total_size_bytes: int
    files: List[FileToPack] = field(default_factory=list)


@dataclass
class PackPlan:
    """Complete plan of shards to be written."""

    shards: List[PlannedShard]


def _validate_file(file: FileToPack) -> None:
    if file.size_bytes < 0:
        raise ValueError("size_bytes must be non-negative")


def _validate_config(config: PlannerConfig) -> None:
    if config.max_shard_size_bytes <= 0:
        raise ValueError("max_shard_size_bytes must be positive")


def _group_files_by_shard_key(files: List[FileToPack], config: PlannerConfig) -> Dict[ShardKey, List[FileToPack]]:
    grouped: Dict[ShardKey, List[FileToPack]] = {}

    for file in files:
        _validate_file(file)
        shard_location = locate_shard(uid=file.uid, created_at=file.created_at, n_bits=config.n_bits)
        key = ShardKey(date_dir=shard_location.date_dir, shard_hex=shard_location.shard_hex)
        grouped.setdefault(key, []).append(file)

    return grouped


def estimate_shard_counts(files: List[FileToPack], config: PlannerConfig) -> Dict[ShardKey, int]:
    """Estimate how many shard files each key will produce based on sizes."""

    _validate_config(config)
    counts: Dict[ShardKey, int] = {}
    grouped = _group_files_by_shard_key(files, config)

    for key, key_files in grouped.items():
        total_size = sum(f.size_bytes for f in key_files)
        shards_needed, remainder = divmod(total_size, config.max_shard_size_bytes)
        counts[key] = shards_needed + (1 if remainder else 0)

    return counts


def build_pack_plan(files: List[FileToPack], config: PlannerConfig) -> PackPlan:
    """Plan how files should be grouped into DES shards.

    The planner is pure and deterministic: given identical inputs it returns the
    same grouping every time. Files are processed in input order within each
    shard key to keep shard contents stable.
    """

    _validate_config(config)
    grouped = _group_files_by_shard_key(files, config)
    planned_shards: List[PlannedShard] = []

    for key, key_files in grouped.items():
        current_files: List[FileToPack] = []
        current_size = 0

        for file in key_files:
            if current_files and current_size + file.size_bytes > config.max_shard_size_bytes:
                planned_shards.append(
                    PlannedShard(key=key, total_size_bytes=current_size, files=list(current_files))
                )
                current_files = []
                current_size = 0

            current_files.append(file)
            current_size += file.size_bytes

        if current_files:
            planned_shards.append(PlannedShard(key=key, total_size_bytes=current_size, files=list(current_files)))

    return PackPlan(shards=planned_shards)
