import tempfile
from pathlib import Path

from des_core.compression import (
    CompressionCodec,
    balanced_zstd_config,
    speed_lz4_config,
)
from des_core.shard_io import ShardReader, ShardWriter


def test_round_trip_zstd_compression() -> None:
    payloads = {
        "file1.txt": b"A" * 1024,
        "file2.bin": b"B" * 2048,
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        shard_path = Path(tmpdir) / "compressed.des"
        with ShardWriter(shard_path, compression=balanced_zstd_config()) as writer:
            for uid, data in payloads.items():
                writer.add_file(uid, data)

        with ShardReader.from_path(shard_path) as reader:
            for uid, data in payloads.items():
                assert reader.read_file(uid) == data
            # ensure at least one entry was compressed
            assert any(
                entry.codec == CompressionCodec.ZSTD and entry.compressed_size < entry.uncompressed_size
                for entry in reader.index.values()
            )


def test_skip_already_compressed_extensions() -> None:
    payloads = {
        "image.jpg": b"\x00" * 512,
        "archive.gz": b"\x01" * 512,
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        shard_path = Path(tmpdir) / "skip.des"
        with ShardWriter(shard_path, compression=balanced_zstd_config()) as writer:
            for uid, data in payloads.items():
                writer.add_file(uid, data)

        with ShardReader.from_path(shard_path) as reader:
            for uid, data in payloads.items():
                assert reader.read_file(uid) == data
                entry = reader.index.get(uid)
                assert entry is not None
                assert entry.codec == CompressionCodec.NONE
                assert entry.compressed_size == entry.uncompressed_size == len(data)


def test_mixed_compression() -> None:
    payloads = {
        "note.txt": b"hello world" * 50,
        "photo.png": b"\x02" * 256,
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        shard_path = Path(tmpdir) / "mixed.des"
        with ShardWriter(shard_path, compression=balanced_zstd_config()) as writer:
            for uid, data in payloads.items():
                writer.add_file(uid, data)

        with ShardReader.from_path(shard_path) as reader:
            assert reader.read_file("note.txt") == payloads["note.txt"]
            assert reader.read_file("photo.png") == payloads["photo.png"]
            note_entry = reader.index.get("note.txt")
            photo_entry = reader.index.get("photo.png")
            assert note_entry is not None and note_entry.codec != CompressionCodec.NONE
            assert photo_entry is not None and photo_entry.codec == CompressionCodec.NONE


def test_lz4_profile_smoke() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        shard_path = Path(tmpdir) / "lz4.des"
        with ShardWriter(shard_path, compression=speed_lz4_config()) as writer:
            writer.add_file("lz4.bin", b"\x03" * 128)

        with ShardReader.from_path(shard_path) as reader:
            entry = reader.index.get("lz4.bin")
            assert entry is not None
            assert entry.codec == CompressionCodec.LZ4
            assert reader.read_file("lz4.bin") == b"\x03" * 128
