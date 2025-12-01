import io
from datetime import datetime, timezone

from botocore.exceptions import ClientError
from botocore.response import StreamingBody

from des_core.config import S3SourceConfig
from des_core.packer import pack_files_to_directory
from des_core.packer_planner import FileToPack, PlannerConfig
from des_core import s3_file_reader
from des_core.shard_io import ShardReader


class _FakeS3Client:
    def __init__(self, objects: dict[tuple[str, str], bytes]):
        self._objects = objects

    def get_object(self, Bucket: str, Key: str):
        try:
            data = self._objects[(Bucket, Key)]
        except KeyError as exc:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "missing"}},
                operation_name="GetObject",
            ) from exc
        return {"Body": StreamingBody(io.BytesIO(data), len(data))}


def test_pack_files_reads_from_s3_sources(monkeypatch, tmp_path):
    payload = b"s3-file-contents"
    fake_client = _FakeS3Client({("bucket", "path/to/file") : payload})

    def fake_boto_client(service_name, **kwargs):
        assert service_name == "s3"
        return fake_client

    monkeypatch.setattr(s3_file_reader.boto3, "client", fake_boto_client)

    cfg = S3SourceConfig(enabled=True, region_name="us-east-1", max_retries=0, retry_delay_seconds=0.01)
    file = FileToPack(
        uid="uid-1",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        size_bytes=len(payload),
        source_path="s3://bucket/path/to/file",
    )

    result = pack_files_to_directory(
        [file],
        tmp_path,
        PlannerConfig(max_shard_size_bytes=10_000, n_bits=8),
        s3_source_config=cfg,
    )

    shard_path = result.shards[0].path
    with ShardReader.from_path(shard_path) as reader:
        assert reader.read_file("uid-1") == payload


def test_pack_files_raises_when_s3_not_enabled(tmp_path):
    payload = b"data"
    file = FileToPack(
        uid="uid-2",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        size_bytes=len(payload),
        source_path="s3://bucket/path/to/file",
    )

    # No S3 config provided => error when encountering an S3 source.
    try:
        pack_files_to_directory(
            [file],
            tmp_path,
            PlannerConfig(max_shard_size_bytes=10_000, n_bits=8),
        )
    except ValueError as exc:
        assert "S3 source config" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("Expected ValueError for missing S3 configuration")
