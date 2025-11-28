from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from des_core.packer_planner import FileToPack, PlannerConfig
from des_core.s3_packer import S3PackerResult, pack_files_to_s3
from des_core.s3_retriever import S3Config


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, Bucket: str, Key: str, Body: bytes) -> None:
        self.objects[(Bucket, Key)] = Body


def make_s3_config(prefix: str = "") -> S3Config:
    return S3Config(bucket="test-bucket", prefix=prefix, region_name="us-east-1", endpoint_url=None)


def _create_files(tmp_path: Path, payloads: dict[str, bytes], created_at: datetime) -> list[FileToPack]:
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


def test_pack_files_to_s3_uploads_shards(tmp_path: Path) -> None:
    payloads = {"100": b"a", "356": b"b"}
    created = datetime(2024, 1, 1)
    files = _create_files(tmp_path, payloads, created)

    fake_client = FakeS3Client()
    config = make_s3_config(prefix="des")
    result = pack_files_to_s3(
        files,
        PlannerConfig(max_shard_size_bytes=16),
        config,
        tmp_dir=tmp_path / "shards",
        delete_local=False,
        client=fake_client,
    )

    assert isinstance(result, S3PackerResult)
    assert len(result.uploaded) >= 1
    assert len(fake_client.objects) == len(result.uploaded)

    for uploaded in result.uploaded:
        assert uploaded.bucket == "test-bucket"
        assert uploaded.key.startswith("des/")
        local_bytes = uploaded.shard.path.read_bytes()
        assert fake_client.objects[(uploaded.bucket, uploaded.key)] == local_bytes


def test_pack_files_to_s3_prefix_normalization(tmp_path: Path) -> None:
    payloads = {"100": b"a"}
    created = datetime(2024, 1, 1)
    files = _create_files(tmp_path, payloads, created)

    for prefix in ["des-prefix", "des-prefix/"]:
        fake_client = FakeS3Client()
        config = make_s3_config(prefix=prefix)
        result = pack_files_to_s3(
            files,
            PlannerConfig(max_shard_size_bytes=16),
            config,
            tmp_dir=tmp_path / f"shards-{prefix}",
            delete_local=True,
            client=fake_client,
        )
        assert len(result.uploaded) == 1
        key = next(iter(fake_client.objects.keys()))[1]
        assert key.startswith("des-prefix/")


def test_pack_files_to_s3_empty_input(tmp_path: Path) -> None:
    fake_client = FakeS3Client()
    config = make_s3_config()

    result = pack_files_to_s3(
        [],
        PlannerConfig(),
        config,
        tmp_dir=tmp_path / "shards-empty",
        delete_local=True,
        client=fake_client,
    )

    assert result.uploaded == []
    assert fake_client.objects == {}
