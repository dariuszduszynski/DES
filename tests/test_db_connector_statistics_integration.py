from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Generator

import pytest

pytest.importorskip("testcontainers.postgres")

from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, create_engine  # type: ignore[no-redef]
from testcontainers.postgres import PostgresContainer  # type: ignore[import-not-found,import-untyped]

from des_core.db_connector import SourceDatabase


@pytest.fixture(scope="module")
def postgres_url() -> Generator[str, None, None]:
    with PostgresContainer("postgres:16") as pg:
        yield pg.get_connection_url().replace("postgresql://", "postgresql+psycopg://")


def _create_and_seed(engine_url: str) -> None:
    engine = create_engine(engine_url, future=True)
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
    with engine.begin() as conn:
        conn.execute(table.delete()) if engine.dialect.has_table(conn, "files") else None
    metadata.drop_all(engine, tables=[table])
    metadata.create_all(engine)

    now = datetime.now(timezone.utc)
    rows = [
        {
            "uid": f"keep-{i}",
            "created_at": now - timedelta(days=20 + i),
            "file_location": f"/f{i}",
            "size_bytes": i * 10,
            "archived": False,
        }
        for i in range(1, 6)
    ]
    rows.append(
        {
            "uid": "archived",
            "created_at": now - timedelta(days=50),
            "file_location": "/old",
            "size_bytes": 5,
            "archived": True,
        }
    )
    with engine.begin() as conn:
        conn.execute(table.insert(), rows)


@pytest.mark.integration
def test_archive_statistics_postgres(postgres_url: str) -> None:
    _create_and_seed(postgres_url)
    db = SourceDatabase(db_url=postgres_url, table_name="files")
    cutoff = datetime.now(timezone.utc)

    stats = db.get_archive_statistics(cutoff)

    assert stats["total_files"] == 5
    assert stats["total_size_bytes"] == sum(i * 10 for i in range(1, 6))
    assert stats["oldest_file"] is not None
    assert stats["newest_file"] is not None
