import base64
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from des_core.auth import PublicKeyAuthenticator


def _build_config(public_key: str, **overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {"public_key": public_key, "permissions": ["read"]}
    entry.update(overrides)
    return {"authorized_keys": [entry]}


def _encode_public_key(public_key: str) -> str:
    return base64.b64encode(public_key.encode("utf-8")).decode("ascii")


def _sign_ed25519(private_key: ed25519.Ed25519PrivateKey, payload: str) -> str:
    signature = private_key.sign(payload.encode("utf-8"))
    return base64.b64encode(signature).decode("ascii")


def _sign_rsa(private_key: rsa.RSAPrivateKey, payload: str) -> str:
    signature = private_key.sign(payload.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode("ascii")


def test_verify_signature_valid_ed25519() -> None:
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode("utf-8")

    config = _build_config(public_key)
    auth = PublicKeyAuthenticator(None, config_data=config, clock=lambda: now)

    created_at = "2024-01-01T00:00:00Z"
    timestamp = now.isoformat().replace("+00:00", "Z")
    nonce = "nonce-1"
    canonical = f"uid-1|{created_at}|{timestamp}|{nonce}"
    signature_b64 = _sign_ed25519(private_key, canonical)

    is_valid, key, error = auth.verify_signature(
        public_key_b64=_encode_public_key(public_key),
        signature_b64=signature_b64,
        canonical_data=canonical,
        timestamp=timestamp,
        nonce=nonce,
    )

    assert is_valid is True
    assert key is not None
    assert error is None


def test_verify_signature_invalid_signature() -> None:
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode("utf-8")

    config = _build_config(public_key)
    auth = PublicKeyAuthenticator(None, config_data=config, clock=lambda: now)

    created_at = "2024-01-01T00:00:00Z"
    timestamp = now.isoformat().replace("+00:00", "Z")
    nonce = "nonce-2"
    canonical = f"uid-1|{created_at}|{timestamp}|{nonce}"
    signature_b64 = _sign_ed25519(private_key, canonical + "tampered")

    is_valid, key, error = auth.verify_signature(
        public_key_b64=_encode_public_key(public_key),
        signature_b64=signature_b64,
        canonical_data=canonical,
        timestamp=timestamp,
        nonce=nonce,
    )

    assert is_valid is False
    assert key is not None
    assert error == "invalid_sig"


def test_verify_signature_expired_key() -> None:
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode("utf-8")

    expires_at = (now - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    config = _build_config(public_key, expires_at=expires_at)
    auth = PublicKeyAuthenticator(None, config_data=config, clock=lambda: now)

    created_at = "2024-01-01T00:00:00Z"
    timestamp = now.isoformat().replace("+00:00", "Z")
    nonce = "nonce-3"
    canonical = f"uid-1|{created_at}|{timestamp}|{nonce}"
    signature_b64 = _sign_ed25519(private_key, canonical)

    is_valid, key, error = auth.verify_signature(
        public_key_b64=_encode_public_key(public_key),
        signature_b64=signature_b64,
        canonical_data=canonical,
        timestamp=timestamp,
        nonce=nonce,
    )

    assert is_valid is False
    assert key is not None
    assert error == "expired"


def test_verify_signature_rate_limited() -> None:
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode("utf-8")

    config = _build_config(public_key, max_requests_per_hour=1)
    auth = PublicKeyAuthenticator(None, config_data=config, clock=lambda: now)

    created_at = "2024-01-01T00:00:00Z"
    timestamp = now.isoformat().replace("+00:00", "Z")

    canonical_one = f"uid-1|{created_at}|{timestamp}|nonce-4"
    signature_one = _sign_ed25519(private_key, canonical_one)
    auth.verify_signature(
        public_key_b64=_encode_public_key(public_key),
        signature_b64=signature_one,
        canonical_data=canonical_one,
        timestamp=timestamp,
        nonce="nonce-4",
    )

    canonical_two = f"uid-1|{created_at}|{timestamp}|nonce-5"
    signature_two = _sign_ed25519(private_key, canonical_two)
    is_valid, key, error = auth.verify_signature(
        public_key_b64=_encode_public_key(public_key),
        signature_b64=signature_two,
        canonical_data=canonical_two,
        timestamp=timestamp,
        nonce="nonce-5",
    )

    assert is_valid is False
    assert key is not None
    assert error == "rate_limited"


def test_verify_signature_valid_rsa() -> None:
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode("utf-8")

    config = _build_config(public_key)
    auth = PublicKeyAuthenticator(None, config_data=config, clock=lambda: now)

    created_at = "2024-01-01T00:00:00Z"
    timestamp = now.isoformat().replace("+00:00", "Z")
    nonce = "nonce-6"
    canonical = f"uid-1|{created_at}|{timestamp}|{nonce}"
    signature_b64 = _sign_rsa(private_key, canonical)

    is_valid, key, error = auth.verify_signature(
        public_key_b64=_encode_public_key(public_key),
        signature_b64=signature_b64,
        canonical_data=canonical,
        timestamp=timestamp,
        nonce=nonce,
    )

    assert is_valid is True
    assert key is not None
    assert error is None


def test_check_permission_with_prefixes() -> None:
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode("utf-8")

    config = _build_config(
        public_key,
        permissions=["read"],
        allowed_prefixes=["finance/"],
        excluded_prefixes=["finance/pii/"],
    )
    auth = PublicKeyAuthenticator(None, config_data=config, clock=lambda: now)
    authorized = list(auth._authorized_keys.values())[0]

    assert auth.check_permission(authorized, "read", "finance/report") is True
    assert auth.check_permission(authorized, "read", "legal/report") is False
    assert auth.check_permission(authorized, "read", "finance/pii/record") is False
