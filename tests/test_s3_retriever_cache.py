from datetime import datetime
from pathlib import Path

from botocore.exceptions import ClientError

from des_core.cache import LRUCache, LRUCacheConfig
from des_core.compression import balanced_zstd_config
from des_core.s3_retriever import CachedIndex, IndexCacheKey, S3Config, S3ShardRetriever, S3ShardStorage
from des_core.shard_io import ShardWriter


class FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class FakeS3Client:
    def __init__(self, data_by_key: dict[str, bytes]) -> None:
        self.data_by_key = data_by_key
        self.calls: list[dict[str, str]] = []

    def get_object(self, Bucket: str, Key: str, Range: str | None = None):
        self.calls.append({"Bucket": Bucket, "Key": Key, "Range": Range or ""})
        data = self.data_by_key[Key]
        if Range is None or Range == "":
            return {"Body": FakeBody(data), "ContentLength": len(data)}

        if Range.startswith("bytes=-"):
            length = int(Range[len("bytes=-") :])
            slice_data = data[-length:]
            start = len(data) - length
            end = len(data) - 1
            return {"Body": FakeBody(slice_data), "ContentRange": f"bytes {start}-{end}/{len(data)}"}

        if Range.startswith("bytes="):
            _, spec = Range.split("=", 1)
            start_str, end_str = spec.split("-")
            start = int(start_str)
            end = int(end_str)
            slice_data = data[start : end + 1]
            return {"Body": FakeBody(slice_data), "ContentRange": f"bytes {start}-{end}/{len(data)}"}

        raise ValueError(f"Unsupported Range: {Range}")

    def list_objects_v2(self, Bucket: str, Prefix: str, **kwargs):
        return {"Contents": [{"Key": k} for k in self.data_by_key.keys() if k.startswith(Prefix)]}

    def head_object(self, Bucket: str, Key: str):
        data = self.data_by_key.get(Key)
        if data is None:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return {"ContentLength": len(data)}


def _make_shard(tmp_path: Path) -> tuple[dict[str, bytes], dict[str, bytes]]:
    payloads = {"file-a": b"A" * 64, "file-b": b"B" * 64}
    shard_path = tmp_path / "20240101_39_0000.des"
    with ShardWriter(shard_path, compression=balanced_zstd_config()) as writer:
        for uid, data in payloads.items():
            writer.add_file(uid, data)
    return {shard_path.name: shard_path.read_bytes()}, payloads


def test_second_get_uses_cached_index(tmp_path: Path) -> None:
    data_by_key, payloads = _make_shard(tmp_path)
    fake_client = FakeS3Client(data_by_key)
    cache = LRUCache[IndexCacheKey, CachedIndex](LRUCacheConfig(max_size=4))

    storage = S3ShardStorage(S3Config(bucket="bucket"), client=fake_client)
    retriever = S3ShardRetriever(storage, n_bits=8, index_cache=cache)
    storage.list_candidate_keys = lambda *_: list(data_by_key.keys())  # type: ignore
    created = datetime(2024, 1, 1)

    retriever.get_file("file-a", created)
    first_call_count = len(fake_client.calls)
    assert first_call_count == 4

    fake_client.calls.clear()
    retriever.get_file("file-b", created)
    assert len(fake_client.calls) == 1
    assert fake_client.calls[0]["Range"].startswith("bytes=")


def test_cache_eviction_respects_max_size(tmp_path: Path) -> None:
    cache = LRUCache[IndexCacheKey, CachedIndex](LRUCacheConfig(max_size=2))

    for i in range(3):
        cache.set(("bucket", f"key-{i}"), (1, {}))
    assert len(cache) == 2
