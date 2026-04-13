from app.db.session import db_cursor
from app.services.runtime_config_service import get_active_llm_config


def list_documents(company_id: str, limit: int = 20, trashed: bool = False) -> list[dict]:
    with db_cursor(dictionary=True) as (_, cursor):
        cursor.execute(
            """
            SELECT
                d.id,
                d.type,
                d.status,
                d.original_filename,
                d.vendor_name,
                d.buyer_name,
                d.issue_date,
                d.supply_amount,
                d.tax_amount,
                d.total_amount,
                d.currency,
                d.deleted_at,
                d.purge_at,
                d.created_at,
                j.model_name,
                j.status AS job_status
            FROM documents d
            LEFT JOIN document_jobs j
              ON j.id = (
                SELECT j2.id
                FROM document_jobs j2
                WHERE j2.document_id = d.id
                ORDER BY j2.created_at DESC, j2.id DESC
                LIMIT 1
              )
            WHERE d.company_id = %s
              AND (
                (%s = 1 AND d.deleted_at IS NOT NULL)
                OR (%s = 0 AND d.deleted_at IS NULL)
              )
            ORDER BY d.created_at DESC
            LIMIT %s
            """,
            (company_id, 1 if trashed else 0, 1 if trashed else 0, limit),
        )
        return cursor.fetchall()


def get_document_detail(document_id: str) -> dict | None:
    with db_cursor(dictionary=True) as (_, cursor):
        cursor.execute(
            """
            SELECT
                id,
                company_id,
                type,
                status,
                original_filename,
                file_path,
                mime_type,
                vendor_name,
                vendor_reg_no,
                buyer_name,
                buyer_reg_no,
                issue_date,
                supply_amount,
                tax_amount,
                total_amount,
                currency,
                invoice_number,
                receipt_number,
                deleted_at,
                purge_at,
                created_at,
                updated_at
            FROM documents
            WHERE id = %s
            """,
            (document_id,),
        )
        document = cursor.fetchone()
        if not document:
            return None

        cursor.execute(
            """
            SELECT id, status, retry_count, max_retries, model_name, use_grayscale, error_message, requested_at, started_at, completed_at
            FROM document_jobs
            WHERE document_id = %s
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (document_id,),
        )
        job = cursor.fetchone()

        cursor.execute(
            """
            SELECT line_no, item_name, quantity, unit_price, line_amount, tax_amount, total_amount
            FROM document_items
            WHERE document_id = %s
            ORDER BY line_no ASC
            """,
            (document_id,),
        )
        items = cursor.fetchall()

        cursor.execute(
            """
            SELECT raw_text, llm_response_json, parser_version, updated_at
            FROM document_ocr_raw
            WHERE document_id = %s
            """,
            (document_id,),
        )
        ocr_raw = cursor.fetchone()

    return {
        "document": document,
        "job": job,
        "items": items,
        "ocr_raw": ocr_raw,
    }


def get_operator_overview(limit: int = 10) -> dict:
    with db_cursor(dictionary=True) as (_, cursor):
        cursor.execute(
            """
            SELECT
                COUNT(*) AS total_documents,
                SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) AS queued_documents,
                SUM(CASE WHEN status = 'processing' THEN 1 ELSE 0 END) AS processing_documents,
                SUM(CASE WHEN status = 'review' THEN 1 ELSE 0 END) AS review_documents,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_documents,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_documents,
                SUM(CASE WHEN status = 'deleted' THEN 1 ELSE 0 END) AS deleted_documents
            FROM documents
            """
        )
        document_counts = cursor.fetchone()

        cursor.execute(
            """
            SELECT
                COUNT(*) AS total_jobs,
                SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) AS queued_jobs,
                SUM(CASE WHEN status = 'processing' THEN 1 ELSE 0 END) AS processing_jobs,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_jobs,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_jobs
            FROM document_jobs
            """
        )
        job_counts = cursor.fetchone()

        cursor.execute(
            """
            SELECT
                d.id AS document_id,
                d.original_filename,
                d.company_id,
                d.status AS document_status,
                d.updated_at,
                j.id AS job_id,
                j.status AS job_status,
                j.retry_count,
                j.max_retries,
                j.model_name,
                j.error_message,
                j.requested_at,
                j.started_at,
                j.completed_at
            FROM document_jobs j
            JOIN documents d ON d.id = j.document_id
            ORDER BY COALESCE(j.started_at, j.requested_at) DESC, j.id DESC
            LIMIT %s
            """,
            (limit,),
        )
        recent_jobs = cursor.fetchall()

    return {
        "document_counts": document_counts,
        "job_counts": job_counts,
        "recent_jobs": recent_jobs,
        "llm_config": get_active_llm_config(),
    }
