from datetime import datetime, timedelta, timezone

import boto3
import pytest
from moto import mock_aws

from des_core.ext_retention import ExtendedRetentionManager


class MockRetriever:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.calls = 0

    def get_file(self, uid: str | int, created_at: datetime) -> bytes:
        self.calls += 1
        return self.payload


class MissingFileRetriever:
    def get_file(self, uid: str | int, created_at: datetime) -> bytes:
        raise KeyError(f"Missing file {uid} at {created_at.isoformat()}")


@mock_aws
def test_first_time_move_to_ext_retention() -> None:
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="test-bucket", ObjectLockEnabledForBucket=True)

    manager = ExtendedRetentionManager("test-bucket", s3)
    retriever = MockRetriever(b"test file content")

    created_at = datetime(2024, 12, 15, 10, 0, 0, tzinfo=timezone.utc)
    due_date = datetime.now(timezone.utc) + timedelta(days=365)

    result = manager.set_retention_policy(
        uid="test-uid",
        created_at=created_at,
        due_date=due_date,
        retriever=retriever,
    )

    assert result["action"] == "moved"
    assert result["location"] == "extended_retention"
    key = str(result["key"])
    assert key.startswith("_ext_retention/20241215/")

    stored = s3.get_object(Bucket="test-bucket", Key=key)["Body"].read()
    assert stored == b"test file content"
    assert retriever.calls == 1


@mock_aws
def test_update_existing_retention() -> None:
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="test-bucket", ObjectLockEnabledForBucket=True)

    manager = ExtendedRetentionManager("test-bucket", s3)
    retriever = MockRetriever(b"original")

    created_at = datetime(2024, 12, 15, 10, 0, 0, tzinfo=timezone.utc)
    first_due_date = datetime.now(timezone.utc) + timedelta(days=30)
    second_due_date = first_due_date + timedelta(days=60)

    first_result = manager.set_retention_policy(
        uid="test-uid",
        created_at=created_at,
        due_date=first_due_date,
        retriever=retriever,
    )

    assert first_result["action"] == "moved"
    retriever.calls = 0
    retriever.payload = b"should-not-be-read"

    second_result = manager.set_retention_policy(
        uid="test-uid",
        created_at=created_at,
        due_date=second_due_date,
        retriever=retriever,
    )

    assert second_result["action"] == "updated"
    assert retriever.calls == 0

    stored = s3.get_object(Bucket="test-bucket", Key=str(first_result["key"]))["Body"].read()
    assert stored == b"original"


@mock_aws
def test_file_not_found_in_primary_storage() -> None:
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="test-bucket", ObjectLockEnabledForBucket=True)

    manager = ExtendedRetentionManager("test-bucket", s3)
    retriever = MissingFileRetriever()

    created_at = datetime(2024, 12, 15, 10, 0, 0, tzinfo=timezone.utc)
    due_date = datetime.now(timezone.utc) + timedelta(days=10)

    with pytest.raises(FileNotFoundError):
        manager.set_retention_policy(
            uid="missing-uid",
            created_at=created_at,
            due_date=due_date,
            retriever=retriever,
        )


@mock_aws
def test_due_date_must_be_future() -> None:
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="test-bucket", ObjectLockEnabledForBucket=True)

    manager = ExtendedRetentionManager("test-bucket", s3)
    retriever = MockRetriever(b"data")

    created_at = datetime(2024, 12, 15, 10, 0, 0, tzinfo=timezone.utc)
    past_due_date = datetime.now(timezone.utc) - timedelta(days=1)

    with pytest.raises(ValueError):
        manager.set_retention_policy(
            uid="uid",
            created_at=created_at,
            due_date=past_due_date,
            retriever=retriever,
        )
