from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, create_engine

from des_core.db_connector import SourceDatabase

pytestmark = pytest.mark.skip(reason="perf test; enable with --runslow")


def test_stats_performance_large_dataset():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
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
            "created_at": now - timedelta(days=i % 365),
            "file_location": f"/f/{i}",
            "size_bytes": i,
            "archived": False,
        }
        for i in range(100_000)
    ]
    with engine.begin() as conn:
        conn.execute(table.insert(), rows)

    db = SourceDatabase(db_url=str(engine.url), table_name="files")
    cutoff = now + timedelta(days=1)

    start = time.perf_counter()
    stats = db.get_archive_statistics(cutoff)
    duration = time.perf_counter() - start

    assert stats["total_files"] == 100_000
    assert duration < 1.5
