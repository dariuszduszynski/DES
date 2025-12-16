#!/bin/bash

# DES Demo Environment - Quick Start Script
# Automates the setup and verification of the demo environment

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  DES Extended Retention - Demo Environment${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Check prerequisites
echo -e "${YELLOW}Checking prerequisites...${NC}"

if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ Docker not found. Please install Docker first.${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Docker found${NC}"

if ! command -v docker-compose &> /dev/null; then
    echo -e "${RED}❌ Docker Compose not found. Please install Docker Compose first.${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Docker Compose found${NC}"

# Check if ports are available
echo ""
echo -e "${YELLOW}Checking port availability...${NC}"

check_port() {
    if lsof -Pi :$1 -sTCP:LISTEN -t >/dev/null 2>&1 ; then
        echo -e "${RED}❌ Port $1 is already in use${NC}"
        return 1
    else
        echo -e "${GREEN}✅ Port $1 is available${NC}"
        return 0
    fi
}

check_port 5432 || echo -e "${YELLOW}   (PostgreSQL will not start if port is busy)${NC}"
check_port 8000 || echo -e "${YELLOW}   (DES API will not start if port is busy)${NC}"
check_port 8080 || echo -e "${YELLOW}   (Business System will not start if port is busy)${NC}"
check_port 9000 || echo -e "${YELLOW}   (MinIO API will not start if port is busy)${NC}"
check_port 9001 || echo -e "${YELLOW}   (MinIO Console will not start if port is busy)${NC}"

# Start services
echo ""
echo -e "${YELLOW}Starting demo environment...${NC}"
docker-compose -f docker-compose.demo.yml up -d

# Wait for services
echo ""
echo -e "${YELLOW}Waiting for services to be ready (this may take 30-60 seconds)...${NC}"

wait_for_service() {
    local name=$1
    local url=$2
    local max_attempts=30
    local attempt=1
    
    while [ $attempt -le $max_attempts ]; do
        if curl -sf "$url" > /dev/null 2>&1; then
            echo -e "${GREEN}✅ $name is ready${NC}"
            return 0
        fi
        echo -e "${YELLOW}   Waiting for $name... (attempt $attempt/$max_attempts)${NC}"
        sleep 2
        attempt=$((attempt + 1))
    done
    
    echo -e "${RED}❌ $name failed to start${NC}"
    return 1
}

# Check PostgreSQL
echo -n "   PostgreSQL... "
sleep 5
if docker exec des-postgres pg_isready -U business_user > /dev/null 2>&1; then
    echo -e "${GREEN}✅${NC}"
else
    echo -e "${RED}❌${NC}"
fi

# Check MinIO
wait_for_service "MinIO" "http://localhost:9000/minio/health/live"

# Check DES API
wait_for_service "DES API" "http://localhost:8000/health"

# Check Business System
wait_for_service "Business System" "http://localhost:8080/health"

# Final status
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}✅ Demo environment is ready!${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "${YELLOW}Access the services:${NC}"
echo ""
echo -e "  🌐 Business System UI:  ${BLUE}http://localhost:8080${NC}"
echo -e "     - Upload files, manage retention"
echo -e "     - View dashboard and statistics"
echo ""
echo -e "  🔧 DES API:             ${BLUE}http://localhost:8000${NC}"
echo -e "     - Extended retention endpoints"
echo -e "     - API documentation (Swagger)"
echo ""
echo -e "  💾 MinIO Console:       ${BLUE}http://localhost:9001${NC}"
echo -e "     - Username: minioadmin"
echo -e "     - Password: minioadmin"
echo -e "     - Browse S3 buckets and objects"
echo ""
echo -e "  🗄️  PostgreSQL:          ${BLUE}localhost:5432${NC}"
echo -e "     - Database: business_system"
echo -e "     - Username: business_user"
echo -e "     - Password: business_pass"
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "${YELLOW}Quick Demo Scenarios:${NC}"
echo ""
echo -e "1️⃣  Upload a file:"
echo -e "   Open ${BLUE}http://localhost:8080${NC}"
echo -e "   Click 'Upload New File' and select a file"
echo ""
echo -e "2️⃣  Extend retention:"
echo -e "   Click 'Extend Retention' button for any file"
echo -e "   Set retention period (e.g., 365 days)"
echo -e "   Choose reason (e.g., Legal Hold)"
echo ""
echo -e "3️⃣  View in MinIO:"
echo -e "   Open ${BLUE}http://localhost:9001${NC}"
echo -e "   Login with minioadmin/minioadmin"
echo -e "   Browse bucket 'des-bucket'"
echo -e "   Check _ext_retention/ folder"
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "${YELLOW}Useful Commands:${NC}"
echo ""
echo -e "  View logs:           ${BLUE}docker-compose -f docker-compose.demo.yml logs -f${NC}"
echo -e "  Stop environment:    ${BLUE}docker-compose -f docker-compose.demo.yml stop${NC}"
echo -e "  Restart:             ${BLUE}docker-compose -f docker-compose.demo.yml restart${NC}"
echo -e "  Clean up (delete):   ${BLUE}docker-compose -f docker-compose.demo.yml down -v${NC}"
echo ""
echo -e "${GREEN}Happy testing! 🚀${NC}"
echo ""

# Optional: Open browser
read -p "Open Business System UI in browser? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    if command -v open &> /dev/null; then
        open http://localhost:8080
    elif command -v xdg-open &> /dev/null; then
        xdg-open http://localhost:8080
    elif command -v start &> /dev/null; then
        start http://localhost:8080
    else
        echo -e "${YELLOW}Could not open browser automatically. Please open http://localhost:8080 manually.${NC}"
    fi
fi
