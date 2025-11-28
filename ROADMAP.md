# 🗺️ Roadmap

## ✅ Zrealizowane (v0.1.0 - v0.2.0)
- [x] Core routing helpers (deterministyczny shard lookup)
- [x] Planner (grupowanie plików do shardów z size limiting)
- [x] Shard I/O (format DES v2: header, data, index, footer)
- [x] Compression implementation – zstd/lz4 per-file w ShardWriter/Reader
- [x] Local filesystem packer
- [x] CLI tool (`des-pack`)
- [x] S3-backed Retriever – odczyt plików z S3 shardów
- [x] S3 Packer – upload shardów do S3 po lokalnym pakowaniu
- [x] Multi-zone S3 Retriever – routing do wielu stref S3 na podstawie shard index
- [x] S3 range-GET optimization dla partial index/payload fetch
- [x] Local cache dla indeksów shardów (LRU cache)
- [x] HTTP Retriever – FastAPI service z trzema backendami (local/s3/multi_s3)
- [x] Prometheus metrics – DES_RETRIEVALS_TOTAL, DES_RETRIEVAL_SECONDS, DES_S3_RANGE_CALLS_TOTAL
- [x] Docker support – Dockerfile + docker-compose.yml
- [x] Kubernetes manifests – Deployment + Service (basic)
- [x] Comprehensive tests (80%+ coverage)
- [x] Dokumentacja kompresji per-file

## 🔥 Priority 0 - Production Blockers (v0.3.0)

### Deletion & Repack System
- [ ] **Tombstone Management**
  - [ ] TombstoneSet data structure + S3 storage (`tombstones/YYYYMMDD/HH.json`)
  - [ ] Tombstone API endpoint: `DELETE /files/{uid}?created_at=...`
  - [ ] Tombstone listing i aggregation
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
- [ ] **Repack Orchestrator**
  - [ ] Scan for repack candidates (deletion ratio > threshold)
  - [ ] Scheduled job executor (cron/K8s CronJob)
  - [ ] Repack metrics (`des_repack_jobs_total`, `des_repack_space_saved_bytes`)
- [ ] **CLI Tools**
  - [ ] `des-repack` – manual repack trigger
  - [ ] `des-verify` – shard integrity verification
  - [ ] `des-tombstones` – tombstone management

### Resilience & Error Handling
- [ ] **Retry Logic**
  - [ ] Exponential backoff dla S3 operations
  - [ ] Configurable retry policy (max_retries, base_delay, max_delay)
  - [ ] Retry metrics (`des_s3_retries_total`)
- [ ] **Circuit Breaker**
  - [ ] Multi-zone failover w MultiS3ShardRetriever
  - [ ] Health tracking per zone
  - [ ] Automatic zone switching on failure
  - [ ] Circuit breaker metrics (`des_circuit_breaker_state`)
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
- [ ] **Cache Metrics Enhancement**
  - [ ] `des_cache_memory_bytes` – actual RAM usage
  - [ ] `des_cache_hit_total` / `des_cache_miss_total`
  - [ ] `des_cache_eviction_total` (by reason: size/ttl/pressure)
- [ ] **TTL Support**
  - [ ] Configurable TTL per cache entry
  - [ ] Automatic expiration cleanup

## 📋 Priority 1 - Operational Excellence (v0.4.0)

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
- [ ] **Auto-scaling**
  - [ ] HorizontalPodAutoscaler based on CPU/memory
  - [ ] Custom metrics autoscaling (queue depth, request rate)
  - [ ] Vertical Pod Autoscaler (VPA) configuration
- [ ] **Storage & Secrets**
  - [ ] PersistentVolumeClaim templates
  - [ ] ConfigMap/Secret management dla multi-zone configs
  - [ ] External Secrets Operator integration
- [ ] **Ingress & Networking**
  - [ ] Ingress configuration (nginx/traefik)
  - [ ] TLS/SSL certificates (cert-manager)
  - [ ] Network policies
- [ ] **Observability**
  - [ ] OpenTelemetry instrumentation
  - [ ] Distributed tracing (Jaeger/Tempo)
  - [ ] Structured logging (JSON format)

## 🚀 Priority 2 - Advanced Features (v0.5.0+)

### Performance Optimizations
- [ ] **Adaptive Compression**
  - [ ] Auto-tuning compression level based on throughput
  - [ ] Content-type detection dla optimal codec selection
  - [ ] A/B testing framework dla compression strategies
- [ ] **Index Compression**
  - [ ] Compress shard index itself (zstd)
  - [ ] Lazy index loading (decompress on-demand)
  - [ ] Index caching strategy
- [ ] **Batch Operations**
  - [ ] Batch retrieval API (`GET /files/batch`)
  - [ ] Parallel S3 fetches dla multiple files
  - [ ] Response streaming dla large batches

### Data Management
- [ ] **Shard Defragmentation**
  - [ ] Automatic defrag gdy fragmentation > threshold
  - [ ] Merge small shards (consolidation)
  - [ ] Split large shards (load balancing)
- [ ] **Lifecycle Management**
  - [ ] Tiering rules (hot → warm → cold → glacier)
  - [ ] Retention policies (auto-delete after N days)
  - [ ] Archive/restore workflows
- [ ] **Data Migration Tools**
  - [ ] Import from other systems (tar, zip, custom formats)
  - [ ] Export to standard formats
  - [ ] Cross-region replication

### API Enhancements
- [ ] **GraphQL API**
  - [ ] Schema definition
  - [ ] Query/mutation resolvers
  - [ ] Subscription support (real-time updates)
- [ ] **Advanced Queries**
  - [ ] Metadata filtering (size, date, tags)
  - [ ] Full-text search integration (Elasticsearch)
  - [ ] Aggregations (count, sum by criteria)
- [ ] **Webhooks**
  - [ ] Event notifications (file packed, deleted, restored)
  - [ ] Custom webhook endpoints
  - [ ] Retry logic dla webhook delivery

### Developer Experience
- [ ] **SDKs & Clients**
  - [ ] Python SDK (async/sync)
  - [ ] Node.js/TypeScript client
  - [ ] Go client library
  - [ ] Java/Kotlin client
- [ ] **Documentation**
  - [ ] Contributing guidelines (CONTRIBUTING.md)
  - [ ] Architecture decision records (ADRs)
  - [ ] API reference (OpenAPI/Swagger)
  - [ ] Performance tuning guide
- [ ] **Testing & Benchmarking**
  - [ ] Load testing suite (k6/Locust)
  - [ ] Performance benchmarking framework
  - [ ] Chaos engineering tests (fault injection)
- [ ] **Examples & Templates**
  - [ ] Reference implementations
  - [ ] Integration examples (S3 lifecycle, Lambda triggers)
  - [ ] Terraform/Pulumi modules

## 🔬 Research & Experimentation (Future)
- [ ] Content-addressable storage (deduplication)
- [ ] Erasure coding dla ultra-reliable storage
- [ ] GPU-accelerated compression (NVIDIA nvCOMP)
- [ ] Smart prefetching (ML-based access patterns)
- [ ] Multi-region active-active setup
- [ ] WORM (Write Once Read Many) compliance mode

---

## 📊 Version Planning Summary

| Version | Focus | Status |
|---------|-------|--------|
| v0.1.0-v0.2.0 | Core features + S3 + HTTP API | ✅ Complete |
| v0.3.0 | Production blockers (repack, retry, circuit breaker) | 🚧 In Progress |
| v0.4.0 | Operational excellence (monitoring, distributed packer) | 📋 Planned |
| v0.5.0+ | Advanced features (perf, API, DX) | 🔮 Future |

---

## 🎯 Current Sprint Focus (v0.3.0)

### Week 1-2: Repack System Foundation
1. Tombstone management + storage
2. Shard verification engine
3. Basic repack engine (single file)

### Week 3-4: Repack Orchestration
1. Repack orchestrator (scheduled jobs)
2. CLI tools (des-repack, des-verify)
3. Metrics + monitoring

### Week 5-6: Resilience
1. Retry logic (exponential backoff)
2. Circuit breaker (multi-zone failover)
3. Rate limiting (S3 throttling protection)

### Week 7-8: Cache & Polish
1. Byte-based cache limits
2. TTL support
3. Integration tests + documentation
4. Release v0.3.0

---

## 💡 Implementation Notes

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