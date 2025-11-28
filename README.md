# Data Easy Store (DES)

Pack huge numbers of small files into larger, S3-optimized shard objects. DES gives deterministic routing (no DB), pluggable compression, and fast retrieval using S3 range-GET (footer → index → payload). It ships with local/S3/multi-S3 backends, a packer CLI, and HTTP retriever service.

## Key features
- Append-only shard format with header/data/index/footer.
- Compression (`zstd`, `lz4`) with smart skipping of already-compressed extensions.
- Optimized S3 retriever: three range-GETs only (footer, index, payload).
- Multi-zone S3 routing (`MultiS3ShardRetriever`) based on shard index ranges.
- HTTP retriever with backends: `local`, `s3`, `multi_s3`; Prometheus metrics at `/metrics`.
- In-memory index cache to reduce repeated S3 calls.
- Packer CLI (`des-pack`), Docker images, docker-compose, and K8s job manifests.

## High-level architecture
- Routing: `des_core.routing.locate_shard` maps `(uid, created_at, n_bits)` to `date_dir`, `shard_index`, `shard_hex`, `object_key`.
- Shard IO: `des_core.shard_io` writes/reads `.des` shards, stores an index+footer, handles compression metadata, decompresses transparently.
- Packing: `des_core.packer` (local) and `des_core.s3_packer` (upload) build shards from manifests.
- Retrieval: `des_core.retriever` (filesystem), `des_core.s3_retriever` (single S3), `des_core.multi_s3_retriever` (zone fan-out).
- HTTP: `des_core.http_retriever` exposes retrieval over FastAPI.

Read path (S3 backend): locate shard → range-GET footer → range-GET index → payload range → decompress if needed → return bytes.

## Installation (source)
```bash
python -m venv .venv
source .venv/bin/activate  # on Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements-dev.txt  # or: pip install .
pytest
```

## Run HTTP retriever (local backend)
From source:
```bash
export DES_BACKEND=local
export DES_BASE_DIR=./example_data/des
export DES_N_BITS=8

uvicorn des_core.http_retriever:app --host 0.0.0.0 --port 8000
```

Via Docker:
```bash
docker build -t des-http:local .
docker run --rm -p 8000:8000 \
  -e DES_BACKEND=local \
  -e DES_BASE_DIR=/data/des \
  -v "$(pwd)"/example_data/des:/data/des:ro \
  des-http:local
```

Key endpoints:
- `GET /files/{uid}?created_at=YYYY-MM-DDTHH:MM:SSZ` → file bytes
- `GET /metrics` → Prometheus metrics
- `GET /health` → simple health check

## Backend configuration
### Local (`DES_BACKEND=local`)
- `DES_BASE_DIR` – directory with `.des` shards.
- `DES_N_BITS` – shard routing bits (default 8).

### Single S3 (`DES_BACKEND=s3`)
- `DES_S3_BUCKET` (required)
- `DES_S3_REGION` (optional)
- `DES_S3_ENDPOINT_URL` (optional, for S3-compatible storage)
- `DES_S3_PREFIX` (optional prefix)
- `DES_N_BITS` must match how shards were written.

### Multi-S3 (`DES_BACKEND=multi_s3`)
- `DES_ZONES_CONFIG` – YAML/JSON zones file (required).

Example zones config:
```yaml
n_bits: 8
zones:
  - name: zone-a
    range: { start: 0, end: 127 }
    s3:
      bucket: des-zone-a
      region_name: us-east-1
      endpoint_url: https://s3.example.com
      prefix: des-a/
  - name: zone-b
    range: { start: 128, end: 255 }
    s3:
      bucket: des-zone-b
      region_name: us-east-1
      endpoint_url: https://s3.example.com
      prefix: des-b/
```
Ranges must not overlap and must be within `[0, 2**n_bits - 1]`.

## Packer CLI (`des-pack`)
`des-pack` reads a JSON manifest and writes `.des` shards to an output directory.

Manifest example (list of files):
```json
[
  {
    "uid": "uid-123",
    "created_at": "2024-01-01T00:00:00Z",
    "size_bytes": 1024,
    "source_path": "/data/input/file-123.bin"
  },
  {
    "uid": "uid-456",
    "created_at": "2024-01-02T00:00:00Z",
    "size_bytes": 2048,
    "source_path": "/data/input/file-456.bin"
  }
]
```

Run locally:
```bash
des-pack \
  --input-json ./example_data/manifests/to-pack.json \
  --output-dir ./example_data/output \
  --max-shard-size 1073741824 \
  --n-bits 8
```

## Docker & docker-compose
docker-compose provides two services:
- HTTP retriever:
  ```bash
  docker compose up des-http
  ```
- Packer (one-shot job):
  ```bash
  docker compose run --rm des-packer
  ```

Mounts:
- `./example_data/input` → `/data/input`
- `./example_data/manifests` → `/data/manifests`
- `./example_data/output` → `/data/output`

## Kubernetes overview
Manifests under `k8s/`:
- `des-http-deployment.yaml` + `des-http-service.yaml` – HTTP retriever.
- `des-packer-job.yaml` – one-off packer job.
- `des-packer-cronjob.yaml` – periodic packer job.

Apply:
```bash
kubectl apply -f k8s/des-http-deployment.yaml
kubectl apply -f k8s/des-http-service.yaml
kubectl apply -f k8s/des-packer-job.yaml  # or des-packer-cronjob.yaml
```

Replace images with your registry (`image: ghcr.io/your-org/des-http:tag`) and use PVCs instead of emptyDir for real data.

## Metrics
Prometheus metrics exposed at `/metrics`:
- `des_retrievals_total{backend,status}`
- `des_retrieval_seconds{backend}`
- `des_s3_range_calls_total{backend,type}`

Example scrape config:
```yaml
scrape_configs:
  - job_name: des-http
    static_configs:
      - targets: ["des-http.default.svc.cluster.local:8000"]
```

## Development & tests
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

Run linters (if desired):
```bash
ruff check src tests
mypy src
```

## Limitations & roadmap
- No built-in auth for HTTP retriever.
- Multi-S3 routing is read-side only; write replication is manual.
- Packer consumes manifest files; upstream DB integration is not included yet.

Further reading: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md), [ROADMAP.md](ROADMAP.md)
