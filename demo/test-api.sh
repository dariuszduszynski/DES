#!/bin/bash

# DES Demo - API Testing Examples
# Collection of useful API calls for testing the demo environment

echo "DES Extended Retention - API Testing Examples"
echo "=============================================="
echo ""

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
BUSINESS_API="http://localhost:8080"
DES_API="http://localhost:8000"

# Health Checks
echo -e "${BLUE}=== Health Checks ===${NC}"
echo ""

echo "Business System Health:"
curl -s ${BUSINESS_API}/health | jq '.'
echo ""

echo "DES API Health:"
curl -s ${DES_API}/health | jq '.'
echo ""

# Business System API Examples
echo -e "${BLUE}=== Business System API ===${NC}"
echo ""

echo "1. List all files:"
curl -s ${BUSINESS_API}/api/files | jq '.'
echo ""

echo "2. List files with extended retention:"
curl -s "${BUSINESS_API}/api/files?status=extended" | jq '.'
echo ""

echo "3. List files for specific case:"
curl -s "${BUSINESS_API}/api/files?case_number=CASE-2024-001" | jq '.'
echo ""

echo "4. Upload a file:"
curl -X POST ${BUSINESS_API}/api/files/upload \
  -F "file=@demo/README.md" \
  -F "case_number=DEMO-TEST-001" \
  -F "department=Testing" \
  -F "document_type=Documentation" | jq '.'
echo ""

echo "5. Extend retention for file ID 1:"
curl -X POST ${BUSINESS_API}/api/files/1/extend-retention \
  -F "retention_days=365" \
  -F "reason=Legal Hold - Test Case" \
  -F "updated_by=test_user" | jq '.'
echo ""

echo "6. Get retention history for file ID 1:"
curl -s ${BUSINESS_API}/api/files/1/retention-history | jq '.'
echo ""

# DES API Examples
echo -e "${BLUE}=== DES API - Extended Retention ===${NC}"
echo ""

echo "7. Set retention policy (first time - will copy to _ext_retention):"
curl -X PUT ${DES_API}/files/demo-file-001/retention-policy \
  -H "Content-Type: application/json" \
  -d '{
    "created_at": "2024-12-15T10:00:00Z",
    "due_date": "2025-12-15T00:00:00Z"
  }' | jq '.'
echo ""

echo "8. Update retention policy (will only update Object Lock):"
curl -X PUT ${DES_API}/files/demo-file-001/retention-policy \
  -H "Content-Type: application/json" \
  -d '{
    "created_at": "2024-12-15T10:00:00Z",
    "due_date": "2026-12-15T00:00:00Z"
  }' | jq '.'
echo ""

echo "9. Retrieve file (checks extended retention first):"
curl -X GET ${DES_API}/files/demo-file-001?created_at=2024-12-15T10:00:00Z
echo ""

# MinIO S3 API Examples (using AWS CLI)
echo -e "${BLUE}=== MinIO S3 API (requires AWS CLI) ===${NC}"
echo ""

if command -v aws &> /dev/null; then
    echo "10. List buckets:"
    aws --endpoint-url http://localhost:9000 \
        --no-verify-ssl \
        s3 ls
    echo ""
    
    echo "11. List objects in des-bucket:"
    aws --endpoint-url http://localhost:9000 \
        --no-verify-ssl \
        s3 ls s3://des-bucket/ --recursive
    echo ""
    
    echo "12. List extended retention files:"
    aws --endpoint-url http://localhost:9000 \
        --no-verify-ssl \
        s3 ls s3://des-bucket/_ext_retention/ --recursive
    echo ""
    
    echo "13. Get object retention:"
    aws --endpoint-url http://localhost:9000 \
        --no-verify-ssl \
        s3api get-object-retention \
        --bucket des-bucket \
        --key _ext_retention/20241215/demo-file-001_2024-12-15T10:00:00Z.dat
    echo ""
else
    echo "AWS CLI not installed. Install it to test S3 API directly."
    echo "Install: pip install awscli"
fi

# PostgreSQL Queries
echo -e "${BLUE}=== PostgreSQL Queries ===${NC}"
echo ""

echo "14. Query files table:"
docker exec des-postgres psql -U business_user -d business_system -c \
  "SELECT uid, filename, status, in_extended_retention FROM files LIMIT 5;"
echo ""

echo "15. Query retention history:"
docker exec des-postgres psql -U business_user -d business_system -c \
  "SELECT * FROM retention_history ORDER BY updated_at DESC LIMIT 5;"
echo ""

echo "16. Query files with retention view:"
docker exec des-postgres psql -U business_user -d business_system -c \
  "SELECT uid, filename, days_until_expiration, retention_extension_count 
   FROM files_with_retention LIMIT 5;"
echo ""

# Advanced Scenarios
echo -e "${BLUE}=== Advanced Test Scenarios ===${NC}"
echo ""

echo "17. Bulk extend retention for multiple files:"
cat > /tmp/bulk_extend.sh << 'EOF'
for i in {1..3}; do
  curl -X POST http://localhost:8080/api/files/$i/extend-retention \
    -F "retention_days=730" \
    -F "reason=Bulk Legal Hold" \
    -F "updated_by=bulk_script"
  echo ""
done
EOF
chmod +x /tmp/bulk_extend.sh
/tmp/bulk_extend.sh
echo ""

echo "18. Simulate multiple retention extensions:"
echo "First extension (180 days):"
curl -X POST ${BUSINESS_API}/api/files/1/extend-retention \
  -F "retention_days=180" \
  -F "reason=Initial Legal Hold" \
  -F "updated_by=legal_team" | jq '.'
sleep 2

echo "Second extension (365 days):"
curl -X POST ${BUSINESS_API}/api/files/1/extend-retention \
  -F "retention_days=365" \
  -F "reason=Case Extended" \
  -F "updated_by=legal_team" | jq '.'
sleep 2

echo "Third extension (730 days):"
curl -X POST ${BUSINESS_API}/api/files/1/extend-retention \
  -F "retention_days=730" \
  -F "reason=New Evidence Found" \
  -F "updated_by=legal_team" | jq '.'
echo ""

echo "19. Check retention history after multiple extensions:"
curl -s ${BUSINESS_API}/api/files/1/retention-history | jq '.'
echo ""

# Performance Testing
echo -e "${BLUE}=== Performance Testing ===${NC}"
echo ""

echo "20. Upload 10 files concurrently:"
for i in {1..10}; do
  (
    curl -X POST ${BUSINESS_API}/api/files/upload \
      -F "file=@demo/README.md" \
      -F "case_number=PERF-TEST-$i" \
      -F "department=Performance" \
      -F "document_type=Test" \
      --silent > /dev/null &
  )
done
wait
echo "Upload completed"
echo ""

echo "21. Extend retention for 10 files concurrently:"
for i in {1..10}; do
  (
    curl -X POST ${BUSINESS_API}/api/files/$i/extend-retention \
      -F "retention_days=365" \
      -F "reason=Performance Test" \
      -F "updated_by=perf_script" \
      --silent > /dev/null &
  )
done
wait
echo "Extension completed"
echo ""

# Monitoring
echo -e "${BLUE}=== Monitoring & Metrics ===${NC}"
echo ""

echo "22. Get system statistics:"
curl -s ${BUSINESS_API}/api/files | jq '[
  {
    total_files: length,
    extended_retention: [.[] | select(.in_extended_retention == true)] | length,
    active_files: [.[] | select(.status == "active")] | length
  }
]'
echo ""

echo "23. Calculate average retention days:"
docker exec des-postgres psql -U business_user -d business_system -c \
  "SELECT 
    AVG(EXTRACT(DAY FROM (extended_retention_due_date - created_at))) as avg_retention_days,
    COUNT(*) as extended_files_count
   FROM files 
   WHERE in_extended_retention = true;"
echo ""

# Cleanup helpers
echo -e "${BLUE}=== Cleanup Commands ===${NC}"
echo ""
echo "To clean up test data:"
echo ""
echo "Delete all test files:"
echo "  docker exec des-postgres psql -U business_user -d business_system -c \"DELETE FROM files WHERE case_number LIKE 'PERF-TEST-%';\""
echo ""
echo "Delete all retention history:"
echo "  docker exec des-postgres psql -U business_user -d business_system -c \"TRUNCATE TABLE retention_history CASCADE;\""
echo ""
echo "Reset to initial state:"
echo "  docker-compose -f docker-compose.demo.yml down -v && docker-compose -f docker-compose.demo.yml up -d"
echo ""

echo -e "${GREEN}All tests completed!${NC}"
