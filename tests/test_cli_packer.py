from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from des_core import cli_packer
from des_core.packer import PackerResult, ShardWriteResult
from des_core.packer_planner import ShardKey


def test_load_files_from_json(tmp_path: Path) -> None:
    payload = [
        {
            "uid": "1",
            "created_at": "2024-01-01T00:00:00Z",
            "size_bytes": 12,
            "source_path": "source-a.bin",
        },
        {
            "uid": 2,
            "created_at": "2024-01-02T00:00:00+00:00",
            "size_bytes": "5",
        },
    ]
    path = tmp_path / "files.json"
    path.write_text(json.dumps(payload))

    files = cli_packer._load_files_from_json(path)

    assert [file.uid for file in files] == ["1", "2"]
    assert files[0].source_path == "source-a.bin"
    assert files[1].source_path is None
    assert files[1].size_bytes == 5
    assert files[0].created_at.utcoffset() == timedelta(0)


def test_main_reports_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    payload = [
        {"uid": "1", "created_at": "2024-01-01T00:00:00Z", "size_bytes": 10, "source_path": "a.bin"},
        {"uid": 2, "created_at": "2024-01-02T00:00:00Z", "size_bytes": 20, "source_path": "b.bin"},
    ]
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(payload))

    shard_a = ShardWriteResult(
        shard_key=ShardKey(date_dir="2024-01-01", shard_hex="aa"),
        path=tmp_path / "a.des",
        file_count=1,
        total_size_bytes=10,
    )
    shard_b = ShardWriteResult(
        shard_key=ShardKey(date_dir="2024-01-02", shard_hex="bb"),
        path=tmp_path / "b.des",
        file_count=2,
        total_size_bytes=20,
    )

    captured: dict[str, object] = {}

    def _fake_pack(files, output_dir, config):
        captured["files"] = files
        captured["output_dir"] = output_dir
        captured["config"] = config
        return PackerResult(shards=[shard_a, shard_b])

    monkeypatch.setattr(cli_packer, "pack_files_to_directory", _fake_pack)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cli_packer",
            "--input-json",
            str(input_path),
            "--output-dir",
            str(tmp_path),
            "--max-shard-size",
            "10",
            "--n-bits",
            "5",
        ],
    )

    cli_packer.main()

    out = capsys.readouterr().out
    assert "Wrote 2 shard(s) containing 3 files, total logical size 30 bytes." in out
    assert f"SHARD: {shard_a.path} files=1 size=10" in out
    assert f"SHARD: {shard_b.path} files=2 size=20" in out

    files = captured["files"]
    assert [file.uid for file in files] == ["1", "2"]
    assert captured["output_dir"] == str(tmp_path)
    config = captured["config"]
    assert config.max_shard_size_bytes == 10
    assert config.n_bits == 5


def test_main_reports_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    payload = [{"uid": "1", "created_at": "2024-01-01T00:00:00Z", "size_bytes": 10}]
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(payload))

    def _fail_pack(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli_packer, "pack_files_to_directory", _fail_pack)
    monkeypatch.setattr(
        sys,
        "argv",
        ["cli_packer", "--input-json", str(input_path), "--output-dir", str(tmp_path)],
    )

    with pytest.raises(SystemExit) as excinfo:
        cli_packer.main()

    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "Packing failed: boom" in out
