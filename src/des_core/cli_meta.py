"""CLI utilities for DES shard metadata management."""

from __future__ import annotations

import sys
from typing import Any, List

import boto3
import click
from botocore.exceptions import ClientError

from .metadata_manager import MetadataManager
from .shard_io import ShardReader


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


def main() -> None:
    meta()


if __name__ == "__main__":  # pragma: no cover
    main()
