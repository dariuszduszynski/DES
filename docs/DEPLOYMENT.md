# Deployment overview

## Images
- **des-http**: HTTP retriever service (uvicorn serving `des_core.http_retriever:app`). Built from `Dockerfile`.
- **des-packer**: Batch packer (`des-pack` entrypoint). Built from `Dockerfile.packer`.

## Deployment patterns
### Local filesystem backend
- Use `DES_BACKEND=local` and mount shards at `/data/des`.
- Suitable for development or small installations.

### Single S3 backend
- `DES_BACKEND=s3`, supply `DES_S3_BUCKET` (and optionally region/endpoint/prefix).
- HTTP retriever + S3 retriever; shards typically produced via `des-pack` then uploaded to S3 (or via `pack_files_to_s3`).

### Multi-zone S3
- `DES_BACKEND=multi_s3` with `DES_ZONES_CONFIG` (YAML/JSON) describing zones and shard index ranges.
- Recommended for scaling across buckets/regions. Ensure non-overlapping ranges within `[0, 2**n_bits - 1]`.

## Kubernetes hints
- **HTTP retriever**: see `k8s/des-http-deployment.yaml` and `k8s/des-http-service.yaml`. Front with Ingress/Gateway in production. Replace images with registry tags, and use resource requests/limits as appropriate.
- **Packer jobs**: `k8s/des-packer-job.yaml` for ad-hoc runs; `k8s/des-packer-cronjob.yaml` for scheduled runs. Mount PVCs for input/manifests/output. Override `command`/args as needed.
- Health: `/health` and `/metrics` are lightweight probes.

## Volumes and storage
- Local backend: mount durable storage at `/data/des`.
- Packer: mount input, manifests, and output. Output PVC can be synced to S3 by external jobs (or switch to `pack_files_to_s3` in future).

## Configuration
Environment variables (HTTP):
- Local: `DES_BACKEND=local`, `DES_BASE_DIR`, `DES_N_BITS`.
- S3: `DES_S3_BUCKET`, optional `DES_S3_REGION`, `DES_S3_ENDPOINT_URL`, `DES_S3_PREFIX`, `DES_N_BITS`.
- Multi-S3: `DES_ZONES_CONFIG`, optional `DES_N_BITS` (zones config provides n_bits).

## Metrics and scaling
- Prometheus scrape `/metrics`.
- Scale HTTP retriever replicas based on traffic; cache reduces S3 range calls.
- Packer jobs are typically bursty; use Jobs/CronJobs with appropriate resource requests.
