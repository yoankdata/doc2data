"""
Invoice parsing script for Doc2Data pipeline.
Extracts structured data from OCR text using regex patterns.
"""
import os
import re
import argparse
import logging
from datetime import datetime, timezone, date
from uuid import UUID

import psycopg
from dotenv import load_dotenv

load_dotenv()

# Configuration du logging (standardisé)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Constants ---
# Score threshold for "success" vs "partial" status
SUCCESS_THRESHOLD = 50
# Number of optional fields used for completeness calculation
TOTAL_OPTIONAL_FIELDS = 6  # invoice_number, supplier, customer, subtotal, tax, due_date


# --- Helper Functions ---
def clean_spaces(s: str) -> str:
    """Normalize whitespace in a string."""
    return re.sub(r"\s+", " ", s).strip()


def parse_amount(raw: str):
    """
    Parse monetary amounts from various formats.
    Handles: "1 234,56", "1,234.56", "1234.56", "1234,56"
    """
    if raw is None:
        return None
    s = raw.strip()
    s = s.replace("\u00a0", " ")  # nbsp
    s = re.sub(r"[^\d,.\- ]", "", s)
    s = s.replace(" ", "")
    # Heuristic: if both separators exist, last one is decimal
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "")
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    else:
        # Single separator
        if "," in s:
            s = s.replace(",", ".")
    try:
        return round(float(s), 2)
    except Exception:
        return None


def parse_date(raw: str):
    """
    Parse dates from common formats.
    Supports: dd/mm/yyyy, dd-mm-yyyy, yyyy-mm-dd
    """
    if raw is None:
        return None
    s = raw.strip()
    # ISO format: yyyy-mm-dd
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
    if m:
        y, mo, d = map(int, m.groups())
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    # European format: dd/mm/yyyy
    m = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", s)
    if m:
        d, mo, y = map(int, m.groups())
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    return None


def first_match(patterns, text, flags=re.IGNORECASE):
    """Try multiple regex patterns and return first match."""
    for p in patterns:
        m = re.search(p, text, flags)
        if m:
            return clean_spaces(m.group(1))
    return None


def detect_currency(text: str):
    """Detect currency from text content."""
    t = text.upper()
    if "XOF" in t or "FCFA" in t:
        return "XOF"
    if "USD" in t or "$" in t:
        return "USD"
    if "EUR" in t or "€" in t:
        return "EUR"
    return "EUR"  # default assumption


def upsert_field(cur, doc_id, run_id, field_name, raw_value, confidence, method="regex"):
    """Upsert an extracted field into staging table."""
    cur.execute(
        """
        INSERT INTO stg_extracted_fields(doc_id, run_id, field_name, raw_value, confidence, method)
        VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT (doc_id, field_name) DO UPDATE
        SET raw_value = EXCLUDED.raw_value,
            confidence = EXCLUDED.confidence,
            method = EXCLUDED.method,
            created_at = now()
        """,
        (doc_id, run_id, field_name, raw_value, confidence, method),
    )


def log_issue(cur, doc_id, run_id, severity, rule_name, field_name, details):
    """Log a data quality issue."""
    cur.execute(
        """
        INSERT INTO dq_issues(doc_id, run_id, severity, rule_name, field_name, details)
        VALUES (%s,%s,%s,%s,%s,%s)
        """,
        (doc_id, run_id, severity, rule_name, field_name, details),
    )


# --- Main Processing ---
def main():
    ap = argparse.ArgumentParser(description="Parse invoice data from OCR text")
    ap.add_argument("--run-id", required=True, help="Pipeline run ID to process")
    args = ap.parse_args()

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        logger.error("Missing DATABASE_URL in .env")
        raise SystemExit("Missing DATABASE_URL in .env")

    run_id = args.run_id

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            # Select only invoices with OCR success
            cur.execute(
                """
                SELECT d.doc_id, o.ocr_text
                FROM dim_documents d
                JOIN stg_ocr_text o ON o.doc_id = d.doc_id
                WHERE d.run_id = %s
                  AND d.doc_type = 'invoice'
                  AND d.ocr_status = 'success'
                """,
                (run_id,),
            )
            rows = cur.fetchall()

            logger.info(f"[START] Parsing run_id={run_id}")
            logger.info(f"Found {len(rows)} documents to parse")

            parsed_ok = 0
            parsed_partial = 0
            parsed_fail = 0

            for doc_id, text in rows:
                try:
                    t = text or ""
                    t_norm = clean_spaces(t)

                    # --- Extract raw fields ---
                    invoice_number = first_match(
                        [
                            r"(?:invoice|facture)\s*(?:n[°o]|no|num(?:éro)?)\s*[:#.]?\s*([A-Z0-9\-\/]+)",
                            r"(?:n[°o]|no|num(?:éro)?)\s*[:#.]?\s*([A-Z0-9\-\/]{4,})",
                        ],
                        t_norm,
                    )
                    
                    # Date: expanded to catch "Date:" followed by date
                    invoice_date_raw = first_match(
                        [
                            r"(?:date\s*(?:de)?\s*facture|invoice\s*date)\s*[:#]?\s*([0-9/\-]{8,10})",
                            r"(?<!due\s)date\s*[:#]?\s*([0-9/\-]{8,10})",
                        ],
                        t_norm,
                    )
                    
                    # Due Date
                    due_date_raw = first_match(
                        [r"(?:due\s*date|date\s*d['eé]ch[ée]ance|[ée]ch[ée]ance)\s*[:#]?\s*([0-9/\-]{8,10})"],
                        t_norm,
                    )

                    # Total: Handle "NET A PAYER" without accents
                    total_raw = first_match(
                        [
                            r"(?:total\s*ttc|amount\s*due|total\s*due|total)\s*[:#]?\s*([0-9][0-9\s.,]+)",
                            r"(?:net\s*[àa]\s*payer)\s*[:#]?\s*([0-9][0-9\s.,]+)", 
                        ],
                        t_norm,
                    )
                    
                    tax_raw = first_match(
                        [r"(?:tva|vat|tax)(?:\s*\(.*?\))?\s*[:#]?\s*([0-9][0-9\s.,]+)"],
                        t_norm,
                    )
                    
                    subtotal_raw = first_match(
                        [r"(?:subtotal|sous-?total|total\s*ht)\s*[:#]?\s*([0-9][0-9\s.,]+)"],
                        t_norm,
                    )

                    currency = detect_currency(t)

                    # Supplier/Customer extraction
                    supplier_name = first_match(
                        [r"(?:seller|supplier|fournisseur)\s*[:#]?\s*([A-Z][A-Za-z0-9 \-&'.]{3,})"],
                        t_norm,
                    )
                    customer_name = first_match(
                        [r"(?:bill\s*to|client|customer)\s*[:#]?\s*([A-Z][A-Za-z0-9 \-&'.]{3,})"],
                        t_norm,
                    )

                    # --- Write extracted fields to staging ---
                    upsert_field(cur, doc_id, run_id, "invoice_number", invoice_number, 70 if invoice_number else 0)
                    upsert_field(cur, doc_id, run_id, "invoice_date_raw", invoice_date_raw, 70 if invoice_date_raw else 0)
                    upsert_field(cur, doc_id, run_id, "due_date_raw", due_date_raw, 60 if due_date_raw else 0)
                    upsert_field(cur, doc_id, run_id, "total_raw", total_raw, 80 if total_raw else 0)
                    upsert_field(cur, doc_id, run_id, "tax_raw", tax_raw, 60 if tax_raw else 0)
                    upsert_field(cur, doc_id, run_id, "subtotal_raw", subtotal_raw, 60 if subtotal_raw else 0)
                    upsert_field(cur, doc_id, run_id, "currency", currency, 90, "heuristic")
                    upsert_field(cur, doc_id, run_id, "supplier_name", supplier_name, 40 if supplier_name else 0)
                    upsert_field(cur, doc_id, run_id, "customer_name", customer_name, 40 if customer_name else 0)

                    # --- Normalize & validate ---
                    invoice_date = parse_date(invoice_date_raw) if invoice_date_raw else None
                    due_date = parse_date(due_date_raw) if due_date_raw else None
                    total_amount = parse_amount(total_raw) if total_raw else None
                    tax_amount = parse_amount(tax_raw) if tax_raw else None
                    subtotal_amount = parse_amount(subtotal_raw) if subtotal_raw else None

                    # DQ rules (hard)
                    hard_fail = []
                    if invoice_date is None:
                        hard_fail.append(("invoice_date", "Missing/invalid invoice date"))
                    if total_amount is None:
                        hard_fail.append(("total_amount", "Missing/invalid total amount"))

                    # DQ rules (soft)
                    if tax_amount is not None and total_amount is not None and tax_amount > total_amount:
                        log_issue(cur, doc_id, run_id, "WARN", "tax_gt_total", "tax_amount", f"tax={tax_amount} > total={total_amount}")

                    if not invoice_number:
                        log_issue(cur, doc_id, run_id, "WARN", "missing_invoice_number", "invoice_number", "No invoice number detected")

                    if hard_fail:
                        for field, msg in hard_fail:
                            log_issue(cur, doc_id, run_id, "FAIL", "required_field_missing", field, msg)
                        cur.execute(
                            "UPDATE dim_documents SET parse_status='failed', completeness_score=0 WHERE doc_id=%s",
                            (doc_id,),
                        )
                        parsed_fail += 1
                        logger.info(f"Doc {doc_id}: failed (missing required fields)")
                        continue

                    # Completeness score
                    present = sum(
                        1 for v in [invoice_number, supplier_name, customer_name, subtotal_amount, tax_amount, due_date] if v is not None
                    )
                    score = int(round((present / TOTAL_OPTIONAL_FIELDS) * 100))

                    # Upsert final invoice row
                    cur.execute(
                        """
                        INSERT INTO fact_invoices(
                          doc_id, run_id, invoice_number, invoice_date, supplier_name, customer_name,
                          currency, subtotal_amount, tax_amount, total_amount, due_date
                        )
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (doc_id) DO UPDATE
                        SET run_id=EXCLUDED.run_id,
                            invoice_number=EXCLUDED.invoice_number,
                            invoice_date=EXCLUDED.invoice_date,
                            supplier_name=EXCLUDED.supplier_name,
                            customer_name=EXCLUDED.customer_name,
                            currency=EXCLUDED.currency,
                            subtotal_amount=EXCLUDED.subtotal_amount,
                            tax_amount=EXCLUDED.tax_amount,
                            total_amount=EXCLUDED.total_amount,
                            due_date=EXCLUDED.due_date
                        """,
                        (
                            doc_id, run_id, invoice_number, invoice_date, supplier_name, customer_name,
                            currency, subtotal_amount, tax_amount, total_amount, due_date
                        ),
                    )

                    status = "success" if score >= SUCCESS_THRESHOLD else "partial"
                    cur.execute(
                        "UPDATE dim_documents SET parse_status=%s, completeness_score=%s WHERE doc_id=%s",
                        (status, score, doc_id),
                    )
                    if status == "success":
                        parsed_ok += 1
                        logger.info(f"✅ Doc {doc_id}: success (score={score})")
                    else:
                        parsed_partial += 1
                        logger.info(f"⚠️ Doc {doc_id}: partial (score={score})")

                except Exception as e:
                    logger.error(f"❌ Error processing doc {doc_id}: {e}")
                    parsed_fail += 1

            # Update run status
            cur.execute(
                """
                UPDATE pipeline_runs
                SET finished_at=%s,
                    status = (CASE
                      WHEN %s > 0 AND %s = 0 THEN 'success'
                      WHEN %s > 0 THEN 'partial'
                      ELSE 'failed'
                    END)::run_status
                WHERE run_id=%s
                """,
                (datetime.now(timezone.utc), parsed_ok, parsed_fail, parsed_ok + parsed_partial, run_id),
            )

    logger.info(f"[DONE] Run complete. Stats: success={parsed_ok}, partial={parsed_partial}, failed={parsed_fail}")
    print(f"run_id={run_id} parsed_success={parsed_ok} parsed_partial={parsed_partial} parsed_failed={parsed_fail}")


if __name__ == "__main__":
    main()
