"""Compression configuration and profiles for DES shards."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class CompressionCodec(str, Enum):
    NONE = "none"
    ZSTD = "zstd"
    LZ4 = "lz4"


class CompressionProfile(str, Enum):
    """High-level compression profiles used in DES."""

    AGGRESSIVE = "aggressive"
    BALANCED = "balanced"
    SPEED = "speed"


@dataclass
class CompressionConfig:
    """Configuration for payload compression in DES shards."""

    codec: CompressionCodec = CompressionCodec.ZSTD
    level: int | None = None
    profile: CompressionProfile = CompressionProfile.BALANCED
    skip_extensions: tuple[str, ...] = (
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".gz",
        ".zip",
        ".bz2",
        ".xz",
    )

    def should_compress(self, logical_name: str) -> bool:
        """Return True if logical name should be compressed."""

        suffix = Path(logical_name).suffix.lower()
        if suffix and suffix in self.skip_extensions:
            return False
        return self.codec != CompressionCodec.NONE


def aggressive_zstd_config() -> CompressionConfig:
    return CompressionConfig(codec=CompressionCodec.ZSTD, level=9, profile=CompressionProfile.AGGRESSIVE)


def balanced_zstd_config() -> CompressionConfig:
    return CompressionConfig(codec=CompressionCodec.ZSTD, level=5, profile=CompressionProfile.BALANCED)


def speed_lz4_config() -> CompressionConfig:
    return CompressionConfig(codec=CompressionCodec.LZ4, level=None, profile=CompressionProfile.SPEED)
