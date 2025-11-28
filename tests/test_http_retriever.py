from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from des_core.http_retriever import HttpRetrieverSettings, create_app
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


def test_http_retriever_happy_path(tmp_path: Path) -> None:
    payloads = {"100": b"a", "356": b"b"}
    created = datetime(2024, 1, 1)
    files = _make_sources(tmp_path, payloads, created)
    config = PlannerConfig(max_shard_size_bytes=8, n_bits=8)
    pack_files_to_directory(files, tmp_path, config)

    settings = HttpRetrieverSettings(base_dir=tmp_path, n_bits=8)
    app = create_app(settings)
    client = TestClient(app)

    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    created_str = created.isoformat()
    for uid, data in payloads.items():
        resp = client.get(f"/files/{uid}", params={"created_at": created_str})
        assert resp.status_code == 200
        assert resp.content == data


def test_http_retriever_not_found(tmp_path: Path) -> None:
    payloads = {"100": b"a"}
    created = datetime(2024, 1, 1)
    files = _make_sources(tmp_path, payloads, created)
    config = PlannerConfig(max_shard_size_bytes=8, n_bits=8)
    pack_files_to_directory(files, tmp_path, config)

    settings = HttpRetrieverSettings(base_dir=tmp_path, n_bits=8)
    client = TestClient(create_app(settings))

    resp = client.get("/files/999", params={"created_at": created.isoformat()})
    assert resp.status_code == 404
    assert resp.json() == {"detail": "File not found"}

    wrong_date = datetime(2024, 1, 2).isoformat()
    resp = client.get("/files/100", params={"created_at": wrong_date})
    assert resp.status_code == 404
    assert resp.json() == {"detail": "File not found"}


def test_http_retriever_invalid_created_at(tmp_path: Path) -> None:
    settings = HttpRetrieverSettings(base_dir=tmp_path, n_bits=8)
    client = TestClient(create_app(settings))

    resp = client.get("/files/uid", params={"created_at": "not-a-date"})
    assert resp.status_code == 400
    assert resp.json() == {"detail": "Invalid created_at format"}
