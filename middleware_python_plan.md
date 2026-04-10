# Python Middleware Plan

## 1. Recommendation

- Language: Python
- Framework: FastAPI
- ASGI server: Uvicorn
- Background jobs: simple DB-backed worker first, optional Redis/Celery later
- DB: MySQL
- Runtime location: LLM server `192.168.20.14`

## 2. Responsibilities

- Receive upload-processing requests from `invoice-app`
- Store file metadata and OCR job state
- Pull OCR work from DB-backed queue
- Call local Ollama / OCR service on the same server
- Parse OCR output into structured JSON
- Save raw OCR text and parsed fields into MySQL
- Handle retry logic up to 2 times
- Write audit logs

## 3. Suggested Project Structure

```text
invoice-middleware/
  app/
    api/
      routes/
        health.py
        jobs.py
        internal.py
    core/
      config.py
      security.py
    db/
      base.py
      models.py
      session.py
    schemas/
      jobs.py
      ocr.py
    services/
      job_service.py
      ocr_service.py
      parser_service.py
      storage_service.py
      audit_service.py
    workers/
      ocr_worker.py
    main.py
  requirements.txt
  Dockerfile
  .env.example
```

## 4. HTTP Endpoints

- `GET /health`
- `POST /internal/ocr/jobs`
- `POST /internal/ocr/callback`
- `POST /internal/ocr/jobs/{job_id}/retry`

## 5. First Build Goal

- Middleware can accept an OCR job request
- Save a queued job in MySQL
- Worker picks the job
- Worker calls local Ollama endpoint
- Worker saves raw and parsed OCR result
- Worker updates job/document status
