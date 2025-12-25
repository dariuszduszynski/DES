from __future__ import annotations

import sys
from datetime import datetime, timezone

import pytest

from des_core import cli_stats


def test_main_reports_stats(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    captured: dict[str, object] = {}

    class DummyDatabase:
        def __init__(self, **kwargs: object) -> None:
            captured["kwargs"] = kwargs

        def get_archive_statistics(self, cutoff: datetime) -> dict[str, object]:
            captured["cutoff"] = cutoff
            return {
                "total_files": 1000,
                "total_size_bytes": 2048,
                "oldest_file": datetime(2024, 1, 1, tzinfo=timezone.utc),
                "newest_file": datetime(2024, 1, 2, tzinfo=timezone.utc),
            }

    monkeypatch.setattr(cli_stats, "SourceDatabase", DummyDatabase)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cli_stats",
            "--db-url",
            "sqlite+pysqlite:///tmp.db",
            "--table",
            "files",
            "--cutoff",
            "2024-01-01T00:00:00Z",
            "--size-bytes-column",
            "",
        ],
    )

    cli_stats.main()

    out = capsys.readouterr().out
    assert "=== DES Dry-Run Statistics ===" in out
    assert "Files eligible for archiving: 1,000" in out
    assert "Total size: 2,048 bytes" in out
    assert "Oldest file: 2024-01-01 00:00:00+00:00" in out
    assert "Newest file: 2024-01-02 00:00:00+00:00" in out

    kwargs = captured["kwargs"]
    assert kwargs["db_url"] == "sqlite+pysqlite:///tmp.db"
    assert kwargs["table_name"] == "files"
    assert kwargs["size_bytes_column"] is None
    assert captured["cutoff"].isoformat().endswith("+00:00")
