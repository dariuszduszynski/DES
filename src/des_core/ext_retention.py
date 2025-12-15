"""Extended retention management for DES objects stored in S3."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Protocol

import boto3
from botocore.exceptions import ClientError

from .metrics import ext_retention_files, ext_retention_moves_total, ext_retention_updates_total

logger = logging.getLogger(__name__)


class RetrieverProtocol(Protocol):
    """Interface for retrieving file bytes by UID and creation time."""

    def get_file(self, uid: str | int, created_at: datetime) -> bytes: ...


@dataclass(frozen=True)
class RetentionActionResult:
    """Outcome of an extended retention update."""

    uid: str
    key: str
    location: str
    retention_until: datetime
    action: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class ExtendedRetentionManager:
    """Manage extended retention copies in S3 with object lock enabled."""

    def __init__(
        self,
        bucket: str,
        s3_client=None,
        *,
        prefix: str = "_ext_retention",
    ) -> None:
        self.bucket = bucket
        self.s3 = s3_client or boto3.client("s3")
        self._prefix = prefix.strip("/")

    def set_retention_policy(
        self,
        uid: str,
        created_at: datetime,
        due_date: datetime,
        retriever: RetrieverProtocol,
    ) -> dict[str, object]:
        """Ensure a file is placed under extended retention.

        Args:
            uid: Unique identifier of the file.
            created_at: Original creation timestamp of the file.
            due_date: Desired retention end datetime (UTC or naive as UTC).
            retriever: Retriever capable of reading the file from primary storage.

        Returns:
            A dictionary describing the action performed.

        Raises:
            ValueError: If due_date is not in the future.
            FileNotFoundError: If the file cannot be found in primary storage.
        """

        retention_until = _ensure_utc(due_date)
        now = datetime.now(timezone.utc)
        if retention_until <= now:
            raise ValueError("due_date must be in the future")

        normalized_created_at = _ensure_utc(created_at)
        ext_key = self._build_ext_key(uid, normalized_created_at)

        if self._exists_in_ext_retention(ext_key):
            self._update_retention(ext_key, retention_until)
            ext_retention_updates_total.inc()
            logger.info("Updated retention for %s until %s", ext_key, retention_until.isoformat())
            return RetentionActionResult(
                uid=str(uid),
                key=ext_key,
                location="extended_retention",
                retention_until=retention_until,
                action="updated",
            ).to_dict()

        result = self._move_to_ext_retention(uid, normalized_created_at, retention_until, ext_key, retriever)
        return result

    def _exists_in_ext_retention(self, key: str) -> bool:
        """Return True if the object exists in the extended retention prefix."""

        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def _update_retention(self, key: str, due_date: datetime) -> None:
        """Update S3 object lock retention for an existing file."""

        self.s3.put_object_retention(
            Bucket=self.bucket,
            Key=key,
            Retention={
                "Mode": "GOVERNANCE",
                "RetainUntilDate": due_date,
            },
        )

    def _move_to_ext_retention(
        self,
        uid: str,
        created_at: datetime,
        due_date: datetime,
        ext_key: str,
        retriever: RetrieverProtocol,
    ) -> dict[str, object]:
        """Copy file bytes to the extended retention area and set retention."""

        try:
            data = retriever.get_file(uid, created_at)
        except KeyError as exc:
            raise FileNotFoundError(f"File {uid} not found for {created_at.isoformat()}") from exc

        self.s3.put_object(
            Bucket=self.bucket,
            Key=ext_key,
            Body=data,
            ObjectLockMode="GOVERNANCE",
            ObjectLockRetainUntilDate=due_date,
        )

        self._create_tombstone(uid, created_at)
        ext_retention_moves_total.inc()
        ext_retention_files.inc()
        logger.info("Moved %s to extended retention until %s", ext_key, due_date.isoformat())

        return RetentionActionResult(
            uid=str(uid),
            key=ext_key,
            location="extended_retention",
            retention_until=due_date,
            action="moved",
        ).to_dict()

    def _build_ext_key(self, uid: str | int, created_at: datetime) -> str:
        normalized = created_at.astimezone(timezone.utc)
        date_prefix = normalized.strftime("%Y%m%d")
        timestamp = normalized.isoformat().replace("+00:00", "Z")
        prefix = self._prefix or "_ext_retention"
        return f"{prefix}/{date_prefix}/{uid}_{timestamp}.dat"

    def _create_tombstone(self, uid: str, created_at: datetime) -> None:
        """Placeholder for tombstone integration."""

        logger.debug("Tombstone placeholder for uid=%s created_at=%s", uid, created_at.isoformat())
