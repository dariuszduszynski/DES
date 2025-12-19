from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

import des_core.http_retriever as http_retriever
from des_core.http_retriever import HttpRetrieverSettings, create_app
from des_core.metadata_manager import MetadataManager
from des_core.packer import pack_files_to_directory
from des_core.packer_planner import FileToPack, PlannerConfig
from des_core.s3_retriever import S3Config, S3ShardRetriever, S3ShardStorage
from des_core.shard_metadata import TombstoneError


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, Bucket: str, Key: str, Body: bytes, **kwargs: Any) -> None:
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
        data = self.objects.get((Bucket, Key))
        if data is None:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "Not Found"}}, "GetObject")

        if Range is None or Range == "":
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
) -> list[str]:
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
    keys = []
    for shard_file in tmp_path.glob("*.des"):
        key = f"{prefix}{shard_file.name}"
        client.put_object(Bucket="test-bucket", Key=key, Body=shard_file.read_bytes())
        keys.append(key)
    return keys


def test_tombstone_lifecycle_e2e(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end test for tombstone lifecycle."""

    test_data = b"test data for e2e"
    uid = "e2e-test-uid"
    created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)

    client = FakeS3Client()
    shard_keys = _pack_to_fake_s3(tmp_path, {uid: test_data}, created_at, client)
    shard_key = shard_keys[0]

    storage = S3ShardStorage(S3Config(bucket="test-bucket"), client=client)
    manager = MetadataManager(client, bucket="test-bucket")
    retriever = S3ShardRetriever(storage, n_bits=8, metadata_manager=manager)

    monkeypatch.setattr(http_retriever, "build_retriever_from_settings", lambda _: retriever)
    settings = HttpRetrieverSettings(backend="s3", s3_bucket="test-bucket", delete_api_key="secret")
    app = create_app(settings)
    api = TestClient(app)

    meta = manager._rebuild_metadata(shard_key)
    assert len(meta.index) == 1
    assert len(meta.tombstones) == 0

    data = retriever.get_file(uid, created_at)
    assert data == test_data

    response = api.delete(
        f"/files/{uid}",
        params={"created_at": created_at.isoformat(), "deleted_by": "e2e-test", "reason": "GDPR"},
        headers={"X-API-Key": "secret"},
    )
    assert response.status_code == 200

    with pytest.raises(TombstoneError):
        retriever.get_file(uid, created_at)

    response = api.get(f"/files/{uid}", params={"created_at": created_at.isoformat()})
    assert response.status_code == 410

    response = api.delete(
        f"/files/{uid}",
        params={"created_at": created_at.isoformat(), "deleted_by": "e2e-test", "reason": "GDPR"},
        headers={"X-API-Key": "secret"},
    )
    assert response.status_code == 410

    meta = manager.get_metadata(shard_key)
    assert meta.is_tombstoned(uid, created_at) is True
    assert len(meta.tombstones) == 1
    assert meta.stats["deleted_files"] == 1
    assert meta.stats["deletion_ratio"] == 1.0
