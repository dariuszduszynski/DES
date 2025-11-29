from __future__ import annotations

import argparse
from datetime import datetime

from .db_connector import SourceDatabase


def _parse_datetime(value: str) -> datetime:
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="DES dry-run statistics for archivable files.")
    parser.add_argument("--db-url", required=True, help="Database URL, e.g. postgresql+psycopg://user:pass@host/db")
    parser.add_argument("--table", required=True, help="Table name containing source files.")
    parser.add_argument("--cutoff", required=True, help="ISO datetime; files older than this are counted.")
    parser.add_argument("--uid-column", default="uid")
    parser.add_argument("--created-at-column", default="created_at")
    parser.add_argument("--file-location-column", default="file_location")
    parser.add_argument("--size-bytes-column", default="size_bytes", help="Set to '' to disable size aggregation.")
    parser.add_argument("--archived-column", default="archived")
    parser.add_argument("--dry-run", action="store_true", help="Only compute statistics (default behavior).")
    args = parser.parse_args()

    cutoff_dt = _parse_datetime(args.cutoff)
    size_col = args.size_bytes_column or None

    db = SourceDatabase(
        db_url=args.db_url,
        table_name=args.table,
        uid_column=args.uid_column,
        created_at_column=args.created_at_column,
        file_location_column=args.file_location_column,
        size_bytes_column=size_col,
        archived_column=args.archived_column,
    )
    stats = db.get_archive_statistics(cutoff_dt)

    print("=== DES Dry-Run Statistics ===")
    print(f"Files eligible for archiving: {stats['total_files']:,}")
    print(f"Total size: {stats['total_size_bytes']:,} bytes")
    print(f"Oldest file: {stats['oldest_file']}")
    print(f"Newest file: {stats['newest_file']}")
    print("==============================")


if __name__ == "__main__":  # pragma: no cover
    main()
