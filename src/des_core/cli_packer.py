"""Minimal CLI for local DES packing."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import List

from .packer import PackerResult, pack_files_to_directory
from .packer_planner import FileToPack, PlannerConfig


def _parse_datetime(value: str) -> datetime:
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _load_files_from_json(path: Path) -> List[FileToPack]:
    raw = json.loads(path.read_text())
    files: List[FileToPack] = []

    for item in raw:
        created_at = _parse_datetime(item["created_at"])
        files.append(
            FileToPack(
                uid=str(item["uid"]),
                created_at=created_at,
                size_bytes=int(item["size_bytes"]),
                source_path=item.get("source_path"),
            )
        )
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description="Pack files into DES shard files locally.")
    parser.add_argument("--input-json", required=True, help="Path to JSON file describing files to pack.")
    parser.add_argument("--output-dir", required=True, help="Directory where .des shards will be written.")
    parser.add_argument("--max-shard-size", type=int, default=1_000_000_000)
    parser.add_argument("--n-bits", type=int, default=8)
    args = parser.parse_args()

    files = _load_files_from_json(Path(args.input_json))
    config = PlannerConfig(max_shard_size_bytes=args.max_shard_size, n_bits=args.n_bits)

    try:
        result: PackerResult = pack_files_to_directory(files, args.output_dir, config)
    except Exception as exc:
        print(f"Packing failed: {exc}")
        raise SystemExit(1) from exc

    total_files = sum(s.file_count for s in result.shards)
    total_size = sum(s.total_size_bytes for s in result.shards)
    print(f"Wrote {len(result.shards)} shard(s) containing {total_files} files, total logical size {total_size} bytes.")
    for shard in result.shards:
        print(f"SHARD: {shard.path} files={shard.file_count} size={shard.total_size_bytes}")


if __name__ == "__main__":  # pragma: no cover
    main()
