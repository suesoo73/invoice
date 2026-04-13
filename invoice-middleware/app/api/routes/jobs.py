from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, Response

from app.core.security import verify_internal_token
from app.schemas.jobs import (
    DocumentCropRequest,
    DocumentReprocessRequest,
    DocumentReviewUpdate,
    DocumentRotateRequest,
    OCRJobCreate,
    OperatorLLMBackendUpdate,
)
from app.services.document_service import (
    complete_document_review,
    crop_document_file,
    create_document_and_queue_job,
    reextract_document_fields,
    render_document_preview_image,
    rotate_document_file,
    restore_document,
    trash_document,
    update_document_review,
)
from app.services.job_service import enqueue_ocr_job
from app.services.query_service import get_document_detail, get_operator_overview, list_documents
from app.services.runtime_config_service import get_active_llm_config, update_llm_backend

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


@router.get("/documents/{document_id}/file")
def get_document_file(
    document_id: str,
    _: None = Depends(verify_internal_token),
):
    payload = get_document_detail(document_id)
    if not payload:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    document = payload["document"]
    file_path = document.get("file_path")
    if not file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File path not found")

    return FileResponse(
        path=file_path,
        media_type=document.get("mime_type") or "application/octet-stream",
        filename=document.get("original_filename") or None,
        content_disposition_type="inline",
    )


@router.get("/documents/{document_id}/preview-image")
def get_document_preview_image(
    document_id: str,
    _: None = Depends(verify_internal_token),
):
    payload = get_document_detail(document_id)
    if not payload:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    document = payload["document"]
    file_path = document.get("file_path")
    if not file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File path not found")

    try:
        image_bytes = render_document_preview_image(file_path, document.get("mime_type"))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return Response(content=image_bytes, media_type="image/jpeg")


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


@router.get("/operator/overview")
def get_operator_overview_route(
    limit: int = 10,
    _: None = Depends(verify_internal_token),
) -> dict:
    return get_operator_overview(limit=limit)


@router.get("/operator/llm-config")
def get_operator_llm_config_route(
    _: None = Depends(verify_internal_token),
) -> dict:
    return {"config": get_active_llm_config()}


@router.post("/operator/llm-config")
def update_operator_llm_config_route(
    payload: OperatorLLMBackendUpdate,
    _: None = Depends(verify_internal_token),
) -> dict:
    try:
        return {
            "config": update_llm_backend(
                payload.llm_backend,
                payload.default_model,
                payload.ocr_backend,
                payload.ocr_model,
                payload.external_llm_api_key,
            )
        }
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


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
                use_grayscale=payload.use_grayscale,
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


@router.post("/documents/{document_id}/reprocess-fields")
def reprocess_document_fields_route(
    document_id: str,
    payload: DocumentReprocessRequest,
    _: None = Depends(verify_internal_token),
) -> dict:
    try:
        return reextract_document_fields(document_id, payload.requested_by, payload.model_name)
    except ValueError as exc:
        message = str(exc)
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
                if message.startswith("User not found") or message.startswith("OCR raw text not found") or message.startswith("Unsupported model")
                else status.HTTP_404_NOT_FOUND
            ),
            detail=message,
        ) from exc


@router.post("/documents/{document_id}/rotate")
def rotate_document_route(
    document_id: str,
    payload: DocumentRotateRequest,
    _: None = Depends(verify_internal_token),
) -> dict:
    try:
        return rotate_document_file(document_id, payload.requested_by, payload.degrees)
    except ValueError as exc:
        message = str(exc)
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
                if message.startswith("Rotation") or message.startswith("Unsupported file type")
                else status.HTTP_404_NOT_FOUND
            ),
            detail=message,
        ) from exc


@router.post("/documents/{document_id}/crop")
def crop_document_route(
    document_id: str,
    payload: DocumentCropRequest,
    _: None = Depends(verify_internal_token),
) -> dict:
    try:
        return crop_document_file(
            document_id,
            payload.requested_by,
            payload.x_ratio,
            payload.y_ratio,
            payload.width_ratio,
            payload.height_ratio,
        )
    except ValueError as exc:
        message = str(exc)
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
                if message.startswith("Crop") or message.startswith("Unsupported file type")
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
