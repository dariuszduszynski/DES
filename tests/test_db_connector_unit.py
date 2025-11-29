from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, create_engine, select

from des_core.db_connector import SourceDatabase, SourceFileRecord


def _setup_sqlite_db(base_dir: Path, include_size: bool = True):
    db_path = base_dir / "files.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    metadata = MetaData()
    columns: list[Column[Any]] = [
        Column("uid", String, primary_key=False),
        Column("created_at", DateTime(timezone=True)),
        Column("file_location", String),
    ]
    if include_size:
        columns.append(Column("size_bytes", Integer))
    columns.append(Column("archived", Boolean))
    table = Table("files", metadata, *columns)
    metadata.create_all(engine)

    now = datetime.now(timezone.utc)
    rows = [
        {
            "uid": "old-keep",
            "created_at": now - timedelta(days=10),
            "file_location": "/a",
            "size_bytes": 10,
            "archived": False,
        },
        {
            "uid": "old-archived",
            "created_at": now - timedelta(days=15),
            "file_location": "/b",
            "size_bytes": 11,
            "archived": True,
        },
        {
            "uid": "new",
            "created_at": now - timedelta(days=1),
            "file_location": "/c",
            "size_bytes": 12,
            "archived": False,
        },
    ]
    with engine.begin() as conn:
        conn.execute(table.insert(), rows)
    return engine


def test_fetch_files_filters_and_orders(tmp_path: Path):
    engine = _setup_sqlite_db(tmp_path, include_size=True)
    cutoff = datetime.now(timezone.utc) - timedelta(days=5)
    db = SourceDatabase(
        db_url=str(engine.url),
        table_name="files",
        uid_column="uid",
        created_at_column="created_at",
        file_location_column="file_location",
        size_bytes_column="size_bytes",
        archived_column="archived",
    )

    records = db.fetch_files_to_archive(cutoff_date=cutoff, limit=10)

    assert [r.uid for r in records] == ["old-keep"]
    assert isinstance(records[0], SourceFileRecord)
    assert records[0].size_bytes == 10


def test_limit_applies(tmp_path: Path):
    engine = _setup_sqlite_db(tmp_path, include_size=True)
    cutoff = datetime.now(timezone.utc) + timedelta(days=1)
    db = SourceDatabase(
        db_url=str(engine.url),
        table_name="files",
        size_bytes_column="size_bytes",
    )

    records = db.fetch_files_to_archive(cutoff_date=cutoff, limit=1)

    assert len(records) == 1
    assert records[0].uid == "old-keep"


def test_size_bytes_optional(tmp_path: Path):
    engine = _setup_sqlite_db(tmp_path, include_size=False)
    cutoff = datetime.now(timezone.utc) + timedelta(days=1)
    db = SourceDatabase(
        db_url=str(engine.url),
        table_name="files",
        size_bytes_column=None,
    )

    records = db.fetch_files_to_archive(cutoff_date=cutoff)

    assert records[0].size_bytes is None


def test_mark_as_archived_updates_rows(tmp_path: Path):
    engine = _setup_sqlite_db(tmp_path, include_size=True)
    db = SourceDatabase(db_url=str(engine.url), table_name="files")

    updated = db.mark_as_archived(["old-keep", "non-existent"])

    assert updated == 1
    with engine.connect() as conn:
        rows = conn.execute(select(Table("files", MetaData(), autoload_with=engine))).mappings().all()
    archived_flags = {row["uid"]: row["archived"] for row in rows}
    assert archived_flags["old-keep"] in (True, 1)


def test_mark_as_archived_empty_list(tmp_path: Path):
    engine = _setup_sqlite_db(tmp_path, include_size=True)
    db = SourceDatabase(db_url=str(engine.url), table_name="files")

    updated = db.mark_as_archived([])

    assert updated == 0


def test_mark_as_archived_requires_column(tmp_path: Path):
    engine = _setup_sqlite_db(tmp_path, include_size=True)
    db = SourceDatabase(db_url=str(engine.url), table_name="files", archived_column=None)

    with pytest.raises(ValueError):
        db.mark_as_archived(["uid-1"])


def test_mark_as_archived_rolls_back_on_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    engine = _setup_sqlite_db(tmp_path, include_size=True)
    db = SourceDatabase(db_url=str(engine.url), table_name="files")

    real_begin = engine.begin

    class FailingCtx:
        def __init__(self) -> None:
            self._ctx = real_begin()

        def __enter__(self) -> Any:
            return self._ctx.__enter__()

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            self._ctx.__exit__(RuntimeError, RuntimeError("fail"), None)
            raise RuntimeError("fail")

    monkeypatch.setattr(db._engine, "begin", lambda: FailingCtx())

    with pytest.raises(RuntimeError):
        db.mark_as_archived(["old-keep"])

    with engine.connect() as conn:
        rows = conn.execute(select(Table("files", MetaData(), autoload_with=engine))).mappings().all()
    archived_flags = {row["uid"]: row["archived"] for row in rows}
    assert archived_flags["old-keep"] in (False, 0)
