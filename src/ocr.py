import os
import argparse
import logging
import traceback
from datetime import datetime, timezone
from concurrent.futures import ProcessPoolExecutor, as_completed

import psycopg
from dotenv import load_dotenv
import pytesseract
from pdf2image import convert_from_path, pdfinfo_from_path

load_dotenv()

# Configuration du logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(message)s')
logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# CORE LOGIC (Worker)
# -----------------------------------------------------------------------------

def ocr_pdf_to_text(pdf_path: str, dpi: int = 200) -> str:
    """Extract text from PDF safely using strict pagination to avoid OOM."""
    try:
        info = pdfinfo_from_path(pdf_path)
        max_pages = info["Pages"]
    except Exception as e:
        logger.warning(f"Could not get pdf info for {pdf_path}: {e}. Skipping fallback to avoid OOM.")
        # If we can't get info, we probably can't process it safely page-by-page.
        raise e

    parts = []
    # Traitement page par page strict pour garder la RAM basse
    for i in range(1, max_pages + 1):
        try:
            # thread_count=1 car nous parallélisons déjà au niveau des fichiers
            images = convert_from_path(pdf_path, dpi=dpi, first_page=i, last_page=i, thread_count=1)
            if images:
                # config='--psm 1' : Automatic page segmentation with OSD (souvent mieux pour les docs complets)
                text = pytesseract.image_to_string(images[0], lang='fra', config='--psm 1')
                parts.append(text)
                del images # Garbage collection immédiat
        except Exception as e:
            logger.error(f"Error reading page {i} of {pdf_path}: {e}")
            continue

    return "\n\n".join(parts).strip()

def process_single_document(doc_data, db_url, input_dir, run_id, dpi):
    """
    Fonction isolée exécutée par un Worker Process.
    Gère sa propre connexion DB pour éviter les conflits de transaction.
    """
    doc_id, filename = doc_data
    path = os.path.join(input_dir, filename)
    
    # Résultat à renvoyer au processus principal
    result = {"status": "failed", "doc_id": doc_id, "error": None}

    if not os.path.exists(path):
        result["error"] = "File not found"
        return result

    try:
        # 1. OCR (CPU Heavy)
        text = ocr_pdf_to_text(path, dpi=dpi)
        if not text.strip():
            raise ValueError("OCR returned empty text")

        # 2. DB Ops (IO Bound) - Connexion courte durée
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                # Upsert OCR
                cur.execute("""
                    INSERT INTO stg_ocr_text(doc_id, run_id, ocr_text, ocr_engine)
                    VALUES (%s, %s, %s, 'tesseract')
                    ON CONFLICT (doc_id) DO UPDATE
                    SET ocr_text = EXCLUDED.ocr_text,
                        ocr_engine = EXCLUDED.ocr_engine,
                        created_at = now()
                """, (doc_id, run_id, text))

                # Update Document Status
                cur.execute("""
                    UPDATE dim_documents SET ocr_status = 'success' WHERE doc_id = %s
                """, (doc_id,))
            conn.commit() # Commit immédiat : si le script crash après, ce fichier est sauvé.
        
        result["status"] = "success"

    except Exception as e:
        result["error"] = str(e)
        # On loggue l'erreur en DB dans une nouvelle connexion pour être sûr
        try:
            with psycopg.connect(db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE dim_documents SET ocr_status = 'failed' WHERE doc_id = %s", (doc_id,))
                    cur.execute("""
                        INSERT INTO dq_issues(doc_id, run_id, severity, rule_name, details)
                        VALUES (%s, %s, 'FAIL', 'ocr_failed', %s)
                    """, (doc_id, run_id, str(e)))
                conn.commit()
        except Exception as db_e:
            logger.error(f"CRITICAL: DB Error while logging failure for {doc_id}: {db_e}")

    return result

# -----------------------------------------------------------------------------
# ORCHESTRATION
# -----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--input-dir", default="data/raw_pdfs")
    ap.add_argument("--dpi", type=int, default=200)
    # Ajout du paramètre workers pour contrôler la charge
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 4) 
    args = ap.parse_args()

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise SystemExit("Missing DATABASE_URL")

    logger.info(f"[START] OCR run_id={args.run_id} with {args.workers} workers")

    # 1. Récupérer la liste des tâches (Leger)
    docs_to_process = []
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT doc_id, source_filename
                FROM dim_documents
                WHERE run_id = %s
                ORDER BY source_filename
            """, (args.run_id,))
            docs_to_process = cur.fetchall()

    if not docs_to_process:
        logger.info("No documents found.")
        return

    logger.info(f"Queueing {len(docs_to_process)} documents...")

    # 2. Traitement Parallèle
    ok_count = 0
    fail_count = 0

    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        # Soumettre toutes les tâches
        futures = {
            executor.submit(process_single_document, doc, db_url, args.input_dir, args.run_id, args.dpi): doc 
            for doc in docs_to_process
        }

        # Traiter les résultats au fur et à mesure qu'ils arrivent
        for future in as_completed(futures):
            res = future.result()
            if res["status"] == "success":
                ok_count += 1
                logger.info(f"✅ Doc {res['doc_id']} processed.")
            else:
                fail_count += 1
                logger.error(f"❌ Doc {res['doc_id']} failed: {res['error']}")

    # 3. Mettre à jour le statut global du run
    logger.info(f"Run complete. OK={ok_count}, FAIL={fail_count}")
    
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            status = 'failed'
            if ok_count > 0 and fail_count == 0: status = 'success'
            elif ok_count > 0: status = 'partial'
            
            cur.execute("""
                UPDATE pipeline_runs
                SET finished_at = %s, status = %s::run_status
                WHERE run_id = %s
            """, (datetime.now(timezone.utc), status, args.run_id))
        conn.commit()

if __name__ == "__main__":
    main()
