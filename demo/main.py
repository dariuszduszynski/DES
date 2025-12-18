"""
Business System Mock - Demo aplikacja symulująca system merytoryczny
Obsługuje upload plików, listowanie, i zarządzanie retencją przez DES API
"""

import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Optional

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, String, Text, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker

from des_core.metrics import idempotency_rejections_total

# Configuration
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://business_user:business_pass@localhost:5432/business_system")
DES_API_URL = os.getenv("DES_API_URL", "http://localhost:8000")
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "http://localhost:9000")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "minioadmin")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "minioadmin")
S3_BUCKET = os.getenv("S3_BUCKET", "des-bucket")
IDEMPOTENCY_WINDOW = int(os.getenv("RETENTION_IDEMPOTENCY_SECONDS", "5"))

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.info("Idempotency window set to %d seconds", IDEMPOTENCY_WINDOW)

# Database setup
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class FileRecord(Base):
    __tablename__ = "files"
    
    id = Column(Integer, primary_key=True)
    uid = Column(String(255), unique=True, nullable=False)
    filename = Column(String(500), nullable=False)
    file_size = Column(BigInteger, nullable=False)
    mime_type = Column(String(100))
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    uploaded_at = Column(DateTime, nullable=False, default=datetime.now)
    
    standard_retention_days = Column(Integer, nullable=False, default=90)
    extended_retention_due_date = Column(DateTime)
    retention_reason = Column(Text)
    retention_updated_at = Column(DateTime)
    retention_updated_by = Column(String(255))
    
    status = Column(String(50), nullable=False, default='active')
    in_extended_retention = Column(Boolean, nullable=False, default=False)
    
    case_number = Column(String(100))
    department = Column(String(100))
    document_type = Column(String(100))
    s3_location = Column(Text)

class RetentionHistoryRecord(Base):
    __tablename__ = "retention_history"
    
    id = Column(Integer, primary_key=True)
    file_id = Column(Integer, nullable=False)
    previous_due_date = Column(DateTime)
    new_due_date = Column(DateTime, nullable=False)
    reason = Column(Text, nullable=False)
    updated_by = Column(String(255), nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)

# FastAPI app
app = FastAPI(title="Business System Mock", version="1.0.0")
templates = Jinja2Templates(directory="/app/templates")

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Routes
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Main dashboard"""
    return templates.TemplateResponse("index.html", {
        "request": request,
        "des_api_url": DES_API_URL,
        "s3_endpoint": S3_ENDPOINT
    })

@app.get("/api/files", response_class=JSONResponse)
async def list_files(
    status: Optional[str] = None,
    case_number: Optional[str] = None,
    db: Session = next(get_db())
):
    """List all files with filters"""
    query = db.query(FileRecord)
    
    if status:
        query = query.filter(FileRecord.status == status)
    if case_number:
        query = query.filter(FileRecord.case_number == case_number)
    
    files = query.order_by(FileRecord.uploaded_at.desc()).all()
    
    return [
        {
            "id": f.id,
            "uid": f.uid,
            "filename": f.filename,
            "file_size": f.file_size,
            "created_at": f.created_at.isoformat() if f.created_at else None,
            "status": f.status,
            "case_number": f.case_number,
            "department": f.department,
            "in_extended_retention": f.in_extended_retention,
            "extended_retention_due_date": f.extended_retention_due_date.isoformat() if f.extended_retention_due_date else None,
            "retention_reason": f.retention_reason,
            "days_until_expiration": calculate_days_until_expiration(f)
        }
        for f in files
    ]

@app.post("/api/files/upload")
async def upload_file(
    file: UploadFile = File(...),
    case_number: Optional[str] = Form(None),
    department: Optional[str] = Form("General"),
    document_type: Optional[str] = Form("Document"),
    db: Session = next(get_db())
):
    """Upload file to system (simulates business system upload)"""
    try:
        # Generate UID
        file_uid = f"file-{uuid.uuid4()}"
        
        # Read file content
        content = await file.read()
        file_size = len(content)
        
        # Store in database
        file_record = FileRecord(
            uid=file_uid,
            filename=file.filename,
            file_size=file_size,
            mime_type=file.content_type,
            case_number=case_number,
            department=department,
            document_type=document_type
        )
        
        db.add(file_record)
        db.commit()
        db.refresh(file_record)
        
        logger.info(f"File uploaded: {file_uid} ({file.filename})")
        
        # TODO: Here you would normally pack this file into DES
        # For demo, we're just tracking metadata
        
        return {
            "uid": file_uid,
            "filename": file.filename,
            "size": file_size,
            "message": "File uploaded successfully"
        }
    
    except Exception as e:
        logger.error(f"Upload failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/files/{file_id}/extend-retention")
async def extend_retention(
    file_id: int,
    retention_days: int = Form(...),
    reason: str = Form(...),
    updated_by: str = Form("system_admin"),
    db: Session = next(get_db())
):
    """Extend retention for a file - calls DES API"""
    # Get file record
    file_record = db.query(FileRecord).filter(FileRecord.id == file_id).first()
    if not file_record:
        raise HTTPException(status_code=404, detail="File not found")
    
    now = datetime.now()
    if file_record.retention_updated_at:
        seconds_since_update = (now - file_record.retention_updated_at).total_seconds()
        if seconds_since_update < IDEMPOTENCY_WINDOW:
            logger.warning("Retention update requested too soon after previous update for file_id=%s", file_id)
            idempotency_rejections_total.inc()
            raise HTTPException(status_code=429, detail="Retention was just updated, please wait")
    
    # Calculate new due date
    new_due_date = datetime.now() + timedelta(days=retention_days)
    
    try:
        # Call DES API to set retention policy
        async with httpx.AsyncClient() as client:
            response = await client.put(
                f"{DES_API_URL}/files/{file_record.uid}/retention-policy",
                json={
                    "created_at": file_record.created_at.isoformat(),
                    "due_date": new_due_date.isoformat()
                },
                timeout=30.0
            )
    except httpx.RequestError as e:
        logger.error(f"DES API request failed: {str(e)}")
        raise HTTPException(status_code=503, detail=f"DES API unavailable: {str(e)}") from e
    
    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail=f"DES API error: {response.text}"
        )
    
    des_result = response.json()
    previous_due_date = file_record.extended_retention_due_date
    updated_at = datetime.now()
    
    if file_record.status in {"active", "expired"}:
        new_status = "extended"
    elif file_record.status == "extended":
        new_status = file_record.status
    else:
        new_status = file_record.status
    
    try:
        file_record.extended_retention_due_date = new_due_date
        file_record.retention_reason = reason
        file_record.retention_updated_at = updated_at
        file_record.retention_updated_by = updated_by
        file_record.status = new_status
        file_record.in_extended_retention = True
        
        # Add to history
        history = RetentionHistoryRecord(
            file_id=file_id,
            previous_due_date=previous_due_date,
            new_due_date=new_due_date,
            reason=reason,
            updated_by=updated_by
        )
        
        db.add(history)
        db.commit()
    except Exception as db_error:
        db.rollback()
        logger.error("Database commit failed for retention extension: %s", str(db_error))
        raise HTTPException(status_code=500, detail="Failed to persist retention update") from db_error
    
    logger.info(f"Retention extended for {file_record.uid}: {retention_days} days")
    
    return {
        "uid": file_record.uid,
        "new_due_date": new_due_date.isoformat(),
        "des_action": des_result.get("action"),
        "message": "Retention extended successfully"
    }

@app.get("/api/files/{file_id}/retention-history")
async def get_retention_history(file_id: int, db: Session = next(get_db())):
    """Get retention change history for a file"""
    history = db.query(RetentionHistoryRecord).filter(
        RetentionHistoryRecord.file_id == file_id
    ).order_by(RetentionHistoryRecord.updated_at.desc()).all()
    
    return [
        {
            "previous_due_date": h.previous_due_date.isoformat() if h.previous_due_date else None,
            "new_due_date": h.new_due_date.isoformat(),
            "reason": h.reason,
            "updated_by": h.updated_by,
            "updated_at": h.updated_at.isoformat()
        }
        for h in history
    ]

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "business-system-mock",
        "timestamp": datetime.now().isoformat()
    }

# Helper functions
def calculate_days_until_expiration(file_record: FileRecord) -> Optional[int]:
    """Calculate days until file expiration"""
    if file_record.extended_retention_due_date:
        delta = file_record.extended_retention_due_date - datetime.now()
        return max(0, delta.days)
    elif file_record.created_at:
        expiration = file_record.created_at + timedelta(days=file_record.standard_retention_days)
        delta = expiration - datetime.now()
        return max(0, delta.days)
    return None

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
