from app.db.session import db_cursor


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
            SELECT id, status, retry_count, max_retries, model_name, error_message, requested_at, started_at, completed_at
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
            SELECT line_no, item_name, quantity, unit_price, line_amount
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
