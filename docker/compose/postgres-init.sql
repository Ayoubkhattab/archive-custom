-- Enable necessary extensions for Paperless-ngx
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- Set timezone
SET timezone = 'Asia/Riyadh';

-- Create indexes for better performance
DO $$
BEGIN
  IF to_regclass('public.documents_document') IS NOT NULL THEN
    CREATE INDEX IF NOT EXISTS documents_document_title_gin ON documents_document USING gin(title gin_trgm_ops);
    CREATE INDEX IF NOT EXISTS documents_document_content_gin ON documents_document USING gin(content gin_trgm_ops);
    CREATE INDEX IF NOT EXISTS documents_document_correspondent_id ON documents_document(correspondent_id);
    CREATE INDEX IF NOT EXISTS documents_document_document_type_id ON documents_document(document_type_id);
    CREATE INDEX IF NOT EXISTS documents_document_created_at ON documents_document(created_at DESC);
    CREATE INDEX IF NOT EXISTS documents_document_modified_at ON documents_document(modified_at DESC);
  END IF;
END $$;
