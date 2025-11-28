"""Pure routing utilities for Datavision Easy Store (DES).

These functions deterministically map `(uid, created_at)` to a shard location
without consulting any database or external state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import zlib


@dataclass(frozen=True, slots=True)
class ShardLocation:
    """Resolved shard metadata for a single UID."""

    uid: str
    created_at: datetime
    date_dir: str
    shard_index: int
    shard_hex: str
    object_key: str


def _validate_n_bits(n_bits: int) -> int:
    if not 4 <= n_bits <= 16:
        raise ValueError("n_bits must be between 4 and 16, inclusive.")
    return n_bits


def normalize_uid(uid: int | str) -> str:
    """Return UID as a string without altering its value."""

    if isinstance(uid, int):
        return str(uid)
    if isinstance(uid, str):
        return uid
    raise TypeError(f"Unsupported UID type: {type(uid)!r}")


def format_date_dir(created_at: datetime) -> str:
    """Format the datetime as a YYYYMMDD directory name."""

    return created_at.strftime("%Y%m%d")


def compute_shard_index_from_uid(uid: str, n_bits: int = 8) -> int:
    """Compute the shard index from a UID using a small hash space.

    Numeric UIDs are sharded via modulo; other strings use CRC32 over UTF-8.
    The function is deterministic and requires no external state.
    """

    validated_bits = _validate_n_bits(n_bits)
    mask = (1 << validated_bits) - 1

    if uid.isdigit():
        return int(uid) % (mask + 1)

    return zlib.crc32(uid.encode("utf-8")) & mask


def shard_index_to_hex(shard_index: int, n_bits: int = 8) -> str:
    """Convert a shard index to zero-padded uppercase hex."""

    validated_bits = _validate_n_bits(n_bits)
    max_value = (1 << validated_bits) - 1

    if shard_index < 0 or shard_index > max_value:
        raise ValueError(f"shard_index {shard_index} outside range 0..{max_value}")

    width = max(1, validated_bits // 4)
    return f"{shard_index:0{width}X}"


def build_object_key(date_dir: str, shard_hex: str) -> str:
    """Build the shard object key as YYYYMMDD/HH.des."""

    return f"{date_dir}/{shard_hex}.des"


def locate_shard(uid: int | str, created_at: datetime, n_bits: int = 8) -> ShardLocation:
    """Resolve full shard location for a UID and timestamp.

    The process is purely functional: normalize UID, derive date directory,
    compute a bounded shard index, format it as hex, and assemble the final
    object key.
    """

    normalized_uid = normalize_uid(uid)
    date_dir = format_date_dir(created_at)
    shard_index = compute_shard_index_from_uid(normalized_uid, n_bits)
    shard_hex = shard_index_to_hex(shard_index, n_bits)
    object_key = build_object_key(date_dir, shard_hex)

    return ShardLocation(
        uid=normalized_uid,
        created_at=created_at,
        date_dir=date_dir,
        shard_index=shard_index,
        shard_hex=shard_hex,
        object_key=object_key,
    )
