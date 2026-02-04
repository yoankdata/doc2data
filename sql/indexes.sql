-- Index on dq_issues.severity for faster filtering
CREATE INDEX IF NOT EXISTS idx_dq_severity ON dq_issues(severity);

-- Index on dim_documents run_id for faster lookups
CREATE INDEX IF NOT EXISTS idx_documents_run_id ON dim_documents(run_id);

-- Index on dim_documents status columns for analytics queries
CREATE INDEX IF NOT EXISTS idx_documents_ocr_status ON dim_documents(ocr_status);
CREATE INDEX IF NOT EXISTS idx_documents_parse_status ON dim_documents(parse_status);

-- Composite index for common query patterns
CREATE INDEX IF NOT EXISTS idx_documents_run_type_status 
ON dim_documents(run_id, doc_type, ocr_status, parse_status);
