"""Thin daily marker helper for advancing the archive cutoff.

Usage:
    repo = ArchiveConfigRepository(conn)
    await repo.ensure_initialized(default_archived_until, default_lag_days=7)
    window = await repo.advance_cutoff(datetime.utcnow())
    # window contains (window_start, window_end] for packers to consume.
"""

from __future__ import annotations

from datetime import datetime

from .archive_config import ArchiveConfigRepository, ArchiveWindow


async def advance_archive_marker(
    repo: ArchiveConfigRepository,
    *,
    default_archived_until: datetime,
    default_lag_days: int = 7,
    now: datetime | None = None,
) -> ArchiveWindow:
    """Ensure config row exists and advance archived_until once per invocation."""

    await repo.ensure_initialized(default_archived_until=default_archived_until, default_lag_days=default_lag_days)
    current_time = now or datetime.utcnow()
    return await repo.advance_cutoff(current_time)
