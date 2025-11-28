from datetime import datetime
from pathlib import Path

import pytest

from des_core.packer import pack_files_to_directory
from des_core.packer_planner import FileToPack, PlannerConfig
from des_core.retriever import LocalRetrieverConfig, LocalShardRetriever
from des_core.shard_io import ShardReader


def _make_source_files(tmp_path: Path, payloads: dict[str, bytes], created_at: datetime) -> list[FileToPack]:
    files = []
    for uid, data in payloads.items():
        src = tmp_path / f"{uid}.bin"
        src.write_bytes(data)
        files.append(
            FileToPack(
                uid=uid,
                created_at=created_at,
                size_bytes=len(data),
                source_path=src,
            )
        )
    return files


def test_retriever_happy_path(tmp_path: Path) -> None:
    payloads = {"100": b"a", "356": b"b", "612": b"c"}
    created = datetime(2024, 1, 1)
    files = _make_source_files(tmp_path, payloads, created)
    config = PlannerConfig(max_shard_size_bytes=8, n_bits=8)
    pack_files_to_directory(files, tmp_path, config)

    retriever = LocalShardRetriever(LocalRetrieverConfig(base_dir=tmp_path, n_bits=8))
    for uid, data in payloads.items():
        assert retriever.has_file(uid, created) is True
        assert retriever.get_file(uid, created) == data


def test_retriever_nonexistent_uid(tmp_path: Path) -> None:
    payloads = {"100": b"a"}
    created = datetime(2024, 1, 1)
    files = _make_source_files(tmp_path, payloads, created)
    config = PlannerConfig(max_shard_size_bytes=8, n_bits=8)
    pack_files_to_directory(files, tmp_path, config)

    retriever = LocalShardRetriever(LocalRetrieverConfig(base_dir=tmp_path, n_bits=8))
    assert retriever.has_file("999", created) is False
    with pytest.raises(KeyError):
        retriever.get_file("999", created)


def test_retriever_wrong_date_dir(tmp_path: Path) -> None:
    payloads = {"100": b"a"}
    files = _make_source_files(tmp_path, payloads, datetime(2024, 1, 1))
    config = PlannerConfig(max_shard_size_bytes=8, n_bits=8)
    pack_files_to_directory(files, tmp_path, config)

    retriever = LocalShardRetriever(LocalRetrieverConfig(base_dir=tmp_path, n_bits=8))
    wrong_date = datetime(2024, 1, 2)
    assert retriever.has_file("100", wrong_date) is False
    with pytest.raises(KeyError):
        retriever.get_file("100", wrong_date)


def test_retriever_multiple_shard_files(tmp_path: Path) -> None:
    payloads = {
        "100": b"a" * 5,
        "356": b"b" * 5,
        "612": b"c" * 5,
        "868": b"d" * 5,
    }
    created = datetime(2024, 1, 1)
    files = _make_source_files(tmp_path, payloads, created)
    config = PlannerConfig(max_shard_size_bytes=8, n_bits=8)  # forces multiple shard files
    result = pack_files_to_directory(files, tmp_path, config)

    # ensure multiple physical shards were created
    assert len(result.shards) >= 2

    retriever = LocalShardRetriever(LocalRetrieverConfig(base_dir=tmp_path, n_bits=8))
    for uid, data in payloads.items():
        assert retriever.has_file(uid, created) is True
        assert retriever.get_file(uid, created) == data
