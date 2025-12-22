"""CLI utilities for DES public key authentication."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

import click
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa


def _load_private_key(path: Path) -> Any:
    data = path.read_bytes()
    try:
        return serialization.load_pem_private_key(data, password=None)
    except ValueError:
        return serialization.load_ssh_private_key(data, password=None)


def _sign_payload(private_key: Any, payload: bytes) -> bytes:
    if isinstance(private_key, ed25519.Ed25519PrivateKey):
        return private_key.sign(payload)
    if isinstance(private_key, rsa.RSAPrivateKey):
        return private_key.sign(payload, padding.PKCS1v15(), hashes.SHA256())
    if isinstance(private_key, ec.EllipticCurvePrivateKey):
        return private_key.sign(payload, ec.ECDSA(hashes.SHA256()))
    raise ValueError("Unsupported private key type")


def _append_created_at(url: str, created_at: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if "created_at" not in query:
        query["created_at"] = [created_at]
    new_query = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def _extract_uid(url: str) -> str:
    path = urlparse(url).path.strip("/")
    parts = path.split("/")
    if len(parts) < 2 or parts[-2] != "files":
        raise ValueError("URL must point to /files/{uid}")
    return parts[-1]


@click.group()
def auth() -> None:
    """Authentication utilities."""


@auth.command()
@click.option("--private-key", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--url", required=True)
@click.option("--created-at", required=True)
def test(private_key: Path, url: str, created_at: str) -> None:
    """Test authentication with a private key."""

    key = _load_private_key(private_key)
    uid = _extract_uid(url)
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    nonce = str(uuid.uuid4())
    canonical = f"{uid}|{created_at}|{timestamp}|{nonce}".encode("utf-8")
    signature = _sign_payload(key, canonical)

    public_key = key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    )

    headers = {
        "X-DES-Public-Key": base64.b64encode(public_key).decode("ascii"),
        "X-DES-Signature": base64.b64encode(signature).decode("ascii"),
        "X-DES-Timestamp": timestamp,
        "X-DES-Nonce": nonce,
    }

    final_url = _append_created_at(url, created_at)
    request = Request(final_url, headers=headers, method="GET")

    try:
        with urlopen(request) as response:
            body = response.read()
            click.echo(f"Status: {response.status}")
            if body:
                click.echo(body.decode("utf-8", errors="replace"))
    except HTTPError as exc:
        body = exc.read()
        click.echo(f"Status: {exc.code}")
        if body:
            try:
                payload = json.loads(body.decode("utf-8", errors="replace"))
                click.echo(json.dumps(payload, indent=2))
            except json.JSONDecodeError:
                click.echo(body.decode("utf-8", errors="replace"))
    except URLError as exc:
        raise click.ClickException(str(exc)) from exc


def main() -> None:
    auth()


if __name__ == "__main__":  # pragma: no cover
    main()
