# DES Demo - Quick Reference Card

## 🚀 Quick Start

```bash
# Start demo environment
chmod +x demo/start-demo.sh
./demo/start-demo.sh

# OR using Docker Compose directly
docker-compose -f docker-compose.demo.yml up -d

# OR using Makefile
make -f Makefile.demo demo-start
```

## 🌐 Access URLs

| Service | URL | Credentials |
|---------|-----|-------------|
| **Business System UI** | http://localhost:8080 | - |
| **DES API** | http://localhost:8000 | - |
| **MinIO Console** | http://localhost:9001 | minioadmin / minioadmin |
| **PostgreSQL** | localhost:5432 | business_user / business_pass |

## 📋 Common Commands

### Service Management
```bash
# Start all services
docker-compose -f docker-compose.demo.yml up -d

# Stop all services
docker-compose -f docker-compose.demo.yml stop

# Restart specific service
docker-compose -f docker-compose.demo.yml restart business-system

# View logs
docker-compose -f docker-compose.demo.yml logs -f

# View logs for specific service
docker-compose -f docker-compose.demo.yml logs -f business-system

# Check status
docker-compose -f docker-compose.demo.yml ps

# Clean up (DELETE ALL DATA)
docker-compose -f docker-compose.demo.yml down -v
```

### Health Checks
```bash
# Business System
curl http://localhost:8080/health

# DES API
curl http://localhost:8000/health

# MinIO
curl http://localhost:9000/minio/health/live

# PostgreSQL
docker exec des-postgres pg_isready -U business_user
```

## 🔧 API Examples

### Business System API

```bash
# List all files
curl http://localhost:8080/api/files | jq '.'

# List files with extended retention
curl "http://localhost:8080/api/files?status=extended" | jq '.'

# Upload file
curl -X POST http://localhost:8080/api/files/upload \
  -F "file=@yourfile.pdf" \
  -F "case_number=CASE-2024-001" \
  -F "department=Legal"

# Extend retention
curl -X POST http://localhost:8080/api/files/1/extend-retention \
  -F "retention_days=365" \
  -F "reason=Legal Hold" \
  -F "updated_by=admin"

# Get retention history
curl http://localhost:8080/api/files/1/retention-history | jq '.'
```

### DES API

```bash
# Set retention policy (first time)
curl -X PUT http://localhost:8000/files/file-uid-123/retention-policy \
  -H "Content-Type: application/json" \
  -d '{
    "created_at": "2024-12-15T10:00:00Z",
    "due_date": "2025-12-15T00:00:00Z"
  }'

# Update retention policy (subsequent calls)
curl -X PUT http://localhost:8000/files/file-uid-123/retention-policy \
  -H "Content-Type: application/json" \
  -d '{
    "created_at": "2024-12-15T10:00:00Z",
    "due_date": "2026-12-15T00:00:00Z"
  }'

# Retrieve file
curl http://localhost:8000/files/file-uid-123?created_at=2024-12-15T10:00:00Z
```

## 🗄️ Database Queries

```bash
# Connect to PostgreSQL
docker exec -it des-postgres psql -U business_user -d business_system

# List all files
SELECT * FROM files;

# List files with extended retention
SELECT * FROM files WHERE in_extended_retention = true;

# View retention history
SELECT * FROM retention_history ORDER BY updated_at DESC;

# Statistics
SELECT 
  COUNT(*) as total_files,
  COUNT(CASE WHEN in_extended_retention THEN 1 END) as extended_files
FROM files;

# Exit
\q
```

## 💾 MinIO / S3 Operations

```bash
# List buckets (using AWS CLI)
aws --endpoint-url http://localhost:9000 s3 ls

# List objects in bucket
aws --endpoint-url http://localhost:9000 s3 ls s3://des-bucket/ --recursive

# List extended retention files
aws --endpoint-url http://localhost:9000 s3 ls s3://des-bucket/_ext_retention/ --recursive

# Get object retention info
aws --endpoint-url http://localhost:9000 s3api get-object-retention \
  --bucket des-bucket \
  --key _ext_retention/20241215/file-uid_2024-12-15T10:00:00Z.dat
```

## 🧪 Test Scenarios

### Scenario 1: Basic File Upload & Extension
```bash
# 1. Upload file
curl -X POST http://localhost:8080/api/files/upload \
  -F "file=@test.pdf" -F "case_number=TEST-001"

# 2. Extend retention (first time - copies to _ext_retention)
curl -X POST http://localhost:8080/api/files/1/extend-retention \
  -F "retention_days=365" -F "reason=Legal Hold"

# 3. Extend again (only updates retention, no copy)
curl -X POST http://localhost:8080/api/files/1/extend-retention \
  -F "retention_days=730" -F "reason=Case Extended"

# 4. View history
curl http://localhost:8080/api/files/1/retention-history | jq '.'
```

### Scenario 2: Bulk Operations
```bash
# Upload multiple files
for i in {1..10}; do
  curl -X POST http://localhost:8080/api/files/upload \
    -F "file=@test.pdf" -F "case_number=BULK-$i"
done

# Extend retention for all
for i in {1..10}; do
  curl -X POST http://localhost:8080/api/files/$i/extend-retention \
    -F "retention_days=365" -F "reason=Bulk Legal Hold"
done
```

## 🐛 Troubleshooting

### Service won't start
```bash
# Check if port is already in use
lsof -i :8080  # Business System
lsof -i :8000  # DES API
lsof -i :9000  # MinIO API
lsof -i :5432  # PostgreSQL

# View detailed logs
docker-compose -f docker-compose.demo.yml logs business-system
```

### Database issues
```bash
# Restart PostgreSQL
docker-compose -f docker-compose.demo.yml restart postgres

# Check PostgreSQL logs
docker-compose -f docker-compose.demo.yml logs postgres

# Verify database connection
docker exec des-postgres psql -U business_user -d business_system -c "SELECT 1;"
```

### MinIO bucket not initialized
```bash
# Check minio-init logs
docker logs des-minio-init

# Manually create bucket
docker exec des-minio mc mb myminio/des-bucket
docker exec des-minio mc version enable myminio/des-bucket
```

### Reset everything
```bash
# Nuclear option - deletes all data
docker-compose -f docker-compose.demo.yml down -v
docker-compose -f docker-compose.demo.yml up -d
```

## 📊 Monitoring

```bash
# Watch logs in real-time
docker-compose -f docker-compose.demo.yml logs -f

# Monitor resource usage
docker stats

# Check container health
docker ps --format "table {{.Names}}\t{{.Status}}"

# View metrics (if Prometheus enabled)
curl http://localhost:8000/metrics
```

## 🔍 Useful Queries

```sql
-- Files expiring soon (next 30 days)
SELECT uid, filename, extended_retention_due_date 
FROM files 
WHERE extended_retention_due_date < NOW() + INTERVAL '30 days'
  AND in_extended_retention = true;

-- Most extended files
SELECT uid, filename, COUNT(*) as extension_count
FROM files f
JOIN retention_history rh ON f.id = rh.file_id
GROUP BY f.id, uid, filename
ORDER BY extension_count DESC
LIMIT 10;

-- Total storage by status
SELECT status, COUNT(*), SUM(file_size) as total_bytes
FROM files
GROUP BY status;

-- Retention history for a file
SELECT * FROM retention_history 
WHERE file_id = 1 
ORDER BY updated_at DESC;
```

## 📝 Notes

- **First extend**: File copied to `_ext_retention/`, tombstone created
- **Subsequent extends**: Only Object Lock retention updated (fast!)
- **Standard retention**: 90 days (configurable)
- **Object Lock mode**: GOVERNANCE (allows updates with proper permissions)
- **Retrieval**: Transparently checks `_ext_retention/` first, then main shard

## 🆘 Getting Help

```bash
# Check demo documentation
cat demo/README.md

# View API documentation (Swagger)
# Open in browser: http://localhost:8000/docs

# Run test suite
chmod +x demo/test-api.sh
./demo/test-api.sh
```

---

**Quick Reference v1.0** | DES Extended Retention Demo
