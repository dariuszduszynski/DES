"""Local filesystem packer that wires planner + shard IO together."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from .config import DESConfig
from .packer_planner import FileToPack, PlannerConfig, ShardKey, build_pack_plan
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
) -> PackerResult:
    """Plan and write DES shard files to the given output directory.

    The function is synchronous and blocking. It reads bytes from each
    FileToPack.source_path, groups files via the planner, and writes shards
    using ShardWriter. This is a local-only helper; no S3 operations occur.
    """

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    des_cfg = des_config or DESConfig.from_env()

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
                data = Path(file.source_path).read_bytes()
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
