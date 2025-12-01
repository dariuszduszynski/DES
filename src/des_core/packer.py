"""Local filesystem packer that wires planner + shard IO together."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from .config import DESConfig, S3SourceConfig
from .packer_planner import FileToPack, PlannerConfig, ShardKey, build_pack_plan
from .s3_file_reader import S3FileReader, is_s3_uri
from .shard_io import ShardWriter


@dataclass
class ShardWriteResult:
    """Metadata about a written shard file."""

    shard_key: ShardKey
    path: Path
    file_count: int
    total_size_bytes: int
    bigfile_hashes: frozenset[str] = field(default_factory=frozenset)


@dataclass
class PackerResult:
    """Summary of all shards produced by the local packer."""

    shards: List[ShardWriteResult]


def pack_files_to_directory(
    files: List[FileToPack],
    output_dir: Path | str,
    config: PlannerConfig,
    *,
    des_config: DESConfig | None = None,
    s3_source_config: S3SourceConfig | None = None,
) -> PackerResult:
    """Plan and write DES shard files to the given output directory.

    The function is synchronous and blocking. It reads bytes from each
    FileToPack.source_path, groups files via the planner, and writes shards
    using ShardWriter. S3 sources are supported when s3_source_config.enabled
    is True; otherwise local filesystem reads are used.
    """

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    des_cfg = des_config or DESConfig.from_env()
    s3_reader = _build_s3_reader(s3_source_config)

    plan = build_pack_plan(files, config)
    shard_counters: dict[ShardKey, int] = {}
    results: List[ShardWriteResult] = []

    for planned_shard in plan.shards:
        shard_index = shard_counters.get(planned_shard.key, 0)
        shard_counters[planned_shard.key] = shard_index + 1

        shard_filename = f"{planned_shard.key.date_dir}_{planned_shard.key.shard_hex}_{shard_index:04d}.des"
        shard_path = output_path / shard_filename

        total_size = 0
        shard_bigfiles: set[str] = set()
        with ShardWriter(shard_path, config=des_cfg) as writer:
            for file in planned_shard.files:
                if file.source_path is None:
                    raise ValueError(f"source_path is required for UID {file.uid!r}")
                data = _read_source_bytes(file.source_path, s3_reader)
                entry = writer.add_file(file.uid, data)
                if entry.is_bigfile and entry.bigfile_hash:
                    shard_bigfiles.add(entry.bigfile_hash)
                total_size += file.size_bytes

        results.append(
            ShardWriteResult(
                shard_key=planned_shard.key,
                path=shard_path,
                file_count=len(planned_shard.files),
                total_size_bytes=total_size,
                bigfile_hashes=frozenset(shard_bigfiles),
            )
        )

    return PackerResult(shards=results)


def _read_source_bytes(source_path: Path | str, s3_reader: S3FileReader | None) -> bytes:
    path_str = str(source_path)
    if is_s3_uri(path_str):
        if s3_reader is None:
            raise ValueError("S3 source encountered but S3 source config is not enabled.")
        return s3_reader.read_file(path_str)
    return Path(path_str).read_bytes()


def _build_s3_reader(config: S3SourceConfig | None) -> S3FileReader | None:
    if config is None or not config.enabled:
        return None
    return S3FileReader.from_config(config)
