-- Types énumérés pour assurer la cohérence des données
CREATE TYPE doc_type AS ENUM ('invoice', 'receipt', 'form');
CREATE TYPE run_status AS ENUM ('success', 'partial', 'failed');
CREATE TYPE dq_severity AS ENUM ('WARN', 'FAIL');

-- Suivi des exécutions du pipeline
CREATE TABLE IF NOT EXISTS pipeline_runs (
  run_id        UUID PRIMARY KEY,
  started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at   TIMESTAMPTZ,
  git_sha       TEXT,
  params        JSONB NOT NULL DEFAULT '{}'::jsonb,
  status        run_status NOT NULL,
  error_message TEXT
);

-- Dimension Documents : Métadonnées sur les fichiers ingérés
CREATE TABLE IF NOT EXISTS dim_documents (
  doc_id             UUID PRIMARY KEY,
  run_id             UUID NOT NULL REFERENCES pipeline_runs(run_id),
  doc_type           doc_type NOT NULL,
  source_filename    TEXT NOT NULL,
  source_sha256      CHAR(64) NOT NULL,
  ingested_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  pages_count        INT NOT NULL CHECK (pages_count > 0),
  ocr_status         run_status NOT NULL,
  parse_status       run_status NOT NULL,
  completeness_score INT NOT NULL CHECK (completeness_score BETWEEN 0 AND 100)
);

-- Index unique pour éviter les doublons par exécution
CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_run_sha_type
ON dim_documents(run_id, source_sha256, doc_type);

-- Table de staging pour le texte brut issu de l'OCR
CREATE TABLE IF NOT EXISTS stg_ocr_text (
  doc_id        UUID PRIMARY KEY REFERENCES dim_documents(doc_id) ON DELETE CASCADE,
  run_id        UUID NOT NULL REFERENCES pipeline_runs(run_id),
  ocr_text      TEXT NOT NULL,
  ocr_engine    TEXT NOT NULL DEFAULT 'tesseract',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Table de staging pour les champs extraits (format clé-valeur)
CREATE TABLE IF NOT EXISTS stg_extracted_fields (
  doc_id      UUID NOT NULL REFERENCES dim_documents(doc_id) ON DELETE CASCADE,
  run_id      UUID NOT NULL REFERENCES pipeline_runs(run_id),
  field_name  TEXT NOT NULL,
  raw_value   TEXT,
  confidence  NUMERIC(5,2) CHECK (confidence >= 0 AND confidence <= 100),
  method      TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (doc_id, field_name)
);

-- Table de faits : Factures (données structurées finales)
CREATE TABLE IF NOT EXISTS fact_invoices (
  doc_id           UUID PRIMARY KEY REFERENCES dim_documents(doc_id) ON DELETE CASCADE,
  run_id           UUID NOT NULL REFERENCES pipeline_runs(run_id),
  invoice_number   TEXT,
  invoice_date     DATE,
  supplier_name    TEXT,
  customer_name    TEXT,
  currency         CHAR(3) NOT NULL DEFAULT 'EUR' CHECK (currency ~ '^[A-Z]{3}$'),
  subtotal_amount  NUMERIC(12,2) CHECK (subtotal_amount IS NULL OR subtotal_amount >= 0),
  tax_amount       NUMERIC(12,2) CHECK (tax_amount IS NULL OR tax_amount >= 0),
  total_amount     NUMERIC(12,2) CHECK (total_amount IS NULL OR total_amount >= 0),
  due_date         DATE,
  CHECK (due_date IS NULL OR invoice_date IS NULL OR due_date >= invoice_date)
);

-- Suivi des problèmes de qualité de données (Data Quality)
CREATE TABLE IF NOT EXISTS dq_issues (
  issue_id    BIGSERIAL PRIMARY KEY,
  doc_id      UUID NOT NULL REFERENCES dim_documents(doc_id) ON DELETE CASCADE,
  run_id      UUID NOT NULL REFERENCES pipeline_runs(run_id),
  severity    dq_severity NOT NULL,
  rule_name   TEXT NOT NULL,
  field_name  TEXT,
  details     TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
