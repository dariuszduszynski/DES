from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from des_core import cli_migrator


def test_requires_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli_migrator.main(["--dry-run"])

    captured = capsys.readouterr()
    assert "--config" in captured.err or "--config" in captured.out


def test_interval_validation() -> None:
    with pytest.raises(SystemExit):
        cli_migrator.main(["--config", "foo.json", "--interval", "-1"])


def test_env_substitution(tmp_path: Path) -> None:
    cfg = {"database": {"url": "postgresql+psycopg://user:${DB_PASS}@host/db"}, "migration": {}}
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps(cfg))
    os.environ["DB_PASS"] = "secret"

    parsed = cli_migrator._load_config(cfg_path)

    assert parsed["database"]["url"] == "postgresql+psycopg://user:secret@host/db"


def test_yaml_loading(tmp_path: Path) -> None:
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text("database:\n  url: sqlite+pysqlite:///./test.db\nmigration:\n  archive_age_days: 1\n")
    parsed = cli_migrator._load_config(cfg_path)
    assert parsed["database"]["url"].startswith("sqlite")
