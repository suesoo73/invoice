# Middleware FastAPI Outline

## `POST /internal/ocr/jobs`

Used by the web app backend after upload metadata is ready.

Request body:

```json
{
  "document_id": "uuid",
  "company_id": "uuid",
  "file_path": "/storage/documents/2026/04/file.pdf",
  "document_type": "invoice",
  "requested_by": "uuid"
}
```

Behavior:
- validate internal token
- create `document_jobs` row with `queued`
- set document status to `processing`
- return job info

## `POST /internal/ocr/callback`

Optional callback route if OCR worker is separated later.
For initial build, the worker can write directly to DB and this route may remain internal-only.

## `GET /health`

Returns:
- app health
- db connectivity
- ollama connectivity

## Worker Loop

1. poll `document_jobs` where `status='queued'`
2. mark job `processing`
3. load source file
4. extract OCR text through local Ollama pipeline
5. parse structured fields
6. update `documents`, `document_items`, `document_ocr_raw`
7. set job `completed` and document `review`
8. on failure, increment retry and requeue until max 2
