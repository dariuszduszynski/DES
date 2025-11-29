"""Orchestrates the full migration flow: fetch -> validate -> pack -> mark -> cleanup."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Protocol

from .db_connector import ArchiveStatistics, SourceFileRecord
from .metrics import (
    DES_MIGRATION_BATCH_SIZE,
    DES_MIGRATION_BYTES_TOTAL,
    DES_MIGRATION_CYCLES_TOTAL,
    DES_MIGRATION_DURATION_SECONDS,
    DES_MIGRATION_FILES_TOTAL,
    DES_MIGRATION_PENDING_FILES,
)
from .packer import PackerResult
from .packer_planner import FileToPack

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MigrationResult:
    """Aggregate result of a single migration cycle."""

    files_processed: int
    files_migrated: int
    files_failed: int
    shards_created: int
    total_size_bytes: int
    duration_seconds: float
    errors: List[str]


class PackerInterface(Protocol):
    """Interface for packer implementations used by the orchestrator."""

    def pack_files(self, files: List[FileToPack]) -> PackerResult: ...


class SourceDatabaseInterface(Protocol):
    """Subset of SourceDatabase used by the orchestrator."""

    def fetch_files_to_archive(self, cutoff_date: datetime, limit: int | None = None) -> List[SourceFileRecord]: ...

    def mark_as_archived(self, uids: List[str]) -> int: ...

    def get_archive_statistics(self, cutoff_date: datetime) -> ArchiveStatistics: ...


class MigrationOrchestrator:
    """Coordinates fetching, validating, packing, marking, and optional cleanup of source files."""

    def __init__(
        self,
        db: SourceDatabaseInterface,
        packer: PackerInterface,
        archive_age_days: int,
        batch_size: int,
        delete_source_files: bool = False,
    ) -> None:
        self._db = db
        self._packer = packer
        self._archive_age_days = archive_age_days
        self._batch_size = batch_size
        self._delete_source_files = delete_source_files
        DES_MIGRATION_BATCH_SIZE.set(batch_size)

    def run_migration_cycle(self) -> MigrationResult:
        """Execute a full migration cycle.

        Steps:
        1) Fetch candidates from SourceDatabase.
        2) Validate local files.
        3) Pack validated files into DES shards.
        4) Mark successfully migrated files as archived.
        5) Optionally delete source files.
        """

        start = time.monotonic()
        status = "success"
        result: MigrationResult | None = None
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._archive_age_days)
        self._update_pending_metrics(cutoff)
        try:
            result = self._execute_cycle(cutoff)
            DES_MIGRATION_FILES_TOTAL.inc(result.files_processed)
            DES_MIGRATION_BYTES_TOTAL.inc(result.total_size_bytes)
            return result
        except Exception:
            status = "failure"
            raise
        finally:
            DES_MIGRATION_CYCLES_TOTAL.labels(status=status).inc()
            DES_MIGRATION_DURATION_SECONDS.observe(time.monotonic() - start)

    def _execute_cycle(self, cutoff: datetime) -> MigrationResult:
        start = time.monotonic()
        errors: List[str] = []
        files_processed = 0
        files_migrated = 0
        files_failed = 0
        shards_created = 0
        total_size_bytes = 0
        migrated_uids: List[str] = []
        valid_files: List[FileToPack] = []
        file_paths_for_cleanup: List[Path] = []

        records = self._db.fetch_files_to_archive(cutoff, limit=self._batch_size)

        files_processed = len(records)
        logger.info("Fetched %d file(s) to archive (cutoff=%s)", files_processed, cutoff.isoformat())
        if not records:
            duration = time.monotonic() - start
            return MigrationResult(
                files_processed=0,
                files_migrated=0,
                files_failed=0,
                shards_created=0,
                total_size_bytes=0,
                duration_seconds=duration,
                errors=errors,
            )

        # Validation
        for record in records:
            validation_error = self._validate_record(record)
            if validation_error:
                files_failed += 1
                errors.append(validation_error)
                logger.warning(validation_error)
                continue

            source_path = Path(record.file_location)
            size_bytes = record.size_bytes if record.size_bytes is not None else source_path.stat().st_size
            file_paths_for_cleanup.append(source_path)
            valid_files.append(
                FileToPack(
                    uid=record.uid,
                    created_at=record.created_at,
                    size_bytes=size_bytes,
                    source_path=str(source_path),
                )
            )

        # Packing per file to isolate failures
        for file in valid_files:
            try:
                result = self._packer.pack_files([file])
                files_migrated += 1
                total_size_bytes += file.size_bytes
                shards_created += len(result.shards)
                migrated_uids.append(file.uid)
                logger.info("Packed file %s into %d shard(s)", file.uid, len(result.shards))
            except Exception as exc:
                files_failed += 1
                msg = f"Packing failed for {file.uid}: {exc}"
                errors.append(msg)
                logger.error(msg)

        # Mark as archived
        if migrated_uids:
            try:
                updated = self._db.mark_as_archived(migrated_uids)
                logger.info("Marked %d/%d files as archived", updated, len(migrated_uids))
            except Exception as exc:  # pragma: no cover - depends on DB failure
                msg = f"Failed to mark files as archived: {exc}"
                errors.append(msg)
                logger.error(msg)

        # Cleanup
        if self._delete_source_files:
            for path in file_paths_for_cleanup:
                try:
                    path.unlink(missing_ok=True)
                    logger.info("Deleted source file %s", path)
                except Exception as exc:
                    msg = f"Failed to delete {path}: {exc}"
                    errors.append(msg)
                    logger.error(msg)

        duration = time.monotonic() - start
        return MigrationResult(
            files_processed=files_processed,
            files_migrated=files_migrated,
            files_failed=files_failed,
            shards_created=shards_created,
            total_size_bytes=total_size_bytes,
            duration_seconds=duration,
            errors=errors,
        )

    def _validate_record(self, record: SourceFileRecord) -> str | None:
        path = Path(record.file_location)
        if not path.exists():
            return f"Validation failed for {record.uid}: file does not exist at {path}"
        if record.size_bytes is not None:
            actual_size = path.stat().st_size
            if actual_size != record.size_bytes:
                return (
                    f"Validation failed for {record.uid}: size mismatch (expected {record.size_bytes}, got {actual_size})"
                )
        return None

    def _update_pending_metrics(self, cutoff: datetime) -> None:
        try:
            stats = self._db.get_archive_statistics(cutoff)
            total_files_raw = stats.get("total_files", 0)
            try:
                total_files = int(total_files_raw)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                total_files = 0
            DES_MIGRATION_PENDING_FILES.set(total_files)
        except Exception as exc:
            logger.debug("Failed to update pending metrics: %s", exc)
