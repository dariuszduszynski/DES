import io
import struct
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from des_core.bigfiles import build_bigfile_key
from des_core.config import DESConfig
from des_core.packer import pack_files_to_directory
from des_core.packer_planner import FileToPack, PlannerConfig
from des_core.s3_retriever import S3Config, S3ShardRetriever, S3ShardStorage
from des_core.shard_io import (
    FOOTER_MAGIC,
    HEADER_MAGIC,
    HEADER_RESERVED,
    LEGACY_VERSION,
    ShardReader,
    ShardWriter,
)


class TrackingS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.requests: list[tuple[str, str | None]] = []

    def put_object(self, Bucket: str, Key: str, Body: bytes) -> None:
        self.objects[(Bucket, Key)] = Body

    def list_objects_v2(self, Bucket: str, Prefix: str, **kwargs: Any) -> dict[str, Any]:
        contents = []
        for (bucket, key), data in self.objects.items():
            if bucket == Bucket and key.startswith(Prefix):
                contents.append({"Key": key})
        return {"Contents": contents} if contents else {}

    def get_object(self, Bucket: str, Key: str, Range: str | None = None) -> dict[str, Any]:
        data = self.objects[(Bucket, Key)]
        self.requests.append((Key, Range))
        if Range is None:
            return {"Body": io.BytesIO(data), "ContentLength": len(data)}

        if Range.startswith("bytes=-"):
            length = int(Range[len("bytes=-") :])
            slice_data = data[-length:]
            start = len(data) - length
            end = len(data) - 1
            return {"Body": io.BytesIO(slice_data), "ContentRange": f"bytes {start}-{end}/{len(data)}"}

        if Range.startswith("bytes="):
            _, spec = Range.split("=", 1)
            start_str, end_str = spec.split("-")
            start = int(start_str)
            end = int(end_str)
            slice_data = data[start : end + 1]
            return {"Body": io.BytesIO(slice_data), "ContentRange": f"bytes {start}-{end}/{len(data)}"}

        raise ValueError(f"Unsupported Range: {Range}")


def test_write_small_file_embeds_in_shard(tmp_path: Path) -> None:
    des_cfg = DESConfig(big_file_threshold_bytes=1024)
    shard_path = tmp_path / "small.des"
    payload = b"tiny-payload"

    with ShardWriter(shard_path, config=des_cfg) as writer:
        writer.add_file("uid-1", payload)

    shard_bytes = shard_path.read_bytes()
    assert payload in shard_bytes
    assert not (tmp_path / des_cfg.bigfiles_prefix).exists()

    with ShardReader.from_path(shard_path, config=des_cfg) as reader:
        entry = reader.index.get("uid-1")
        assert entry is not None and entry.is_bigfile is False
        assert reader.read_file("uid-1") == payload


def test_write_big_file_goes_to_bigfiles_dir(tmp_path: Path) -> None:
    des_cfg = DESConfig(big_file_threshold_bytes=10)
    shard_path = tmp_path / "big.des"
    payload = b"x" * 64

    with ShardWriter(shard_path, config=des_cfg) as writer:
        entry = writer.add_file("uid-big", payload, meta={"source": "test"})

    big_dir = tmp_path / des_cfg.bigfiles_prefix
    expected_path = big_dir / (entry.bigfile_hash or "")
    assert expected_path.exists()
    assert expected_path.read_bytes() == payload
    assert payload not in shard_path.read_bytes()

    with ShardReader.from_path(shard_path, config=des_cfg) as reader:
        idx_entry = reader.index.get("uid-big")
        assert idx_entry is not None and idx_entry.is_bigfile
        assert idx_entry.bigfile_size == len(payload)


def test_reader_resolves_bigfile_correctly(tmp_path: Path) -> None:
    des_cfg = DESConfig(big_file_threshold_bytes=8)
    shard_path = tmp_path / "reader.des"
    payload = b"payload" * 5

    with ShardWriter(shard_path, config=des_cfg) as writer:
        writer.add_file("uid-reader", payload)

    with ShardReader.from_path(shard_path, config=des_cfg) as reader:
        assert reader.read_file("uid-reader") == payload


def test_s3_reader_resolves_bigfile_correctly(tmp_path: Path) -> None:
    des_cfg = DESConfig(big_file_threshold_bytes=8)
    created_at = datetime(2024, 1, 1)
    payload = b"A" * 128
    files = [
        FileToPack(
            uid="s3-uid",
            created_at=created_at,
            size_bytes=len(payload),
            source_path=tmp_path / "s3_source.bin",
        )
    ]
    src_path = Path(files[0].source_path)  # type: ignore[arg-type]
    src_path.write_bytes(payload)

    packer_result = pack_files_to_directory(files, tmp_path, PlannerConfig(max_shard_size_bytes=512), des_config=des_cfg)
    shard = packer_result.shards[0]

    client = TrackingS3Client()
    shard_key = f"des/{shard.path.name}"
    client.put_object(Bucket="bucket", Key=shard_key, Body=shard.path.read_bytes())

    for bf_hash in shard.bigfile_hashes:
        bf_path = shard.path.parent / des_cfg.bigfiles_prefix / bf_hash
        bf_key = build_bigfile_key(shard_key, des_cfg.bigfiles_prefix, bf_hash)
        client.put_object(Bucket="bucket", Key=bf_key, Body=bf_path.read_bytes())

    storage = S3ShardStorage(S3Config(bucket="bucket", prefix="des/"), client=client)
    retriever = S3ShardRetriever(storage, n_bits=8, config=des_cfg)

    assert retriever.get_file("s3-uid", created_at) == payload
    bf_key = build_bigfile_key(shard_key, des_cfg.bigfiles_prefix, next(iter(shard.bigfile_hashes)))
    assert any(key == bf_key and rng is None for key, rng in client.requests)


def test_backward_compatibility(tmp_path: Path) -> None:
    shard_path = tmp_path / "legacy.des"
    payload = b"legacy-data"
    uid = "legacy-uid"

    buffer = io.BytesIO()
    buffer.write(HEADER_MAGIC)
    buffer.write(struct.pack("<B", LEGACY_VERSION))
    buffer.write(HEADER_RESERVED)
    buffer.write(payload)
    index_buf = io.BytesIO()
    index_buf.write(struct.pack("<I", 1))
    uid_bytes = uid.encode("utf-8")
    index_buf.write(struct.pack("<H", len(uid_bytes)))
    index_buf.write(uid_bytes)
    offset = len(HEADER_MAGIC) + 1 + len(HEADER_RESERVED)
    index_buf.write(struct.pack("<QQ", offset, len(payload)))
    index_buf.write(struct.pack("<B", 0))
    index_buf.write(struct.pack("<QQ", len(payload), len(payload)))
    index_bytes = index_buf.getvalue()
    buffer.write(index_bytes)
    buffer.write(FOOTER_MAGIC)
    buffer.write(struct.pack("<Q", len(index_bytes)))
    shard_path.write_bytes(buffer.getvalue())

    with ShardReader.from_path(shard_path) as reader:
        assert reader.read_file(uid) == payload
        entry = reader.index.get(uid)
        assert entry is not None and entry.is_bigfile is False


_uid_strategy = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1, max_size=8)

_case_strategy = st.builds(
    lambda base, big: {**base, big[0]: big[1]},
    base=st.dictionaries(
        _uid_strategy,
        st.binary(min_size=0, max_size=128),
        min_size=0,
        max_size=5,
    ),
    big=st.tuples(
        _uid_strategy,
        st.binary(min_size=65, max_size=128),
    ),
).map(lambda items: list(items.items()))


@given(cases=_case_strategy)
@settings(max_examples=15)
def test_bigfile_roundtrip_fuzz(cases: list[tuple[str, bytes]]) -> None:
    des_cfg = DESConfig(big_file_threshold_bytes=64)
    created_at = datetime(2024, 1, 1)

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        files: list[FileToPack] = []
        for uid, payload in cases:
            src = base / f"{uid}.bin"
            src.write_bytes(payload)
            files.append(
                FileToPack(
                    uid=uid,
                    created_at=created_at,
                    size_bytes=len(payload),
                    source_path=src,
                )
            )

        packer_result = pack_files_to_directory(files, base, PlannerConfig(max_shard_size_bytes=1024), des_config=des_cfg)

        for uid, payload in cases:
            found = False
            for shard in packer_result.shards:
                with ShardReader.from_path(shard.path, config=des_cfg) as reader:
                    if reader.has_uid(uid):
                        assert reader.read_file(uid) == payload
                        found = True
                        break
            assert found, f"Missing uid {uid}"
