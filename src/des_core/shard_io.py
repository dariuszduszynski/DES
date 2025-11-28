"""Local DES shard file IO for version 2 shards.

Format overview (little-endian, append-only):

    [ HEADER ][ DATA ... ][ INDEX ][ FOOTER ]

Header:
    4 bytes  magic      = b"DES2"
    1 byte   version    = 0x01
    3 bytes  reserved   = b"\\x00\\x00\\x00"

Data section:
    Raw file payloads written back-to-back in the order added.

Index section:
    4 bytes  entry_count (uint32)
    repeat entry_count times:
        2 bytes name_len (uint16)
        name_len bytes UTF-8 UID
        8 bytes offset (uint64, absolute from file start)
        8 bytes length (uint64)

Footer:
    4 bytes  footer magic = b"DESI"
    8 bytes  index_size (uint64) - size in bytes of the entire index section.
"""

from __future__ import annotations

from dataclasses import dataclass
import io
from pathlib import Path
import struct
from typing import BinaryIO, Dict, Iterable, List, Optional

from .compression import CompressionCodec, CompressionConfig


HEADER_MAGIC = b"DES2"
FOOTER_MAGIC = b"DESI"
VERSION = 0x01
HEADER_RESERVED = b"\x00\x00\x00"
HEADER_SIZE = 4 + 1 + 3
FOOTER_SIZE = 4 + 8
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


@dataclass(frozen=True)
class ShardFileEntry:
    """Single file record inside a shard."""

    uid: str
    offset: int
    length: int
    codec: CompressionCodec
    compressed_size: int
    uncompressed_size: int


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

    def items(self):
        return self.entries.items()


class ShardWriter:
    """Context manager for writing DES v2 shard files locally."""

    def __init__(self, target: Path | str | BinaryIO, *, compression: CompressionConfig | None = None):
        self._owns_handle = False
        if isinstance(target, (str, Path)):
            self._fp = open(target, "wb")
            self._owns_handle = True
        else:
            self._fp = target
        self._started = False
        self._closed = False
        self._entries: Dict[str, ShardFileEntry] = {}
        self._compression = compression or CompressionConfig(codec=CompressionCodec.NONE)

    def __enter__(self) -> "ShardWriter":
        self._write_header()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self._finalize()
        self._close_if_owned()

    def add_file(self, uid: str, data: bytes) -> None:
        """Append a file payload to the shard."""

        self._write_header()
        if self._closed:
            raise ValueError("ShardWriter is closed.")
        if uid in self._entries:
            raise ValueError(f"UID {uid!r} already exists in shard.")
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data must be bytes")

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
        self._entries[uid] = ShardFileEntry(
            uid=uid,
            offset=offset,
            length=len(compressed),
            codec=codec,
            compressed_size=len(compressed),
            uncompressed_size=len(data),
        )

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
            index_buffer.write(struct.pack("<QQ", entry.offset, entry.length))
            index_buffer.write(struct.pack("<B", _codec_to_byte(entry.codec)))
            index_buffer.write(struct.pack("<QQ", entry.compressed_size, entry.uncompressed_size))

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
    """Reader for DES v2 shard files."""

    def __init__(self, fp: BinaryIO, owns_stream: bool = False):
        self._fp = fp
        self._owns_stream = owns_stream
        self.index = self._load_index()

    @classmethod
    def from_path(cls, path: Path | str) -> "ShardReader":
        fp = open(path, "rb")
        return cls._from_stream(fp, owns_stream=True)

    @classmethod
    def from_bytes(cls, data: bytes) -> "ShardReader":
        """Create a reader from in-memory shard bytes."""

        stream = io.BytesIO(data)
        return cls._from_stream(stream, owns_stream=True)

    @classmethod
    def _from_stream(cls, stream: BinaryIO, owns_stream: bool) -> "ShardReader":
        return cls(stream, owns_stream=owns_stream)

    def __enter__(self) -> "ShardReader":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
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

        self._fp.seek(entry.offset)
        data = self._fp.read(entry.length)
        if len(data) != entry.length:
            raise ValueError(f"Unexpected end of file while reading UID {uid!r}")

        if entry.codec == CompressionCodec.NONE:
            return data

        if entry.codec == CompressionCodec.ZSTD:
            import zstandard as zstd

            decompressor = zstd.ZstdDecompressor()
            result = decompressor.decompress(data, max_output_size=entry.uncompressed_size)
        elif entry.codec == CompressionCodec.LZ4:
            import lz4.frame as lz4f

            result = lz4f.decompress(data)
        else:
            raise ValueError(f"Unsupported compression codec {entry.codec}")

        if entry.uncompressed_size is not None and len(result) != entry.uncompressed_size:
            raise ValueError("Decompressed size mismatch")
        return result

    def _load_index(self) -> ShardIndex:
        fp = self._fp
        fp.seek(0, io.SEEK_END)
        file_size = fp.tell()

        if file_size < HEADER_SIZE + FOOTER_SIZE:
            raise ValueError("File too small to be a valid DES shard.")

        fp.seek(0)
        header = fp.read(HEADER_SIZE)
        magic, version, reserved = header[:4], header[4], header[5:]
        if magic != HEADER_MAGIC:
            raise ValueError("Invalid shard header magic.")
        if version != VERSION:
            raise ValueError(f"Unsupported shard version: {version}")
        if len(reserved) != 3:
            raise ValueError("Invalid header size.")

        fp.seek(file_size - FOOTER_SIZE)
        footer_magic = fp.read(4)
        if footer_magic != FOOTER_MAGIC:
            raise ValueError("Invalid shard footer magic.")
        index_size_bytes = fp.read(8)
        if len(index_size_bytes) != 8:
            raise ValueError("Truncated footer.")
        (index_size,) = struct.unpack("<Q", index_size_bytes)

        index_start = file_size - FOOTER_SIZE - index_size
        if index_start < HEADER_SIZE or index_start > file_size - FOOTER_SIZE:
            raise ValueError("Invalid index size or position.")

        fp.seek(index_start)
        index_data = fp.read(index_size)
        if len(index_data) != index_size:
            raise ValueError("Failed to read full index section.")

        entries = self._parse_index(index_data, data_section_end=index_start)
        return ShardIndex(entries=entries)

    def _parse_index(self, data: bytes, data_section_end: int) -> Dict[str, ShardFileEntry]:
        if len(data) < 4:
            raise ValueError("Index too small to contain entry count.")

        entry_count = struct.unpack_from("<I", data, 0)[0]
        offset = 4
        entries: Dict[str, ShardFileEntry] = {}

        for _ in range(entry_count):
            if offset + 2 > len(data):
                raise ValueError("Truncated index while reading name length.")
            name_len = struct.unpack_from("<H", data, offset)[0]
            offset += 2

            if offset + name_len > len(data):
                raise ValueError("Truncated index while reading UID.")
            name_bytes = data[offset : offset + name_len]
            offset += name_len
            uid = name_bytes.decode("utf-8")

            if offset + 16 + 1 + 16 > len(data):
                raise ValueError("Truncated index while reading entry metadata.")
            file_offset, length = struct.unpack_from("<QQ", data, offset)
            offset += 16
            codec_byte = struct.unpack_from("<B", data, offset)[0]
            offset += 1
            compressed_size, uncompressed_size = struct.unpack_from("<QQ", data, offset)
            offset += 16

            if file_offset + length > data_section_end:
                raise ValueError("Indexed file extends beyond data section.")

            entries[uid] = ShardFileEntry(
                uid=uid,
                offset=file_offset,
                length=length,
                codec=_byte_to_codec(codec_byte),
                compressed_size=compressed_size,
                uncompressed_size=uncompressed_size,
            )

        return entries
