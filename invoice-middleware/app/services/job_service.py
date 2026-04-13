import uuid

from app.core.config import settings
from app.db.session import db_cursor
from app.schemas.jobs import OCRJobCreate
from app.services.audit_service import insert_audit_log
from app.services.runtime_config_service import get_active_llm_config


def resolve_model_name(model_name: str | None) -> str:
    active_config = get_active_llm_config()
    chosen = model_name or active_config["default_model"]
    if not chosen:
        raise ValueError("No default model configured for the selected LLM backend")
    allowed_models = active_config["allowed_models"]
    if chosen not in allowed_models:
        raise ValueError(
            f"Unsupported model '{chosen}'. Allowed models: {', '.join(allowed_models)}"
        )
    return chosen


def enqueue_ocr_job(payload: OCRJobCreate) -> dict:
    model_name = resolve_model_name(payload.model_name)

    with db_cursor(dictionary=True) as (_, cursor):
        cursor.execute(
            """
            SELECT id, company_id, status
            FROM documents
            WHERE id = %s
            """,
            (payload.document_id,),
        )
        document = cursor.fetchone()

        if not document:
            raise ValueError("Document not found")

        if document["company_id"] != payload.company_id:
            raise ValueError("Document company mismatch")

        job_id = str(uuid.uuid4())

        cursor.execute(
            """
            INSERT INTO document_jobs (
                id, document_id, job_type, status, retry_count, max_retries, requested_by, model_name, use_grayscale
            ) VALUES (%s, %s, 'ocr', 'queued', 0, %s, %s, %s, %s)
            """,
            (job_id, payload.document_id, settings.ocr_max_retries, payload.requested_by, model_name, payload.use_grayscale),
        )

        cursor.execute(
            """
            UPDATE documents
            SET status = 'queued', updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (payload.document_id,),
        )

        insert_audit_log(
            cursor,
            company_id=payload.company_id,
            document_id=payload.document_id,
            user_id=payload.requested_by,
            action="ocr_queued",
            payload={
                "job_id": job_id,
                "file_path": payload.file_path,
                "document_type": payload.document_type,
                "model_name": model_name,
                "use_grayscale": payload.use_grayscale,
            },
        )

    return {
        "job_id": job_id,
        "status": "queued",
        "document_id": payload.document_id,
        "company_id": payload.company_id,
        "retry_count": 0,
        "model_name": model_name,
        "use_grayscale": payload.use_grayscale,
    }
