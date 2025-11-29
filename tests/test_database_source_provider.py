import sqlite3
from datetime import datetime
from typing import Iterable

import pytest

from des_core.archive_config import ArchiveWindow
from des_core.database_source import DatabaseSourceProvider, SourceDatabaseConfig, SourceRecord


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE big_files (
            uid TEXT NOT NULL,
            created_at TEXT NOT NULL,
            file_location TEXT NOT NULL
        )
        """
    )
    return conn


def _insert_rows(conn: sqlite3.Connection, rows: Iterable[tuple[str, datetime, str]]) -> None:
    conn.executemany(
        "INSERT INTO big_files (uid, created_at, file_location) VALUES (?, ?, ?)",
        [(uid, ts.isoformat(), loc) for uid, ts, loc in rows],
    )
    conn.commit()


async def _collect(provider: DatabaseSourceProvider, window: ArchiveWindow) -> list[SourceRecord]:
    results: list[SourceRecord] = []
    async for record in provider.iter_records_for_window(window):
        results.append(record)
    return results


@pytest.mark.asyncio
async def test_iter_records_filters_by_window_and_orders() -> None:
    conn = _make_conn()
    _insert_rows(
        conn,
        [
            ("a", datetime(2023, 12, 31, 23, 0, 0), "/old"),
            ("b", datetime(2024, 1, 2, 10, 0, 0), "/in-1"),
            ("c", datetime(2024, 1, 5, 12, 0, 0), "/in-2"),
            ("d", datetime(2024, 1, 7, 9, 0, 0), "/after"),
        ],
    )

    cfg = SourceDatabaseConfig(dsn=":memory:", table_name="big_files", page_size=2)
    provider = DatabaseSourceProvider(conn, cfg)
    window = ArchiveWindow(window_start=datetime(2024, 1, 1), window_end=datetime(2024, 1, 6), lag_days=7)

    records = await _collect(provider, window)
    assert [r.uid for r in records] == ["b", "c"]
    assert [r.file_location for r in records] == ["/in-1", "/in-2"]


@pytest.mark.asyncio
async def test_shards_are_disjoint_and_complete() -> None:
    conn = _make_conn()
    rows = [
        ("u1", datetime(2024, 1, 2), "/f1"),
        ("u2", datetime(2024, 1, 3), "/f2"),
        ("u3", datetime(2024, 1, 4), "/f3"),
        ("u4", datetime(2024, 1, 5), "/f4"),
    ]
    _insert_rows(conn, rows)
    window = ArchiveWindow(window_start=datetime(2024, 1, 1), window_end=datetime(2024, 1, 6), lag_days=7)

    cfg0 = SourceDatabaseConfig(dsn=":memory:", table_name="big_files", shards_total=2, shard_id=0, page_size=2)
    cfg1 = SourceDatabaseConfig(dsn=":memory:", table_name="big_files", shards_total=2, shard_id=1, page_size=2)

    shard0 = await _collect(DatabaseSourceProvider(conn, cfg0), window)
    shard1 = await _collect(DatabaseSourceProvider(conn, cfg1), window)

    union = {r.uid for r in shard0} | {r.uid for r in shard1}
    assert union == {r[0] for r in rows}
    assert {r.uid for r in shard0} & {r.uid for r in shard1} == set()


@pytest.mark.asyncio
async def test_keyset_pagination_no_duplicates_or_gaps() -> None:
    conn = _make_conn()
    rows = [
        ("u1", datetime(2024, 1, 2, 8), "/f1"),
        ("u2", datetime(2024, 1, 2, 9), "/f2"),
        ("u3", datetime(2024, 1, 2, 10), "/f3"),
        ("u4", datetime(2024, 1, 3, 1), "/f4"),
    ]
    _insert_rows(conn, rows)

    cfg = SourceDatabaseConfig(dsn=":memory:", table_name="big_files", page_size=1)
    provider = DatabaseSourceProvider(conn, cfg)
    window = ArchiveWindow(window_start=datetime(2024, 1, 1), window_end=datetime(2024, 1, 4), lag_days=7)

    records = await _collect(provider, window)
    assert [r.uid for r in records] == ["u1", "u2", "u3", "u4"]
