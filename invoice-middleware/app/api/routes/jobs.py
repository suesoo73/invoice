from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.core.security import verify_internal_token
from app.schemas.jobs import DocumentReprocessRequest, DocumentReviewUpdate, OCRJobCreate
from app.services.document_service import (
    complete_document_review,
    create_document_and_queue_job,
    restore_document,
    trash_document,
    update_document_review,
)
from app.services.job_service import enqueue_ocr_job
from app.services.query_service import get_document_detail, list_documents

router = APIRouter()


@router.post("/jobs")
def create_job(
    payload: OCRJobCreate,
    _: None = Depends(verify_internal_token),
) -> dict:
    try:
        return enqueue_ocr_job(payload)
    except ValueError as exc:
        message = str(exc)
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
                if message.startswith("Unsupported model") or message.startswith("Document company mismatch")
                else status.HTTP_404_NOT_FOUND
            ),
            detail=message,
        ) from exc


@router.post("/uploads")
def upload_document_for_ocr(
    company_id: str = Form(...),
    requested_by: str = Form(...),
    document_type: str = Form(...),
    model_name: str | None = Form(default=None),
    file: UploadFile = File(...),
    _: None = Depends(verify_internal_token),
) -> dict:
    try:
        return create_document_and_queue_job(
            company_id=company_id,
            requested_by=requested_by,
            document_type=document_type,
            model_name=model_name,
            upload_file=file,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/documents/{document_id}")
def get_document_status(
    document_id: str,
    _: None = Depends(verify_internal_token),
) -> dict:
    payload = get_document_detail(document_id)
    if not payload:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return payload


@router.get("/documents")
def get_document_list(
    company_id: str,
    limit: int = 20,
    trashed: bool = False,
    _: None = Depends(verify_internal_token),
) -> dict:
    return {
        "items": list_documents(company_id=company_id, limit=limit, trashed=trashed),
    }


@router.patch("/documents/{document_id}/review")
def update_document_review_route(
    document_id: str,
    payload: DocumentReviewUpdate,
    _: None = Depends(verify_internal_token),
) -> dict:
    try:
        return update_document_review(document_id, payload)
    except ValueError as exc:
        message = str(exc)
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
                if message.startswith("User not found")
                else status.HTTP_404_NOT_FOUND
            ),
            detail=message,
        ) from exc


@router.post("/documents/{document_id}/complete")
def complete_document_review_route(
    document_id: str,
    payload: DocumentReprocessRequest,
    _: None = Depends(verify_internal_token),
) -> dict:
    try:
        return complete_document_review(document_id, payload.requested_by)
    except ValueError as exc:
        message = str(exc)
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
                if message.startswith("User not found")
                else status.HTTP_404_NOT_FOUND
            ),
            detail=message,
        ) from exc


@router.post("/documents/{document_id}/reprocess")
def reprocess_document_route(
    document_id: str,
    payload: DocumentReprocessRequest,
    _: None = Depends(verify_internal_token),
) -> dict:
    detail = get_document_detail(document_id)
    if not detail:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    document = detail["document"]
    try:
        return enqueue_ocr_job(
            OCRJobCreate(
                document_id=document_id,
                company_id=document["company_id"],
                file_path=document["file_path"],
                document_type=document["type"],
                requested_by=payload.requested_by,
                model_name=payload.model_name,
            )
        )
    except ValueError as exc:
        message = str(exc)
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
                if message.startswith("Unsupported model") or message.startswith("Document company mismatch")
                else status.HTTP_404_NOT_FOUND
            ),
            detail=message,
        ) from exc


@router.delete("/documents/{document_id}")
def trash_document_route(
    document_id: str,
    payload: DocumentReprocessRequest,
    _: None = Depends(verify_internal_token),
) -> dict:
    try:
        return trash_document(document_id, payload.requested_by)
    except ValueError as exc:
        message = str(exc)
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
                if message.startswith("User not found") or message.startswith("Document already") or message.startswith("Document is not")
                else status.HTTP_404_NOT_FOUND
            ),
            detail=message,
        ) from exc


@router.post("/documents/{document_id}/restore")
def restore_document_route(
    document_id: str,
    payload: DocumentReprocessRequest,
    _: None = Depends(verify_internal_token),
) -> dict:
    try:
        return restore_document(document_id, payload.requested_by)
    except ValueError as exc:
        message = str(exc)
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
                if message.startswith("User not found") or message.startswith("Document is not") or message.startswith("Trash retention")
                else status.HTTP_404_NOT_FOUND
            ),
            detail=message,
        ) from exc
