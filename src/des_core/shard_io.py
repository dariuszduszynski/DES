"""Local DES shard file IO for versioned shards with optional BigFiles.

Format overview (little-endian, append-only):

    [ HEADER ][ DATA ... ][ INDEX ][ FOOTER ]

Header (always 8 bytes):
    4 bytes  magic      = b"DES2"
    1 byte   version    = 0x01 (legacy) or 0x02 (BigFiles-aware)
    3 bytes  reserved   = b"\x00\x00\x00"

Data section:
    Raw file payloads written back-to-back in the order added (omitted for
    BigFiles stored externally).

Index section:
    4 bytes  entry_count (uint32)
    repeat entry_count times (layout depends on header version):
      v1 entries:
        2 bytes name_len (uint16)
        name_len bytes UTF-8 UID
        8 bytes offset (uint64, absolute from file start)
        8 bytes length (uint64)
        1 byte  codec (uint8)
        8 bytes compressed_size (uint64)
        8 bytes uncompressed_size (uint64)
      v2 entries:
        2 bytes name_len (uint16)
        name_len bytes UTF-8 UID
        1 byte  flags (bit0 = is_bigfile)
        if flags & 0x01:
            2 bytes hash_len (uint16)
            hash_len bytes UTF-8 hash
            8 bytes bigfile_size (uint64)
            4 bytes meta_len (uint32)
            meta_len bytes UTF-8 JSON metadata (may be empty)
        else:
            8 bytes offset (uint64)
            8 bytes length (uint64)
            1 byte  codec (uint8)
            8 bytes compressed_size (uint64)
            8 bytes uncompressed_size (uint64)
            4 bytes meta_len (uint32)
            meta_len bytes UTF-8 JSON metadata (may be empty)

Footer:
    4 bytes  footer magic = b"DESI"
    8 bytes  index_size (uint64) - size in bytes of the entire index section.
Footer is fixed-size and sits at the end of the file; index starts at
(file_size - FOOTER_SIZE - index_size).
"""

from __future__ import annotations

import hashlib
import io
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO, Dict, List, Optional, Type

from .bigfiles import resolve_bigfiles_dir
from .compression import CompressionCodec, CompressionConfig
from .config import DESConfig

HEADER_MAGIC = b"DES2"
FOOTER_MAGIC = b"DESI"
LEGACY_VERSION = 0x01
VERSION = 0x02
SUPPORTED_VERSIONS = {LEGACY_VERSION, VERSION}
HEADER_RESERVED = b"\x00\x00\x00"
HEADER_SIZE = 4 + 1 + 3
FOOTER_SIZE = 4 + 8
BIGFILE_FLAG = 0x01
_CODEC_BYTE_MAP = {
    CompressionCodec.NONE: 0,
    CompressionCodec.ZSTD: 1,
    CompressionCodec.LZ4: 2,
}


def _codec_to_byte(codec: CompressionCodec) -> int:
    try:
        return _CODEC_BYTE_MAP[codec]
    except KeyError:
        raise ValueError(f"Unsupported codec {codec}")


def _byte_to_codec(value: int) -> CompressionCodec:
    for codec, b in _CODEC_BYTE_MAP.items():
        if b == value:
            return codec
    raise ValueError(f"Unknown codec byte {value}")


def _serialize_meta(meta: dict[str, Any]) -> bytes:
    if not meta:
        return b""
    return json.dumps(meta, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def _deserialize_meta(meta_bytes: bytes) -> dict[str, Any]:
    if not meta_bytes:
        return {}
    value = json.loads(meta_bytes.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Index metadata must decode to a mapping")
    return value


@dataclass(frozen=True)
class HeaderInfo:
    version: int


@dataclass(frozen=True)
class FooterInfo:
    index_size: int
    index_offset: int


def parse_header(header_bytes: bytes) -> HeaderInfo:
    """Parse and validate the shard header."""

    if len(header_bytes) != HEADER_SIZE:
        raise ValueError("Invalid header size.")
    magic, version, reserved = header_bytes[:4], header_bytes[4], header_bytes[5:]
    if magic != HEADER_MAGIC:
        raise ValueError("Invalid shard header magic.")
    if version not in SUPPORTED_VERSIONS:
        raise ValueError(f"Unsupported shard version: {version}")
    if reserved != HEADER_RESERVED:
        raise ValueError("Invalid shard header reserved bytes.")
    return HeaderInfo(version=version)


def parse_footer(footer_bytes: bytes, total_size: int) -> FooterInfo:
    if len(footer_bytes) != FOOTER_SIZE:
        raise ValueError("Invalid footer size")
    magic = footer_bytes[:4]
    if magic != FOOTER_MAGIC:
        raise ValueError("Invalid shard footer magic.")
    (index_size,) = struct.unpack("<Q", footer_bytes[4:])
    index_offset = total_size - FOOTER_SIZE - index_size
    if index_offset < HEADER_SIZE:
        raise ValueError("Computed index offset is invalid.")
    return FooterInfo(index_size=index_size, index_offset=index_offset)


@dataclass(frozen=True)
class ShardFileEntry:
    """Single file record inside a shard."""

    uid: str
    offset: int | None
    length: int | None
    codec: CompressionCodec | None
    compressed_size: int | None
    uncompressed_size: int | None
    is_bigfile: bool = False
    bigfile_hash: str | None = None
    bigfile_size: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ShardIndex:
    """In-memory index mapping UID to file entry."""

    entries: Dict[str, ShardFileEntry]

    def __len__(self) -> int:
        return len(self.entries)

    def __contains__(self, uid: str) -> bool:
        return uid in self.entries

    def get(self, uid: str) -> Optional[ShardFileEntry]:
        return self.entries.get(uid)

    def keys(self) -> List[str]:
        return list(self.entries.keys())

    def values(self) -> List[ShardFileEntry]:
        return list(self.entries.values())

    def items(self) -> List[tuple[str, ShardFileEntry]]:
        return list(self.entries.items())


class ShardWriter:
    """Context manager for writing DES shard files locally."""

    def __init__(
        self,
        target: Path | str | BinaryIO,
        *,
        compression: CompressionConfig | None = None,
        config: DESConfig | None = None,
        bigfiles_dir: Path | None = None,
    ):
        self._owns_handle = False
        self._fp: BinaryIO
        self._target_path: Path | None = None
        if isinstance(target, (str, Path)):
            path = Path(target)
            self._target_path = path
            self._fp = open(path, "wb")
            self._owns_handle = True
        else:
            self._fp = target
            if hasattr(target, "name"):
                try:
                    self._target_path = Path(getattr(target, "name"))
                except TypeError:
                    self._target_path = None

        self._started = False
        self._closed = False
        self._entries: Dict[str, ShardFileEntry] = {}
        self._compression = compression or CompressionConfig(codec=CompressionCodec.NONE)
        self._config = config or DESConfig.from_env()
        self._bigfiles_dir = bigfiles_dir

    def __enter__(self) -> "ShardWriter":
        self._write_header()
        return self

    def __exit__(
        self,
        exc_type: Type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self._finalize()
        self._close_if_owned()

    def add_file(self, uid: str, data: bytes, meta: Optional[dict[str, Any]] = None) -> ShardFileEntry:
        """Append a file payload to the shard. Returns the index entry."""

        self._write_header()
        if self._closed:
            raise ValueError("ShardWriter is closed.")
        if uid in self._entries:
            raise ValueError(f"UID {uid!r} already exists in shard.")
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data must be bytes")

        meta_dict = dict(meta) if meta else {}
        if len(data) > self._config.big_file_threshold_bytes:
            entry = self._write_bigfile(uid, bytes(data), meta_dict)
        else:
            entry = self._write_inline(uid, bytes(data), meta_dict)

        self._entries[uid] = entry
        return entry

    def _write_inline(self, uid: str, data: bytes, meta: dict[str, Any]) -> ShardFileEntry:
        codec = CompressionCodec.NONE
        compressed = bytes(data)

        if self._compression.should_compress(uid):
            codec = self._compression.codec
            if codec is CompressionCodec.ZSTD:
                import zstandard as zstd

                compressor = zstd.ZstdCompressor(level=self._compression.level or 3)
                compressed = compressor.compress(compressed)
            elif codec is CompressionCodec.LZ4:
                import lz4.frame as lz4f

                if self._compression.level is not None:
                    compressed = lz4f.compress(compressed, compression_level=self._compression.level)
                else:
                    compressed = lz4f.compress(compressed)
            else:
                codec = CompressionCodec.NONE
                compressed = bytes(data)

        fp = self._fp
        offset = fp.tell()
        fp.write(compressed)
        return ShardFileEntry(
            uid=uid,
            offset=offset,
            length=len(compressed),
            codec=codec,
            compressed_size=len(compressed),
            uncompressed_size=len(data),
            meta=meta,
        )

    def _write_bigfile(self, uid: str, data: bytes, meta: dict[str, Any]) -> ShardFileEntry:
        bigfiles_root = self._resolve_bigfiles_root()
        bigfiles_root.mkdir(parents=True, exist_ok=True)
        bigfile_hash = hashlib.sha256(data).hexdigest()
        target_path = bigfiles_root / bigfile_hash
        target_path.write_bytes(data)
        return ShardFileEntry(
            uid=uid,
            offset=None,
            length=None,
            codec=None,
            compressed_size=None,
            uncompressed_size=len(data),
            is_bigfile=True,
            bigfile_hash=bigfile_hash,
            bigfile_size=len(data),
            meta=meta,
        )

    def _resolve_bigfiles_root(self) -> Path:
        if self._bigfiles_dir is not None:
            return self._bigfiles_dir
        if self._target_path is None:
            raise ValueError("bigfiles_dir must be provided when writing to a stream without a filesystem path.")
        return resolve_bigfiles_dir(self._target_path.parent, self._config.bigfiles_prefix)

    def _write_header(self) -> None:
        if self._started:
            return
        self._fp.write(HEADER_MAGIC)
        self._fp.write(struct.pack("<B", VERSION))
        self._fp.write(HEADER_RESERVED)
        self._started = True

    def _finalize(self) -> None:
        if self._closed:
            return

        index_buffer = io.BytesIO()
        index_buffer.write(struct.pack("<I", len(self._entries)))

        for entry in self._entries.values():
            name_bytes = entry.uid.encode("utf-8")
            if len(name_bytes) > 0xFFFF:
                raise ValueError(f"UID too long to encode: {entry.uid!r}")
            index_buffer.write(struct.pack("<H", len(name_bytes)))
            index_buffer.write(name_bytes)
            meta_bytes = _serialize_meta(entry.meta)
            if entry.is_bigfile:
                flags = BIGFILE_FLAG
                hash_bytes = (entry.bigfile_hash or "").encode("utf-8")
                index_buffer.write(struct.pack("<B", flags))
                index_buffer.write(struct.pack("<H", len(hash_bytes)))
                index_buffer.write(hash_bytes)
                index_buffer.write(struct.pack("<Q", entry.bigfile_size or 0))
                index_buffer.write(struct.pack("<I", len(meta_bytes)))
                index_buffer.write(meta_bytes)
            else:
                flags = 0
                if entry.offset is None or entry.length is None or entry.codec is None:
                    raise ValueError(f"Inline entry missing required fields for UID {entry.uid!r}")
                index_buffer.write(struct.pack("<B", flags))
                index_buffer.write(struct.pack("<QQ", entry.offset, entry.length))
                index_buffer.write(struct.pack("<B", _codec_to_byte(entry.codec)))
                index_buffer.write(struct.pack("<QQ", entry.compressed_size or 0, entry.uncompressed_size or 0))
                index_buffer.write(struct.pack("<I", len(meta_bytes)))
                index_buffer.write(meta_bytes)

        index_bytes = index_buffer.getvalue()
        index_size = len(index_bytes)

        self._fp.write(index_bytes)
        self._fp.write(FOOTER_MAGIC)
        self._fp.write(struct.pack("<Q", index_size))
        self._fp.flush()
        self._closed = True

    def _close_if_owned(self) -> None:
        if self._owns_handle:
            self._fp.close()


class ShardReader:
    """Reader for DES shard files."""

    def __init__(
        self,
        fp: BinaryIO,
        owns_stream: bool = False,
        *,
        config: DESConfig | None = None,
        base_dir: Path | None = None,
    ):
        self._fp = fp
        self._owns_stream = owns_stream
        self._config = config or DESConfig.from_env()
        self._base_dir = base_dir
        self.header = self._parse_header()
        self.index = self._load_index()

    @classmethod
    def from_path(cls, path: Path | str, *, config: DESConfig | None = None) -> "ShardReader":
        shard_path = Path(path)
        fp = open(shard_path, "rb")
        return cls._from_stream(fp, owns_stream=True, config=config, base_dir=shard_path.parent)

    @classmethod
    def from_bytes(cls, data: bytes, *, config: DESConfig | None = None, base_dir: Path | None = None) -> "ShardReader":
        """Create a reader from in-memory shard bytes."""

        stream = io.BytesIO(data)
        return cls._from_stream(stream, owns_stream=True, config=config, base_dir=base_dir)

    @classmethod
    def _from_stream(
        cls,
        stream: BinaryIO,
        owns_stream: bool,
        *,
        config: DESConfig | None = None,
        base_dir: Path | None = None,
    ) -> "ShardReader":
        return cls(stream, owns_stream=owns_stream, config=config, base_dir=base_dir)

    def __enter__(self) -> "ShardReader":
        return self

    def __exit__(
        self,
        exc_type: Type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._owns_stream:
            self._fp.close()

    def list_uids(self) -> List[str]:
        return self.index.keys()

    def has_uid(self, uid: str) -> bool:
        return uid in self.index

    def read_file(self, uid: str) -> bytes:
        entry = self.index.get(uid)
        if entry is None:
            raise KeyError(f"UID {uid!r} not found in shard.")
        if entry.is_bigfile:
            return self._load_bigfile(entry)

        if entry.offset is None or entry.length is None:
            raise ValueError(f"Inline entry missing offsets for UID {uid!r}")

        self._fp.seek(entry.offset)
        data = self._fp.read(entry.length)
        if len(data) != entry.length:
            raise ValueError(f"Unexpected end of file while reading UID {uid!r}")

        return decompress_entry(entry, data)

    def _parse_header(self) -> HeaderInfo:
        self._fp.seek(0)
        header_bytes = self._fp.read(HEADER_SIZE)
        if len(header_bytes) != HEADER_SIZE:
            raise ValueError("File too small to be a valid DES shard.")
        return parse_header(header_bytes)

    def _load_index(self) -> ShardIndex:
        fp = self._fp
        fp.seek(0, io.SEEK_END)
        file_size = fp.tell()

        if file_size < HEADER_SIZE + FOOTER_SIZE:
            raise ValueError("File too small to be a valid DES shard.")

        fp.seek(file_size - FOOTER_SIZE)
        footer_bytes = fp.read(FOOTER_SIZE)
        footer = parse_footer(footer_bytes, total_size=file_size)

        fp.seek(footer.index_offset)
        index_data = fp.read(footer.index_size)
        if len(index_data) != footer.index_size:
            raise ValueError("Failed to read full index section.")

        entries = parse_index(index_data, data_section_end=footer.index_offset, version=self.header.version)
        return ShardIndex(entries=entries)

    def _load_bigfile(self, entry: ShardFileEntry) -> bytes:
        if entry.bigfile_hash is None:
            raise ValueError("Bigfile entry missing hash.")
        bigfiles_root = self._resolve_bigfile_root()
        path = bigfiles_root / entry.bigfile_hash
        data = path.read_bytes()
        if entry.bigfile_size is not None and len(data) != entry.bigfile_size:
            raise ValueError(f"Bigfile size mismatch for UID {entry.uid!r}")
        return data

    def _resolve_bigfile_root(self) -> Path:
        if self._base_dir is not None:
            return resolve_bigfiles_dir(self._base_dir, self._config.bigfiles_prefix)
        raise ValueError("Bigfile root unknown for this shard reader.")


# Complexity reduced: split UID reading and version-specific entry parsing into helpers to flatten branching in parse_index.
def _ensure_available(data: bytes, offset: int, needed: int, message: str) -> None:
    if offset + needed > len(data):
        raise ValueError(message)


def _read_uid(data: bytes, offset: int) -> tuple[str, int]:
    _ensure_available(data, offset, 2, "Truncated index while reading name length.")
    name_len = struct.unpack_from("<H", data, offset)[0]
    offset += 2

    _ensure_available(data, offset, name_len, "Truncated index while reading UID.")
    name_bytes = data[offset : offset + name_len]
    offset += name_len
    return name_bytes.decode("utf-8"), offset


def _parse_legacy_entry(data: bytes, offset: int, uid: str, data_section_end: int) -> tuple[ShardFileEntry, int]:
    _ensure_available(data, offset, 16 + 1 + 16, "Truncated index while reading entry metadata.")
    file_offset, length = struct.unpack_from("<QQ", data, offset)
    offset += 16
    codec_byte = struct.unpack_from("<B", data, offset)[0]
    offset += 1
    compressed_size, uncompressed_size = struct.unpack_from("<QQ", data, offset)
    offset += 16

    if file_offset + length > data_section_end:
        raise ValueError("Indexed file extends beyond data section.")

    entry = ShardFileEntry(
        uid=uid,
        offset=file_offset,
        length=length,
        codec=_byte_to_codec(codec_byte),
        compressed_size=compressed_size,
        uncompressed_size=uncompressed_size,
    )
    return entry, offset


def _parse_bigfile_entry(data: bytes, offset: int, uid: str) -> tuple[ShardFileEntry, int]:
    _ensure_available(data, offset, 2, "Truncated bigfile entry while reading hash length.")
    hash_len = struct.unpack_from("<H", data, offset)[0]
    offset += 2

    _ensure_available(data, offset, hash_len, "Truncated bigfile entry while reading hash.")
    hash_bytes = data[offset : offset + hash_len]
    offset += hash_len

    _ensure_available(data, offset, 8 + 4, "Truncated bigfile entry while reading sizes.")
    (bigfile_size,) = struct.unpack_from("<Q", data, offset)
    offset += 8
    meta_len = struct.unpack_from("<I", data, offset)[0]
    offset += 4

    _ensure_available(data, offset, meta_len, "Truncated bigfile entry while reading metadata.")
    meta = _deserialize_meta(data[offset : offset + meta_len])
    offset += meta_len

    entry = ShardFileEntry(
        uid=uid,
        offset=None,
        length=None,
        codec=None,
        compressed_size=None,
        uncompressed_size=bigfile_size,
        is_bigfile=True,
        bigfile_hash=hash_bytes.decode("utf-8"),
        bigfile_size=bigfile_size,
        meta=meta,
    )
    return entry, offset


def _parse_inline_entry(
    data: bytes, offset: int, uid: str, data_section_end: int
) -> tuple[ShardFileEntry, int]:
    _ensure_available(data, offset, 16 + 1 + 16 + 4, "Truncated index while reading entry metadata.")
    file_offset, length = struct.unpack_from("<QQ", data, offset)
    offset += 16
    codec_byte = struct.unpack_from("<B", data, offset)[0]
    offset += 1
    compressed_size, uncompressed_size = struct.unpack_from("<QQ", data, offset)
    offset += 16
    meta_len = struct.unpack_from("<I", data, offset)[0]
    offset += 4

    _ensure_available(data, offset, meta_len, "Truncated entry while reading metadata.")
    meta = _deserialize_meta(data[offset : offset + meta_len])
    offset += meta_len

    if file_offset + length > data_section_end:
        raise ValueError("Indexed file extends beyond data section.")

    entry = ShardFileEntry(
        uid=uid,
        offset=file_offset,
        length=length,
        codec=_byte_to_codec(codec_byte),
        compressed_size=compressed_size,
        uncompressed_size=uncompressed_size,
        meta=meta,
    )
    return entry, offset


def _parse_v2_entry(data: bytes, offset: int, uid: str, data_section_end: int) -> tuple[ShardFileEntry, int]:
    _ensure_available(data, offset, 1, "Truncated index while reading flags.")
    flags = struct.unpack_from("<B", data, offset)[0]
    offset += 1

    if flags & BIGFILE_FLAG:
        return _parse_bigfile_entry(data, offset, uid)
    return _parse_inline_entry(data, offset, uid, data_section_end)


def parse_index(data: bytes, data_section_end: int, *, version: int) -> Dict[str, ShardFileEntry]:
    _ensure_available(data, 0, 4, "Index too small to contain entry count.")

    entry_count = struct.unpack_from("<I", data, 0)[0]
    offset = 4
    entries: Dict[str, ShardFileEntry] = {}

    for _ in range(entry_count):
        uid, offset = _read_uid(data, offset)
        if version == LEGACY_VERSION:
            entry, offset = _parse_legacy_entry(data, offset, uid, data_section_end)
        else:
            entry, offset = _parse_v2_entry(data, offset, uid, data_section_end)
        entries[uid] = entry

    return entries


def decompress_entry(entry: ShardFileEntry, data: bytes) -> bytes:
    if entry.is_bigfile:
        raise ValueError("decompress_entry should not be used for bigfile entries.")
    if entry.codec == CompressionCodec.NONE or entry.codec is None:
        return data

    if entry.codec == CompressionCodec.ZSTD:
        import zstandard as zstd

        decompressor = zstd.ZstdDecompressor()
        result = decompressor.decompress(data, max_output_size=entry.uncompressed_size or 0)
    elif entry.codec == CompressionCodec.LZ4:
        import lz4.frame as lz4f

        result = lz4f.decompress(data)
    else:
        raise ValueError(f"Unsupported compression codec {entry.codec}")

    if entry.uncompressed_size is not None and len(result) != entry.uncompressed_size:
        raise ValueError("Decompressed size mismatch")
    return result
