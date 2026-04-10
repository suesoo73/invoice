import json
import logging
import time
import uuid

from app.core.config import settings
from app.db.session import db_cursor
from app.services.audit_service import insert_audit_log
from app.services.job_service import resolve_model_name
from app.services.ocr_service import run_ocr_with_model

logger = logging.getLogger("invoice_middleware.worker")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def reset_stale_processing_jobs() -> int:
    with db_cursor(dictionary=True) as (_, cursor):
        cursor.execute(
            """
            SELECT id, document_id, retry_count, max_retries, model_name
            FROM document_jobs
            WHERE status = 'processing'
              AND started_at < (CURRENT_TIMESTAMP - INTERVAL %s SECOND)
            """,
            (settings.worker_stale_after_seconds,),
        )
        stale_jobs = cursor.fetchall()

        for job in stale_jobs:
            next_retry_count = job["retry_count"] + 1
            should_retry = next_retry_count <= job["max_retries"]

            cursor.execute(
                """
                UPDATE document_jobs
                SET
                    status = %s,
                    retry_count = %s,
                    error_message = %s,
                    updated_at = CURRENT_TIMESTAMP,
                    started_at = NULL
                WHERE id = %s
                """,
                (
                    "queued" if should_retry else "failed",
                    next_retry_count,
                    "Job recovered after stale processing timeout",
                    job["id"],
                ),
            )

            cursor.execute(
                """
                UPDATE documents
                SET status = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                ("uploaded" if should_retry else "failed", job["document_id"]),
            )

            logger.warning(
                "Recovered stale OCR job",
                extra={
                    "job_id": job["id"],
                    "document_id": job["document_id"],
                    "model_name": job["model_name"],
                },
            )

        return len(stale_jobs)


def fetch_next_job():
    with db_cursor(dictionary=True) as (_, cursor):
        cursor.execute(
            """
            SELECT
                j.id,
                j.document_id,
                j.retry_count,
                j.max_retries,
                j.requested_by,
                j.model_name,
                d.company_id,
                d.file_path,
                d.type
            FROM document_jobs j
            JOIN documents d ON d.id = j.document_id
            WHERE j.status = 'queued'
            ORDER BY j.requested_at ASC
            LIMIT 1
            """
        )
        job = cursor.fetchone()

        if not job:
            return None

        logger.info(
            "Picked queued OCR job",
            extra={
                "job_id": job["id"],
                "document_id": job["document_id"],
                "model_name": job["model_name"],
            },
        )

        cursor.execute(
            """
            UPDATE document_jobs
            SET status = 'processing', started_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (job["id"],),
        )

        return job


def complete_job(job: dict, ocr_result: dict) -> None:
    logger.info(
        "Completing OCR job",
        extra={
            "job_id": job["id"],
            "document_id": job["document_id"],
            "item_count": len(ocr_result["items"]),
        },
    )
    with db_cursor(dictionary=True) as (_, cursor):
        cursor.execute(
            """
            INSERT INTO document_ocr_raw (
                id, document_id, raw_text, llm_response_json, parser_version
            ) VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                raw_text = VALUES(raw_text),
                llm_response_json = VALUES(llm_response_json),
                parser_version = VALUES(parser_version),
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                str(uuid.uuid4()),
                job["document_id"],
                ocr_result["raw_text"],
                json.dumps(ocr_result["llm_response_json"]),
                "v1",
            ),
        )

        cursor.execute(
            """
            UPDATE document_jobs
            SET status = 'completed', completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (job["id"],),
        )

        cursor.execute(
            """
            UPDATE documents
            SET
                status = 'review',
                vendor_name = %s,
                issue_date = %s,
                supply_amount = %s,
                tax_amount = %s,
                total_amount = %s,
                currency = %s,
                payment_method = %s,
                invoice_number = %s,
                receipt_number = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (
                ocr_result["fields"]["vendor_name"],
                ocr_result["fields"]["issue_date"],
                ocr_result["fields"]["supply_amount"],
                ocr_result["fields"]["tax_amount"],
                ocr_result["fields"]["total_amount"],
                ocr_result["fields"]["currency"],
                ocr_result["fields"]["payment_method"],
                ocr_result["fields"]["invoice_number"],
                ocr_result["fields"]["receipt_number"],
                job["document_id"],
            ),
        )

        cursor.execute(
            "DELETE FROM document_items WHERE document_id = %s",
            (job["document_id"],),
        )

        for item in ocr_result["items"]:
            cursor.execute(
                """
                INSERT INTO document_items (
                    id, document_id, line_no, item_name, quantity, unit_price, line_amount
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(uuid.uuid4()),
                    job["document_id"],
                    item["line_no"],
                    item["item_name"],
                    item["quantity"],
                    item["unit_price"],
                    item["line_amount"],
                ),
            )

        insert_audit_log(
            cursor,
            company_id=job["company_id"],
            document_id=job["document_id"],
            user_id=job["requested_by"],
            action="ocr_completed",
            payload={
                "job_id": job["id"],
                "model_name": ocr_result["model_name"],
                "item_count": len(ocr_result["items"]),
            },
        )


def fail_job(job: dict, error_message: str) -> None:
    next_retry_count = job["retry_count"] + 1
    should_retry = next_retry_count <= job["max_retries"]
    logger.exception(
        "OCR job failed",
        extra={
            "job_id": job["id"],
            "document_id": job["document_id"],
            "retry_count": next_retry_count,
            "model_name": job.get("model_name") or settings.ollama_model,
        },
    )

    with db_cursor(dictionary=True) as (_, cursor):
        cursor.execute(
            """
            UPDATE document_jobs
            SET
                status = %s,
                retry_count = %s,
                error_message = %s,
                updated_at = CURRENT_TIMESTAMP,
                completed_at = CASE WHEN %s = 'failed' THEN CURRENT_TIMESTAMP ELSE completed_at END
            WHERE id = %s
            """,
            (
                "queued" if should_retry else "failed",
                next_retry_count,
                error_message[:1000],
                "queued" if should_retry else "failed",
                job["id"],
            ),
        )

        cursor.execute(
            """
            UPDATE documents
            SET status = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            ("processing" if should_retry else "failed", job["document_id"]),
        )

        insert_audit_log(
            cursor,
            company_id=job["company_id"],
            document_id=job["document_id"],
            user_id=job["requested_by"],
            action="ocr_failed" if not should_retry else "ocr_retried",
            payload={
                "job_id": job["id"],
                "retry_count": next_retry_count,
                "error_message": error_message[:500],
                "model_name": job.get("model_name") or settings.ollama_model,
            },
        )


def process_next_job() -> bool:
    job = fetch_next_job()
    if not job:
        return False

    model_name = resolve_model_name(job.get("model_name"))
    logger.info(
        "Starting OCR model call",
        extra={
            "job_id": job["id"],
            "document_id": job["document_id"],
            "model_name": model_name,
            "file_path": job["file_path"],
        },
    )
    try:
        result = run_ocr_with_model(
            model_name=model_name,
            file_path=job["file_path"],
            document_type=job["type"],
        )
        logger.info(
            "Finished OCR model call",
            extra={
                "job_id": job["id"],
                "document_id": job["document_id"],
                "model_name": model_name,
            },
        )
        complete_job(job, result)
    except Exception as exc:
        fail_job(job, str(exc))
    return True


def run_worker(poll_interval: int = 5) -> None:
    while True:
        recovered = reset_stale_processing_jobs()
        if recovered:
            logger.warning("Recovered stale jobs count=%s", recovered)
        processed = process_next_job()
        if not processed:
            time.sleep(poll_interval)


def run_once() -> bool:
    return process_next_job()


if __name__ == "__main__":
    run_worker()
