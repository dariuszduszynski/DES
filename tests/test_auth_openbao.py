from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from des_core import auth as auth_module
from des_core.auth import PublicKeyAuthenticator, create_authenticator_from_env


def _build_config(public_key: str) -> dict[str, object]:
    entry: dict[str, object] = {"public_key": public_key, "permissions": ["read"]}
    return {"authorized_keys": [entry]}


def _generate_public_key() -> str:
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    )
    return public_key.decode("utf-8")


def _mock_session(monkeypatch: pytest.MonkeyPatch, payload: dict[str, object]) -> Mock:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    session = Mock()
    session.request.return_value = response
    monkeypatch.setattr(auth_module.requests, "Session", lambda: session)
    return session


def test_openbao_get_authorized_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _mock_session(monkeypatch, {"data": {"data": {"authorized_keys": []}}})
    client = auth_module.OpenBaoClient(addr="http://vault", token=None, role="role")
    client._authenticate = Mock(side_effect=lambda: setattr(client, "_token", "token"))

    result = client.get_authorized_keys()

    assert result == {"authorized_keys": []}
    client._authenticate.assert_called_once()
    session.request.assert_called_once()
    call_kwargs = session.request.call_args.kwargs
    assert call_kwargs["url"] == "http://vault/v1/secret/data/des/authorized_keys"
    assert call_kwargs["headers"] == {"X-Vault-Token": "token"}


def test_openbao_authenticate_kubernetes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    token_path = tmp_path / "token"
    token_path.write_text("jwt-token", encoding="utf-8")
    monkeypatch.setattr(auth_module, "_K8S_TOKEN_PATH", str(token_path))

    session = _mock_session(monkeypatch, {"auth": {"client_token": "vault-token"}})
    client = auth_module.OpenBaoClient(addr="http://vault", token=None, role="role")

    client._authenticate()

    assert client._token == "vault-token"
    session.request.assert_called_once()
    call_kwargs = session.request.call_args.kwargs
    assert call_kwargs["url"] == "http://vault/v1/auth/kubernetes/login"
    assert call_kwargs["json"] == {"role": "role", "jwt": "jwt-token"}


def test_create_authenticator_from_env_openbao(monkeypatch: pytest.MonkeyPatch) -> None:
    public_key = _generate_public_key()
    config = _build_config(public_key)
    monkeypatch.setenv("DES_VAULT_ADDR", "http://vault")
    monkeypatch.setenv("DES_VAULT_TOKEN", "token")
    monkeypatch.delenv("DES_AUTHORIZED_KEYS_PATH", raising=False)
    monkeypatch.setattr(auth_module.OpenBaoClient, "get_authorized_keys", lambda self: config)

    authenticator = create_authenticator_from_env()

    assert isinstance(authenticator, PublicKeyAuthenticator)
    assert len(authenticator._authorized_keys) == 1


def test_create_authenticator_from_env_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    public_key = _generate_public_key()
    config_path = tmp_path / "authorized_keys.yaml"
    config_path.write_text(yaml.safe_dump(_build_config(public_key)), encoding="utf-8")
    monkeypatch.setenv("DES_AUTHORIZED_KEYS_PATH", str(config_path))
    monkeypatch.delenv("DES_VAULT_ADDR", raising=False)
    monkeypatch.delenv("DES_VAULT_TOKEN", raising=False)
    monkeypatch.delenv("DES_VAULT_ROLE", raising=False)

    authenticator = create_authenticator_from_env()

    assert len(authenticator._authorized_keys) == 1


def test_create_authenticator_from_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DES_VAULT_ADDR", raising=False)
    monkeypatch.delenv("DES_AUTHORIZED_KEYS_PATH", raising=False)
    monkeypatch.delenv("DES_VAULT_TOKEN", raising=False)
    monkeypatch.delenv("DES_VAULT_ROLE", raising=False)

    with pytest.raises(ValueError):
        create_authenticator_from_env()


def test_public_key_authenticator_openbao_client() -> None:
    public_key = _generate_public_key()
    config = _build_config(public_key)
    openbao_client = Mock()
    openbao_client.get_authorized_keys.return_value = config

    authenticator = PublicKeyAuthenticator(None, openbao_client=openbao_client)

    openbao_client.get_authorized_keys.assert_called_once()
    assert len(authenticator._authorized_keys) == 1
