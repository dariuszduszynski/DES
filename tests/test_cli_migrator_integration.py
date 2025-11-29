from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, create_engine


def _create_db(db_path: Path, files: list[Path]) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
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
            "created_at": now - timedelta(days=10 + i),
            "file_location": str(path),
            "size_bytes": path.stat().st_size,
            "archived": False,
        }
        for i, path in enumerate(files, start=1)
    ]
    with engine.begin() as conn:
        conn.execute(table.insert(), rows)


@pytest.mark.integration
def test_cli_migrate_dry_run(tmp_path: Path) -> None:
    db_path = tmp_path / "files.db"
    out_dir = tmp_path / "out"
    f = tmp_path / "file.bin"
    f.write_bytes(b"a" * 10)
    _create_db(db_path, [f])

    cfg = {
        "database": {"url": f"sqlite+pysqlite:///{db_path}", "table_name": "files"},
        "migration": {"archive_age_days": 1, "batch_size": 10},
        "packer": {"output_dir": str(out_dir)},
    }
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps(cfg))

    result = subprocess.run(
        [sys.executable, "-m", "des_core.cli_migrator", "--config", str(cfg_path), "--dry-run"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "mode=dry_run" in result.stdout


def test_cli_missing_env_var(tmp_path: Path) -> None:
    cfg = {"database": {"url": "postgresql+psycopg://user:${MISSING}@host/db"}, "migration": {}}
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps(cfg))

    result = subprocess.run(
        [sys.executable, "-m", "des_core.cli_migrator", "--config", str(cfg_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
