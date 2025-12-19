"""Metadata sidecar for DES shards containing index and tombstones."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


class TombstoneError(Exception):
    """Raised when a requested file is tombstoned."""


def _parse_datetime(value: str, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid {field_name} datetime format") from exc


def _validate_mapping(name: str, value: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    for key, entry in value.items():
        if not isinstance(key, str):
            raise ValueError(f"{name} keys must be strings")
        if not isinstance(entry, dict):
            raise ValueError(f"{name} values must be objects")
    return value


@dataclass
class ShardMetadata:
    """Metadata sidecar for DES shard containing index and tombstones."""

    version: int
    shard_file: str
    shard_size: int
    created_at: datetime
    last_updated: datetime
    index: Dict[str, Dict[str, Any]]
    tombstones: Dict[str, Dict[str, Any]]
    stats: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def format_timestamp(value: datetime) -> str:
        """Normalize timestamp to UTC ISO string with Z suffix."""

        if value.tzinfo is None:
            normalized = value.replace(tzinfo=timezone.utc)
        else:
            normalized = value.astimezone(timezone.utc)
        return normalized.isoformat().replace("+00:00", "Z")

    @classmethod
    def build_key(cls, uid: str, created_at: datetime) -> str:
        """Build the lookup key for a UID and creation timestamp."""

        return f"{uid}:{cls.format_timestamp(created_at)}"

    def to_json(self) -> str:
        """Serialize metadata to JSON."""

        return json.dumps(
            {
                "version": self.version,
                "shard_file": self.shard_file,
                "shard_size": self.shard_size,
                "created_at": self.format_timestamp(self.created_at),
                "last_updated": self.format_timestamp(self.last_updated),
                "index": self.index,
                "tombstones": self.tombstones,
                "stats": self.stats,
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, data: str) -> "ShardMetadata":
        """Deserialize metadata from JSON."""

        if not isinstance(data, str):
            raise TypeError("data must be a JSON string")
        try:
            obj = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSON payload") from exc
        if not isinstance(obj, dict):
            raise ValueError("Metadata JSON must be an object")

        required = ["version", "shard_file", "shard_size", "created_at", "last_updated", "index", "tombstones"]
        missing = [field for field in required if field not in obj]
        if missing:
            raise ValueError(f"Missing required metadata fields: {', '.join(missing)}")

        version = obj["version"]
        shard_file = obj["shard_file"]
        shard_size = obj["shard_size"]
        if not isinstance(version, int):
            raise ValueError("version must be an integer")
        if not isinstance(shard_file, str):
            raise ValueError("shard_file must be a string")
        if not isinstance(shard_size, int):
            raise ValueError("shard_size must be an integer")
        if shard_size < 0:
            raise ValueError("shard_size must be non-negative")

        created_at = _parse_datetime(obj["created_at"], "created_at")
        last_updated = _parse_datetime(obj["last_updated"], "last_updated")
        index = _validate_mapping("index", obj["index"])
        tombstones = _validate_mapping("tombstones", obj["tombstones"])

        stats_value = obj.get("stats", {})
        if not isinstance(stats_value, dict):
            raise ValueError("stats must be a mapping")

        return cls(
            version=version,
            shard_file=shard_file,
            shard_size=shard_size,
            created_at=created_at,
            last_updated=last_updated,
            index=index,
            tombstones=tombstones,
            stats=stats_value,
        )

    def is_tombstoned(self, uid: str, created_at: datetime) -> bool:
        """Return True when the file is tombstoned."""

        key = self.build_key(uid, created_at)
        return key in self.tombstones

    def get_entry(self, uid: str, created_at: datetime) -> Optional[Dict[str, Any]]:
        """Return the index entry for a file if present."""

        key = self.build_key(uid, created_at)
        entry = self.index.get(key)
        if entry is not None:
            return entry

        entry = self.index.get(uid)
        if entry is not None:
            return entry

        prefix = f"{uid}:"
        matches = [value for idx_key, value in self.index.items() if idx_key.startswith(prefix)]
        if len(matches) == 1:
            return matches[0]

        return None
