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


def _make_client(retriever: S3ShardRetriever, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(http_retriever, "build_retriever_from_settings", lambda _: retriever)
    settings = HttpRetrieverSettings(backend="s3", s3_bucket="test-bucket", delete_api_key="secret")
    app = create_app(settings)
    return TestClient(app)


def test_delete_file_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    created = datetime(2024, 1, 1, tzinfo=timezone.utc)
    client = FakeS3Client()
    shard_keys = _pack_to_fake_s3(tmp_path, {"uid-1": b"data"}, created, client)

    storage = S3ShardStorage(S3Config(bucket="test-bucket"), client=client)
    metadata_manager = MetadataManager(client, bucket="test-bucket")
    retriever = S3ShardRetriever(storage, n_bits=8, metadata_manager=metadata_manager)

    api = _make_client(retriever, monkeypatch)
    resp = api.delete(
        "/files/uid-1",
        params={"created_at": created.isoformat(), "deleted_by": "admin", "reason": "GDPR"},
        headers={"X-API-Key": "secret"},
    )

    assert resp.status_code == 200
    meta = metadata_manager.get_metadata(shard_keys[0])
    assert meta.is_tombstoned("uid-1", created) is True


def test_delete_file_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    created = datetime(2024, 1, 1, tzinfo=timezone.utc)
    client = FakeS3Client()
    _pack_to_fake_s3(tmp_path, {"uid-1": b"data"}, created, client)

    storage = S3ShardStorage(S3Config(bucket="test-bucket"), client=client)
    metadata_manager = MetadataManager(client, bucket="test-bucket")
    retriever = S3ShardRetriever(storage, n_bits=8, metadata_manager=metadata_manager)

    api = _make_client(retriever, monkeypatch)
    api.delete(
        "/files/uid-1",
        params={"created_at": created.isoformat(), "deleted_by": "admin", "reason": "GDPR"},
        headers={"X-API-Key": "secret"},
    )
    resp = api.delete(
        "/files/uid-1",
        params={"created_at": created.isoformat(), "deleted_by": "admin", "reason": "GDPR"},
        headers={"X-API-Key": "secret"},
    )

    assert resp.status_code == 410


def test_delete_file_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    created = datetime(2024, 1, 1, tzinfo=timezone.utc)
    client = FakeS3Client()
    _pack_to_fake_s3(tmp_path, {"uid-1": b"data"}, created, client)

    storage = S3ShardStorage(S3Config(bucket="test-bucket"), client=client)
    metadata_manager = MetadataManager(client, bucket="test-bucket")
    retriever = S3ShardRetriever(storage, n_bits=8, metadata_manager=metadata_manager)

    api = _make_client(retriever, monkeypatch)
    resp = api.delete(
        "/files/unknown",
        params={"created_at": created.isoformat(), "deleted_by": "admin", "reason": "GDPR"},
        headers={"X-API-Key": "secret"},
    )

    assert resp.status_code == 404


def test_delete_file_no_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """DELETE without API key should return 401 Unauthorized."""

    created = datetime(2024, 1, 1, tzinfo=timezone.utc)
    client = FakeS3Client()
    _pack_to_fake_s3(tmp_path, {"uid-1": b"data"}, created, client)

    storage = S3ShardStorage(S3Config(bucket="test-bucket"), client=client)
    metadata_manager = MetadataManager(client, bucket="test-bucket")
    retriever = S3ShardRetriever(storage, n_bits=8, metadata_manager=metadata_manager)

    monkeypatch.setattr(http_retriever, "build_retriever_from_settings", lambda _: retriever)
    settings = HttpRetrieverSettings(backend="s3", s3_bucket="test-bucket", delete_api_key="secret")
    app = create_app(settings)
    api = TestClient(app)

    resp = api.delete(
        "/files/uid-1",
        params={"created_at": created.isoformat(), "deleted_by": "admin", "reason": "GDPR"},
    )

    assert resp.status_code == 401
    assert "Unauthorized" in resp.json()["detail"]


def test_delete_file_wrong_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """DELETE with wrong API key should return 401 Unauthorized."""

    created = datetime(2024, 1, 1, tzinfo=timezone.utc)
    client = FakeS3Client()
    _pack_to_fake_s3(tmp_path, {"uid-1": b"data"}, created, client)

    storage = S3ShardStorage(S3Config(bucket="test-bucket"), client=client)
    metadata_manager = MetadataManager(client, bucket="test-bucket")
    retriever = S3ShardRetriever(storage, n_bits=8, metadata_manager=metadata_manager)

    monkeypatch.setattr(http_retriever, "build_retriever_from_settings", lambda _: retriever)
    settings = HttpRetrieverSettings(backend="s3", s3_bucket="test-bucket", delete_api_key="secret")
    app = create_app(settings)
    api = TestClient(app)

    resp = api.delete(
        "/files/uid-1",
        params={"created_at": created.isoformat(), "deleted_by": "admin", "reason": "GDPR"},
        headers={"X-API-Key": "wrong-key"},
    )

    assert resp.status_code == 401
    assert "Unauthorized" in resp.json()["detail"]


def test_delete_file_invalid_created_at(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """DELETE with invalid created_at should return 400 Bad Request."""

    client = FakeS3Client()
    storage = S3ShardStorage(S3Config(bucket="test-bucket"), client=client)
    metadata_manager = MetadataManager(client, bucket="test-bucket")
    retriever = S3ShardRetriever(storage, n_bits=8, metadata_manager=metadata_manager)

    monkeypatch.setattr(http_retriever, "build_retriever_from_settings", lambda _: retriever)
    settings = HttpRetrieverSettings(backend="s3", s3_bucket="test-bucket", delete_api_key="secret")
    app = create_app(settings)
    api = TestClient(app)

    resp = api.delete(
        "/files/uid-1",
        params={"created_at": "not-a-date", "deleted_by": "admin", "reason": "GDPR"},
        headers={"X-API-Key": "secret"},
    )

    assert resp.status_code == 400
    assert "Invalid created_at format" in resp.json()["detail"]


def test_delete_file_missing_deleted_by(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """DELETE without deleted_by should return 400 Bad Request."""

    created = datetime(2024, 1, 1, tzinfo=timezone.utc)
    client = FakeS3Client()
    _pack_to_fake_s3(tmp_path, {"uid-1": b"data"}, created, client)

    storage = S3ShardStorage(S3Config(bucket="test-bucket"), client=client)
    metadata_manager = MetadataManager(client, bucket="test-bucket")
    retriever = S3ShardRetriever(storage, n_bits=8, metadata_manager=metadata_manager)

    monkeypatch.setattr(http_retriever, "build_retriever_from_settings", lambda _: retriever)
    settings = HttpRetrieverSettings(backend="s3", s3_bucket="test-bucket", delete_api_key="secret")
    app = create_app(settings)
    api = TestClient(app)

    resp = api.delete(
        "/files/uid-1",
        params={"created_at": created.isoformat(), "deleted_by": "", "reason": "GDPR"},
        headers={"X-API-Key": "secret"},
    )

    assert resp.status_code == 400
    assert "deleted_by is required" in resp.json()["detail"]


def test_delete_file_api_not_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """DELETE when delete_api_key not configured should return 503."""

    created = datetime(2024, 1, 1, tzinfo=timezone.utc)
    client = FakeS3Client()
    storage = S3ShardStorage(S3Config(bucket="test-bucket"), client=client)
    retriever = S3ShardRetriever(storage, n_bits=8)

    monkeypatch.setattr(http_retriever, "build_retriever_from_settings", lambda _: retriever)
    settings = HttpRetrieverSettings(backend="s3", s3_bucket="test-bucket", delete_api_key=None)
    app = create_app(settings)
    api = TestClient(app)

    resp = api.delete(
        "/files/uid-1",
        params={"created_at": created.isoformat(), "deleted_by": "admin", "reason": "GDPR"},
    )

    assert resp.status_code == 503
    assert "Delete API not configured" in resp.json()["detail"]
