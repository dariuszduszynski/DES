# DES Architecture

## Shard format
Shards are append-only files with four sections:
- **HEADER**: magic/version/reserved.
- **DATA**: concatenated file payloads (may be compressed or omitted for BigFiles).
- **INDEX**: entry_count followed by per-file records (flags/offsets/codec/sizes/meta).
- **FOOTER**: magic + index_size (fixed-size trailer). Index starts at `file_size - FOOTER_SIZE - index_size`.

## Write path
- `des_core.packer_planner` groups files into shards based on routing (`n_bits`) and size limits.
- `des_core.packer`/`s3_packer` use `ShardWriter` to write payloads (with optional compression) and emit index/footer.
- `ShardWriter` stores BigFiles (payloads above `big_file_threshold_bytes`) under `_bigFiles/<hash>` next to the shard and records only metadata in the index.
- Compression configs in `des_core.compression` decide codec/level and skip already-compressed extensions.

## Read path
- Given `(uid, created_at)`, `locate_shard` computes `date_dir`, `shard_index`, `shard_hex`, `object_key`.
- Local retriever reads shard from filesystem via `ShardReader` and decompresses as needed.
- S3 retriever:
  - range-GET header/footer/index -> parse offsets/flags
  - range-GET payload slice -> decompress per entry (or fetch BigFile object when `is_bigfile` is set)
- In-memory index cache avoids repeated header/footer/index fetches; payload still uses range GET for inline files.

## Backends and routing
- **Local**: filesystem directory of `.des` shards.
- **S3**: single bucket/prefix; deterministic hashing on UID (`n_bits`).
- **Multi-S3**: `S3ZoneRange` partitions shard indices across zones; `MultiS3ShardRetriever` delegates to the zone owning the shard index.

## Observability
- Prometheus metrics in `des_core.metrics`:
  - `des_retrievals_total{backend,status}`
  - `des_retrieval_seconds{backend}`
  - `des_s3_range_calls_total{backend,type}`
- HTTP retriever exposes `/metrics`.

## Components map
- Routing: `des_core.routing`
- Shard IO: `des_core.shard_io`
- Compression: `des_core.compression`
- Packer: `des_core.packer`, `des_core.s3_packer`
- Retrievers: `des_core.retriever`, `des_core.s3_retriever`, `des_core.multi_s3_retriever`
- HTTP: `des_core.http_retriever`
- Cache: `des_core.cache`
