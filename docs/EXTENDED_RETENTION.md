# Extended Retention Management

## Overview
Allows external systems to set custom retention policies for individual files.

## API Usage
```bash
curl -X PUT https://des.example.com/files/uid-123/retention-policy \
  -H "Content-Type: application/json" \
  -d '{
    "created_at": "2024-12-15T10:00:00Z",
    "due_date": "2027-12-15T00:00:00Z"
  }'
```

## How It Works
1. First call: File copied from shard to `_ext_retention/`
2. Subsequent calls: Only S3 retention date updated
3. Retrieval: Transparently checks `_ext_retention/` first

## Cost Optimization
Only 1% of files need extended retention → 99% cost savings vs keeping entire shard

## Technical Requirements
- Python 3.12+
- boto3 for S3 operations
- FastAPI for HTTP API
- S3 bucket must have Object Lock enabled
- Use GOVERNANCE mode (not COMPLIANCE) for updateable retention

## Error Handling
- Handle S3 404 gracefully (file not found)
- Retry transient S3 errors (500, 503) with exponential backoff
- Validate due_date is in future
- Log all operations for audit trail

## Integration Points
- Retriever: Checks `_ext_retention/` before main shard
- Tombstone system: Reuse existing tombstone creation logic if available
- Metrics: Prometheus counters/gauges emitted from `des_core.metrics`
- Config: Read S3 bucket from environment/config

## Implementation Order
1. `ExtendedRetentionManager` core class
2. Update retrievers to read extended retention first
3. FastAPI endpoint
4. Unit tests
5. Integration tests
6. Documentation
