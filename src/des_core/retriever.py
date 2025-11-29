"""Local filesystem retriever for DES shards."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, List

from .config import DESConfig
from .routing import locate_shard, normalize_uid
from .shard_io import ShardReader


@dataclass(frozen=True)
class LocalRetrieverConfig:
    """Configuration for a filesystem-backed retriever."""

    base_dir: Path
    n_bits: int = 8


def make_local_config(base_dir: str | Path, n_bits: int = 8) -> LocalRetrieverConfig:
    """Helper to build LocalRetrieverConfig from a path-like value."""

    return LocalRetrieverConfig(base_dir=Path(base_dir), n_bits=n_bits)


class LocalShardRetriever:
    """Retrieve files from local DES shard files produced by the packer."""

    def __init__(self, config: LocalRetrieverConfig, des_config: DESConfig | None = None) -> None:
        base_dir = config.base_dir
        if not base_dir.exists() or not base_dir.is_dir():
            raise FileNotFoundError(f"Base directory does not exist: {base_dir}")
        self.config = config
        self._des_config = des_config or DESConfig.from_env()

    def has_file(self, uid: str | int, created_at: datetime) -> bool:
        """Return True if the file exists in any matching shard."""

        normalized_uid = normalize_uid(uid)
        for path in self._iter_candidate_shard_paths(normalized_uid, created_at):
            with ShardReader.from_path(path, config=self._des_config) as reader:
                if reader.has_uid(normalized_uid):
                    return True
        return False

    def get_file(self, uid: str | int, created_at: datetime) -> bytes:
        """Return file contents for the given UID, searching matching shards."""

        normalized_uid = normalize_uid(uid)
        for path in self._iter_candidate_shard_paths(normalized_uid, created_at):
            with ShardReader.from_path(path, config=self._des_config) as reader:
                if reader.has_uid(normalized_uid):
                    return reader.read_file(normalized_uid)
        raise KeyError(f"UID {normalized_uid!r} not found for date {created_at.date()} in base_dir {self.config.base_dir}")

    def _iter_candidate_shard_paths(self, uid: str, created_at: datetime) -> Iterator[Path]:
        shard_location = locate_shard(uid=uid, created_at=created_at, n_bits=self.config.n_bits)
        prefix = f"{shard_location.date_dir}_{shard_location.shard_hex}"

        shard_paths: List[Path] = []
        for path in self.config.base_dir.glob(f"{prefix}_*.des"):
            shard_paths.append(path)

        for path in sorted(shard_paths):
            yield path
