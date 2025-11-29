# 🗺️ DES Roadmap

## ✅ Zrealizowane (v0.1.0 - v0.3.0)

### Core Architecture & Shard Format
- [x] Core routing helpers (deterministyczny shard lookup)
- [x] Planner (grupowanie plików do shardów z size limiting)
- [x] Shard I/O (format DES v2: header, data, index, footer)
- [x] Compression implementation – zstd/lz4 per-file w ShardWriter/Reader
- [x] Smart compression skipping (already-compressed extensions)
- [x] Compression metadata tracking (compressed_size, uncompressed_size, codec)

### Packing & Storage
- [x] Local filesystem packer
- [x] CLI tool (`des-pack`)
- [x] S3 Packer – upload shardów do S3 po lokalnym pakowaniu
- [x] Deterministic routing (no DB dependency)
- [x] Shard splitting by size limit

### Retrieval & Access
- [x] Local filesystem Retriever
- [x] S3-backed Retriever – odczyt plików z S3 shardów
- [x] Multi-zone S3 Retriever – routing do wielu stref S3 na podstawie shard index
- [x] S3 range-GET optimization (partial index/payload fetch)
- [x] Local cache dla indeksów shardów (LRU cache with configurable max_size)
- [x] HTTP Retriever – FastAPI service z trzema backendami (local/s3/multi_s3)
- [x] Zone configuration loader (YAML/JSON support)

### Observability & Metrics
- [x] Prometheus metrics – DES_RETRIEVALS_TOTAL, DES_RETRIEVAL_SECONDS, DES_S3_RANGE_CALLS_TOTAL
- [x] Migration metrics – cycles, files, bytes, duration, pending files, batch size
- [x] Metrics endpoint (`/metrics`) w HTTP retriever
- [x] Health check endpoint (`/health`)

### Deployment & Infrastructure
- [x] Docker support – Dockerfile + docker-compose.yml
- [x] Separate Dockerfile for packer (Dockerfile.packer)
- [x] Kubernetes manifests – Deployment + Service + Job + CronJob
- [x] Multi-environment K8s support (ConfigMap, Secret, PVC, RBAC)
- [x] Environment-based configuration (DES_BACKEND, DES_BASE_DIR, etc.)

### Database Integration & Migration
- [x] **Database connector (SourceDatabase)** – SQLAlchemy-based with connection pooling
- [x] **Archive configuration repository** – tiny DES-owned table for cutoff tracking
- [x] **Database source provider** – keyset pagination for large tables
- [x] **Migration orchestrator** – fetch → validate → pack → mark → cleanup flow
- [x] **CLI migration tool (`des-migrate`)** – single run, dry-run, continuous modes
- [x] **Archive statistics** – dry-run preview of migration scope
- [x] **Retry logic** – exponential backoff for transient DB errors
- [x] **Batch processing** – configurable batch sizes for migration
- [x] **File validation** – existence and size verification before packing
- [x] **Archive marker advancement** – daily cutoff updates with lag_days
- [x] **Source file cleanup** – optional deletion after successful migration
- [x] **Error isolation** – per-file packing to isolate failures
- [x] **PostgreSQL integration tests** – via testcontainers
- [x] **Environment variable substitution** – in migration config files
- [x] **YAML/JSON config support** – for migration orchestration

### Testing & Quality
- [x] Comprehensive tests (80%+ coverage)
- [x] Integration tests (DB, S3, migration end-to-end)
- [x] CI/CD pipeline (GitHub Actions: pytest, mypy, ruff)
- [x] Test fixtures and mocking strategies
- [x] Performance test framework (marked as skip by default)

### Documentation
- [x] Dokumentacja kompresji per-file
- [x] Architecture documentation (ARCHITECTURE.md)
- [x] Deployment guide (DEPLOYMENT.md)
- [x] Migration guide (MIGRATION.md)
- [x] Comprehensive README with examples
- [x] Example configurations (migration-config.json/yaml, zones.yaml)
- [x] Grafana dashboard example (migration metrics)
- [x] Prometheus alerts examples

---

## 🔥 Priority 0 - Production Blockers (v0.4.0)

### Deletion & Repack System
- [ ] **Tombstone Management**
  - [ ] TombstoneSet data structure + S3 storage (`tombstones/YYYYMMDD/HH.json`)
  - [ ] Tombstone API endpoint: `DELETE /files/{uid}?created_at=...`
  - [ ] Tombstone listing i aggregation
  - [ ] Tombstone cleanup after repack grace period
- [ ] **Shard Verification Engine**
  - [ ] Integrity checker (header/footer/index validation)
  - [ ] Decompression test dla wszystkich entries
  - [ ] Corruption detection i reporting
  - [ ] Verification metrics (`des_shard_corruption_rate`)
- [ ] **Repack Engine Core**
  - [ ] ShardRepacker – rebuild shard bez deleted/corrupted files
  - [ ] Compression upgrade podczas repack (optional)
  - [ ] Atomic version swap (old → new shard)
  - [ ] Grace period cleanup (delayed deletion old shards)
  - [ ] Versioned shards: `YYYYMMDD_HH_NNNN_R001.des` (R = repack iteration)
- [ ] **Repack Orchestrator**
  - [ ] Scan for repack candidates (deletion ratio > threshold)
  - [ ] Scheduled job executor (cron/K8s CronJob)
  - [ ] Repack metrics (`des_repack_jobs_total`, `des_repack_space_saved_bytes`)
- [ ] **CLI Tools**
  - [ ] `des-repack` – manual repack trigger
  - [ ] `des-verify` – shard integrity verification
  - [ ] `des-tombstones` – tombstone management

### Resilience & Error Handling
- [ ] **Retry Logic Enhancement**
  - [ ] Exponential backoff dla S3 operations (currently only for DB)
  - [ ] Configurable retry policy (max_retries, base_delay, max_delay)
  - [ ] Retry metrics (`des_s3_retries_total`)
- [ ] **Circuit Breaker**
  - [ ] Multi-zone failover w MultiS3ShardRetriever
  - [ ] Health tracking per zone
  - [ ] Automatic zone switching on failure
  - [ ] Circuit breaker metrics (`des_circuit_breaker_state`)
  - [ ] Per-zone health tracking – independent failure detection
  - [ ] Recovery timeout – exponential backoff before retry
  - [ ] Fallback order – primary → secondary → tertiary zones
- [ ] **Rate Limiting**
  - [ ] S3 request throttling protection
  - [ ] Token bucket algorithm implementation
  - [ ] Per-zone rate limits
  - [ ] Backpressure handling

### Cache Improvements
- [ ] **Byte-based Cache Limits**
  - [ ] Track actual memory usage (nie tylko entry count)
  - [ ] Max cache size w bytes (np. 1GB RAM limit)
  - [ ] Memory pressure eviction
  - [ ] Size estimation – index size + overhead calculation
- [ ] **Cache Metrics Enhancement**
  - [ ] `des_cache_memory_bytes` – actual RAM usage
  - [ ] `des_cache_hit_total` / `des_cache_miss_total`
  - [ ] `des_cache_eviction_total` (by reason: size/ttl/pressure)
- [ ] **TTL Support**
  - [ ] Configurable TTL per cache entry
  - [ ] Automatic expiration cleanup

---

## 📋 Priority 1 - Operational Excellence (v0.5.0)

### Enhanced Monitoring & Alerting
- [ ] **SLI/SLO Framework**
  - [ ] Define SLIs: availability, latency, error rate
  - [ ] Define SLOs: 99.9% availability, p99 < 100ms
  - [ ] SLO tracking dashboard (Grafana templates)
- [ ] **Enhanced Metrics**
  - [ ] Error breakdown by type (`des_retrieval_errors_total{error_type}`)
  - [ ] Compression ratio histograms (`des_compression_ratio`)
  - [ ] Storage savings counter (`des_bytes_saved_total`)
  - [ ] Shard size distribution (`des_shard_size_bytes`, `des_shard_files_total`)
  - [ ] Packer lag tracking (`des_packer_lag_seconds`)
- [ ] **Alert Rules**
  - [ ] High error rate (>1% errors/min)
  - [ ] High latency (p99 > SLO)
  - [ ] Cache thrashing detection
  - [ ] S3 throttling alerts
  - [ ] Repack backlog alerts
  - [ ] Migration backlog alerts

### Distributed Packer
- [ ] **Coordination Layer**
  - [ ] Distributed locking (Redis/etcd/DynamoDB)
  - [ ] Work queue implementation (SQS/RabbitMQ/Kafka)
  - [ ] Leader election dla orchestrator
- [ ] **Checkpoint & Resume**
  - [ ] Checkpoint state during packing (każde N plików)
  - [ ] Resume capability po crash
  - [ ] Orphaned work detection i cleanup
- [ ] **Progress Tracking**
  - [ ] Real-time progress reporting
  - [ ] ETA estimation
  - [ ] Packer fleet metrics (`des_packer_active_workers`)

### Advanced Kubernetes
- [ ] **Helm Charts**
  - [ ] Parametryzowalne values.yaml
  - [ ] Multi-environment support (dev/staging/prod)
  - [ ] Dependency management
  - [ ] Chart versioning and releases
- [ ] **Auto-scaling**
  - [ ] HorizontalPodAutoscaler based on CPU/memory
  - [ ] Custom metrics autoscaling (queue depth, request rate)
  - [ ] Vertical Pod Autoscaler (VPA) configuration
- [ ] **Storage & Secrets**
  - [ ] PersistentVolumeClaim templates (currently using emptyDir/manual)
  - [ ] ConfigMap/Secret management dla multi-zone configs
  - [ ] External Secrets Operator integration
  - [ ] Storage class selection per environment
- [ ] **Ingress & Networking**
  - [ ] Ingress configuration (nginx/traefik)
  - [ ] TLS/SSL certificates (cert-manager)
  - [ ] Network policies
  - [ ] Service mesh integration (optional)
- [ ] **Observability**
  - [ ] OpenTelemetry instrumentation
  - [ ] Distributed tracing (Jaeger/Tempo)
  - [ ] Structured logging (JSON format)
  - [ ] Log aggregation (ELK/Loki)

---

## 🚀 Priority 2 - Advanced Features (v0.6.0+)

### Performance Optimizations
- [ ] **Adaptive Compression**
  - [ ] Auto-tuning compression level based on throughput
  - [ ] Content-type detection dla optimal codec selection
  - [ ] A/B testing framework dla compression strategies
  - [ ] Machine learning-based codec prediction
- [ ] **Index Compression**
  - [ ] Compress shard index itself (zstd)
  - [ ] Lazy index loading (decompress on-demand)
  - [ ] Index caching strategy enhancement
- [ ] **Batch Operations**
  - [ ] Batch retrieval API (`GET /files/batch`)
  - [ ] Parallel S3 fetches dla multiple files
  - [ ] Response streaming dla large batches
  - [ ] Connection pooling optimization

### Data Management
- [ ] **Shard Defragmentation**
  - [ ] Automatic defrag gdy fragmentation > threshold
  - [ ] Merge small shards (consolidation)
  - [ ] Split large shards (load balancing)
- [ ] **Lifecycle Management**
  - [ ] Tiering rules (hot → warm → cold → glacier)
  - [ ] Retention policies (auto-delete after N days)
  - [ ] Archive/restore workflows
  - [ ] S3 lifecycle integration
- [ ] **Data Migration Tools**
  - [ ] Import from other systems (tar, zip, custom formats)
  - [ ] Export to standard formats
  - [ ] Cross-region replication
  - [ ] Multi-cloud support (AWS, GCP, Azure)

### API Enhancements
- [ ] **Authentication & Authorization**
  - [ ] API key authentication
  - [ ] OAuth2/OIDC integration
  - [ ] Role-based access control (RBAC)
  - [ ] Per-file access policies
- [ ] **GraphQL API**
  - [ ] Schema definition
  - [ ] Query/mutation resolvers
  - [ ] Subscription support (real-time updates)
- [ ] **Advanced Queries**
  - [ ] Metadata filtering (size, date, tags)
  - [ ] Full-text search integration (Elasticsearch)
  - [ ] Aggregations (count, sum by criteria)
  - [ ] Query optimization and caching
- [ ] **Webhooks**
  - [ ] Event notifications (file packed, deleted, restored)
  - [ ] Custom webhook endpoints
  - [ ] Retry logic dla webhook delivery
  - [ ] Webhook signature verification

### Developer Experience
- [ ] **SDKs & Clients**
  - [ ] Python SDK (async/sync)
  - [ ] Node.js/TypeScript client
  - [ ] Go client library
  - [ ] Java/Kotlin client
  - [ ] CLI enhancements (interactive mode)
- [ ] **Documentation**
  - [ ] Contributing guidelines (CONTRIBUTING.md)
  - [ ] Architecture decision records (ADRs)
  - [ ] API reference (OpenAPI/Swagger)
  - [ ] Performance tuning guide
  - [ ] Troubleshooting guide
  - [ ] Migration best practices
- [ ] **Testing & Benchmarking**
  - [ ] Load testing suite (k6/Locust)
  - [ ] Performance benchmarking framework
  - [ ] Chaos engineering tests (fault injection)
  - [ ] Regression test suite
- [ ] **Examples & Templates**
  - [ ] Reference implementations
  - [ ] Integration examples (S3 lifecycle, Lambda triggers)
  - [ ] Terraform/Pulumi modules
  - [ ] CloudFormation templates

---

## 🔬 Research & Experimentation (Future)

### Advanced Storage Techniques
- [ ] Content-addressable storage (deduplication)
- [ ] Erasure coding dla ultra-reliable storage
- [ ] GPU-accelerated compression (NVIDIA nvCOMP)
- [ ] Smart prefetching (ML-based access patterns)
- [ ] Delta encoding for similar files

### Scalability & Distribution
- [ ] Multi-region active-active setup
- [ ] Geo-replication with conflict resolution
- [ ] Edge caching integration (CloudFront, Cloudflare)
- [ ] P2P distribution for reads

### Compliance & Security
- [ ] WORM (Write Once Read Many) compliance mode
- [ ] Encryption at rest (client-side, server-side)
- [ ] Encryption in transit (TLS, mutual TLS)
- [ ] Audit logging (immutable audit trail)
- [ ] GDPR compliance tooling (right to deletion, data portability)
- [ ] SOC2/ISO compliance documentation

### Advanced Analytics
- [ ] Usage analytics dashboard
- [ ] Cost optimization recommendations
- [ ] Capacity planning tools
- [ ] Access pattern analysis
- [ ] Anomaly detection

---

## 📊 Version Planning Summary

| Version | Focus | Status | Key Deliverables |
|---------|-------|--------|------------------|
| v0.1.0 | Core features | ✅ Complete | Routing, shard I/O, local packer |
| v0.2.0 | S3 + HTTP API | ✅ Complete | S3 retriever, multi-zone, HTTP service |
| v0.3.0 | Database integration | ✅ Complete | Migration orchestrator, DB connector, CLI tools |
| v0.4.0 | Production blockers | 🚧 Next | Repack system, circuit breaker, enhanced cache |
| v0.5.0 | Operational excellence | 📋 Planned | Monitoring, distributed packer, Helm charts |
| v0.6.0+ | Advanced features | 🔮 Future | Performance, API enhancements, SDKs |

---

## 🎯 Current Sprint Focus (v0.4.0)

### Week 1-2: Repack System Foundation
1. ✅ **COMPLETED**: Core infrastructure (compression, routing, shard I/O)
2. Tombstone management + storage
3. Shard verification engine
4. Basic repack engine (single shard)

### Week 3-4: Repack Orchestration
1. Repack orchestrator (scheduled jobs)
2. CLI tools (des-repack, des-verify, des-tombstones)
3. Metrics + monitoring
4. K8s CronJob for periodic repack

### Week 5-6: Resilience
1. S3 retry logic (exponential backoff)
2. Circuit breaker (multi-zone failover)
3. Rate limiting (S3 throttling protection)
4. Enhanced error handling

### Week 7-8: Cache & Polish
1. Byte-based cache limits
2. TTL support
3. Memory monitoring metrics
4. Integration tests + documentation
5. Release v0.4.0

---

## 💡 Implementation Notes

### Completed Implementation Highlights

#### Database Integration (v0.3.0)
- **SourceDatabase**: Production-ready SQLAlchemy connector with:
  - Connection pooling (configurable pool_size, max_overflow)
  - Pre-ping for connection health
  - Exponential backoff retry for transient errors
  - Support for PostgreSQL, SQLite
  - Optional size_bytes column
- **ArchiveConfigRepository**: Lightweight config table approach
  - Single-row singleton pattern
  - Avoids large table scans
  - Tracks archived_until cutoff with lag_days
  - Floor-to-midnight timestamp normalization
- **DatabaseSourceProvider**: Keyset pagination for scalability
  - Cursor-based pagination (no OFFSET)
  - Optional shard filtering (hash-based)
  - Configurable page_size
  - Works with multi-TB tables
- **MigrationOrchestrator**: Production-ready orchestration
  - Per-file error isolation
  - File validation (existence, size)
  - Optional source cleanup
  - Comprehensive metrics
  - Graceful degradation

#### HTTP Retriever Features
- Three backend modes: local, s3, multi_s3
- Environment-based configuration
- FastAPI with async support
- Prometheus metrics integration
- Health check endpoint
- Proper error handling (404, 400, 500)

#### Metrics Implementation
- Counter: retrievals, cycles, files, bytes
- Histogram: duration with bucketing
- Gauge: pending files, batch size
- Labels: backend, status, error_type
- Prometheus-compatible export

### Repack System Design Decisions
- **Immutable storage**: S3 objects are never modified, only replaced
- **Eventual deletion**: GDPR compliance within 48h (tombstone → repack → cleanup)
- **Verification on repack**: Integrity check before and after repacking
- **Compression upgrade**: Optional recompression during repack for space savings
- **Versioned shards**: `YYYYMMDD_HH_NNNN_R001.des` (R = repack iteration)

### Circuit Breaker Strategy
- **Per-zone health tracking**: Independent failure detection
- **Automatic failover**: Try alternate zones on failure
- **Recovery timeout**: Exponential backoff before retry
- **Fallback order**: Primary → Secondary → Tertiary zones

### Cache Memory Management
- **Byte-based limits**: Track actual memory usage, not just entry count
- **Eviction policy**: LRU + TTL + memory pressure
- **Size estimation**: Index size + overhead calculation
- **Memory monitoring**: Prometheus metrics for cache RAM usage

### Migration Best Practices (Learned)
1. **Always use keyset pagination** for large tables (>1M rows)
2. **Isolate failures** per file, not per batch
3. **Track cutoff separately** from source table
4. **Validate before packing** (existence, size match)
5. **Use metrics** for observability
6. **Support dry-run** for planning
7. **Batch size tuning**: 100-1000 files optimal for most workloads
8. **Connection pooling**: Essential for PostgreSQL performance

---

## 🔄 Migration from v0.2.0 to v0.3.0

Projects upgrading from v0.2.0 should note:

### New Dependencies
- `sqlalchemy>=2.0.0`
- `psycopg[binary]>=3.1.0` (for PostgreSQL)
- `pyyaml>=6.0.0` (for YAML configs)

### New CLI Tools
- `des-stats` – dry-run statistics
- `des-migrate` – migration orchestrator

### New Metrics
- `des_migration_cycles_total{status}`
- `des_migration_files_total`
- `des_migration_bytes_total`
- `des_migration_duration_seconds`
- `des_migration_pending_files`
- `des_migration_batch_size`

### Configuration Changes
- Migration config files support YAML and JSON
- Environment variable substitution in configs
- New K8s manifests for migration CronJob

---

## 📝 Changelog Summary

### v0.3.0 (Latest) - Database Integration & Migration
**Added:**
- Database connector with SQLAlchemy and connection pooling
- Archive configuration repository for cutoff tracking
- Database source provider with keyset pagination
- Migration orchestrator with comprehensive error handling
- CLI migration tool with dry-run and continuous modes
- Migration metrics and Prometheus integration
- PostgreSQL integration tests via testcontainers
- Environment variable substitution in configs
- YAML/JSON config file support
- Grafana dashboard and alert examples

**Improved:**
- Retry logic for database operations
- Error isolation in batch processing
- Test coverage to 80%+
- Documentation (MIGRATION.md added)

### v0.2.0 - S3 & Multi-Zone Support
**Added:**
- S3-backed retriever with range-GET optimization
- Multi-zone S3 retriever
- Zone configuration loader
- S3 packer
- In-memory LRU cache for shard indices
- HTTP retriever with multiple backends
- Prometheus metrics
- Docker and Kubernetes support

### v0.1.0 - Core Foundation
**Added:**
- Deterministic routing
- Shard I/O (DES v2 format)
- Compression (zstd, lz4)
- Local packer
- Planner
- CLI tools (des-pack)
- Comprehensive test suite

---

## 🤝 Contributing

This roadmap is a living document. Priorities may shift based on:
- Production feedback
- Performance metrics
- User requirements
- Security considerations
- Infrastructure evolution

For feature requests or roadmap discussions, please open an issue on GitHub.

---

**Last Updated**: Based on code analysis of v0.3.0 implementation
**Next Review**: Before v0.4.0 release