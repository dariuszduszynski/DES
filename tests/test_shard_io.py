import tempfile
from pathlib import Path

import pytest

from des_core.shard_io import ShardReader, ShardWriter


def test_write_and_read_single_file_round_trip() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        shard_path = Path(tmpdir) / "single.des"
        with ShardWriter(shard_path) as writer:
            writer.add_file("uid-1", b"hello world")

        with ShardReader.from_path(shard_path) as reader:
            assert "uid-1" in reader.list_uids()
            assert reader.read_file("uid-1") == b"hello world"


def test_write_and_read_multiple_files() -> None:
    payloads = {
        "a": b"alpha",
        "b": b"beta-data",
        "c": b"gamma" * 10,
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        shard_path = Path(tmpdir) / "multi.des"
        with ShardWriter(shard_path) as writer:
            for uid, data in payloads.items():
                writer.add_file(uid, data)

        with ShardReader.from_path(shard_path) as reader:
            assert len(reader.index) == len(payloads)
            for uid, data in payloads.items():
                assert reader.read_file(uid) == data


def test_reader_from_bytes() -> None:
    payloads = {"x": b"foo", "y": b"barbaz"}

    with tempfile.TemporaryDirectory() as tmpdir:
        shard_path = Path(tmpdir) / "bytes.des"
        with ShardWriter(shard_path) as writer:
            for uid, data in payloads.items():
                writer.add_file(uid, data)

        shard_bytes = shard_path.read_bytes()
        with ShardReader.from_bytes(shard_bytes) as reader:
            assert set(reader.list_uids()) == set(payloads.keys())
            for uid, data in payloads.items():
                assert reader.read_file(uid) == data


def test_duplicate_uid_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        shard_path = Path(tmpdir) / "dup.des"
        with pytest.raises(ValueError):
            with ShardWriter(shard_path) as writer:
                writer.add_file("dup", b"one")
                writer.add_file("dup", b"two")


def test_invalid_file_detection() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        shard_path = Path(tmpdir) / "invalid.des"
        shard_path.write_bytes(b"BADFILE")

        with pytest.raises(ValueError):
            ShardReader.from_path(shard_path)


def test_round_trip_determinism() -> None:
    files = [
        ("001", b"x" * 5),
        ("002", b"y" * 2),
        ("003", b"z" * 7),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        shard_path = Path(tmpdir) / "deterministic.des"
        with ShardWriter(shard_path) as writer:
            for uid, data in files:
                writer.add_file(uid, data)

        with ShardReader.from_path(shard_path) as reader:
            for uid, data in files:
                assert reader.read_file(uid) == data
