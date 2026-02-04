"""
Ingestion script for Doc2Data pipeline.
Scans a directory for PDF files and registers them in dim_documents.
"""
import os
import argparse
import hashlib
import json
import logging
from uuid import uuid4
from datetime import datetime, timezone

import psycopg
from dotenv import load_dotenv

load_dotenv()

# Configuration du logging (standardisé avec ocr.py)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(message)s'
)
logger = logging.getLogger(__name__)


def sha256_file(path: str) -> str:
    """Calcule le hash SHA256 d'un fichier pour détecter les doublons."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description="Ingest PDF documents into the pipeline")
    ap.add_argument("--doc-type", required=True, choices=["invoice", "receipt", "form"],
                    help="Type of document being ingested")
    ap.add_argument("--input-dir", default="data/raw_pdfs",
                    help="Directory containing PDF files to ingest")
    ap.add_argument("--pages-count", type=int, default=1,
                    help="Placeholder page count (OCR will update with real value)")
    args = ap.parse_args()

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        logger.error("Missing DATABASE_URL in .env")
        raise SystemExit("Missing DATABASE_URL in .env")

    run_id = uuid4()
    now = datetime.now(timezone.utc)

    # Scan for PDF files
    pdfs = []
    if os.path.exists(args.input_dir):
        for name in os.listdir(args.input_dir):
            if name.lower().endswith(".pdf"):
                pdfs.append(os.path.join(args.input_dir, name))
    pdfs.sort()

    logger.info(f"[START] Ingestion run_id={run_id} doc_type={args.doc_type}")
    logger.info(f"Found {len(pdfs)} PDF files in {args.input_dir}")

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            # Create pipeline run record
            params_dict = {"step": "ingest", "doc_type": args.doc_type}
            params_json = json.dumps(params_dict)
            cur.execute(
                """
                INSERT INTO pipeline_runs(run_id, started_at, status, params)
                VALUES (%s, %s, 'partial', %s::jsonb)
                """,
                (run_id, now, params_json),
            )

            inserted = 0
            skipped = 0

            for path in pdfs:
                filename = os.path.basename(path)
                file_hash = sha256_file(path)
                doc_id = uuid4()

                try:
                    cur.execute(
                        """
                        INSERT INTO dim_documents(
                          doc_id, run_id, doc_type, source_filename, source_sha256,
                          pages_count, ocr_status, parse_status, completeness_score
                        )
                        VALUES (%s,%s,%s,%s,%s,%s,'failed','failed',0)
                        ON CONFLICT (run_id, source_sha256, doc_type) DO NOTHING
                        """,
                        (doc_id, run_id, args.doc_type, filename, file_hash, args.pages_count),
                    )
                    if cur.rowcount == 1:
                        inserted += 1
                        logger.info(f"✅ Registered: {filename}")
                    else:
                        skipped += 1
                        logger.warning(f"⏭️ Skipped (duplicate): {filename}")
                except Exception as e:
                    # If INSERT fails (not due to conflict), log to stdout only
                    # We can't insert into dq_issues without a valid doc_id (FK constraint)
                    logger.error(f"❌ Error inserting {filename}: {e}")

            # Finalize run status
            final_status = 'success' if len(pdfs) > 0 else 'failed'
            error_msg = None if len(pdfs) > 0 else 'No PDFs found'
            
            cur.execute(
                """
                UPDATE pipeline_runs
                SET finished_at = %s,
                    status = %s::run_status,
                    error_message = %s
                WHERE run_id = %s
                """,
                (datetime.now(timezone.utc), final_status, error_msg, run_id),
            )

    logger.info(f"[DONE] run_id={run_id} pdfs_found={len(pdfs)} inserted={inserted} skipped={skipped}")
    print(f"run_id={run_id} pdfs_found={len(pdfs)} inserted={inserted} skipped={skipped}")


if __name__ == "__main__":
    main()
