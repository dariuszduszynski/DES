from datetime import datetime
from pathlib import Path

from des_core.compression import balanced_zstd_config
from des_core.s3_retriever import S3Config, S3ShardRetriever, S3ShardStorage
from des_core.shard_io import FOOTER_SIZE, HEADER_SIZE, ShardWriter


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


def _build_shard(tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    payloads = {"file-a": b"A" * 64, "file-b": b"B" * 128}
    shard_path = tmp_path / "test-shard.des"
    with ShardWriter(shard_path, compression=balanced_zstd_config()) as writer:
        for uid, data in payloads.items():
            writer.add_file(uid, data)
    return shard_path, payloads


def test_s3_retriever_uses_range_requests(tmp_path: Path) -> None:
    shard_path, payloads = _build_shard(tmp_path)
    data_by_key = {"20240101_39_0000.des": shard_path.read_bytes()}
    fake_client = FakeS3Client(data_by_key)

    storage = S3ShardStorage(S3Config(bucket="bucket"), client=fake_client)
    storage.list_candidate_keys = lambda *_: list(data_by_key.keys())  # type: ignore
    retriever = S3ShardRetriever(storage, n_bits=8)

    created = datetime(2024, 1, 1)
    data = retriever.get_file("file-a", created)

    assert data == payloads["file-a"]
    assert len(fake_client.calls) == 4
    assert fake_client.calls[0]["Range"] == f"bytes=0-{HEADER_SIZE - 1}"
    assert fake_client.calls[1]["Range"] == f"bytes=-{FOOTER_SIZE}"
    # verify index range call starts at computed offset
    index_call = fake_client.calls[2]["Range"]
    assert index_call.startswith("bytes=")
    # payload range should include offset and length
    payload_call = fake_client.calls[3]["Range"]
    assert payload_call.startswith("bytes=")


def test_multiple_gets_issue_separate_range_calls(tmp_path: Path) -> None:
    shard_path, payloads = _build_shard(tmp_path)
    data_by_key = {"20240101_39_0000.des": shard_path.read_bytes()}
    fake_client = FakeS3Client(data_by_key)

    storage = S3ShardStorage(S3Config(bucket="bucket"), client=fake_client)
    storage.list_candidate_keys = lambda *_: list(data_by_key.keys())  # type: ignore
    retriever = S3ShardRetriever(storage, n_bits=8)
    created = datetime(2024, 1, 1)

    retriever.get_file("file-a", created)
    retriever.get_file("file-b", created)

    assert len(fake_client.calls) == 5  # header/footer/index/payload first, payload only second (cached index)
