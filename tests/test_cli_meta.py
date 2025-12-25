from __future__ import annotations

import io
from datetime import datetime
from typing import Iterable

import pytest
from botocore.exceptions import ClientError
from click.testing import CliRunner

from des_core import cli_meta


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code}}, "HeadObject")


class DummyProgress:
    def __init__(self, items: Iterable[str], label: str | None = None) -> None:
        self._items = list(items)

    def __enter__(self) -> list[str]:
        return list(self._items)

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def test_is_not_found_error() -> None:
    for code in ("404", "NoSuchKey", "NotFound"):
        assert cli_meta._is_not_found_error(_client_error(code)) is True
    assert cli_meta._is_not_found_error(_client_error("AccessDenied")) is False


def test_list_shard_keys_filters_des() -> None:
    pages = [
        {"Contents": [{"Key": "shards/a.des"}, {"Key": "shards/b.txt"}, {"Key": None}]},
        {"Contents": [{"Key": "shards/c.des"}]},
    ]

    class DummyPaginator:
        def paginate(self, **kwargs: object) -> list[dict[str, list[dict[str, str | None]]]]:
            return pages

    class DummyS3:
        def get_paginator(self, name: str) -> DummyPaginator:
            assert name == "list_objects_v2"
            return DummyPaginator()

    keys = cli_meta._list_shard_keys(DummyS3(), "bucket", "shards/")

    assert keys == ["shards/a.des", "shards/c.des"]


def test_meta_exists_handles_not_found() -> None:
    class DummyS3:
        def __init__(self, error: ClientError | None) -> None:
            self._error = error

        def head_object(self, **kwargs: object) -> None:
            if self._error is not None:
                raise self._error

    assert cli_meta._meta_exists(DummyS3(None), "bucket", "key.meta") is True
    assert cli_meta._meta_exists(DummyS3(_client_error("404")), "bucket", "key.meta") is False

    with pytest.raises(ClientError):
        cli_meta._meta_exists(DummyS3(_client_error("AccessDenied")), "bucket", "key.meta")


def test_generate_rebuilds_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    rebuilt: list[str] = []

    class DummyManager:
        def __init__(self, s3: object, bucket: str) -> None:
            pass

        def _rebuild_metadata(self, shard_key: str) -> None:
            rebuilt.append(shard_key)

    monkeypatch.setattr(cli_meta, "MetadataManager", DummyManager)
    monkeypatch.setattr(cli_meta, "_list_shard_keys", lambda s3, bucket, prefix: ["shards/a.des", "shards/b.des"])
    monkeypatch.setattr(cli_meta, "_meta_exists", lambda s3, bucket, key: key.endswith("a.meta"))
    monkeypatch.setattr(cli_meta.boto3, "client", lambda service: object())
    monkeypatch.setattr(cli_meta.click, "progressbar", DummyProgress)

    runner = CliRunner()
    result = runner.invoke(cli_meta.meta, ["generate", "--bucket", "bucket", "--prefix", "shards/"])

    assert result.exit_code == 0
    assert rebuilt == ["shards/b.des"]


def test_verify_reports_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyMeta:
        index = {"uid-1:2024-01-01T00:00:00Z": {}}

    class DummyManager:
        def __init__(self, s3: object, bucket: str) -> None:
            pass

        def get_metadata(self, shard: str, rebuild_on_missing: bool = False) -> DummyMeta:
            return DummyMeta()

    class DummyShardReader:
        def __init__(self, index: dict[str, object]) -> None:
            self.index = index

        @classmethod
        def from_bytes(cls, data: bytes) -> "DummyShardReader":
            return cls({"uid-1": {}, "uid-2": {}})

    class DummyS3:
        def get_object(self, **kwargs: object) -> dict[str, object]:
            return {"Body": io.BytesIO(b"data")}

    monkeypatch.setattr(cli_meta, "MetadataManager", DummyManager)
    monkeypatch.setattr(cli_meta, "ShardReader", DummyShardReader)
    monkeypatch.setattr(cli_meta.boto3, "client", lambda service: DummyS3())

    runner = CliRunner()
    result = runner.invoke(cli_meta.meta, ["verify", "--bucket", "bucket", "--shard", "shards/a.des"])

    assert result.exit_code == 1
    assert "Missing in metadata: 1" in result.output


def test_verify_reports_match(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyMeta:
        index = {"uid-1:2024-01-01T00:00:00Z": {}}

    class DummyManager:
        def __init__(self, s3: object, bucket: str) -> None:
            pass

        def get_metadata(self, shard: str, rebuild_on_missing: bool = False) -> DummyMeta:
            return DummyMeta()

    class DummyShardReader:
        def __init__(self, index: dict[str, object]) -> None:
            self.index = index

        @classmethod
        def from_bytes(cls, data: bytes) -> "DummyShardReader":
            return cls({"uid-1": {}})

    class DummyS3:
        def get_object(self, **kwargs: object) -> dict[str, object]:
            return {"Body": io.BytesIO(b"data")}

    monkeypatch.setattr(cli_meta, "MetadataManager", DummyManager)
    monkeypatch.setattr(cli_meta, "ShardReader", DummyShardReader)
    monkeypatch.setattr(cli_meta.boto3, "client", lambda service: DummyS3())

    runner = CliRunner()
    result = runner.invoke(cli_meta.meta, ["verify", "--bucket", "bucket", "--shard", "shards/a.des"])

    assert result.exit_code == 0
    assert "Metadata matches shard index." in result.output


def test_verify_checksums_reports_missing_checksum(monkeypatch: pytest.MonkeyPatch) -> None:
    index = {
        "uid-1:2024-01-01T00:00:00Z": {
            "uid": "uid-1",
            "meta": {"created_at": "2024-01-01T00:00:00Z"},
            "checksum": "abc",
        },
        "uid-2:2024-01-02T00:00:00Z": {"uid": "uid-2", "checksum": None},
    }

    class DummyMeta:
        def __init__(self, index: dict[str, dict[str, object]]) -> None:
            self.index = index

    class DummyManager:
        def __init__(self, s3: object, bucket: str) -> None:
            pass

        def get_metadata(self, shard: str, rebuild_on_missing: bool = False) -> DummyMeta:
            return DummyMeta(index)

        def _fetch_entry_payload(self, shard: str, entry: object) -> bytes:
            return b"payload"

        def verify_entry_checksum(self, shard: str, uid: str, created_at: datetime, data: bytes) -> bool:
            return uid == "uid-1"

    monkeypatch.setattr(cli_meta, "MetadataManager", DummyManager)
    monkeypatch.setattr(cli_meta.boto3, "client", lambda service: object())
    monkeypatch.setattr(cli_meta.click, "progressbar", DummyProgress)

    runner = CliRunner()
    result = runner.invoke(
        cli_meta.meta,
        ["verify-checksums", "--bucket", "bucket", "--shard", "shards/a.des", "--sample-size", "10"],
    )

    assert result.exit_code == 0
    assert "Verified: 1" in result.output
    assert "Corrupted: 0" in result.output
    assert "Missing checksum: 1" in result.output


def test_verify_checksums_exits_on_corruption(monkeypatch: pytest.MonkeyPatch) -> None:
    index = {
        "entry-1:2024-01-01T00:00:00Z": {"meta": {"created_at": "2024-01-01T00:00:00Z"}},
    }

    class DummyMeta:
        def __init__(self, index: dict[str, dict[str, object]]) -> None:
            self.index = index

    class DummyManager:
        def __init__(self, s3: object, bucket: str) -> None:
            pass

        def get_metadata(self, shard: str, rebuild_on_missing: bool = False) -> DummyMeta:
            return DummyMeta(index)

    monkeypatch.setattr(cli_meta, "MetadataManager", DummyManager)
    monkeypatch.setattr(cli_meta.boto3, "client", lambda service: object())
    monkeypatch.setattr(cli_meta.click, "progressbar", DummyProgress)

    runner = CliRunner()
    result = runner.invoke(cli_meta.meta, ["verify-checksums", "--bucket", "bucket", "--shard", "shards/a.des"])

    assert result.exit_code == 1
    assert "Missing uid in entry entry-1:2024-01-01T00:00:00Z" in result.output
