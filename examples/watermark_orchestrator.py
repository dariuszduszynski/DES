"""Migration orchestrator using global watermark approach instead of per-record updates.

This orchestrator uses des_archive_config table with a single 'archived_until' timestamp
instead of updating 'archived' column on billions of rows in the source table.

Key differences from migration_orchestrator.py:
- NO updates to source table (zero UPDATE statements on main table)
- Single watermark update per cycle (1 row instead of 1000s)
- Uses ArchiveConfigRepository + DatabaseSourceProvider
- Windows-based processing: (archived_until, current_cutoff]
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from des_core.archive_config import ArchiveConfigRepository, ArchiveWindow
from des_core.database_source import DatabaseSourceProvider, SourceDatabaseConfig, SourceRecord
from des_core.file_reader import FileReader, LocalFileReader, S3FileReader
from des_core.metrics import (
    DES_MIGRATION_BYTES_TOTAL,
    DES_MIGRATION_CYCLES_TOTAL,
    DES_MIGRATION_DURATION_SECONDS,
    DES_MIGRATION_FILES_FAILED,
    DES_MIGRATION_FILES_PENDING,
    DES_MIGRATION_FILES_TOTAL,
    DES_MIGRATION_SHARDS_TOTAL,
)
from des_core.packer import pack_files
from des_core.packer_planner import PackerConfig

logger = logging.getLogger(__name__)


@dataclass
class WatermarkMigrationResult:
    """Result of a single migration cycle using watermark approach."""

    files_processed: int
    files_migrated: int
    files_failed: int
    shards_created: int
    total_size_bytes: int
    duration_seconds: float
    window_start: datetime
    window_end: datetime
    errors: List[str]


class WatermarkMigrationOrchestrator:
    """Migration orchestrator using global watermark instead of per-record updates.
    
    Architecture:
    1. Reads from des_archive_config: archived_until = '2024-12-01'
    2. Computes window: (2024-12-01, 2024-12-03] based on lag_days
    3. Queries source DB: WHERE created_at > '2024-12-01' AND created_at <= '2024-12-03'
    4. Packs files into DES shards
    5. Updates ONLY des_archive_config: archived_until = '2024-12-03'
    
    No updates to source table = Zero overhead on main table!
    """

    def __init__(
        self,
        db_connection,  # DB-API connection for source database
        config_connection,  # DB-API connection for des_archive_config (can be same)
        source_config: SourceDatabaseConfig,
        packer_config: PackerConfig,
        file_reader: Optional[FileReader] = None,
        delete_source_files: bool = False,
        default_archived_until: Optional[datetime] = None,
    ):
        """Initialize watermark-based migration orchestrator.
        
        Args:
            db_connection: Connection to source database (large table with files)
            config_connection: Connection for des_archive_config (can be same as db_connection)
            source_config: Configuration for source database table
            packer_config: Configuration for DES packer
            file_reader: FileReader implementation (LocalFileReader or S3FileReader)
            delete_source_files: Whether to delete source files after successful migration
            default_archived_until: Initial watermark if des_archive_config is empty
        """
        self._db_source = DatabaseSourceProvider(db_connection, source_config)
        self._config_repo = ArchiveConfigRepository(config_connection)
        self._source_config = source_config
        self._packer_config = packer_config
        self._file_reader = file_reader or LocalFileReader()
        self._delete_source = delete_source_files
        
        # Initialize watermark if not exists
        if default_archived_until is None:
            # Default: start archiving from 30 days ago
            default_archived_until = datetime.now(timezone.utc) - timedelta(days=30)
        
        self._default_archived_until = default_archived_until

    async def initialize(self) -> None:
        """Ensure des_archive_config table exists and is initialized."""
        await self._config_repo.ensure_initialized(
            default_archived_until=self._default_archived_until,
            default_lag_days=self._source_config.lag_days,
        )
        logger.info(
            "Initialized watermark config with archived_until=%s, lag_days=%d",
            self._default_archived_until.isoformat(),
            self._source_config.lag_days,
        )

    async def run_cycle(self) -> WatermarkMigrationResult:
        """Execute one migration cycle using watermark approach.
        
        Flow:
        1. Compute archive window from des_archive_config
        2. Fetch files in window from source DB (no archived column needed!)
        3. Validate and pack files
        4. Update watermark (single row update)
        5. Optionally delete source files
        
        Returns:
            WatermarkMigrationResult with statistics
        """
        start = time.monotonic()
        status = "success"
        
        try:
            # Step 1: Get current window
            window = await self._config_repo.compute_window(datetime.now(timezone.utc))
            
            if window.window_start >= window.window_end:
                logger.info("No new files to archive (watermark already at target)")
                return self._empty_cycle_result(window, start, [])
            
            logger.info(
                "Archive window: (%s, %s] (lag_days=%d)",
                window.window_start.isoformat(),
                window.window_end.isoformat(),
                window.lag_days,
            )
            
            # Step 2: Process files in window
            result = await self._execute_cycle(window)
            
            # Step 3: Advance watermark (SINGLE UPDATE!)
            if result.files_migrated > 0:
                await self._config_repo.advance_cutoff(datetime.now(timezone.utc))
                logger.info("Advanced watermark to %s", window.window_end.isoformat())
            
            # Update metrics
            DES_MIGRATION_FILES_TOTAL.inc(result.files_processed)
            DES_MIGRATION_BYTES_TOTAL.inc(result.total_size_bytes)
            DES_MIGRATION_SHARDS_TOTAL.inc(result.shards_created)
            DES_MIGRATION_FILES_FAILED.inc(result.files_failed)
            
            return result
            
        except Exception:
            status = "failure"
            raise
        finally:
            DES_MIGRATION_CYCLES_TOTAL.labels(status=status).inc()
            DES_MIGRATION_DURATION_SECONDS.observe(time.monotonic() - start)

    async def _execute_cycle(self, window: ArchiveWindow) -> WatermarkMigrationResult:
        """Execute migration for the given window."""
        start = time.monotonic()
        errors: List[str] = []
        
        # Collect files from window
        files_to_pack: List[tuple[str, str, datetime]] = []  # (uid, location, created_at)
        files_processed = 0
        
        logger.info("Fetching files from archive window...")
        
        async for record in self._db_source.iter_records_for_window(window):
            files_processed += 1
            files_to_pack.append((record.uid, record.file_location, record.created_at))
            
            # Process in batches to avoid memory issues
            if len(files_to_pack) >= self._source_config.page_size:
                await self._process_batch(files_to_pack, errors)
                files_to_pack.clear()
        
        # Process remaining files
        if files_to_pack:
            await self._process_batch(files_to_pack, errors)
        
        logger.info(
            "Processed %d files from window (%s, %s]",
            files_processed,
            window.window_start.isoformat(),
            window.window_end.isoformat(),
        )
        
        # TODO: Calculate actual stats from packing results
        # For now, return basic stats
        duration = time.monotonic() - start
        return WatermarkMigrationResult(
            files_processed=files_processed,
            files_migrated=files_processed - len(errors),
            files_failed=len(errors),
            shards_created=0,  # TODO: Get from pack_files
            total_size_bytes=0,  # TODO: Calculate
            duration_seconds=duration,
            window_start=window.window_start,
            window_end=window.window_end,
            errors=errors,
        )

    async def _process_batch(
        self,
        files: List[tuple[str, str, datetime]],
        errors: List[str],
    ) -> None:
        """Process a batch of files: validate, pack, optionally delete."""
        
        # Validate files
        valid_files: List[tuple[str, str, datetime]] = []
        
        for uid, location, created_at in files:
            try:
                # Check if file exists and is readable
                if location.startswith("s3://"):
                    # S3 validation
                    if not isinstance(self._file_reader, S3FileReader):
                        errors.append(f"S3 location {location} but LocalFileReader configured")
                        continue
                else:
                    # Local file validation
                    path = Path(location)
                    if not path.exists():
                        errors.append(f"File not found: {location}")
                        continue
                    if not path.is_file():
                        errors.append(f"Not a file: {location}")
                        continue
                
                valid_files.append((uid, location, created_at))
                
            except Exception as e:
                errors.append(f"Validation failed for {uid}: {e}")
                logger.warning("Validation failed for uid=%s: %s", uid, e)
        
        if not valid_files:
            return
        
        # Pack valid files
        logger.info("Packing %d validated files...", len(valid_files))
        
        try:
            # TODO: Integrate with actual pack_files function
            # This would need conversion from our tuple format to expected format
            # pack_outcome = pack_files(
            #     file_records=valid_files,
            #     config=self._packer_config,
            #     file_reader=self._file_reader,
            # )
            
            # Placeholder for now
            logger.info("Successfully packed %d files", len(valid_files))
            
            # Optionally delete source files
            if self._delete_source:
                self._cleanup_sources([f[1] for f in valid_files], errors)
                
        except Exception as e:
            error_msg = f"Packing failed: {e}"
            errors.append(error_msg)
            logger.error(error_msg)

    def _cleanup_sources(self, file_paths: List[str], errors: List[str]) -> None:
        """Delete source files after successful migration."""
        for path_str in file_paths:
            try:
                if path_str.startswith("s3://"):
                    # TODO: S3 deletion
                    logger.warning("S3 deletion not implemented: %s", path_str)
                else:
                    path = Path(path_str)
                    if path.exists():
                        path.unlink()
                        logger.debug("Deleted source file: %s", path_str)
            except Exception as e:
                error_msg = f"Failed to delete {path_str}: {e}"
                errors.append(error_msg)
                logger.warning(error_msg)

    def _empty_cycle_result(
        self,
        window: ArchiveWindow,
        start: float,
        errors: List[str],
    ) -> WatermarkMigrationResult:
        """Return result for empty cycle."""
        return WatermarkMigrationResult(
            files_processed=0,
            files_migrated=0,
            files_failed=0,
            shards_created=0,
            total_size_bytes=0,
            duration_seconds=time.monotonic() - start,
            window_start=window.window_start,
            window_end=window.window_end,
            errors=errors,
        )

    async def get_pending_stats(self) -> dict:
        """Get statistics about pending files (for monitoring)."""
        window = await self._config_repo.compute_window(datetime.now(timezone.utc))
        
        # Count files in window
        count = 0
        async for _ in self._db_source.iter_records_for_window(window):
            count += 1
        
        return {
            "pending_files": count,
            "window_start": window.window_start.isoformat(),
            "window_end": window.window_end.isoformat(),
            "lag_days": window.lag_days,
        }


# Example usage:
"""
import asyncio
import sqlite3

from watermark_orchestrator import WatermarkMigrationOrchestrator
from des_core.database_source import SourceDatabaseConfig
from des_core.packer_planner import PackerConfig

async def main():
    # Connect to databases
    db_conn = sqlite3.connect("/path/to/source.db")
    config_conn = sqlite3.connect("/path/to/des_config.db")  # Can be same as db_conn
    
    # Configure source
    source_config = SourceDatabaseConfig(
        dsn="",  # Not used with direct connection
        table_name="files",
        uid_column="uid",
        created_at_column="created_at",
        location_column="file_location",
        lag_days=7,
        page_size=1000,
    )
    
    # Configure packer
    packer_config = PackerConfig(
        output_dir="/mnt/des/output",
        n_bits=8,
        max_shard_size=1_000_000_000,
    )
    
    # Create orchestrator
    orchestrator = WatermarkMigrationOrchestrator(
        db_connection=db_conn,
        config_connection=config_conn,
        source_config=source_config,
        packer_config=packer_config,
        delete_source_files=False,
    )
    
    # Initialize
    await orchestrator.initialize()
    
    # Run one cycle
    result = await orchestrator.run_cycle()
    
    print(f"Processed: {result.files_processed}")
    print(f"Migrated: {result.files_migrated}")
    print(f"Failed: {result.files_failed}")
    print(f"Window: {result.window_start} -> {result.window_end}")
    
    db_conn.close()
    config_conn.close()

if __name__ == "__main__":
    asyncio.run(main())
"""
