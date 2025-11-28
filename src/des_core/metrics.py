"""Prometheus metrics for DES retrievers."""

from __future__ import annotations

from prometheus_client import Counter, Histogram

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
