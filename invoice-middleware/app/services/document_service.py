import json
import os
import shutil
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import fitz
from fastapi import UploadFile
from PIL import Image

from app.core.config import settings
from app.db.session import db_cursor
from app.schemas.jobs import DocumentReviewUpdate
from app.services.audit_service import insert_audit_log
from app.services.job_service import resolve_model_name
from app.services.ocr_service import extract_fields_with_llm
from app.services.parser_service import coerce_issue_date, fallback_parse_from_text, merge_with_fallback
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
            ) VALUES (%s, %s, %s, %s, 'queued', %s, %s, %s, %s, 'KRW')
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
                id, document_id, job_type, status, retry_count, max_retries, requested_by, model_name, use_grayscale
            ) VALUES (%s, %s, 'ocr', 'queued', 0, %s, %s, %s, %s)
            """,
            (job_id, document_id, settings.ocr_max_retries, requested_by, chosen_model, True),
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
                "use_grayscale": True,
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
                "use_grayscale": True,
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


def _coerce_decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    return Decimal(str(value))


def _normalize_item_for_storage(item: dict) -> dict:
    quantity = _coerce_decimal(item.get("quantity"))
    unit_price = _coerce_decimal(item.get("unit_price"))
    line_amount = _coerce_decimal(item.get("line_amount"))
    tax_amount = _coerce_decimal(item.get("tax_amount"))
    total_amount = _coerce_decimal(item.get("total_amount"))

    if line_amount is None and quantity is not None and unit_price is not None:
        line_amount = quantity * unit_price
    if tax_amount is None:
        tax_amount = Decimal("0")
    if total_amount is None:
        total_amount = (line_amount or Decimal("0")) + tax_amount

    return {
        "line_no": item.get("line_no"),
        "item_name": item.get("item_name") or "",
        "quantity": float(quantity) if quantity is not None else None,
        "unit_price": float(unit_price) if unit_price is not None else None,
        "line_amount": float(line_amount) if line_amount is not None else None,
        "tax_amount": float(tax_amount) if tax_amount is not None else None,
        "total_amount": float(total_amount) if total_amount is not None else None,
    }


def _sum_document_amounts(items: list[dict], fallback_fields: dict | None = None) -> tuple[float | None, float | None, float | None]:
    normalized_items = [_normalize_item_for_storage(item) for item in items if item]
    meaningful_items = [item for item in normalized_items if any(item.get(key) is not None for key in ("line_amount", "tax_amount", "total_amount"))]
    if not meaningful_items:
        fields = fallback_fields or {}
        return fields.get("supply_amount"), fields.get("tax_amount"), fields.get("total_amount")

    supply_amount = sum(Decimal(str(item.get("line_amount") or 0)) for item in meaningful_items)
    tax_amount = sum(Decimal(str(item.get("tax_amount") or 0)) for item in meaningful_items)
    total_amount = sum(Decimal(str(item.get("total_amount") or ((item.get("line_amount") or 0) + (item.get("tax_amount") or 0)))) for item in meaningful_items)
    return float(supply_amount), float(tax_amount), float(total_amount)


def _replace_document_items(cursor, document_id: str, items: list[dict]) -> None:
    cursor.execute(
        "DELETE FROM document_items WHERE document_id = %s",
        (document_id,),
    )

    for index, item in enumerate(items, start=1):
        normalized = _normalize_item_for_storage(item)
        line_no = normalized.get("line_no") or index

        cursor.execute(
            """
            INSERT INTO document_items (
                id, document_id, line_no, item_name, quantity, unit_price, line_amount, tax_amount, total_amount
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                str(uuid.uuid4()),
                document_id,
                line_no,
                normalized.get("item_name") or "",
                normalized.get("quantity"),
                normalized.get("unit_price"),
                normalized.get("line_amount"),
                normalized.get("tax_amount"),
                normalized.get("total_amount"),
            ),
        )


def _apply_extracted_result(cursor, document_id: str, result: dict) -> None:
    fields = result["fields"]
    items = result.get("items") or []
    safe_issue_date = coerce_issue_date(fields.get("issue_date"))
    supply_amount, tax_amount, total_amount = _sum_document_amounts(items, fields)
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
            fields.get("vendor_name"),
            fields.get("vendor_reg_no"),
            fields.get("buyer_name"),
            fields.get("buyer_reg_no"),
            safe_issue_date,
            supply_amount,
            tax_amount,
            total_amount,
            fields.get("currency") or "KRW",
            fields.get("payment_method"),
            fields.get("invoice_number"),
            fields.get("receipt_number"),
            document_id,
        ),
    )
    _replace_document_items(cursor, document_id, items)


def reextract_document_fields(document_id: str, requested_by: str, model_name: str | None) -> dict:
    detail = get_document_detail(document_id)
    if not detail:
        raise ValueError("Document not found")

    document = detail["document"]
    ocr_raw = detail.get("ocr_raw") or {}
    ocr_text = (ocr_raw.get("raw_text") or "").strip()
    if not ocr_text:
        raise ValueError("OCR raw text not found")

    chosen_model = resolve_model_name(model_name)
    parsed, llm_payload = extract_fields_with_llm(
        model_name=chosen_model,
        document_type=document["type"],
        ocr_text=ocr_text,
    )
    parsed = merge_with_fallback(parsed, fallback_parse_from_text(ocr_text))

    with db_cursor(dictionary=True) as (_, cursor):
        _validate_user_in_company(cursor, requested_by, document["company_id"])

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
                document_id,
                ocr_text,
                json.dumps(llm_payload),
                "v1-fields-only",
            ),
        )

        _apply_extracted_result(cursor, document_id, parsed)

        insert_audit_log(
            cursor,
            company_id=document["company_id"],
            document_id=document_id,
            user_id=requested_by,
            action="fields_reextracted",
            payload={
                "model_name": chosen_model,
                "item_count": len(parsed.get("items") or []),
            },
        )

    refreshed = get_document_detail(document_id)
    if not refreshed:
        raise ValueError("Document not found after field re-extraction")
    return refreshed


def _rotate_image_file(file_path: str, mime_type: str | None, degrees: int) -> None:
    with Image.open(file_path) as image:
        rotated = image.rotate(-degrees, expand=True)
        save_format = image.format or ("PNG" if (mime_type or "").lower() == "image/png" else "JPEG")
        rotated.save(file_path, format=save_format)


def _rotate_pdf_file(file_path: str, degrees: int) -> None:
    with fitz.open(file_path) as source:
        rotated = fitz.open()
        for page_index in range(source.page_count):
            page = source.load_page(page_index)
            width = page.rect.width
            height = page.rect.height
            if degrees in (90, 270):
                new_page = rotated.new_page(width=height, height=width)
            else:
                new_page = rotated.new_page(width=width, height=height)
            new_page.show_pdf_page(new_page.rect, source, page_index, rotate=degrees)

        temp_path = f"{file_path}.rotated"
        rotated.save(temp_path, deflate=True)
        rotated.close()

    os.replace(temp_path, file_path)


def _normalize_crop_ratios(x_ratio: float, y_ratio: float, width_ratio: float, height_ratio: float) -> tuple[float, float, float, float]:
    left = max(0.0, min(1.0, float(x_ratio)))
    top = max(0.0, min(1.0, float(y_ratio)))
    width = max(0.01, min(1.0, float(width_ratio)))
    height = max(0.01, min(1.0, float(height_ratio)))
    right = min(1.0, left + width)
    bottom = min(1.0, top + height)
    if right <= left or bottom <= top:
        raise ValueError("Crop area is invalid")
    return left, top, right, bottom


def _crop_image_file(file_path: str, left: float, top: float, right: float, bottom: float, mime_type: str | None) -> None:
    with Image.open(file_path) as image:
        width, height = image.size
        crop_box = (
            int(round(width * left)),
            int(round(height * top)),
            int(round(width * right)),
            int(round(height * bottom)),
        )
        cropped = image.crop(crop_box)
        save_format = image.format or ("PNG" if (mime_type or "").lower() == "image/png" else "JPEG")
        cropped.save(file_path, format=save_format)


def _crop_pdf_file(file_path: str, left: float, top: float, right: float, bottom: float) -> None:
    with fitz.open(file_path) as source:
        cropped = fitz.open()
        for page_index in range(source.page_count):
            page = source.load_page(page_index)
            rect = page.rect
            clip = fitz.Rect(
                rect.x0 + rect.width * left,
                rect.y0 + rect.height * top,
                rect.x0 + rect.width * right,
                rect.y0 + rect.height * bottom,
            )
            new_page = cropped.new_page(width=clip.width, height=clip.height)
            new_page.show_pdf_page(new_page.rect, source, page_index, clip=clip)

        temp_path = f"{file_path}.cropped"
        cropped.save(temp_path, deflate=True)
        cropped.close()

    os.replace(temp_path, file_path)


def render_document_preview_image(file_path: str, mime_type: str | None) -> bytes:
    preview_path = Path(f"{file_path}.preview.jpg")
    lower_mime = (mime_type or "").lower()

    if lower_mime == "application/pdf" or file_path.lower().endswith(".pdf"):
        with fitz.open(file_path) as document:
            if document.page_count == 0:
                raise ValueError("Document preview could not be generated")
            page = document.load_page(0)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            pix.save(preview_path)
    elif lower_mime.startswith("image/"):
        with Image.open(file_path) as image:
            image.convert("RGB").save(preview_path, format="JPEG", quality=90)
    else:
        raise ValueError("Unsupported file type for preview")

    try:
        return preview_path.read_bytes()
    finally:
        preview_path.unlink(missing_ok=True)


def rotate_document_file(document_id: str, requested_by: str, degrees: int) -> dict:
    normalized = degrees % 360
    if normalized not in (90, 180, 270):
        raise ValueError("Rotation must be one of 90, 180, 270")

    with db_cursor(dictionary=True) as (_, cursor):
        cursor.execute(
            """
            SELECT id, company_id, status, file_path, mime_type
            FROM documents
            WHERE id = %s
            """,
            (document_id,),
        )
        document = cursor.fetchone()
        if not document:
            raise ValueError("Document not found")

        _validate_user_in_company(cursor, requested_by, document["company_id"])

        file_path = document["file_path"]
        mime_type = (document.get("mime_type") or "").lower()
        if not file_path or not os.path.exists(file_path):
            raise ValueError("Document file not found")

        if mime_type == "application/pdf" or file_path.lower().endswith(".pdf"):
            _rotate_pdf_file(file_path, normalized)
        elif mime_type.startswith("image/"):
            _rotate_image_file(file_path, mime_type, normalized)
        else:
            raise ValueError("Unsupported file type for rotation")

        file_size = os.path.getsize(file_path)
        cursor.execute(
            """
            UPDATE documents
            SET file_size = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (file_size, document_id),
        )

        insert_audit_log(
            cursor,
            company_id=document["company_id"],
            document_id=document_id,
            user_id=requested_by,
            action="document_rotated",
            payload={
                "degrees": normalized,
                "status_before": document["status"],
                "file_path": file_path,
            },
        )

    detail = get_document_detail(document_id)
    if not detail:
        raise ValueError("Document not found after rotate")
    return detail


def crop_document_file(
    document_id: str,
    requested_by: str,
    x_ratio: float,
    y_ratio: float,
    width_ratio: float,
    height_ratio: float,
) -> dict:
    left, top, right, bottom = _normalize_crop_ratios(x_ratio, y_ratio, width_ratio, height_ratio)

    with db_cursor(dictionary=True) as (_, cursor):
        cursor.execute(
            """
            SELECT id, company_id, status, file_path, mime_type
            FROM documents
            WHERE id = %s
            """,
            (document_id,),
        )
        document = cursor.fetchone()
        if not document:
            raise ValueError("Document not found")

        _validate_user_in_company(cursor, requested_by, document["company_id"])

        file_path = document["file_path"]
        mime_type = (document.get("mime_type") or "").lower()
        if not file_path or not os.path.exists(file_path):
            raise ValueError("Document file not found")

        if mime_type == "application/pdf" or file_path.lower().endswith(".pdf"):
            _crop_pdf_file(file_path, left, top, right, bottom)
        elif mime_type.startswith("image/"):
            _crop_image_file(file_path, left, top, right, bottom, mime_type)
        else:
            raise ValueError("Unsupported file type for crop")

        file_size = os.path.getsize(file_path)
        cursor.execute(
            """
            UPDATE documents
            SET file_size = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (file_size, document_id),
        )

        insert_audit_log(
            cursor,
            company_id=document["company_id"],
            document_id=document_id,
            user_id=requested_by,
            action="document_cropped",
            payload={
                "crop": {
                    "x_ratio": left,
                    "y_ratio": top,
                    "width_ratio": right - left,
                    "height_ratio": bottom - top,
                },
                "status_before": document["status"],
                "file_path": file_path,
            },
        )

    detail = get_document_detail(document_id)
    if not detail:
        raise ValueError("Document not found after crop")
    return detail


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

        normalized_items = [item.model_dump() for item in payload.items]
        supply_amount, tax_amount, total_amount = _sum_document_amounts(normalized_items, {
            "supply_amount": payload.supply_amount,
            "tax_amount": payload.tax_amount,
            "total_amount": payload.total_amount,
        })

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
                supply_amount,
                tax_amount,
                total_amount,
                payload.payment_method,
                payload.invoice_number,
                payload.receipt_number,
                document_id,
            ),
        )

        _replace_document_items(cursor, document_id, normalized_items)

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
