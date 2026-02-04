-- 1) Health check global
SELECT
  COUNT(*) AS docs_total,
  SUM(CASE WHEN ocr_status='success' THEN 1 ELSE 0 END) AS ocr_ok,
  SUM(CASE WHEN parse_status='success' THEN 1 ELSE 0 END) AS parse_ok,
  SUM(CASE WHEN parse_status='partial' THEN 1 ELSE 0 END) AS parse_partial,
  SUM(CASE WHEN parse_status='failed' THEN 1 ELSE 0 END) AS parse_failed
FROM dim_documents;

-- 2) Top reasons of failure (DQ)
SELECT severity, rule_name, COUNT(*) AS n
FROM dq_issues
GROUP BY 1,2
ORDER BY n DESC
LIMIT 15;

-- 3) Invoices loaded + completeness distribution
SELECT
  COUNT(*) AS invoices_loaded,
  MIN(completeness_score) AS min_score,
  ROUND(AVG(completeness_score),2) AS avg_score,
  MAX(completeness_score) AS max_score
FROM dim_documents
WHERE doc_type='invoice';

-- 4) Monthly totals (basic analytics)
SELECT date_trunc('month', invoice_date)::date AS month,
       SUM(total_amount) AS total_amount
FROM fact_invoices
WHERE invoice_date IS NOT NULL AND total_amount IS NOT NULL
GROUP BY 1
ORDER BY 1;

-- 5) Incomplete/partial documents list (actionable)
SELECT doc_id, doc_type, source_filename, ocr_status, parse_status, completeness_score
FROM dim_documents
WHERE parse_status IN ('partial','failed')
ORDER BY completeness_score ASC, source_filename;
