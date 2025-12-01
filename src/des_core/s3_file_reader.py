"""Read source files from S3 with retries and metrics."""

from __future__ import annotations

import time
from typing import Protocol, Tuple
from urllib.parse import urlparse

import boto3
from botocore.exceptions import BotoCoreError, ClientError, EndpointConnectionError
from botocore.response import StreamingBody

from .config import S3SourceConfig
from .metrics import (
    DES_S3_SOURCE_BYTES_DOWNLOADED,
    DES_S3_SOURCE_READ_SECONDS,
    DES_S3_SOURCE_READS_TOTAL,
)


class FileReaderProtocol(Protocol):
    """Abstract interface for reading files from any source."""

    def read_file(self, path: str) -> bytes: ...


class LocalFileReader:
    """Simple local filesystem reader."""

    def read_file(self, path: str) -> bytes:
        from pathlib import Path

        return Path(path).read_bytes()


def is_s3_uri(path: str) -> bool:
    return path.startswith("s3://")


def _parse_s3_uri(uri: str) -> Tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Invalid S3 URI: {uri!r}")
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    if not key:
        raise ValueError(f"Invalid S3 URI missing key: {uri!r}")
    if parsed.query or parsed.fragment:
        raise ValueError(f"Unexpected query/fragment in S3 URI: {uri!r}")
    return bucket, key


def _should_retry_client_error(exc: ClientError) -> bool:
    code = exc.response.get("Error", {}).get("Code")
    # Retry on transient/5xx-ish errors; do not retry on auth/not-found.
    return code not in {"AccessDenied", "403", "NoSuchKey", "404"}


def _read_streaming_body(body: StreamingBody) -> bytes:
    chunks: list[bytes] = []
    # Use a moderate chunk size to keep memory bounded for large objects.
    for chunk in iter(lambda: body.read(1024 * 1024), b""):
        chunks.append(chunk)
    return b"".join(chunks)


class S3FileReader:
    """S3-backed reader with retries and Prometheus metrics."""

    def __init__(
        self,
        *,
        region_name: str | None = None,
        endpoint_url: str | None = None,
        max_retries: int = 3,
        retry_delay_seconds: float = 2.0,
        client=None,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if retry_delay_seconds <= 0:
            raise ValueError("retry_delay_seconds must be positive")
        self._max_retries = max_retries
        self._retry_delay_seconds = retry_delay_seconds
        self._client = client or boto3.client("s3", region_name=region_name, endpoint_url=endpoint_url)

    @classmethod
    def from_config(cls, cfg: S3SourceConfig, *, client=None) -> "S3FileReader":
        return cls(
            region_name=cfg.region_name,
            endpoint_url=cfg.endpoint_url,
            max_retries=cfg.max_retries,
            retry_delay_seconds=cfg.retry_delay_seconds,
            client=client,
        )

    def read_file(self, s3_uri: str) -> bytes:
        bucket, key = _parse_s3_uri(s3_uri)
        attempt = 0
        delay = self._retry_delay_seconds
        while True:
            status = "error"
            start = time.monotonic()
            try:
                response = self._client.get_object(Bucket=bucket, Key=key)
                body = response["Body"]
                data = _read_streaming_body(body)
                DES_S3_SOURCE_READS_TOTAL.labels(status="success").inc()
                DES_S3_SOURCE_BYTES_DOWNLOADED.inc(len(data))
                status = "success"
                return data
            except ClientError as exc:
                if not _should_retry_client_error(exc) or attempt >= self._max_retries:
                    message = exc.response.get("Error", {}).get("Message", str(exc))
                    code = exc.response.get("Error", {}).get("Code", "unknown")
                    DES_S3_SOURCE_READS_TOTAL.labels(status="error").inc()
                    raise ValueError(f"Failed to read {s3_uri} (code={code}): {message}") from exc
            except (EndpointConnectionError, BotoCoreError, OSError) as exc:
                if attempt >= self._max_retries:
                    DES_S3_SOURCE_READS_TOTAL.labels(status="error").inc()
                    raise RuntimeError(f"Failed to read {s3_uri}: {exc}") from exc
            finally:
                DES_S3_SOURCE_READ_SECONDS.labels(status=status).observe(time.monotonic() - start)

            attempt += 1
            time.sleep(delay)
            delay *= 2
