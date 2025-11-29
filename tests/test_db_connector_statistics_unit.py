from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, create_engine

from des_core.db_connector import ArchiveStatistics, SourceDatabase


def _build_engine(db_path: Path, include_size: bool = True):
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    metadata = MetaData()
    columns: list[Column[Any]] = [
        Column("uid", String),
        Column("created_at", DateTime(timezone=True)),
        Column("file_location", String),
    ]
    if include_size:
        columns.append(Column("size_bytes", Integer))
    columns.append(Column("archived", Boolean))
    table = Table("files", metadata, *columns)
    metadata.create_all(engine)
    return engine, table


def _seed(engine, table, include_size: bool = True):
    now = datetime.now(timezone.utc)
    rows = [
        {"uid": "a", "created_at": now - timedelta(days=10), "file_location": "/a", "size_bytes": 100, "archived": False},
        {"uid": "b", "created_at": now - timedelta(days=5), "file_location": "/b", "size_bytes": 200, "archived": False},
        {"uid": "c", "created_at": now - timedelta(days=1), "file_location": "/c", "size_bytes": 300, "archived": True},
    ]
    if not include_size:
        for row in rows:
            row.pop("size_bytes")
    with engine.begin() as conn:
        conn.execute(table.insert(), rows)


def test_get_archive_statistics_basic(tmp_path: Path):
    engine, table = _build_engine(tmp_path / "stats_basic.db", include_size=True)
    _seed(engine, table, include_size=True)
    cutoff = datetime.now(timezone.utc)
    db = SourceDatabase(db_url=str(engine.url), table_name="files")

    stats: ArchiveStatistics = db.get_archive_statistics(cutoff)

    assert stats["total_files"] == 2
    assert stats["total_size_bytes"] == 300
    assert stats["oldest_file"] is not None
    assert stats["newest_file"] is not None
    assert stats["oldest_file"] < stats["newest_file"]


def test_get_archive_statistics_no_size_column(tmp_path: Path):
    engine, table = _build_engine(tmp_path / "stats_nosize.db", include_size=False)
    _seed(engine, table, include_size=False)
    cutoff = datetime.now(timezone.utc)
    db = SourceDatabase(db_url=str(engine.url), table_name="files", size_bytes_column=None)

    stats = db.get_archive_statistics(cutoff)

    assert stats["total_files"] == 2
    assert stats["total_size_bytes"] == 0


def test_get_archive_statistics_empty_result(tmp_path: Path):
    engine, table = _build_engine(tmp_path / "stats_empty.db", include_size=True)
    _seed(engine, table, include_size=True)
    cutoff = datetime.now(timezone.utc) - timedelta(days=20)
    db = SourceDatabase(db_url=str(engine.url), table_name="files")

    stats = db.get_archive_statistics(cutoff)

    assert stats == {
        "total_files": 0,
        "total_size_bytes": 0,
        "oldest_file": None,
        "newest_file": None,
    }
