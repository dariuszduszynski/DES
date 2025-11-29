"""Database connector for fetching and updating source file metadata in upstream databases."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, List, Mapping, Optional, Sequence, TypedDict, TypeVar, cast

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    and_,
    asc,
    create_engine,
    func,
    literal,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.sql import Select

_T = TypeVar("_T")

logger = logging.getLogger(__name__)


class ArchiveStatistics(TypedDict):
    total_files: int
    total_size_bytes: int
    oldest_file: Optional[datetime]
    newest_file: Optional[datetime]


@dataclass(frozen=True)
class SourceFileRecord:
    """Immutable representation of a single source file row fetched from upstream DB."""

    uid: str
    created_at: datetime
    file_location: str
    size_bytes: Optional[int] = None


class SourceDatabase:
    """Connector that fetches files older than a cutoff date which are not yet archived.

    Manages an SQLAlchemy engine with pooling and pre-ping, executes parametrized selects, maps rows to
    `SourceFileRecord`, and retries transient connection errors before failing.
    """

    def __init__(
        self,
        db_url: str,
        table_name: str,
        uid_column: str = "uid",
        created_at_column: str = "created_at",
        file_location_column: str = "file_location",
        size_bytes_column: Optional[str] = "size_bytes",
        archived_column: Optional[str] = "archived",
        pool_size: int = 5,
        max_overflow: int = 10,
        max_retries: int = 3,
        backoff_base: float = 0.1,
    ) -> None:
        engine_kwargs: dict[str, Any] = {
            "pool_pre_ping": True,
            "future": True,
        }
        if not db_url.startswith("sqlite"):
            engine_kwargs["pool_size"] = pool_size
            engine_kwargs["max_overflow"] = max_overflow
        self._engine: Engine = create_engine(db_url, **engine_kwargs)
        self._max_retries = max_retries
        self._backoff_base = backoff_base

        metadata = MetaData()
        columns: list[Column[Any]] = [
            Column(uid_column, String, primary_key=False),
            Column(created_at_column, DateTime(timezone=True)),
            Column(file_location_column, String),
        ]
        if size_bytes_column:
            columns.append(Column(size_bytes_column, Integer))
        if archived_column is not None:
            columns.append(Column(archived_column, Boolean))

        self._table = Table(table_name, metadata, *columns, extend_existing=True)
        self._uid_column = uid_column
        self._created_at_column = created_at_column
        self._file_location_column = file_location_column
        self._size_bytes_column = size_bytes_column
        self._archived_column = archived_column
        self._table_name = table_name

    def fetch_files_to_archive(self, cutoff_date: datetime, limit: Optional[int] = None) -> List[SourceFileRecord]:
        """Return files older than `cutoff_date` not marked as archived, ordered by created_at ascending.

        Retries transient connection/operational errors up to `max_retries` with exponential backoff. Raises the last
        exception if retries are exhausted.
        """

        if self._archived_column is None:
            raise ValueError("archived_column is not configured for SourceDatabase")
        archived_col = self._archived_column
        stmt = self._build_statement(cutoff_date, limit, archived_col)
        rows = self._with_retry(lambda: self._execute(stmt))
        return [self._row_to_record(row) for row in rows]

    def get_archive_statistics(self, cutoff_date: datetime) -> ArchiveStatistics:
        """Return aggregated statistics for files eligible for archiving.

        Statistics include counts, total size (0 when size_bytes_column is not configured), and oldest/newest created_at.
        Retries transient connection/operational errors using the same strategy as fetch/mark operations.
        """

        if self._archived_column is None:
            raise ValueError("archived_column is not configured for SourceDatabase")

        count_expr = func.count().label("total_files")
        sum_expr = (
            func.coalesce(func.sum(getattr(self._table.c, self._size_bytes_column)), 0).label("total_size_bytes")
            if self._size_bytes_column
            else literal(0).label("total_size_bytes")
        )
        min_expr = func.min(getattr(self._table.c, self._created_at_column)).label("oldest_file")
        max_expr = func.max(getattr(self._table.c, self._created_at_column)).label("newest_file")

        stmt = (
            select(count_expr, sum_expr, min_expr, max_expr)
            .where(
                and_(
                    getattr(self._table.c, self._created_at_column) < cutoff_date,
                    getattr(self._table.c, self._archived_column).is_(False),
                )
            )
            .limit(1)
        )

        def _run() -> ArchiveStatistics:
            rows = self._execute(stmt)
            row = rows[0] if rows else {}
            total_files = int(row.get("total_files", 0))
            total_size = int(row.get("total_size_bytes", 0))
            oldest = cast(Optional[datetime], row.get("oldest_file"))
            newest = cast(Optional[datetime], row.get("newest_file"))
            if total_files == 0:
                return {
                    "total_files": 0,
                    "total_size_bytes": 0,
                    "oldest_file": None,
                    "newest_file": None,
                }
            return {
                "total_files": total_files,
                "total_size_bytes": total_size,
                "oldest_file": oldest,
                "newest_file": newest,
            }

        return self._with_retry(_run)

    def mark_as_archived(self, uids: List[str]) -> int:
        """Mark files as archived in the source database.

        Args:
            uids: List of UIDs to mark as archived.

        Returns:
            Number of rows updated.

        Raises:
            ValueError: If `archived_column` is not configured.
        """

        if self._archived_column is None:
            raise ValueError("archived_column is not configured for SourceDatabase")
        if not uids:
            return 0

        archived_col = self._archived_column
        stmt = (
            update(self._table)
            .where(getattr(self._table.c, self._uid_column).in_(uids))
            .values({archived_col: True})
        )

        def _run_update() -> int:
            with self._engine.begin() as conn:
                result = conn.execute(stmt)
                updated = result.rowcount or 0
            logger.info("Marked %d/%d files as archived in table %s", updated, len(uids), self._table_name)
            logger.debug("UIDs to mark as archived: %s", uids)
            return updated

        return self._with_retry(_run_update)

    def _build_statement(self, cutoff_date: datetime, limit: Optional[int], archived_column: str) -> Select[Any]:
        stmt = (
            select(
                getattr(self._table.c, self._uid_column).label("uid"),
                getattr(self._table.c, self._created_at_column).label("created_at"),
                getattr(self._table.c, self._file_location_column).label("file_location"),
            )
            .where(
                and_(
                    getattr(self._table.c, self._created_at_column) < cutoff_date,
                    getattr(self._table.c, archived_column).is_(False),
                )
            )
            .order_by(asc(getattr(self._table.c, self._created_at_column)))
        )
        size_column = (
            getattr(self._table.c, self._size_bytes_column).label("size_bytes")
            if self._size_bytes_column
            else literal(None).label("size_bytes")
        )
        stmt = stmt.add_columns(size_column)
        if limit is not None:
            stmt = stmt.limit(limit)
        return stmt

    def _execute(self, stmt: Select[Any]) -> Sequence[Mapping[str, Any]]:
        with self._engine.connect() as conn:
            result = conn.execute(stmt)
            rows = result.mappings().all()
        return cast(Sequence[Mapping[str, Any]], rows)

    def _with_retry(self, func: Callable[[], _T]) -> _T:
        attempt = 0
        while True:
            try:
                return func()
            except (OperationalError, DBAPIError) as exc:
                attempt += 1
                should_retry = isinstance(exc, OperationalError) or (
                    isinstance(exc, DBAPIError) and exc.connection_invalidated
                )
                if attempt > self._max_retries or not should_retry:
                    raise
                sleep_for = self._backoff_base * (2 ** (attempt - 1))
                time.sleep(sleep_for)

    def _row_to_record(self, row: Mapping[str, Any]) -> SourceFileRecord:
        created_at_val = row["created_at"]
        if not isinstance(created_at_val, datetime):
            raise TypeError(f"created_at must be datetime, got {type(created_at_val)!r}")

        size_val = row.get("size_bytes")
        size_bytes: Optional[int]
        if size_val is None:
            size_bytes = None
        elif isinstance(size_val, int):
            size_bytes = size_val
        else:
            size_bytes = int(size_val)

        return SourceFileRecord(
            uid=str(row["uid"]),
            created_at=created_at_val,
            file_location=str(row["file_location"]),
            size_bytes=size_bytes,
        )
