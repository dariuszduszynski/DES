from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError

from des_core.packer import pack_files_to_directory
from des_core.packer_planner import FileToPack, PlannerConfig
from des_core.s3_retriever import S3Config, S3ShardRetriever, S3ShardStorage


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, Bucket: str, Key: str, Body: bytes) -> None:
        self.objects[(Bucket, Key)] = Body

    def list_objects_v2(self, Bucket: str, Prefix: str, **kwargs: Any) -> dict[str, Any]:
        contents = []
        for (bucket, key), data in self.objects.items():
            if bucket == Bucket and key.startswith(Prefix):
                contents.append({"Key": key})
        if not contents:
            return {}
        return {"Contents": contents}

    def get_object(self, Bucket: str, Key: str, Range: str | None = None) -> dict[str, Any]:
        data = self.objects[(Bucket, Key)]
        if Range is None:
            return {"Body": BytesIO(data), "ContentLength": len(data)}

        if Range.startswith("bytes=-"):
            length = int(Range[len("bytes=-") :])
            slice_data = data[-length:]
            start = len(data) - length
            end = len(data) - 1
            return {"Body": BytesIO(slice_data), "ContentRange": f"bytes {start}-{end}/{len(data)}"}

        if Range.startswith("bytes="):
            _, spec = Range.split("=", 1)
            start_str, end_str = spec.split("-")
            start = int(start_str)
            end = int(end_str)
            slice_data = data[start : end + 1]
            return {"Body": BytesIO(slice_data), "ContentRange": f"bytes {start}-{end}/{len(data)}"}

        raise ValueError(f"Unsupported Range: {Range}")

    def head_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        obj = self.objects.get((Bucket, Key))
        if obj is None:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return {"ContentLength": len(obj)}


def _pack_to_fake_s3(
    tmp_path: Path,
    payloads: dict[str, bytes],
    created_at: datetime,
    client: FakeS3Client,
    prefix: str = "",
    max_shard_size: int = 8,
) -> None:
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

    pack_files_to_directory(files, tmp_path, PlannerConfig(max_shard_size_bytes=max_shard_size))
    for shard_file in tmp_path.glob("*.des"):
        key = f"{prefix}{shard_file.name}"
        client.put_object(Bucket="test-bucket", Key=key, Body=shard_file.read_bytes())


def test_s3_retriever_happy_path(tmp_path: Path) -> None:
    payloads = {"100": b"a", "356": b"b"}
    created = datetime(2024, 1, 1)
    client = FakeS3Client()
    _pack_to_fake_s3(tmp_path, payloads, created, client, prefix="des/")

    storage = S3ShardStorage(S3Config(bucket="test-bucket", prefix="des/"), client=client)
    retriever = S3ShardRetriever(storage, n_bits=8)

    for uid, data in payloads.items():
        assert retriever.has_file(uid, created) is True
        assert retriever.get_file(uid, created) == data


def test_s3_retriever_not_found(tmp_path: Path) -> None:
    payloads = {"100": b"a"}
    created = datetime(2024, 1, 1)
    client = FakeS3Client()
    _pack_to_fake_s3(tmp_path, payloads, created, client)

    storage = S3ShardStorage(S3Config(bucket="test-bucket", prefix=""), client=client)
    retriever = S3ShardRetriever(storage, n_bits=8)

    assert retriever.has_file("999", created) is False
    with pytest.raises(KeyError):
        retriever.get_file("999", created)


def test_s3_retriever_multiple_shards_same_key(tmp_path: Path) -> None:
    payloads = {
        "100": b"a" * 5,
        "356": b"b" * 5,
        "612": b"c" * 5,
        "868": b"d" * 5,
    }
    created = datetime(2024, 1, 1)
    client = FakeS3Client()
    _pack_to_fake_s3(tmp_path, payloads, created, client, max_shard_size=8)

    storage = S3ShardStorage(S3Config(bucket="test-bucket"), client=client)
    retriever = S3ShardRetriever(storage, n_bits=8)

    for uid, data in payloads.items():
        assert retriever.has_file(uid, created)
        assert retriever.get_file(uid, created) == data


def test_s3_retriever_prefers_extended_retention() -> None:
    client = FakeS3Client()
    created = datetime(2024, 1, 1, tzinfo=timezone.utc)
    ext_key = "_ext_retention/20240101/uid_2024-01-01T00:00:00Z.dat"
    client.put_object(Bucket="test-bucket", Key=ext_key, Body=b"from-ext")

    storage = S3ShardStorage(S3Config(bucket="test-bucket"), client=client)
    retriever = S3ShardRetriever(storage, n_bits=8)

    assert retriever.get_file("uid", created) == b"from-ext"
