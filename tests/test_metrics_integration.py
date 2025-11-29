from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

from des_core.db_connector import ArchiveStatistics
from des_core.metrics import (
    DES_MIGRATION_BATCH_SIZE,
    DES_MIGRATION_BYTES_TOTAL,
    DES_MIGRATION_CYCLES_TOTAL,
    DES_MIGRATION_DURATION_SECONDS,
    DES_MIGRATION_FILES_TOTAL,
    DES_MIGRATION_PENDING_FILES,
)
from des_core.migration_orchestrator import MigrationOrchestrator, MigrationResult, PackerInterface, SourceDatabaseInterface
from des_core.packer import PackerResult, ShardWriteResult
from des_core.packer_planner import FileToPack, ShardKey


def _counter_value(counter: Any, labels: Dict[str, str] | None = None) -> float:
    metric = counter.collect()[0]
    for sample in metric.samples:
        if labels is None and not sample.labels:
            return float(sample.value)
        if labels is not None and sample.labels == labels:
            return float(sample.value)
    return 0.0


def _histogram_count(histogram: Any) -> float:
    metric = histogram.collect()[0]
    for sample in metric.samples:
        if sample.name.endswith("_count"):
            return float(sample.value)
    return 0.0


def _gauge_value(gauge: Any) -> float:
    metric = gauge.collect()[0]
    return float(metric.samples[0].value)


class MetricFakeDB(SourceDatabaseInterface):
    def __init__(self, files: List[FileToPack]):
        self.files = files

    def fetch_files_to_archive(self, cutoff_date: datetime, limit: int | None = None):
        from des_core.db_connector import SourceFileRecord

        return [
            SourceFileRecord(uid=f.uid, created_at=f.created_at, file_location=str(f.source_path), size_bytes=f.size_bytes)
            for f in self.files[: limit or len(self.files)]
        ]

    def mark_as_archived(self, uids: List[str]) -> int:
        return len(uids)

    def get_archive_statistics(self, cutoff_date: datetime) -> ArchiveStatistics:
        return {"total_files": len(self.files), "total_size_bytes": 0, "oldest_file": None, "newest_file": None}


class MetricFakePacker(PackerInterface):
    def __init__(self, fail: bool = False):
        self.fail = fail

    def pack_files(self, files: List[FileToPack]) -> PackerResult:
        if self.fail:
            raise RuntimeError("pack failed")
        shard = ShardWriteResult(
            shard_key=ShardKey(date_dir="20240101", shard_hex="aa"),
            path=Path("out.des"),
            file_count=len(files),
            total_size_bytes=sum(f.size_bytes for f in files),
        )
        return PackerResult(shards=[shard])


def test_metrics_success(tmp_path: Path) -> None:
    files = [
        FileToPack(
            uid="u1",
            created_at=datetime.now(timezone.utc) - timedelta(days=5),
            size_bytes=10,
            source_path=str(tmp_path / "f1"),
        ),
        FileToPack(
            uid="u2",
            created_at=datetime.now(timezone.utc) - timedelta(days=6),
            size_bytes=20,
            source_path=str(tmp_path / "f2"),
        ),
    ]
    for f in files:
        Path(str(f.source_path)).write_bytes(b"x" * f.size_bytes)

    db = MetricFakeDB(files)
    packer = MetricFakePacker()
    orchestrator = MigrationOrchestrator(db, packer, archive_age_days=1, batch_size=10)

    start_success = _counter_value(DES_MIGRATION_CYCLES_TOTAL, {"status": "success"})
    start_files = _counter_value(DES_MIGRATION_FILES_TOTAL)
    start_bytes = _counter_value(DES_MIGRATION_BYTES_TOTAL)
    start_hist = _histogram_count(DES_MIGRATION_DURATION_SECONDS)

    result: MigrationResult = orchestrator.run_migration_cycle()

    assert result.files_processed == 2
    assert _counter_value(DES_MIGRATION_CYCLES_TOTAL, {"status": "success"}) == start_success + 1
    assert _counter_value(DES_MIGRATION_FILES_TOTAL) == start_files + 2
    assert _counter_value(DES_MIGRATION_BYTES_TOTAL) == start_bytes + 30
    assert _histogram_count(DES_MIGRATION_DURATION_SECONDS) == start_hist + 1
    assert _gauge_value(DES_MIGRATION_PENDING_FILES) == 2
    assert _gauge_value(DES_MIGRATION_BATCH_SIZE) == 10


def test_metrics_failure(tmp_path: Path) -> None:
    class ExplodingOrchestrator(MigrationOrchestrator):
        def _execute_cycle(self, cutoff: datetime):
            raise RuntimeError("boom")

    files = [
        FileToPack(
            uid="u1",
            created_at=datetime.now(timezone.utc) - timedelta(days=5),
            size_bytes=10,
            source_path=str(tmp_path / "f1"),
        ),
    ]
    Path(str(files[0].source_path)).write_bytes(b"x" * files[0].size_bytes)
    db = MetricFakeDB(files)
    packer = MetricFakePacker()
    orchestrator = ExplodingOrchestrator(db, packer, archive_age_days=1, batch_size=5)

    start_failure = _counter_value(DES_MIGRATION_CYCLES_TOTAL, {"status": "failure"})
    start_hist = _histogram_count(DES_MIGRATION_DURATION_SECONDS)

    with pytest.raises(RuntimeError):
        orchestrator.run_migration_cycle()

    assert _counter_value(DES_MIGRATION_CYCLES_TOTAL, {"status": "failure"}) == start_failure + 1
    assert _histogram_count(DES_MIGRATION_DURATION_SECONDS) == start_hist + 1
