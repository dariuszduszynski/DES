-- Business System Demo Database Schema

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Files table - simulates business documents/files
CREATE TABLE files (
    id SERIAL PRIMARY KEY,
    uid VARCHAR(255) UNIQUE NOT NULL,
    filename VARCHAR(500) NOT NULL,
    file_size BIGINT NOT NULL,
    mime_type VARCHAR(100),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    uploaded_at TIMESTAMP NOT NULL DEFAULT NOW(),
    
    -- Retention management
    standard_retention_days INTEGER NOT NULL DEFAULT 90,
    extended_retention_due_date TIMESTAMP,
    retention_reason TEXT,
    retention_updated_at TIMESTAMP,
    retention_updated_by VARCHAR(255),
    
    -- Status tracking
    status VARCHAR(50) NOT NULL DEFAULT 'active',  -- active, extended, expired
    in_extended_retention BOOLEAN NOT NULL DEFAULT FALSE,
    
    -- Business metadata
    case_number VARCHAR(100),
    department VARCHAR(100),
    document_type VARCHAR(100),
    
    -- S3 location tracking
    s3_location TEXT,  -- main shard or _ext_retention path
    
    -- Indexes
    CONSTRAINT files_uid_created_at_unique UNIQUE (uid, created_at)
);

-- Retention history - audit trail
CREATE TABLE retention_history (
    id SERIAL PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    previous_due_date TIMESTAMP,
    new_due_date TIMESTAMP NOT NULL,
    reason TEXT NOT NULL,
    updated_by VARCHAR(255) NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Cases table - simulates legal/business cases
CREATE TABLE cases (
    id SERIAL PRIMARY KEY,
    case_number VARCHAR(100) UNIQUE NOT NULL,
    case_name VARCHAR(500) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'open',  -- open, closed, extended
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMP,
    retention_due_date TIMESTAMP,
    department VARCHAR(100)
);

-- Case-File associations
CREATE TABLE case_files (
    case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    added_at TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (case_id, file_id)
);

-- Indexes for performance
CREATE INDEX idx_files_uid ON files(uid);
CREATE INDEX idx_files_created_at ON files(created_at);
CREATE INDEX idx_files_status ON files(status);
CREATE INDEX idx_files_case_number ON files(case_number);
CREATE INDEX idx_files_extended_retention ON files(in_extended_retention);
CREATE INDEX idx_retention_history_file_id ON retention_history(file_id);
CREATE INDEX idx_cases_case_number ON cases(case_number);

-- Insert sample cases
INSERT INTO cases (case_number, case_name, status, department, retention_due_date) VALUES
    ('CASE-2024-001', 'Smith vs. Corporation XYZ', 'open', 'Legal', NOW() + INTERVAL '365 days'),
    ('CASE-2024-002', 'Financial Audit Q4', 'open', 'Finance', NOW() + INTERVAL '2555 days'),
    ('CASE-2024-003', 'HR Investigation - Employee A', 'closed', 'HR', NOW() + INTERVAL '180 days');

-- Sample data for demo
INSERT INTO files (uid, filename, file_size, mime_type, case_number, document_type, department) VALUES
    ('demo-file-001', 'contract_draft.pdf', 524288, 'application/pdf', 'CASE-2024-001', 'Contract', 'Legal'),
    ('demo-file-002', 'financial_statement.xlsx', 1048576, 'application/vnd.ms-excel', 'CASE-2024-002', 'Financial', 'Finance'),
    ('demo-file-003', 'meeting_notes.docx', 102400, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'CASE-2024-003', 'Notes', 'HR');

-- View for easy querying
CREATE VIEW files_with_retention AS
SELECT 
    f.id,
    f.uid,
    f.filename,
    f.file_size,
    f.created_at,
    f.standard_retention_days,
    f.extended_retention_due_date,
    f.retention_reason,
    f.status,
    f.in_extended_retention,
    f.case_number,
    c.case_name,
    c.department,
    CASE 
        WHEN f.extended_retention_due_date IS NOT NULL 
        THEN f.extended_retention_due_date - NOW()
        ELSE (f.created_at + (f.standard_retention_days || ' days')::INTERVAL) - NOW()
    END as days_until_expiration,
    (SELECT COUNT(*) FROM retention_history rh WHERE rh.file_id = f.id) as retention_extension_count
FROM files f
LEFT JOIN cases c ON f.case_number = c.case_number;

COMMENT ON TABLE files IS 'Business system files with retention tracking';
COMMENT ON TABLE retention_history IS 'Audit trail of all retention changes';
COMMENT ON TABLE cases IS 'Business cases that may require extended retention';
