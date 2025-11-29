"""Helpers for resolving BigFiles paths and keys."""

from __future__ import annotations

from pathlib import Path, PurePosixPath


def build_bigfile_key(shard_key: str, bigfiles_prefix: str, bigfile_hash: str) -> str:
    """Return the S3 object key for a bigfile next to `shard_key`."""

    prefix_clean = bigfiles_prefix.strip("/")
    parent = PurePosixPath(shard_key).parent
    parent_str = "" if str(parent) == "." else str(parent)

    parts = [p for p in (parent_str, prefix_clean, bigfile_hash) if p]
    return "/".join(parts)


def resolve_bigfiles_dir(base_path: Path, bigfiles_prefix: str) -> Path:
    """Return the directory under `base_path` where bigfiles live."""

    return base_path / bigfiles_prefix
