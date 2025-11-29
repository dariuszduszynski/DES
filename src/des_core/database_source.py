"""Database-backed source provider for DES packers.

The provider reads from a large external table using keyset pagination and an
optional shard filter. It performs SELECT-only operations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncIterator, Iterable, Protocol

from .archive_config import ArchiveWindow


class SupportsSelect(Protocol):
    """Minimal DB-API surface for read-only access."""

    def cursor(self): ...

    def commit(self) -> None: ...


@dataclass(frozen=True)
class SourceDatabaseConfig:
    dsn: str
    table_name: str
    uid_column: str = "uid"
    created_at_column: str = "created_at"
    location_column: str = "file_location"
    lag_days: int = 7
    shards_total: int = 1
    shard_id: int = 0
    page_size: int = 1000


@dataclass(frozen=True)
class SourceRecord:
    uid: str
    created_at: datetime
    file_location: str


class DatabaseSourceProvider:
    """Iterate source records in a window using keyset pagination."""

    def __init__(self, conn: SupportsSelect, config: SourceDatabaseConfig):
        self._conn = conn
        self._cfg = config

    async def iter_records_for_window(self, window: ArchiveWindow) -> AsyncIterator[SourceRecord]:
        """Yield SourceRecord rows in (window_start, window_end], ordered by (created_at, uid)."""

        last_created_at: datetime | None = None
        last_uid: str | None = None

        while True:
            rows = self._fetch_page(window, last_created_at, last_uid)
            if not rows:
                break

            # Track the last row from the DB to drive keyset pagination even if we filter by shard in Python.
            last_created_at = rows[-1][self._cfg.created_at_column]
            last_uid = rows[-1][self._cfg.uid_column]

            for row in rows:
                if self._cfg.shards_total > 1:
                    if hash(str(row[self._cfg.uid_column])) % self._cfg.shards_total != self._cfg.shard_id:
                        continue
                yield SourceRecord(
                    uid=str(row[self._cfg.uid_column]),
                    created_at=_coerce_datetime(row[self._cfg.created_at_column]),
                    file_location=str(row[self._cfg.location_column]),
                )

    # --- internal helpers ---

    def _fetch_page(
        self,
        window: ArchiveWindow,
        last_created_at: datetime | None,
        last_uid: str | None,
    ) -> list[dict[str, Any]]:
        cursor = self._conn.cursor()

        conditions: list[str] = [
            f"{self._cfg.created_at_column} > ?",
            f"{self._cfg.created_at_column} <= ?",
        ]
        params: list[Any] = [window.window_start, window.window_end]

        if last_created_at is not None and last_uid is not None:
            conditions.append(
                f"({self._cfg.created_at_column} > ? OR "
                f"({self._cfg.created_at_column} = ? AND {self._cfg.uid_column} > ?))"
            )
            params.extend([last_created_at, last_created_at, last_uid])

        shard_condition = self._shard_filter_condition()
        if shard_condition:
            conditions.append(shard_condition)

        sql = (
            f"SELECT {self._cfg.uid_column}, {self._cfg.created_at_column}, {self._cfg.location_column} "
            f"FROM {self._cfg.table_name} "
            f"WHERE {' AND '.join(conditions)} "
            f"ORDER BY {self._cfg.created_at_column}, {self._cfg.uid_column} "
            f"LIMIT ?"
        )
        params.append(self._cfg.page_size)

        cursor.execute(sql, tuple(params))
        rows = cursor.fetchall()
        return self._rows_to_dicts(cursor, rows)

    def _shard_filter_condition(self) -> str | None:
        """Override to inject DB-specific shard predicate; Python fallback is always applied."""

        if self._cfg.shards_total <= 1:
            return None
        # No portable SQL hash across engines; subclasses may override to add an engine-specific expression.
        return None

    @staticmethod
    def _rows_to_dicts(cursor: Any, rows: Iterable[Iterable[Any]]) -> list[dict[str, Any]]:
        columns = [col[0] for col in cursor.description]
        result: list[dict[str, Any]] = []
        for row in rows:
            row_values = list(row)
            result.append({col: row_values[idx] for idx, col in enumerate(columns)})
        return result


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError(f"Unsupported datetime value for created_at: {value!r}")


# Quick-start summary:
# - SourceDatabaseConfig describes the external table/columns and optional sharding.
# - DatabaseSourceProvider.iter_records_for_window(window) yields SourceRecord rows in (window_start, window_end],
#   applying shard filtering in Python by default; override _shard_filter_condition for SQL-level hashing.
# - Pair with ArchiveConfigRepository.advance_cutoff/compute_window to drive daily packer runs.
