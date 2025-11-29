import sqlite3
from datetime import datetime

import pytest

from des_core.archive_config import ArchiveConfigRepository


def _make_conn() -> sqlite3.Connection:
    return sqlite3.connect(":memory:")


@pytest.mark.asyncio
async def test_ensure_initialized_creates_row() -> None:
    conn = _make_conn()
    repo = ArchiveConfigRepository(conn)

    default_until = datetime(2024, 1, 1)
    await repo.ensure_initialized(default_archived_until=default_until, default_lag_days=7)

    archived_until, lag_days = await repo.get_config()
    assert archived_until == default_until
    assert lag_days == 7


@pytest.mark.asyncio
async def test_advance_cutoff_noop_when_target_not_ahead() -> None:
    conn = _make_conn()
    repo = ArchiveConfigRepository(conn)
    await repo.ensure_initialized(default_archived_until=datetime(2024, 1, 10), default_lag_days=7)

    window = await repo.advance_cutoff(datetime(2024, 1, 12))

    assert window.window_start == datetime(2024, 1, 10)
    assert window.window_end == datetime(2024, 1, 10)

    archived_until, lag_days = await repo.get_config()
    assert archived_until == datetime(2024, 1, 10)
    assert lag_days == 7


@pytest.mark.asyncio
async def test_advance_cutoff_updates_when_target_moves_forward() -> None:
    conn = _make_conn()
    repo = ArchiveConfigRepository(conn)
    await repo.ensure_initialized(default_archived_until=datetime(2024, 1, 1), default_lag_days=3)

    window = await repo.advance_cutoff(datetime(2024, 1, 10))

    assert window.window_start == datetime(2024, 1, 1)
    assert window.window_end == datetime(2024, 1, 7)

    archived_until, lag_days = await repo.get_config()
    assert archived_until == datetime(2024, 1, 7)
    assert lag_days == 3
