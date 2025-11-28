from datetime import datetime
from pathlib import Path

import pytest

from des_core.packer import pack_files_to_directory
from des_core.packer_planner import FileToPack, PlannerConfig
from des_core.shard_io import ShardReader


def test_pack_files_end_to_end(tmp_path: Path) -> None:
    contents = {
        "100": b"a" * 4,
        "356": b"b" * 4,
        "612": b"c" * 4,
    }

    files = []
    for uid, data in contents.items():
        src = tmp_path / f"{uid}.bin"
        src.write_bytes(data)
        files.append(
            FileToPack(
                uid=uid,
                created_at=datetime(2024, 1, 1),
                size_bytes=len(data),
                source_path=src,
            )
        )

    config = PlannerConfig(max_shard_size_bytes=8, n_bits=8)
    result = pack_files_to_directory(files, tmp_path, config)

    assert len(result.shards) == 2
    for shard_result in result.shards:
        assert shard_result.path.exists()

    recovered: dict[str, bytes] = {}
    for shard_result in result.shards:
        with ShardReader.from_path(shard_result.path) as reader:
            for uid in reader.list_uids():
                recovered[uid] = reader.read_file(uid)

    assert recovered == contents


def test_pack_files_missing_source_path(tmp_path: Path) -> None:
    files = [
        FileToPack(
            uid="x",
            created_at=datetime(2024, 1, 1),
            size_bytes=1,
            source_path=None,
        )
    ]
    config = PlannerConfig()

    with pytest.raises(ValueError):
        pack_files_to_directory(files, tmp_path, config)


def test_pack_files_nonexistent_source(tmp_path: Path) -> None:
    missing = tmp_path / "missing.bin"
    files = [
        FileToPack(
            uid="x",
            created_at=datetime(2024, 1, 1),
            size_bytes=1,
            source_path=missing,
        )
    ]
    config = PlannerConfig()

    with pytest.raises(FileNotFoundError):
        pack_files_to_directory(files, tmp_path, config)
