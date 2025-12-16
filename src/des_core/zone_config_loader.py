"""Utilities for loading multi-zone S3 configuration from YAML or JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Tuple, cast

from .multi_s3_retriever import S3ZoneConfig, S3ZoneRange
from .s3_retriever import S3Config


def _load_raw_config(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - depends on optional dep
            raise RuntimeError("PyYAML is required to load YAML zone configs") from exc
        loaded: Any = yaml.safe_load(path.read_text())
        if not isinstance(loaded, dict):
            raise ValueError("Zones config must be a mapping")
        return cast(dict[str, Any], loaded)
    if suffix == ".json":
        loaded_json: Any = json.loads(path.read_text())
        if not isinstance(loaded_json, dict):
            raise ValueError("Zones config must be a mapping")
        return cast(dict[str, Any], loaded_json)
    raise ValueError(f"Unsupported zones config format: {path}")


def load_zones_config(path: Path) -> Tuple[int, List[S3ZoneConfig]]:
    """Load MultiS3 zones configuration from a YAML or JSON file."""

    if not path.exists():
        raise ValueError(f"Zones config file does not exist: {path}")

    raw = _load_raw_config(path)
    if not isinstance(raw, dict):
        raise ValueError("Zones config must be a mapping")

    n_bits = int(raw.get("n_bits", 8))
    zones_raw = raw.get("zones")
    if not isinstance(zones_raw, list):
        raise ValueError("Zones config must contain a 'zones' list")

    zones: List[S3ZoneConfig] = []
    for zone in zones_raw:
        try:
            name = zone["name"]
            range_info = zone["range"]
            s3_info = zone["s3"]
        except Exception as exc:
            raise ValueError("Each zone must have 'name', 'range', and 's3' sections") from exc

        zr = S3ZoneRange(start=int(range_info["start"]), end=int(range_info["end"]))
        s3_config = S3Config(
            bucket=s3_info["bucket"],
            prefix=s3_info.get("prefix", ""),
            region_name=s3_info.get("region_name"),
            endpoint_url=s3_info.get("endpoint_url"),
        )
        zones.append(S3ZoneConfig(name=name, range=zr, s3_config=s3_config))

    return n_bits, zones
