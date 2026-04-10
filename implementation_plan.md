# Invoice / Receipt OCR Service Implementation Plan

## 1. MVP Build Order

### Phase 1. Foundation
- Set up separate web app project
- Implement WordPress JWT validation flow
- Add tenant-aware user session handling
- Create MySQL 8 schema and initial migrations

### Phase 2. Upload and Storage
- Build multi-file upload UI
- Store files through middleware
- Save document metadata and create OCR jobs
- Add audit logging for login and upload

### Phase 3. OCR Pipeline
- Build middleware queue worker
- Integrate middleware with LLM OCR server
- Save raw OCR text and structured fields
- Add automatic retry logic up to 2 times

### Phase 4. Review and Search
- Build document list and filter UI
- Build detail review screen with item line editing
- Add complete action and search indexing strategy
- Add manual OCR retry button

### Phase 5. Reports and Operations
- Build monthly and quarterly summary reports
- Add Excel and PDF export
- Add trash and restore flow with 7-day retention
- Add cleanup batch for expired trash

## 2. Suggested Team Split

### Web App
- Login integration
- Upload UI
- List and search UI
- Review UI
- Report UI

### Middleware
- File storage
- OCR job queue
- Retry handling
- Internal authentication
- Audit logging

### LLM OCR
- OCR extraction prompt and parser
- PDF/image ingestion pipeline
- Structured JSON output contract
- OCR callback integration

## 3. Key Risks

- Table-style item extraction quality may vary by vendor format
- Large PDF files may increase OCR processing time
- WordPress JWT payload may not include enough tenant info by default
- Search by item name may need indexing optimization after data grows
- PDF export layout can become complex if users expect custom formatting

## 4. Early Technical Decisions Recommended

- Database: MySQL 8, preferably a separate database on the same MySQL server used by WordPress
- Object/file storage: S3-compatible storage or dedicated file volume
- Queue: Redis-based queue or database-backed queue
- Web app API: REST
- OCR output format: strict JSON schema enforced in middleware

## 5. First Dev Milestone Definition

Goal:
- A user can log in with JWT, upload one or more files, wait for OCR completion, review extracted data, and search completed documents.

Completion criteria:
- Multi-file upload works
- OCR pipeline completes end-to-end
- Raw text and structured data are saved
- Review screen can edit and complete a document
- Search works by date, vendor, amount, and item name

## 6. Operational Jobs Needed

- `trash_purge_job`: permanently delete documents after 7 days in trash
- `ocr_retry_job`: retry failed OCR jobs up to 2 times
- `report_cache_job`: optional precompute for heavy report queries later

## 7. Next Deliverables

- Frontend screen wireframes
- OpenAPI or Swagger spec
- SQL migration files
- OCR JSON schema
- Infrastructure deployment diagram
