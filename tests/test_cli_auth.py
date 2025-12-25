from __future__ import annotations

import base64
import io
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

import pytest
from click.testing import CliRunner
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa

from des_core import cli_auth


def _write_private_key(path: Path, key: object) -> None:
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path.write_bytes(pem)


def test_load_private_key_ed25519(tmp_path: Path) -> None:
    key = ed25519.Ed25519PrivateKey.generate()
    key_path = tmp_path / "key.pem"
    _write_private_key(key_path, key)

    loaded = cli_auth._load_private_key(key_path)

    assert isinstance(loaded, ed25519.Ed25519PrivateKey)


def test_sign_payload_supports_key_types() -> None:
    payload = b"payload"
    keys = [
        ed25519.Ed25519PrivateKey.generate(),
        rsa.generate_private_key(public_exponent=65537, key_size=2048),
        ec.generate_private_key(ec.SECP256R1()),
    ]

    for key in keys:
        signature = cli_auth._sign_payload(key, payload)
        public_key = key.public_key()
        if isinstance(key, ed25519.Ed25519PrivateKey):
            public_key.verify(signature, payload)
        elif isinstance(key, rsa.RSAPrivateKey):
            public_key.verify(signature, payload, padding.PKCS1v15(), hashes.SHA256())
        else:
            public_key.verify(signature, payload, ec.ECDSA(hashes.SHA256()))


def test_sign_payload_rejects_unknown_key() -> None:
    with pytest.raises(ValueError):
        cli_auth._sign_payload(object(), b"payload")


def test_append_created_at() -> None:
    url = "https://example.com/files/uid-1"
    updated = cli_auth._append_created_at(url, "2024-01-01T00:00:00Z")
    query = parse_qs(urlparse(updated).query)
    assert query["created_at"] == ["2024-01-01T00:00:00Z"]

    updated_again = cli_auth._append_created_at(updated, "2024-01-01T00:00:00Z")
    query = parse_qs(urlparse(updated_again).query)
    assert query["created_at"] == ["2024-01-01T00:00:00Z"]


def test_extract_uid() -> None:
    assert cli_auth._extract_uid("https://example.com/files/uid-2") == "uid-2"
    with pytest.raises(ValueError):
        cli_auth._extract_uid("https://example.com/other/uid-2")


def test_cli_auth_test_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    key = ed25519.Ed25519PrivateKey.generate()
    key_path = tmp_path / "key.pem"
    _write_private_key(key_path, key)

    captured: dict[str, object] = {}

    class DummyResponse:
        def __init__(self, status: int, body: bytes) -> None:
            self.status = status
            self._body = body

        def read(self) -> bytes:
            return self._body

        def __enter__(self) -> "DummyResponse":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    def fake_urlopen(request: object) -> DummyResponse:
        captured["request"] = request
        return DummyResponse(200, b"ok")

    class FixedDateTime:
        @staticmethod
        def now(tz: timezone | None = None) -> datetime:
            return datetime(2024, 1, 1, tzinfo=timezone.utc)

    monkeypatch.setattr(cli_auth, "urlopen", fake_urlopen)
    monkeypatch.setattr(cli_auth, "datetime", FixedDateTime)
    monkeypatch.setattr(cli_auth.uuid, "uuid4", lambda: uuid.UUID("00000000-0000-0000-0000-000000000001"))

    runner = CliRunner()
    result = runner.invoke(
        cli_auth.auth,
        [
            "test",
            "--private-key",
            str(key_path),
            "--url",
            "https://example.com/files/uid-1",
            "--created-at",
            "2024-01-01T00:00:00Z",
        ],
    )

    assert result.exit_code == 0
    assert "Status: 200" in result.output
    assert "ok" in result.output

    request = captured["request"]
    headers = {k.lower(): v for k, v in request.headers.items()}
    public_key_b64 = headers["x-des-public-key"]
    signature_b64 = headers["x-des-signature"]
    timestamp = headers["x-des-timestamp"]
    nonce = headers["x-des-nonce"]

    assert timestamp == "2024-01-01T00:00:00Z"
    assert nonce == "00000000-0000-0000-0000-000000000001"

    expected_public = key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode("utf-8")
    assert base64.b64decode(public_key_b64).decode("utf-8") == expected_public

    signature = base64.b64decode(signature_b64)
    canonical = f"uid-1|2024-01-01T00:00:00Z|{timestamp}|{nonce}".encode("utf-8")
    key.public_key().verify(signature, canonical)

    query = parse_qs(urlparse(request.full_url).query)
    assert query["created_at"] == ["2024-01-01T00:00:00Z"]


def test_cli_auth_http_error_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    key = ed25519.Ed25519PrivateKey.generate()
    key_path = tmp_path / "key.pem"
    _write_private_key(key_path, key)

    def fake_urlopen(_request: object) -> None:
        body = io.BytesIO(b'{"detail": "bad"}')
        raise HTTPError("https://example.com/files/uid-1", 400, "Bad Request", None, body)

    monkeypatch.setattr(cli_auth, "urlopen", fake_urlopen)

    runner = CliRunner()
    result = runner.invoke(
        cli_auth.auth,
        [
            "test",
            "--private-key",
            str(key_path),
            "--url",
            "https://example.com/files/uid-1",
            "--created-at",
            "2024-01-01T00:00:00Z",
        ],
    )

    assert result.exit_code == 0
    assert "Status: 400" in result.output
    assert '"detail": "bad"' in result.output


def test_cli_auth_url_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    key = ed25519.Ed25519PrivateKey.generate()
    key_path = tmp_path / "key.pem"
    _write_private_key(key_path, key)

    def fake_urlopen(_request: object) -> None:
        raise URLError("boom")

    monkeypatch.setattr(cli_auth, "urlopen", fake_urlopen)

    runner = CliRunner()
    result = runner.invoke(
        cli_auth.auth,
        [
            "test",
            "--private-key",
            str(key_path),
            "--url",
            "https://example.com/files/uid-1",
            "--created-at",
            "2024-01-01T00:00:00Z",
        ],
    )

    assert result.exit_code == 1
    assert "boom" in result.output
