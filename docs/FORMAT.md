# DES shard format

## Sections
- **Header (8 bytes)**: `magic=b"DES2"`, `version` byte, 3 reserved bytes.
- **Data**: concatenated payloads for inline files (BigFiles skip this section).
- **Index**: see per-version layouts below.
- **Footer (12 bytes)**: `magic=b"DESI"`, `index_size` (uint64). Index starts at `file_size - FOOTER_SIZE - index_size`.

## Versions
`version` in the header selects the index schema. Readers accept v1 and v2; writers emit v2.

### v1 (legacy, inline-only)
- entry_count (uint32)
- For each entry:
  - name_len (uint16) + UTF-8 UID
  - offset (uint64, absolute from file start)
  - length (uint64, compressed)
  - codec (uint8) where 0=none, 1=zstd, 2=lz4
  - compressed_size (uint64)
  - uncompressed_size (uint64)

### v2 (BigFiles-aware)
- entry_count (uint32)
- For each entry:
  - name_len (uint16) + UTF-8 UID
  - flags (uint8) bit0 = `is_bigfile`
  - If `is_bigfile`:
    - hash_len (uint16) + UTF-8 `bigfile_hash` (sha256 hex)
    - bigfile_size (uint64)
    - meta_len (uint32) + UTF-8 JSON metadata (may be empty)
  - Else (inline payload):
    - offset (uint64) absolute from file start
    - length (uint64) compressed length
    - codec (uint8)
    - compressed_size (uint64)
    - uncompressed_size (uint64)
    - meta_len (uint32) + UTF-8 JSON metadata (may be empty)

## BigFiles layout
- Payloads larger than `big_file_threshold_bytes` are written to `_bigFiles/<sha256>` next to the shard (local FS or S3 prefix).
- The index stores only metadata; the data section omits the payload entirely.
- Readers detect `is_bigfile` and fetch content from the `_bigFiles/` path instead of issuing a payload range-GET.
