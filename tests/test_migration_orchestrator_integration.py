from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, create_engine

from des_core.db_connector import SourceDatabase
from des_core.migration_orchestrator import MigrationOrchestrator
from des_core.packer import PackerResult, pack_files_to_directory
from des_core.packer_planner import FileToPack, PlannerConfig


class LocalPacker:
    def __init__(self, output_dir: Path):
        self._output_dir = output_dir

    def pack_files(self, files: list[FileToPack]) -> PackerResult:
        return pack_files_to_directory(files, self._output_dir, PlannerConfig())


def _setup_db(db_path: Path, files: list[Path]) -> SourceDatabase:
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    metadata = MetaData()
    table = Table(
        "files",
        metadata,
        Column("uid", String),
        Column("created_at", DateTime(timezone=True)),
        Column("file_location", String),
        Column("size_bytes", Integer),
        Column("archived", Boolean),
    )
    metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    rows = [
        {
            "uid": f"u{i}",
            "created_at": now - timedelta(days=10 + i),
            "file_location": str(path),
            "size_bytes": path.stat().st_size,
            "archived": False,
        }
        for i, path in enumerate(files, start=1)
    ]
    with engine.begin() as conn:
        conn.execute(table.insert(), rows)
    return SourceDatabase(db_url=f"sqlite+pysqlite:///{db_path}", table_name="files")


@pytest.mark.integration
def test_migration_orchestrator_end_to_end(tmp_path: Path) -> None:
    file1 = tmp_path / "f1.bin"
    file2 = tmp_path / "f2.bin"
    file1.write_bytes(b"a" * 10)
    file2.write_bytes(b"b" * 20)
    db = _setup_db(tmp_path / "files.db", [file1, file2])

    packer = LocalPacker(tmp_path / "shards")
    orchestrator = MigrationOrchestrator(db, packer, archive_age_days=1, batch_size=10)

    result = orchestrator.run_migration_cycle()

    assert result.files_processed == 2
    assert result.files_migrated == 2
    assert result.shards_created >= 1
    assert (tmp_path / "shards").exists()
