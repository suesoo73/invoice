import json
import logging
import time
import uuid
from datetime import datetime, timezone

from app.core.config import settings
from app.db.session import db_cursor
from app.services.audit_service import insert_audit_log
from app.services.document_service import _sum_document_amounts
from app.services.job_service import resolve_model_name
from app.services.ocr_service import run_ocr_with_model
from app.services.parser_service import coerce_issue_date

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
                ("queued" if should_retry else "failed", job["document_id"]),
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
                j.use_grayscale,
                d.company_id,
                d.file_path,
                d.type
            FROM document_jobs j
            JOIN documents d ON d.id = j.document_id
            WHERE j.status = 'queued'
            ORDER BY COALESCE(j.requested_at, j.created_at) ASC, j.id ASC
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

        cursor.execute(
            """
            UPDATE documents
            SET status = 'processing', updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (job["document_id"],),
        )

        return job


def has_queued_job() -> bool:
    with db_cursor(dictionary=True) as (_, cursor):
        cursor.execute(
            """
            SELECT 1
            FROM document_jobs
            WHERE status = 'queued'
            LIMIT 1
            """
        )
        return cursor.fetchone() is not None


def get_last_ocr_finish_time() -> datetime | None:
    with db_cursor(dictionary=True) as (_, cursor):
        cursor.execute(
            """
            SELECT MAX(finished_at) AS last_finished_at
            FROM (
                SELECT completed_at AS finished_at
                FROM document_jobs
                WHERE completed_at IS NOT NULL

                UNION ALL

                SELECT updated_at AS finished_at
                FROM document_jobs
                WHERE status = 'queued'
                  AND error_message IS NOT NULL
            ) AS job_finishes
            """
        )
        row = cursor.fetchone() or {}
        return row.get("last_finished_at")


def wait_for_ocr_gap(min_gap_seconds: int = 60) -> None:
    last_finished_at = get_last_ocr_finish_time()
    if not last_finished_at:
        return

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    elapsed_seconds = (now_utc - last_finished_at).total_seconds()
    wait_seconds = max(0.0, min_gap_seconds - elapsed_seconds)
    if wait_seconds <= 0:
        return

    logger.info("Waiting %.1f seconds before next OCR job", wait_seconds)
    time.sleep(wait_seconds)


def complete_job(job: dict, ocr_result: dict) -> None:
    logger.info(
        "Completing OCR job",
        extra={
            "job_id": job["id"],
            "document_id": job["document_id"],
            "item_count": len(ocr_result["items"]),
        },
    )
    supply_amount, tax_amount, total_amount = _sum_document_amounts(
        ocr_result["items"],
        ocr_result["fields"],
    )
    safe_issue_date = coerce_issue_date(ocr_result["fields"].get("issue_date"))
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
            SET status = 'completed',
                error_message = NULL,
                completed_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
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
                vendor_reg_no = %s,
                buyer_name = %s,
                buyer_reg_no = %s,
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
                ocr_result["fields"]["vendor_reg_no"],
                ocr_result["fields"]["buyer_name"],
                ocr_result["fields"]["buyer_reg_no"],
                safe_issue_date,
                supply_amount,
                tax_amount,
                total_amount,
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
                    id, document_id, line_no, item_name, quantity, unit_price, line_amount, tax_amount, total_amount
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(uuid.uuid4()),
                    job["document_id"],
                    item["line_no"],
                    item["item_name"],
                    item["quantity"],
                    item["unit_price"],
                    item["line_amount"],
                    item.get("tax_amount"),
                    item.get("total_amount") if item.get("total_amount") is not None else (item.get("line_amount") or 0) + (item.get("tax_amount") or 0),
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
                "use_grayscale": ocr_result.get("use_grayscale", True),
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
            "model_name": job.get("model_name") or settings.default_llm_model,
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
            ("queued" if should_retry else "failed", job["document_id"]),
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
                "model_name": job.get("model_name") or settings.default_llm_model,
            },
        )


def process_next_job() -> bool:
    if not has_queued_job():
        return False

    wait_for_ocr_gap()
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
            use_grayscale=bool(job.get("use_grayscale", 1)),
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
