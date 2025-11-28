from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from des_core import http_retriever
from des_core.http_retriever import HttpRetrieverSettings, _load_settings_from_env, create_app
from des_core.packer import pack_files_to_directory
from des_core.packer_planner import FileToPack, PlannerConfig


def _make_sources(tmp_path: Path, payloads: dict[str, bytes], created_at: datetime) -> list[FileToPack]:
    files = []
    for uid, data in payloads.items():
        src = tmp_path / f"{uid}.bin"
        src.write_bytes(data)
        files.append(
            FileToPack(
                uid=uid,
                created_at=created_at,
                size_bytes=len(data),
                source_path=src,
            )
        )
    return files


def test_http_retriever_local_backend_still_works(tmp_path: Path) -> None:
    payloads = {"100": b"a"}
    created = datetime(2024, 1, 1)
    files = _make_sources(tmp_path, payloads, created)
    pack_files_to_directory(files, tmp_path, PlannerConfig(max_shard_size_bytes=16))

    settings = HttpRetrieverSettings(backend="local", base_dir=tmp_path, n_bits=8)
    app = create_app(settings)
    client = TestClient(app)

    resp = client.get(f"/files/100", params={"created_at": created.isoformat()})
    assert resp.status_code == 200
    assert resp.content == b"a"


def test_http_retriever_s3_backend_uses_s3_retriever(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[str, datetime]] = []

    class FakeS3Retriever:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def get_file(self, uid: str, created_at: datetime) -> bytes:
            calls.append((uid, created_at))
            return b"dummy"

    monkeypatch.setattr(http_retriever, "S3ShardRetriever", FakeS3Retriever)

    settings = HttpRetrieverSettings(
        backend="s3",
        s3_bucket="test-bucket",
        n_bits=8,
    )
    app = create_app(settings)
    client = TestClient(app)

    created_str = datetime(2024, 1, 1).isoformat()
    resp = client.get("/files/uid-123", params={"created_at": created_str})
    assert resp.status_code == 200
    assert resp.content == b"dummy"

    assert calls
    uid, dt = calls[0]
    assert uid == "uid-123"
    assert dt == datetime.fromisoformat(created_str)


def test_load_settings_from_env_local(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DES_S3_BUCKET", raising=False)
    monkeypatch.setenv("DES_BASE_DIR", str(tmp_path / "desdata"))
    settings = _load_settings_from_env()

    assert settings.backend == "local"
    assert settings.base_dir == tmp_path / "desdata"


def test_load_settings_from_env_s3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DES_S3_BUCKET", "bucket")
    monkeypatch.setenv("DES_BACKEND", "s3")
    settings = _load_settings_from_env()

    assert settings.backend == "s3"
    assert settings.s3_bucket == "bucket"
