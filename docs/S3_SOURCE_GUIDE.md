# S3 Source Guide

This guide describes how to read migration sources directly from S3 (`s3://bucket/key`).

## Configuration

```yaml
packer:
  s3_source:
    enabled: true
    region_name: "us-east-1"   # optional
    endpoint_url: null         # optional (MinIO/LocalStack)
    max_retries: 3
    retry_delay_seconds: 2
```

- When `enabled` is false or omitted, DES reads only local filesystem paths.
- `endpoint_url` allows S3-compatible stores (MinIO, LocalStack, Ceph).
- Retries use exponential backoff with the configured delay.

## IAM / permissions

Minimal policy for read-only migration sources:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject"],
      "Resource": "arn:aws:s3:::source-bucket/*"
    }
  ]
}
```

## Failure modes

- Invalid URI: `s3://` prefix required and a non-empty bucket/key.
- 403 `AccessDenied`: credentials or IAM policy missing `s3:GetObject`.
- 404/NoSuchKey: object not found.
- Network/endpoint issues: retried up to `max_retries` with backoff; final failure raises a clear error.

## Metrics

- `des_s3_source_reads_total{status="success|error"}`
- `des_s3_source_bytes_downloaded`
- `des_s3_source_read_seconds{status="success|error"}`

## Performance tips

- Keep shards close to the S3 source bucket/endpoint to reduce latency.
- Prefer dedicated endpoints (VPC endpoints/MinIO local) for higher throughput.
- For very large batches, consider pre-warming DNS/connections or colocating the migrator near the bucket.
