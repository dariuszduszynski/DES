from datetime import datetime

import pytest

import des_core.multi_s3_retriever as multi
from des_core.multi_s3_retriever import MultiS3ShardRetriever, S3ZoneConfig, S3ZoneRange
from des_core.s3_retriever import S3Config


class FakeS3ShardRetriever:
    def __init__(self, config, n_bits=8):
        self.config = config
        self.calls: list[tuple[str, str]] = []

    def get_file(self, uid: str, created_at: datetime) -> bytes:
        self.calls.append((uid, created_at.isoformat()))
        bucket = getattr(self.config, "bucket", "unknown")
        return f"data-from-{bucket}".encode("utf-8")


def make_two_zones(n_bits: int = 4):
    zone_a = S3ZoneConfig(
        name="zone-a",
        range=S3ZoneRange(start=0, end=7),
        s3_config=S3Config(bucket="bucket-a", prefix="", region_name=None, endpoint_url=None),
    )
    zone_b = S3ZoneConfig(
        name="zone-b",
        range=S3ZoneRange(start=8, end=15),
        s3_config=S3Config(bucket="bucket-b", prefix="", region_name=None, endpoint_url=None),
    )
    return [zone_a, zone_b]


def test_multi_s3_retriever_routes_to_correct_zone(monkeypatch: pytest.MonkeyPatch):
    calls = []

    class DummyLocation:
        def __init__(self, shard_index: int):
            self.shard_index = shard_index

    def fake_locate_shard(uid, created_at, n_bits):
        if uid == "uid-a":
            return DummyLocation(3)
        return DummyLocation(12)

    monkeypatch.setattr(multi, "S3ShardRetriever", FakeS3ShardRetriever)
    monkeypatch.setattr(multi, "locate_shard", fake_locate_shard)

    retriever = MultiS3ShardRetriever(make_two_zones(), n_bits=4)

    data_a = retriever.get_file("uid-a", datetime(2024, 1, 1))
    data_b = retriever.get_file("uid-b", datetime(2024, 1, 1))

    assert data_a == b"data-from-bucket-a"
    assert data_b == b"data-from-bucket-b"


def test_multi_s3_retriever_rejects_overlapping_ranges():
    overlapping = [
        S3ZoneConfig(
            name="zone-a",
            range=S3ZoneRange(start=0, end=7),
            s3_config=S3Config(bucket="bucket-a", prefix=""),
        ),
        S3ZoneConfig(
            name="zone-b",
            range=S3ZoneRange(start=7, end=10),
            s3_config=S3Config(bucket="bucket-b", prefix=""),
        ),
    ]

    with pytest.raises(ValueError):
        MultiS3ShardRetriever(overlapping, n_bits=4)


def test_multi_s3_retriever_raises_when_no_zone_matches(monkeypatch: pytest.MonkeyPatch):
    zones = [
        S3ZoneConfig(
            name="zone-a",
            range=S3ZoneRange(start=0, end=3),
            s3_config=S3Config(bucket="bucket-a", prefix=""),
        )
    ]

    monkeypatch.setattr(multi, "S3ShardRetriever", FakeS3ShardRetriever)

    class DummyLocation:
        def __init__(self, shard_index: int):
            self.shard_index = shard_index

    def fake_locate_shard(uid, created_at, n_bits):
        return DummyLocation(10)

    monkeypatch.setattr(multi, "locate_shard", fake_locate_shard)

    retriever = MultiS3ShardRetriever(zones, n_bits=4)
    with pytest.raises(KeyError):
        retriever.get_file("uid-x", datetime(2024, 1, 1))
