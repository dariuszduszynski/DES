from datetime import datetime, timedelta, timezone

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws
from tenacity import RetryError

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


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": f"{code} error"}}, "s3_operation")


class FlakyS3Client:
    def __init__(self, failures_before_success: int, error_code: str = "503"):
        self.failures_before_success = failures_before_success
        self.error_code = error_code
        self.put_calls = 0
        self.head_calls = 0
        self.retention_calls = 0
        self.stored_body: bytes | None = None

    def head_object(self, Bucket: str, Key: str) -> dict[str, object]:
        self.head_calls += 1
        # Simulate object not existing to force move flow.
        raise _client_error("404")

    def put_object(
        self,
        Bucket: str,
        Key: str,
        Body: bytes,
        ObjectLockMode: str,
        ObjectLockRetainUntilDate: datetime,
    ) -> dict[str, object]:
        self.put_calls += 1
        if self.put_calls <= self.failures_before_success:
            raise _client_error(self.error_code)
        self.stored_body = Body
        return {"ResponseMetadata": {"HTTPStatusCode": 200}, "Key": Key}

    def put_object_retention(self, Bucket: str, Key: str, Retention: dict[str, object]) -> dict[str, object]:
        self.retention_calls += 1
        if self.retention_calls <= self.failures_before_success:
            raise _client_error(self.error_code)
        return {"ResponseMetadata": {"HTTPStatusCode": 200}, "Key": Key, "Retention": Retention}


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


def test_s3_retry_on_transient_error() -> None:
    flaky_client = FlakyS3Client(failures_before_success=2, error_code="503")
    manager = ExtendedRetentionManager("test-bucket", flaky_client)
    retriever = MockRetriever(b"retry-me")

    created_at = datetime(2024, 12, 15, 10, 0, 0, tzinfo=timezone.utc)
    due_date = datetime.now(timezone.utc) + timedelta(days=5)

    result = manager.set_retention_policy(
        uid="retry-uid",
        created_at=created_at,
        due_date=due_date,
        retriever=retriever,
    )

    assert result["action"] == "moved"
    assert flaky_client.put_calls == 3  # 2 failures + 1 success
    assert retriever.calls == 3
    assert flaky_client.stored_body == b"retry-me"


def test_s3_fails_after_max_retries() -> None:
    failing_client = FlakyS3Client(failures_before_success=10, error_code="503")
    manager = ExtendedRetentionManager("test-bucket", failing_client)
    retriever = MockRetriever(b"data")

    created_at = datetime(2024, 12, 15, 10, 0, 0, tzinfo=timezone.utc)
    due_date = datetime.now(timezone.utc) + timedelta(days=5)

    with pytest.raises(RetryError):
        manager.set_retention_policy(
            uid="retry-uid",
            created_at=created_at,
            due_date=due_date,
            retriever=retriever,
        )

    assert failing_client.put_calls == 3  # Max retry attempts
    assert retriever.calls == 3


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
