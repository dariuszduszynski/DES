from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

from des_core.db_connector import ArchiveStatistics, SourceFileRecord
from des_core.migration_orchestrator import MigrationOrchestrator, MigrationResult
from des_core.packer import PackerResult, ShardWriteResult
from des_core.packer_planner import ShardKey


class FakeDB:
    def __init__(self, records: List[SourceFileRecord]):
        self._records = records
        self.marked: List[str] = []
        self.raise_on_mark = False
        self.stats_calls: List[datetime] = []

    def fetch_files_to_archive(self, cutoff_date: datetime, limit: int | None = None):
        return self._records[: limit or len(self._records)]

    def mark_as_archived(self, uids: List[str]) -> int:
        if self.raise_on_mark:
            raise RuntimeError("mark failure")
        self.marked.extend(uids)
        return len(uids)

    def get_archive_statistics(self, cutoff_date: datetime) -> ArchiveStatistics:
        self.stats_calls.append(cutoff_date)
        return {
            "total_files": len(self._records),
            "total_size_bytes": 0,
            "oldest_file": None,
            "newest_file": None,
        }


class FakePacker:
    def __init__(self, fail_uids: set[str] | None = None):
        self.fail_uids = fail_uids or set()

    def pack_files(self, files: List) -> PackerResult:
        if files[0].uid in self.fail_uids:
            raise RuntimeError("pack failed")
        shard = ShardWriteResult(
            shard_key=ShardKey(date_dir="20240101", shard_hex="aa"),
            path=Path("out.des"),
            file_count=len(files),
            total_size_bytes=sum(f.size_bytes for f in files),
        )
        return PackerResult(shards=[shard])


def _make_file(path: Path, content: bytes) -> None:
    path.write_bytes(content)


def test_happy_path(tmp_path: Path):
    f1 = tmp_path / "f1.bin"
    f2 = tmp_path / "f2.bin"
    _make_file(f1, b"a" * 10)
    _make_file(f2, b"b" * 20)
    now = datetime.now(timezone.utc)
    records = [
        SourceFileRecord("u1", now - timedelta(days=10), str(f1), 10),
        SourceFileRecord("u2", now - timedelta(days=12), str(f2), 20),
    ]
    db = FakeDB(records)
    orchestrator = MigrationOrchestrator(db, FakePacker(), archive_age_days=5, batch_size=10)

    result: MigrationResult = orchestrator.run_migration_cycle()

    assert result.files_processed == 2
    assert result.files_migrated == 2
    assert result.files_failed == 0
    assert result.shards_created == 2
    assert result.total_size_bytes == 30
    assert db.marked == ["u1", "u2"]


def test_validation_failure(tmp_path: Path):
    missing = tmp_path / "missing.bin"
    now = datetime.now(timezone.utc)
    records = [SourceFileRecord("u1", now - timedelta(days=10), str(missing), 10)]
    db = FakeDB(records)
    orchestrator = MigrationOrchestrator(db, FakePacker(), archive_age_days=5, batch_size=10)

    result = orchestrator.run_migration_cycle()

    assert result.files_processed == 1
    assert result.files_migrated == 0
    assert result.files_failed == 1
    assert "does not exist" in result.errors[0]


def test_size_mismatch(tmp_path: Path):
    f1 = tmp_path / "f1.bin"
    _make_file(f1, b"a" * 5)
    now = datetime.now(timezone.utc)
    records = [SourceFileRecord("u1", now - timedelta(days=10), str(f1), 10)]
    db = FakeDB(records)
    orchestrator = MigrationOrchestrator(db, FakePacker(), archive_age_days=5, batch_size=10)

    result = orchestrator.run_migration_cycle()

    assert result.files_failed == 1
    assert result.files_migrated == 0
    assert "size mismatch" in result.errors[0]


def test_packing_failure(tmp_path: Path):
    f1 = tmp_path / "f1.bin"
    _make_file(f1, b"a" * 10)
    now = datetime.now(timezone.utc)
    records = [SourceFileRecord("u1", now - timedelta(days=10), str(f1), 10)]
    db = FakeDB(records)
    orchestrator = MigrationOrchestrator(db, FakePacker(fail_uids={"u1"}), archive_age_days=5, batch_size=10)

    result = orchestrator.run_migration_cycle()

    assert result.files_failed == 1
    assert result.files_migrated == 0
    assert "Packing failed" in result.errors[0]


def test_mark_failure(tmp_path: Path):
    f1 = tmp_path / "f1.bin"
    _make_file(f1, b"a" * 10)
    now = datetime.now(timezone.utc)
    records = [SourceFileRecord("u1", now - timedelta(days=10), str(f1), 10)]
    db = FakeDB(records)
    db.raise_on_mark = True
    orchestrator = MigrationOrchestrator(db, FakePacker(), archive_age_days=5, batch_size=10)

    result = orchestrator.run_migration_cycle()

    assert result.files_migrated == 1
    assert any("Failed to mark" in err for err in result.errors)


def test_cleanup_failure(tmp_path: Path):
    f1 = tmp_path / "f1.bin"
    _make_file(f1, b"a" * 10)
    now = datetime.now(timezone.utc)
    records = [SourceFileRecord("u1", now - timedelta(days=10), str(f1), 10)]
    db = FakeDB(records)

    class DeletingPacker(FakePacker):
        def pack_files(self, files: List) -> PackerResult:
            res = super().pack_files(files)
            # make file read-only to simulate deletion failure
            Path(files[0].source_path).chmod(0o400)
            return res

    orchestrator = MigrationOrchestrator(db, DeletingPacker(), archive_age_days=5, batch_size=10, delete_source_files=True)

    result = orchestrator.run_migration_cycle()

    assert result.files_migrated == 1
    assert any("Failed to delete" in err for err in result.errors)
