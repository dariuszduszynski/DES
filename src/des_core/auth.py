"""Public key authentication using SSH-style authorized keys."""

from __future__ import annotations

import base64
import hashlib
import logging
import signal
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa

from .metrics import des_auth_requests_total

logger = logging.getLogger(__name__)

_MAX_CLOCK_SKEW = timedelta(minutes=5)
_NONCE_TTL = timedelta(minutes=10)
_RATE_LIMIT_WINDOW = timedelta(hours=1)


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


class PublicKeyAuthenticator:
    """Authenticator based on SSH-style public keys."""

    def __init__(
        self,
        config_path: str | Path | None,
        *,
        config_data: dict[str, Any] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._config_path = Path(config_path) if config_path else None
        self._config_data = config_data
        self._clock = clock or (lambda: datetime.now(timezone.utc))
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
        if self._config_path is None:
            raise ValueError("authorized_keys config path is required")
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
