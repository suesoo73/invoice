import os
import shutil
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings
from app.db.session import db_cursor
from app.schemas.jobs import DocumentReviewUpdate
from app.services.audit_service import insert_audit_log
from app.services.job_service import resolve_model_name
from app.services.query_service import get_document_detail


def _storage_path(filename: str) -> str:
    now = datetime.now()
    target_dir = Path(settings.storage_root) / "documents" / f"{now.year:04d}" / f"{now.month:02d}"
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"{uuid.uuid4()}-{Path(filename).name}"
    return str(target_dir / safe_name)


def create_document_and_queue_job(
    *,
    company_id: str,
    requested_by: str,
    document_type: str,
    model_name: str | None,
    upload_file: UploadFile,
) -> dict:
    chosen_model = resolve_model_name(model_name)
    document_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    stored_file_path = _storage_path(upload_file.filename or "upload.bin")

    with open(stored_file_path, "wb") as target:
        shutil.copyfileobj(upload_file.file, target)

    file_size = os.path.getsize(stored_file_path)
    mime_type = upload_file.content_type or "application/octet-stream"

    with db_cursor(dictionary=True) as (_, cursor):
        cursor.execute(
            "SELECT id FROM companies WHERE id = %s",
            (company_id,),
        )
        if not cursor.fetchone():
            raise ValueError("Company not found")

        cursor.execute(
            "SELECT id FROM users WHERE id = %s AND company_id = %s",
            (requested_by, company_id),
        )
        if not cursor.fetchone():
            raise ValueError("User not found for company")

        cursor.execute(
            """
            INSERT INTO documents (
                id, company_id, created_by, type, status, original_filename,
                file_path, file_size, mime_type, currency
            ) VALUES (%s, %s, %s, %s, 'processing', %s, %s, %s, %s, 'KRW')
            """,
            (
                document_id,
                company_id,
                requested_by,
                document_type,
                upload_file.filename or "upload.bin",
                stored_file_path,
                file_size,
                mime_type,
            ),
        )

        cursor.execute(
            """
            INSERT INTO document_jobs (
                id, document_id, job_type, status, retry_count, max_retries, requested_by, model_name
            ) VALUES (%s, %s, 'ocr', 'queued', 0, %s, %s, %s)
            """,
            (job_id, document_id, settings.ocr_max_retries, requested_by, chosen_model),
        )

        insert_audit_log(
            cursor,
            company_id=company_id,
            document_id=document_id,
            user_id=requested_by,
            action="upload",
            payload={
                "job_id": job_id,
                "stored_file_path": stored_file_path,
                "original_filename": upload_file.filename,
                "model_name": chosen_model,
            },
        )

        insert_audit_log(
            cursor,
            company_id=company_id,
            document_id=document_id,
            user_id=requested_by,
            action="ocr_queued",
            payload={
                "job_id": job_id,
                "stored_file_path": stored_file_path,
                "document_type": document_type,
                "model_name": chosen_model,
            },
        )

    return {
        "document_id": document_id,
        "job_id": job_id,
        "status": "queued",
        "stored_file_path": stored_file_path,
        "model_name": chosen_model,
    }


def _validate_user_in_company(cursor, requested_by: str, company_id: str) -> None:
    cursor.execute(
        "SELECT id FROM users WHERE id = %s AND company_id = %s",
        (requested_by, company_id),
    )
    if not cursor.fetchone():
        raise ValueError("User not found for company")


def update_document_review(document_id: str, payload: DocumentReviewUpdate) -> dict:
    with db_cursor(dictionary=True) as (_, cursor):
        cursor.execute(
            """
            SELECT id, company_id, status
            FROM documents
            WHERE id = %s
            """,
            (document_id,),
        )
        document = cursor.fetchone()
        if not document:
            raise ValueError("Document not found")

        _validate_user_in_company(cursor, payload.requested_by, document["company_id"])

        cursor.execute(
            """
            UPDATE documents
            SET vendor_name = %s,
                vendor_reg_no = %s,
                buyer_name = %s,
                buyer_reg_no = %s,
                issue_date = %s,
                supply_amount = %s,
                tax_amount = %s,
                total_amount = %s,
                payment_method = %s,
                invoice_number = %s,
                receipt_number = %s,
                status = 'review',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (
                payload.vendor_name,
                payload.vendor_reg_no,
                payload.buyer_name,
                payload.buyer_reg_no,
                payload.issue_date,
                payload.supply_amount,
                payload.tax_amount,
                payload.total_amount,
                payload.payment_method,
                payload.invoice_number,
                payload.receipt_number,
                document_id,
            ),
        )

        cursor.execute(
            "DELETE FROM document_items WHERE document_id = %s",
            (document_id,),
        )

        for item in payload.items:
            line_amount = item.line_amount
            if line_amount is None and item.quantity is not None and item.unit_price is not None:
                line_amount = float(Decimal(str(item.quantity)) * Decimal(str(item.unit_price)))

            cursor.execute(
                """
                INSERT INTO document_items (
                    id, document_id, line_no, item_name, quantity, unit_price, line_amount
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(uuid.uuid4()),
                    document_id,
                    item.line_no,
                    item.item_name,
                    item.quantity,
                    item.unit_price,
                    line_amount,
                ),
            )

        insert_audit_log(
            cursor,
            company_id=document["company_id"],
            document_id=document_id,
            user_id=payload.requested_by,
            action="review_updated",
            payload={
                "item_count": len(payload.items),
                "status_before": document["status"],
            },
        )

    detail = get_document_detail(document_id)
    if not detail:
        raise ValueError("Document not found after update")
    return detail


def complete_document_review(document_id: str, requested_by: str) -> dict:
    with db_cursor(dictionary=True) as (_, cursor):
        cursor.execute(
            """
            SELECT id, company_id, status
            FROM documents
            WHERE id = %s
            """,
            (document_id,),
        )
        document = cursor.fetchone()
        if not document:
            raise ValueError("Document not found")

        _validate_user_in_company(cursor, requested_by, document["company_id"])

        cursor.execute(
            """
            UPDATE documents
            SET status = 'completed',
                reviewed_by = %s,
                reviewed_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (requested_by, document_id),
        )

        insert_audit_log(
            cursor,
            company_id=document["company_id"],
            document_id=document_id,
            user_id=requested_by,
            action="review_completed",
            payload={
                "status_before": document["status"],
            },
        )

    detail = get_document_detail(document_id)
    if not detail:
        raise ValueError("Document not found after completion")
    return detail


def trash_document(document_id: str, requested_by: str) -> dict:
    with db_cursor(dictionary=True) as (_, cursor):
        cursor.execute(
            """
            SELECT id, company_id, status, deleted_at
            FROM documents
            WHERE id = %s
            """,
            (document_id,),
        )
        document = cursor.fetchone()
        if not document:
            raise ValueError("Document not found")
        if document["deleted_at"] is not None:
            raise ValueError("Document already deleted")

        _validate_user_in_company(cursor, requested_by, document["company_id"])
        purge_at = datetime.now() + timedelta(days=7)

        cursor.execute(
            """
            UPDATE documents
            SET status = 'deleted',
                deleted_at = CURRENT_TIMESTAMP,
                purge_at = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (purge_at, document_id),
        )

        cursor.execute(
            """
            INSERT INTO deleted_documents (
                id, document_id, company_id, deleted_by, deleted_at, purge_at
            ) VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, %s)
            ON DUPLICATE KEY UPDATE
                deleted_by = VALUES(deleted_by),
                deleted_at = CURRENT_TIMESTAMP,
                purge_at = VALUES(purge_at)
            """,
            (str(uuid.uuid4()), document_id, document["company_id"], requested_by, purge_at),
        )

        insert_audit_log(
            cursor,
            company_id=document["company_id"],
            document_id=document_id,
            user_id=requested_by,
            action="document_deleted",
            payload={"status_before": document["status"], "purge_at": purge_at.isoformat()},
        )

    detail = get_document_detail(document_id)
    if not detail:
        raise ValueError("Document not found after delete")
    return detail


def restore_document(document_id: str, requested_by: str) -> dict:
    with db_cursor(dictionary=True) as (_, cursor):
        cursor.execute(
            """
            SELECT id, company_id, status, deleted_at, purge_at
            FROM documents
            WHERE id = %s
            """,
            (document_id,),
        )
        document = cursor.fetchone()
        if not document:
            raise ValueError("Document not found")
        if document["deleted_at"] is None:
            raise ValueError("Document is not deleted")
        if document["purge_at"] and document["purge_at"] < datetime.now():
            raise ValueError("Trash retention period expired")

        _validate_user_in_company(cursor, requested_by, document["company_id"])

        cursor.execute(
            """
            UPDATE documents
            SET status = 'review',
                deleted_at = NULL,
                purge_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (document_id,),
        )

        cursor.execute(
            "DELETE FROM deleted_documents WHERE document_id = %s",
            (document_id,),
        )

        insert_audit_log(
            cursor,
            company_id=document["company_id"],
            document_id=document_id,
            user_id=requested_by,
            action="document_restored",
            payload={"status_before": document["status"]},
        )

    detail = get_document_detail(document_id)
    if not detail:
        raise ValueError("Document not found after restore")
    return detail
