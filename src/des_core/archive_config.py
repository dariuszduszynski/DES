"""Archive configuration utilities for DES packers.

This module keeps state in a tiny DES-owned table and never touches the large
source table. Only the singleton `des_archive_config` row is updated when the
archive cutoff advances.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol


class SupportsExecute(Protocol):
    """Minimal DB-API surface needed by the repository."""

    def execute(self, sql: str, params: Any | None = None) -> Any: ...

    def cursor(self) -> Any: ...

    def commit(self) -> None: ...


@dataclass(frozen=True)
class ArchiveWindow:
    """Represents the archive window (window_start, window_end]."""

    window_start: datetime
    window_end: datetime
    lag_days: int


def floor_to_midnight(dt: datetime) -> datetime:
    """Clamp a datetime to midnight, preserving tzinfo."""

    return datetime(dt.year, dt.month, dt.day, tzinfo=dt.tzinfo)


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError(f"Unsupported datetime value: {value!r}")


class ArchiveConfigRepository:
    """Read/maintain the singleton archive config row."""

    def __init__(self, conn: SupportsExecute):
        self._conn = conn

    async def ensure_initialized(self, default_archived_until: datetime, default_lag_days: int) -> None:
        """Create table + seed row if missing."""

        self._ensure_initialized_sync(default_archived_until, default_lag_days)

    async def get_config(self) -> tuple[datetime, int]:
        """Return (archived_until, lag_days)."""

        return self._get_config_sync()

    async def compute_window(self, now: datetime) -> ArchiveWindow:
        """Compute window without persisting any updates."""

        archived_until, lag_days = await self.get_config()
        target_cutoff = self._compute_target_cutoff(now, lag_days)
        return ArchiveWindow(window_start=archived_until, window_end=target_cutoff, lag_days=lag_days)

    async def advance_cutoff(self, now: datetime) -> ArchiveWindow:
        """Advance archived_until if the computed cutoff moves forward."""

        archived_until, lag_days = await self.get_config()
        target_cutoff = self._compute_target_cutoff(now, lag_days)
        if target_cutoff <= archived_until:
            return ArchiveWindow(window_start=archived_until, window_end=archived_until, lag_days=lag_days)

        self._update_archived_until_sync(target_cutoff)
        return ArchiveWindow(window_start=archived_until, window_end=target_cutoff, lag_days=lag_days)

    # --- sync helpers (run in thread) ---

    def _ensure_initialized_sync(self, default_archived_until: datetime, default_lag_days: int) -> None:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS des_archive_config (
                id INTEGER PRIMARY KEY,
                archived_until TIMESTAMP NOT NULL,
                lag_days INTEGER NOT NULL
            )
            """
        )
        cursor.execute("SELECT archived_until, lag_days FROM des_archive_config WHERE id = 1")
        row = cursor.fetchone()
        if row is None:
            cursor.execute(
                "INSERT INTO des_archive_config (id, archived_until, lag_days) VALUES (1, ?, ?)",
                (default_archived_until.isoformat(), default_lag_days),
            )
            self._conn.commit()

    def _get_config_sync(self) -> tuple[datetime, int]:
        cursor = self._conn.cursor()
        cursor.execute("SELECT archived_until, lag_days FROM des_archive_config WHERE id = 1")
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("des_archive_config not initialized; call ensure_initialized first.")
        archived_until = _coerce_datetime(row[0])
        lag_days = int(row[1])
        return archived_until, lag_days

    def _update_archived_until_sync(self, target_cutoff: datetime) -> None:
        cursor = self._conn.cursor()
        cursor.execute(
            "UPDATE des_archive_config SET archived_until = ? WHERE id = 1",
            (target_cutoff.isoformat(),),
        )
        self._conn.commit()

    @staticmethod
    def _compute_target_cutoff(now: datetime, lag_days: int) -> datetime:
        return floor_to_midnight(now - timedelta(days=lag_days))


# Quick-start summary (see README/task context):
# - Files: archive_config.py (repository + window helpers).
# - Initialize: ArchiveConfigRepository.ensure_initialized(conn, default_archived_until, default_lag_days).
# - Daily marker: ArchiveConfigRepository.advance_cutoff(now) returns the new ArchiveWindow and updates only
#   the tiny des_archive_config row.
# - Use ArchiveWindow with DatabaseSourceProvider.iter_records_for_window to read the big source table.
