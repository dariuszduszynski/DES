"""CLI utilities for DES shard metadata management."""

from __future__ import annotations

import sys
from datetime import datetime
from typing import Any, List

import boto3
import click
from botocore.exceptions import ClientError

from .metadata_manager import MetadataManager, entry_from_dict
from .shard_io import ShardReader, decompress_entry


def _is_not_found_error(exc: ClientError) -> bool:
    code = exc.response.get("Error", {}).get("Code")
    return code in {"404", "NoSuchKey", "NotFound"}


def _list_shard_keys(s3: Any, bucket: str, prefix: str) -> List[str]:
    paginator = s3.get_paginator("list_objects_v2")
    keys: List[str] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            key = item.get("Key")
            if key and key.endswith(".des"):
                keys.append(key)
    return keys


def _meta_exists(s3: Any, bucket: str, meta_key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=meta_key)
        return True
    except ClientError as exc:
        if _is_not_found_error(exc):
            return False
        raise


@click.group()
def meta() -> None:
    """Metadata management commands."""


@meta.command()
@click.option("--bucket", required=True)
@click.option("--prefix", default="shards/")
def generate(bucket: str, prefix: str) -> None:
    """Generate .meta files for all .des shards (migration)."""

    s3 = boto3.client("s3")
    manager = MetadataManager(s3, bucket)
    shard_keys = _list_shard_keys(s3, bucket, prefix)

    with click.progressbar(shard_keys, label="Generating metadata") as bar:
        for shard_key in bar:
            meta_key = shard_key[:-4] + ".meta" if shard_key.endswith(".des") else f"{shard_key}.meta"
            if _meta_exists(s3, bucket, meta_key):
                continue
            manager._rebuild_metadata(shard_key)


@meta.command()
@click.option("--bucket", envvar="DES_S3_BUCKET", required=True)
@click.option("--shard", required=True)
def verify(bucket: str, shard: str) -> None:
    """Verify .meta file consistency with .des shard."""

    s3 = boto3.client("s3")
    manager = MetadataManager(s3, bucket)
    try:
        meta = manager.get_metadata(shard, rebuild_on_missing=False)
    except Exception as exc:
        raise click.ClickException(f"Failed to load metadata: {exc}") from exc

    response = s3.get_object(Bucket=bucket, Key=shard)
    body = response["Body"].read()
    reader = ShardReader.from_bytes(body)

    meta_uids = {key.split(":", 1)[0] for key in meta.index.keys()}
    shard_uids = set(reader.index.keys())

    missing = shard_uids - meta_uids
    extra = meta_uids - shard_uids

    click.echo(f"Shard entries: {len(shard_uids)}")
    click.echo(f"Metadata entries: {len(meta_uids)}")

    if missing:
        click.echo(f"Missing in metadata: {len(missing)}")
    if extra:
        click.echo(f"Extra in metadata: {len(extra)}")

    if missing or extra:
        sys.exit(1)
    click.echo("Metadata matches shard index.")


@meta.command()
@click.option("--bucket", required=True)
@click.option("--shard", required=True)
@click.option("--sample-size", default=100, show_default=True, help="Number of entries to verify")
def verify_checksums(bucket: str, shard: str, sample_size: int) -> None:
    """Verify checksums for sample entries in a shard."""

    s3 = boto3.client("s3")
    manager = MetadataManager(s3, bucket)

    try:
        meta = manager.get_metadata(shard, rebuild_on_missing=False)
    except Exception as exc:
        raise click.ClickException(f"Failed to load metadata: {exc}") from exc

    entries = list(meta.index.items())
    if sample_size < len(entries):
        entries = entries[:sample_size]

    verified = 0
    corrupted = 0
    missing_checksum = 0

    with click.progressbar(entries, label="Verifying checksums") as bar:
        for key, entry_dict in bar:
            uid = entry_dict.get("uid")
            if not uid:
                click.echo(f"Missing uid in entry {key}")
                corrupted += 1
                continue

            created_at_str = None
            meta_value = entry_dict.get("meta")
            if isinstance(meta_value, dict):
                created_at_str = meta_value.get("created_at")
            if not created_at_str and ":" in key:
                _, created_at_str = key.split(":", 1)

            if not created_at_str:
                click.echo(f"Missing created_at for {uid}")
                corrupted += 1
                continue

            try:
                created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
            except ValueError as exc:
                click.echo(f"Invalid created_at for {uid}: {exc}")
                corrupted += 1
                continue

            try:
                entry = entry_from_dict(entry_dict)
                payload = manager._fetch_entry_payload(shard, entry)
                if entry.is_bigfile:
                    data = payload
                else:
                    data = decompress_entry(entry, payload)
                is_valid = manager.verify_entry_checksum(shard, uid, created_at, data)
            except Exception as exc:
                click.echo(f"Error verifying {uid}: {exc}")
                corrupted += 1
                continue

            if is_valid:
                verified += 1
            else:
                if entry_dict.get("checksum") is None:
                    missing_checksum += 1
                else:
                    corrupted += 1

    click.echo(f"\n✅ Verified: {verified}")
    click.echo(f"❌ Corrupted: {corrupted}")
    click.echo(f"⚠️  Missing checksum: {missing_checksum}")

    if corrupted > 0:
        sys.exit(1)


def main() -> None:
    meta()


if __name__ == "__main__":  # pragma: no cover
    main()
