import io

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from botocore.response import StreamingBody
from botocore.stub import Stubber

from des_core.s3_file_reader import S3FileReader


def test_s3_file_reader_success_with_stub():
    import boto3

    client = boto3.client("s3", region_name="us-east-1")
    stubber = Stubber(client)
    body = StreamingBody(io.BytesIO(b"hello"), len(b"hello"))
    stubber.add_response(
        "get_object",
        {"Body": body},
        {"Bucket": "bucket", "Key": "path/to/file.txt"},
    )
    stubber.activate()

    reader = S3FileReader(client=client, max_retries=0)
    data = reader.read_file("s3://bucket/path/to/file.txt")
    assert data == b"hello"
    stubber.assert_no_pending_responses()


def test_s3_file_reader_retries_on_transient_error(monkeypatch):
    calls = {"count": 0}

    class FakeClient:
        def get_object(self, Bucket, Key):
            calls["count"] += 1
            if calls["count"] == 1:
                raise EndpointConnectionError(endpoint_url="http://example.com")
            return {"Body": StreamingBody(io.BytesIO(b"ok"), len(b"ok"))}

    reader = S3FileReader(client=FakeClient(), max_retries=1, retry_delay_seconds=0.01)
    assert reader.read_file("s3://bucket/key") == b"ok"
    assert calls["count"] == 2


def test_s3_file_reader_stops_on_access_denied():
    import boto3

    client = boto3.client("s3", region_name="us-east-1")
    stubber = Stubber(client)
    stubber.add_client_error(
        "get_object",
        service_error_code="AccessDenied",
        service_message="denied",
        http_status_code=403,
        expected_params={"Bucket": "bucket", "Key": "secret"},
    )
    stubber.activate()

    reader = S3FileReader(client=client, max_retries=2, retry_delay_seconds=0.01)
    with pytest.raises(ValueError):
        reader.read_file("s3://bucket/secret")
    stubber.assert_no_pending_responses()


def test_invalid_s3_uri_rejected():
    reader = S3FileReader(max_retries=0, retry_delay_seconds=0.01)
    with pytest.raises(ValueError):
        reader.read_file("file:///tmp/not-s3")
