from datetime import datetime, timezone
from pathlib import Path

from botocore.exceptions import ClientError

from des_core.metadata_manager import MetadataManager
from des_core.shard_io import ShardWriter
from des_core.shard_metadata import ShardMetadata


class FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.get_calls = 0

    def put_object(self, Bucket: str, Key: str, Body: bytes, **kwargs) -> None:
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket: str, Key: str, **kwargs):
        self.get_calls += 1
        data = self.objects.get((Bucket, Key))
        if data is None:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "Not Found"}}, "GetObject")
        return {"Body": FakeBody(data), "ContentLength": len(data), "LastModified": datetime.now(timezone.utc)}

    def head_object(self, Bucket: str, Key: str, **kwargs):
        data = self.objects.get((Bucket, Key))
        if data is None:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return {"ContentLength": len(data)}


def _build_shard(tmp_path: Path) -> bytes:
    shard_path = tmp_path / "20240101_39_0000.des"
    with ShardWriter(shard_path) as writer:
        writer.add_file("uid-1", b"payload")
    return shard_path.read_bytes()


def test_get_metadata_with_cache(tmp_path: Path) -> None:
    created = datetime(2024, 1, 1, tzinfo=timezone.utc)
    shard_key = "20240101_39_0000.des"
    meta_key = "20240101_39_0000.meta"
    key = ShardMetadata.build_key("uid-1", created)
    meta = ShardMetadata(
        version=1,
        shard_file=shard_key,
        shard_size=10,
        created_at=created,
        last_updated=created,
        index={key: {"uid": "uid-1", "offset": 0}},
        tombstones={},
    )

    client = FakeS3Client()
    client.put_object(Bucket="bucket", Key=meta_key, Body=meta.to_json().encode("utf-8"))
    manager = MetadataManager(client, bucket="bucket")

    manager.get_metadata(shard_key)
    manager.get_metadata(shard_key)

    assert client.get_calls == 1


def test_rebuild_metadata(tmp_path: Path) -> None:
    shard_key = "20240101_39_0000.des"
    client = FakeS3Client()
    client.put_object(Bucket="bucket", Key=shard_key, Body=_build_shard(tmp_path))
    manager = MetadataManager(client, bucket="bucket")

    meta = manager.get_metadata(shard_key)
    meta_key = "20240101_39_0000.meta"
    stored = client.objects.get(("bucket", meta_key))

    assert stored is not None
    loaded = ShardMetadata.from_json(stored.decode("utf-8"))
    assert loaded.tombstones == {}
    assert len(loaded.index) == len(meta.index)


def test_add_tombstone(tmp_path: Path) -> None:
    shard_key = "20240101_39_0000.des"
    client = FakeS3Client()
    client.put_object(Bucket="bucket", Key=shard_key, Body=_build_shard(tmp_path))
    manager = MetadataManager(client, bucket="bucket")

    created = datetime(2024, 1, 1, tzinfo=timezone.utc)
    manager.add_tombstone(
        shard_key=shard_key,
        uid="uid-1",
        created_at=created,
        deleted_by="tester",
        reason="GDPR",
    )

    meta_key = "20240101_39_0000.meta"
    stored = client.objects.get(("bucket", meta_key))
    assert stored is not None
    loaded = ShardMetadata.from_json(stored.decode("utf-8"))
    assert loaded.is_tombstoned("uid-1", created) is True
    assert loaded.stats["deleted_files"] == 1
