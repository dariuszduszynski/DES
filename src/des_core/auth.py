"""Public key authentication using SSH-style authorized keys."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import signal
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, cast

import requests  # type: ignore[import-untyped]
import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa

from .metrics import des_auth_requests_total

logger = logging.getLogger(__name__)

_DEFAULT_REQUEST_TIMEOUT = 10.0
_K8S_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
_MAX_CLOCK_SKEW = timedelta(minutes=5)
_NONCE_TTL = timedelta(minutes=10)
_RATE_LIMIT_WINDOW = timedelta(hours=1)


class _ResponseProtocol(Protocol):
    """Protocol for HTTP responses used by OpenBaoClient."""

    def raise_for_status(self) -> None:
        """Raise if the response indicates an error."""

    def json(self) -> object:
        """Return the JSON payload."""


class _SessionProtocol(Protocol):
    """Protocol for HTTP sessions used by OpenBaoClient."""

    def request(
        self,
        method: str,
        url: str,
        *,
        json: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        timeout: float = _DEFAULT_REQUEST_TIMEOUT,
    ) -> _ResponseProtocol:
        """Issue an HTTP request."""


@dataclass(frozen=True)
class AuthorizedKey:
    public_key: bytes
    key_obj: Any
    permissions: list[str]
    allowed_prefixes: Optional[list[str]]
    excluded_prefixes: Optional[list[str]]
    expires_at: Optional[datetime]
    max_requests_per_hour: Optional[int]
    comment: Optional[str]
    fingerprint: str


def _parse_datetime(value: str) -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fingerprint(public_key: bytes) -> str:
    digest = hashlib.sha256(public_key).digest()
    b64 = base64.b64encode(digest).decode("ascii").rstrip("=")
    return f"SHA256:{b64}"


class OpenBaoClient:
    """Client for OpenBao/Vault KV v2 authorized keys.

    Args:
        addr: Base OpenBao/Vault address.
        token: Token for authentication. If not provided, Kubernetes auth is used.
        role: Kubernetes auth role name required for Kubernetes auth.
        mount: KV v2 mount name.
        path: Secret path within the mount.
    """

    def __init__(
        self,
        *,
        addr: str,
        token: Optional[str] = None,
        role: Optional[str] = None,
        mount: str = "secret",
        path: str = "des/authorized_keys",
    ) -> None:
        self._addr = addr.rstrip("/")
        self._token = token or None
        self._role = role or None
        self._mount = mount.strip("/")
        self._path = path.strip("/")
        self._session: _SessionProtocol = requests.Session()

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        json_payload: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Send an HTTP request and return the JSON response.

        Args:
            method: HTTP method to use.
            url: Full request URL.
            json_payload: Optional JSON payload.

        Returns:
            JSON payload as a mapping.

        Raises:
            RuntimeError: If the request fails due to network errors.
            ValueError: If the response is not a JSON object.
            requests.HTTPError: If OpenBao returns a non-2xx status code.
        """
        headers = {"X-Vault-Token": self._token} if self._token is not None else None
        try:
            response = self._session.request(
                method=method.upper(),
                url=url,
                json=json_payload,
                headers=headers,
                timeout=_DEFAULT_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            logger.error("OpenBao HTTP error url=%s error=%s", url, exc)
            raise
        except requests.RequestException as exc:
            logger.error("OpenBao request failed url=%s error=%s", url, exc)
            raise RuntimeError("OpenBao request failed") from exc

        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("OpenBao response must be a JSON object")
        return cast(dict[str, Any], payload)

    def _authenticate(self) -> None:
        """Authenticate using Kubernetes ServiceAccount token.

        Raises:
            ValueError: If role is missing, token file is missing/empty, or response is invalid.
            RuntimeError: If the request fails due to network errors.
            requests.HTTPError: If OpenBao returns a non-2xx status code.
        """
        if self._role is None:
            logger.error("OpenBao Kubernetes auth role is required")
            raise ValueError("Kubernetes auth role is required for OpenBao authentication")

        token_path = Path(_K8S_TOKEN_PATH)
        try:
            jwt = token_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError as exc:
            logger.error("Kubernetes service account token missing at %s", token_path)
            raise ValueError(f"Kubernetes service account token not found at {token_path}") from exc

        if not jwt:
            logger.error("Kubernetes service account token empty at %s", token_path)
            raise ValueError(f"Kubernetes service account token is empty at {token_path}")

        url = f"{self._addr}/v1/auth/kubernetes/login"
        response = self._request_json("POST", url, json_payload={"role": self._role, "jwt": jwt})
        auth_data = response.get("auth")
        if not isinstance(auth_data, dict):
            raise ValueError("OpenBao auth response missing auth data")

        token = auth_data.get("client_token")
        if not isinstance(token, str) or not token:
            raise ValueError("OpenBao auth response missing client_token")
        self._token = token

    def get_authorized_keys(self) -> dict[str, Any]:
        """Fetch authorized keys from OpenBao/Vault KV v2.

        Returns:
            Authorized keys payload.

        Raises:
            ValueError: If the response is missing expected fields.
            RuntimeError: If the request fails due to network errors.
            requests.HTTPError: If OpenBao returns a non-2xx status code.
        """
        if self._token is None:
            self._authenticate()

        url = f"{self._addr}/v1/{self._mount}/data/{self._path}"
        response = self._request_json("GET", url)
        data = response.get("data")
        if not isinstance(data, dict):
            raise ValueError("OpenBao response missing data")
        inner = data.get("data")
        if not isinstance(inner, dict):
            raise ValueError("OpenBao response missing data.data")
        return cast(dict[str, Any], inner)


class PublicKeyAuthenticator:
    """Authenticator based on SSH-style public keys."""

    def __init__(
        self,
        config_path: Optional[str | Path],
        *,
        config_data: Optional[dict[str, Any]] = None,
        clock: Optional[Callable[[], datetime]] = None,
        openbao_client: Optional[OpenBaoClient] = None,
    ) -> None:
        """Initialize a PublicKeyAuthenticator.

        Args:
            config_path: Path to YAML authorized keys config.
            config_data: In-memory config override.
            clock: Optional clock provider for testing.
            openbao_client: Optional OpenBao/Vault client for loading authorized keys.

        Raises:
            ValueError: If configuration cannot be loaded.
            RuntimeError: If OpenBao requests fail due to network errors.
            requests.HTTPError: If OpenBao returns a non-2xx status code.
        """
        self._config_path = Path(config_path) if config_path else None
        self._config_data = config_data
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._openbao_client = openbao_client
        self._lock = threading.RLock()
        self._authorized_keys: dict[bytes, AuthorizedKey] = {}
        self._rate_limits: dict[str, deque[datetime]] = {}
        self._nonce_cache: dict[str, datetime] = {}
        self.reload()

    def install_signal_handler(self) -> None:
        """Enable SIGHUP reload when available."""

        if hasattr(signal, "SIGHUP"):
            signal.signal(signal.SIGHUP, lambda *_: self.reload())

    def reload(self) -> None:
        """Reload authorized keys from config."""

        config = self._load_config()
        keys = self._parse_config(config)
        with self._lock:
            self._authorized_keys = {entry.public_key: entry for entry in keys}
        logger.info("Loaded %d authorized keys", len(keys))

    def verify_signature(
        self,
        public_key_b64: str,
        signature_b64: str,
        canonical_data: str,
        timestamp: str,
        nonce: str,
    ) -> tuple[bool, Optional[AuthorizedKey], Optional[str]]:
        """Verify signature and return (is_valid, authorized_key, error_reason)."""

        now = self._clock()
        try:
            public_key_bytes = base64.b64decode(public_key_b64, validate=True)
            signature = base64.b64decode(signature_b64, validate=True)
        except (ValueError, TypeError) as exc:
            logger.warning("Invalid base64 auth header: %s", exc)
            des_auth_requests_total.labels(result="invalid_sig").inc()
            return False, None, "invalid_sig"

        try:
            key_obj = serialization.load_ssh_public_key(public_key_bytes)
        except (ValueError, TypeError) as exc:
            logger.warning("Invalid public key: %s", exc)
            des_auth_requests_total.labels(result="invalid_sig").inc()
            return False, None, "invalid_sig"

        normalized_key = key_obj.public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH,
        )
        fingerprint = _fingerprint(normalized_key)

        with self._lock:
            authorized = self._authorized_keys.get(normalized_key)

        if authorized is None:
            logger.warning("Unauthorized key fingerprint=%s", fingerprint)
            des_auth_requests_total.labels(result="invalid_sig").inc()
            return False, None, "invalid_sig"

        if not timestamp or not nonce:
            logger.warning("Missing auth timestamp/nonce fingerprint=%s", fingerprint)
            des_auth_requests_total.labels(result="invalid_sig").inc()
            return False, authorized, "invalid_sig"

        if authorized.expires_at and now > authorized.expires_at:
            logger.warning("Expired key fingerprint=%s", fingerprint)
            des_auth_requests_total.labels(result="expired").inc()
            return False, authorized, "expired"

        try:
            ts = _parse_datetime(timestamp)
        except ValueError:
            logger.warning("Invalid auth timestamp fingerprint=%s", fingerprint)
            des_auth_requests_total.labels(result="invalid_sig").inc()
            return False, authorized, "invalid_sig"

        if abs(now - ts) > _MAX_CLOCK_SKEW:
            logger.warning("Timestamp outside allowed skew fingerprint=%s", fingerprint)
            des_auth_requests_total.labels(result="invalid_sig").inc()
            return False, authorized, "invalid_sig"

        if self._is_nonce_reused(nonce, now):
            logger.warning("Replay nonce detected fingerprint=%s", fingerprint)
            des_auth_requests_total.labels(result="invalid_sig").inc()
            return False, authorized, "invalid_sig"

        if authorized.max_requests_per_hour is not None:
            if self._is_rate_limited(fingerprint, now, authorized.max_requests_per_hour):
                logger.warning("Rate limited fingerprint=%s", fingerprint)
                des_auth_requests_total.labels(result="rate_limited").inc()
                return False, authorized, "rate_limited"

        if not self._verify_signature_with_key(key_obj, signature, canonical_data):
            logger.warning("Invalid signature fingerprint=%s", fingerprint)
            des_auth_requests_total.labels(result="invalid_sig").inc()
            return False, authorized, "invalid_sig"

        logger.info("Auth success fingerprint=%s", fingerprint)
        des_auth_requests_total.labels(result="success").inc()
        return True, authorized, None

    def check_permission(self, authorized_key: AuthorizedKey, action: str, resource_path: str) -> bool:
        """Check if key has permission for action on resource."""

        if action not in authorized_key.permissions:
            return False

        if authorized_key.allowed_prefixes:
            if not any(resource_path.startswith(prefix) for prefix in authorized_key.allowed_prefixes):
                return False

        if authorized_key.excluded_prefixes:
            if any(resource_path.startswith(prefix) for prefix in authorized_key.excluded_prefixes):
                return False

        return True

    def _load_config(self) -> dict[str, Any]:
        if self._config_data is not None:
            return self._config_data
        if self._openbao_client is not None:
            logger.info("Loading authorized keys from OpenBao")
            return self._openbao_client.get_authorized_keys()
        if self._config_path is None:
            raise ValueError("authorized_keys config path is required")
        logger.info("Loading authorized keys from YAML")
        payload = self._config_path.read_text(encoding="utf-8")
        value = yaml.safe_load(payload)
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("authorized keys config must be a mapping")
        return value

    def _parse_config(self, data: dict[str, Any]) -> list[AuthorizedKey]:
        raw_keys = data.get("authorized_keys", [])
        if not isinstance(raw_keys, list):
            raise ValueError("authorized_keys must be a list")
        parsed: list[AuthorizedKey] = []
        for entry in raw_keys:
            parsed.append(self._parse_key_entry(entry))
        return parsed

    def _parse_key_entry(self, entry: dict[str, Any]) -> AuthorizedKey:
        if not isinstance(entry, dict):
            raise ValueError("authorized_keys entry must be a mapping")
        public_key_str = entry.get("public_key")
        if not isinstance(public_key_str, str):
            raise ValueError("public_key must be a string")
        try:
            key_obj = serialization.load_ssh_public_key(public_key_str.encode("utf-8"))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid public_key: {exc}") from exc

        normalized_key = key_obj.public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH,
        )
        permissions = entry.get("permissions", [])
        if not isinstance(permissions, list) or not all(isinstance(p, str) for p in permissions):
            raise ValueError("permissions must be a list of strings")

        allowed = entry.get("allowed_prefixes")
        if allowed is not None:
            if not isinstance(allowed, list) or not all(isinstance(p, str) for p in allowed):
                raise ValueError("allowed_prefixes must be a list of strings")

        excluded = entry.get("excluded_prefixes")
        if excluded is not None:
            if not isinstance(excluded, list) or not all(isinstance(p, str) for p in excluded):
                raise ValueError("excluded_prefixes must be a list of strings")

        expires_at_value = entry.get("expires_at")
        expires_at = None
        if expires_at_value is not None:
            if not isinstance(expires_at_value, str):
                raise ValueError("expires_at must be a string")
            expires_at = _parse_datetime(expires_at_value)

        max_requests = entry.get("max_requests_per_hour")
        if max_requests is not None:
            if not isinstance(max_requests, int):
                raise ValueError("max_requests_per_hour must be an integer")
            if max_requests <= 0:
                raise ValueError("max_requests_per_hour must be positive")

        comment = entry.get("comment")
        if comment is not None and not isinstance(comment, str):
            raise ValueError("comment must be a string")

        return AuthorizedKey(
            public_key=normalized_key,
            key_obj=key_obj,
            permissions=permissions,
            allowed_prefixes=allowed,
            excluded_prefixes=excluded,
            expires_at=expires_at,
            max_requests_per_hour=max_requests,
            comment=comment,
            fingerprint=_fingerprint(normalized_key),
        )

    def _is_rate_limited(self, fingerprint: str, now: datetime, limit: int) -> bool:
        with self._lock:
            queue = self._rate_limits.setdefault(fingerprint, deque())
            cutoff = now - _RATE_LIMIT_WINDOW
            while queue and queue[0] < cutoff:
                queue.popleft()
            if len(queue) >= limit:
                return True
            queue.append(now)
        return False

    def _is_nonce_reused(self, nonce: str, now: datetime) -> bool:
        with self._lock:
            cutoff = now - _NONCE_TTL
            expired = [key for key, ts in self._nonce_cache.items() if ts < cutoff]
            for key in expired:
                self._nonce_cache.pop(key, None)
            if nonce in self._nonce_cache:
                return True
            self._nonce_cache[nonce] = now
        return False

    @staticmethod
    def _verify_signature_with_key(key_obj: Any, signature: bytes, canonical_data: str) -> bool:
        data = canonical_data.encode("utf-8")
        try:
            if isinstance(key_obj, ed25519.Ed25519PublicKey):
                key_obj.verify(signature, data)
            elif isinstance(key_obj, rsa.RSAPublicKey):
                key_obj.verify(signature, data, padding.PKCS1v15(), hashes.SHA256())
            elif isinstance(key_obj, ec.EllipticCurvePublicKey):
                key_obj.verify(signature, data, ec.ECDSA(hashes.SHA256()))
            else:
                return False
        except InvalidSignature:
            return False
        return True


def create_authenticator_from_env() -> PublicKeyAuthenticator:
    """Create an authenticator from environment variables.

    Environment variables:
        DES_VAULT_ADDR: Base OpenBao/Vault address (enables OpenBao backend).
        DES_VAULT_TOKEN: OpenBao/Vault token (optional).
        DES_VAULT_ROLE: Kubernetes auth role (required if token is not set).
        DES_VAULT_MOUNT: KV v2 mount point (default: "secret").
        DES_VAULT_PATH: Secret path (default: "des/authorized_keys").
        DES_AUTHORIZED_KEYS_PATH: YAML fallback path when Vault is not configured.

    Returns:
        Configured authenticator instance.

    Raises:
        ValueError: If neither Vault nor YAML configuration is provided.
        RuntimeError: If OpenBao requests fail due to network errors.
        requests.HTTPError: If OpenBao returns a non-2xx status code.

    Examples:
        >>> os.environ["DES_VAULT_ADDR"] = "https://vault.local"
        >>> os.environ["DES_VAULT_TOKEN"] = "s.xxxxx"
        >>> authenticator = create_authenticator_from_env()
        >>> os.environ["DES_AUTHORIZED_KEYS_PATH"] = "/etc/des/authorized_keys.yaml"
        >>> authenticator = create_authenticator_from_env()
    """
    addr = os.environ.get("DES_VAULT_ADDR")
    if addr:
        token = os.environ.get("DES_VAULT_TOKEN") or None
        role = os.environ.get("DES_VAULT_ROLE") or None
        if token is None and role is None:
            raise ValueError("DES_VAULT_ROLE is required when DES_VAULT_TOKEN is not set")
        mount = os.environ.get("DES_VAULT_MOUNT", "secret")
        path = os.environ.get("DES_VAULT_PATH", "des/authorized_keys")
        client = OpenBaoClient(addr=addr, token=token, role=role, mount=mount, path=path)
        return PublicKeyAuthenticator(None, openbao_client=client)

    config_path = os.environ.get("DES_AUTHORIZED_KEYS_PATH")
    if config_path:
        return PublicKeyAuthenticator(config_path)

    raise ValueError("DES_VAULT_ADDR or DES_AUTHORIZED_KEYS_PATH must be set")
