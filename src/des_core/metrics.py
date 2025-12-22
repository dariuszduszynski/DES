"""Prometheus metrics for DES retrievers and migration pipeline."""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, start_http_server

DES_RETRIEVALS_TOTAL = Counter(
    "des_retrievals_total",
    "Number of DES file retrievals",
    ["backend", "status"],
)

DES_RETRIEVAL_SECONDS = Histogram(
    "des_retrieval_seconds",
    "Time spent retrieving a file from DES",
    ["backend"],
)

DES_S3_RANGE_CALLS_TOTAL = Counter(
    "des_s3_range_calls_total",
    "Number of S3 range GETs performed",
    ["backend", "type"],
)

tombstones_created_total = Counter(
    "des_tombstones_created_total",
    "Number of tombstones created",
    ["reason"],
)

tombstone_checks_total = Counter(
    "des_tombstone_checks_total",
    "Number of tombstone checks during retrieval",
    ["result"],
)

metadata_cache_hits_total = Counter(
    "des_metadata_cache_hits_total",
    "Metadata cache hits",
)

metadata_cache_misses_total = Counter(
    "des_metadata_cache_misses_total",
    "Metadata cache misses",
)

metadata_rebuilds_total = Counter(
    "des_metadata_rebuilds_total",
    "Number of metadata files rebuilt from .des",
)

metadata_load_duration_seconds = Histogram(
    "des_metadata_load_duration_seconds",
    "Time to load metadata file",
)

des_auth_requests_total = Counter(
    "des_auth_requests_total",
    "Number of public key auth requests",
    ["result"],
)

checksum_verifications_total = Counter(
    "des_checksum_verifications_total",
    "Number of checksum verifications",
    ["status"],
)

checksum_computation_seconds = Histogram(
    "des_checksum_computation_seconds",
    "Time to compute checksums",
    ["operation"],
)

ext_retention_moves_total = Counter(
    "des_ext_retention_moves_total",
    "Total files moved to extended retention",
)

ext_retention_updates_total = Counter(
    "des_ext_retention_updates_total",
    "Total retention policy updates",
)

ext_retention_files = Gauge(
    "des_ext_retention_files_total",
    "Current number of files in extended retention",
)

idempotency_rejections_total = Counter(
    "des_idempotency_rejections_total",
    "Number of rejected requests due to idempotency check",
)

DES_MIGRATION_CYCLES_TOTAL = Counter(
    "des_migration_cycles_total",
    "Number of migration cycles executed",
    ["status"],
)

DES_MIGRATION_FILES_TOTAL = Counter(
    "des_migration_files_total",
    "Total number of files processed by DES migration",
)

DES_MIGRATION_BYTES_TOTAL = Counter(
    "des_migration_bytes_total",
    "Total number of bytes migrated by DES",
)

DES_MIGRATION_FILES_FAILED = Counter(
    "des_migration_files_failed",
    "Number of files that failed during DES migration",
)

DES_MIGRATION_SHARDS_TOTAL = Counter(
    "des_migration_shards_total",
    "Total number of shards produced by DES migration",
)

DES_MIGRATION_DURATION_SECONDS = Histogram(
    "des_migration_duration_seconds",
    "Duration of migration cycles in seconds",
    buckets=[1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0],
)

DES_MIGRATION_PENDING_FILES = Gauge(
    "des_migration_pending_files",
    "Number of files pending migration (based on cutoff statistics)",
)

DES_MIGRATION_BATCH_SIZE = Gauge(
    "des_migration_batch_size",
    "Configured batch size for migration",
)

DES_S3_SOURCE_READS_TOTAL = Counter(
    "des_s3_source_reads_total",
    "Number of S3 source reads during migration",
    ["status"],
)

DES_S3_SOURCE_BYTES_DOWNLOADED = Counter(
    "des_s3_source_bytes_downloaded",
    "Total bytes downloaded from S3 as migration sources",
)

DES_S3_SOURCE_READ_SECONDS = Histogram(
    "des_s3_source_read_seconds",
    "Latency of S3 source reads during migration",
    ["status"],
)

__all__ = [
    "DES_RETRIEVALS_TOTAL",
    "DES_RETRIEVAL_SECONDS",
    "DES_S3_RANGE_CALLS_TOTAL",
    "tombstone_checks_total",
    "tombstones_created_total",
    "metadata_cache_hits_total",
    "metadata_cache_misses_total",
    "metadata_rebuilds_total",
    "metadata_load_duration_seconds",
    "des_auth_requests_total",
    "checksum_verifications_total",
    "checksum_computation_seconds",
    "DES_S3_SOURCE_READS_TOTAL",
    "DES_S3_SOURCE_BYTES_DOWNLOADED",
    "DES_S3_SOURCE_READ_SECONDS",
    "DES_MIGRATION_CYCLES_TOTAL",
    "DES_MIGRATION_FILES_TOTAL",
    "DES_MIGRATION_BYTES_TOTAL",
    "DES_MIGRATION_FILES_FAILED",
    "DES_MIGRATION_SHARDS_TOTAL",
    "DES_MIGRATION_DURATION_SECONDS",
    "DES_MIGRATION_PENDING_FILES",
    "DES_MIGRATION_BATCH_SIZE",
    "start_http_server",
    "CONTENT_TYPE_LATEST",
    "ext_retention_moves_total",
    "ext_retention_updates_total",
    "ext_retention_files",
    "idempotency_rejections_total",
]
