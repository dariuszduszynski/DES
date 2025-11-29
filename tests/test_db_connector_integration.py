from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Generator

import pytest

pytest.importorskip("testcontainers.postgres")

from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, create_engine, select, text
from testcontainers.postgres import PostgresContainer  # type: ignore[import-not-found]

from des_core.db_connector import SourceDatabase


@pytest.fixture(scope="module")
def postgres_url() -> Generator[str, None, None]:
    with PostgresContainer("postgres:16") as pg:
        yield pg.get_connection_url().replace("postgresql://", "postgresql+psycopg://")


def _create_table(engine_url: str) -> None:
    engine = create_engine(engine_url, future=True)
    metadata = MetaData()
    columns: list[Column[Any]] = [
        Column("uid", String, primary_key=False),
        Column("created_at", DateTime(timezone=True)),
        Column("file_location", String),
        Column("size_bytes", Integer),
        Column("archived", Boolean),
    ]
    table = Table(
        "files",
        metadata,
        *columns,
    )
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS files"))
    metadata.create_all(engine)

    now = datetime.now(timezone.utc)
    rows = [
        {
            "uid": "keep-1",
            "created_at": now - timedelta(days=30),
            "file_location": "/a",
            "size_bytes": 100,
            "archived": False,
        },
        {
            "uid": "skip-archived",
            "created_at": now - timedelta(days=40),
            "file_location": "/b",
            "size_bytes": 200,
            "archived": True,
        },
        {
            "uid": "skip-new",
            "created_at": now - timedelta(days=5),
            "file_location": "/c",
            "size_bytes": 300,
            "archived": False,
        },
    ]
    with engine.begin() as conn:
        conn.execute(table.insert(), rows)


@pytest.mark.integration
def test_fetch_from_postgres(postgres_url: str) -> None:
    _create_table(postgres_url)
    cutoff = datetime.now(timezone.utc) - timedelta(days=10)
    db = SourceDatabase(
        db_url=postgres_url,
        table_name="files",
    )

    records = db.fetch_files_to_archive(cutoff_date=cutoff, limit=10)

    assert [r.uid for r in records] == ["keep-1"]
    assert records[0].file_location == "/a"
    assert records[0].size_bytes == 100


@pytest.mark.integration
def test_mark_as_archived_postgres(postgres_url: str) -> None:
    _create_table(postgres_url)
    db = SourceDatabase(db_url=postgres_url, table_name="files")

    updated = db.mark_as_archived(["keep-1", "skip-new"])

    assert updated == 2
    engine = create_engine(postgres_url, future=True)
    with engine.connect() as conn:
        table = Table("files", MetaData(), autoload_with=engine)
        rows = conn.execute(select(table)).mappings().all()
    archive_flags = {row["uid"]: row["archived"] for row in rows}
    assert archive_flags["keep-1"] is True
    assert archive_flags["skip-new"] is True
